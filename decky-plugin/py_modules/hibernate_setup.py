"""Hibernate (S4) setup for OneXPlayer Apex on Bazzite.

S0i3 deep sleep is broken on Strix Halo (kernel 6.17, needs 6.18+ for ACPI C4).
S3 is not available (ACPI supports S0, S4, S5 only).
Hibernate (S4 — suspend to disk) is the only viable deep power-save option.

HHD's hibernate wraps systemctl hibernate, which works, but Bazzite's default
configuration is missing the resume infrastructure:
  1. No dracut resume module in initramfs → kernel never checks for hibernate image
  2. zram used by default → no persistent swap for hibernate to write to
  3. No resume= / resume_offset= kernel params → kernel doesn't know where to look

This module automates the full setup so hibernate actually resumes properly.
"""

import logging
import os
import subprocess
import tempfile

logger = logging.getLogger("OXP-Hibernate")

# Allow main.py to inject its log callbacks
_log_info = logger.info
_log_error = logger.error
_log_warning = logger.warning


def set_log_callbacks(info_fn, error_fn, warning_fn):
    global _log_info, _log_error, _log_warning
    _log_info = info_fn
    _log_error = error_fn
    _log_warning = warning_fn


# Paths
SWAP_SUBVOL = "/var/swap"
SWAP_FILE = "/var/swap/swapfile"
DRACUT_RESUME_CONF = "/etc/dracut.conf.d/resume.conf"
ZRAM_CONF = "/etc/systemd/zram-generator.conf"
FSTAB = "/etc/fstab"
FSTAB_MARKER = "# OXP-Apex hibernate swap"
POLKIT_RULE = "/etc/polkit-1/rules.d/85-oxp-hibernate.rules"
SLEEP_CONF = "/etc/systemd/sleep.conf.d/oxp-hibernate.conf"
LOGIND_OVERRIDE_DIR = "/etc/systemd/system/systemd-logind.service.d"
LOGIND_OVERRIDE = "/etc/systemd/system/systemd-logind.service.d/oxp-hibernate.conf"
HIBERNATE_OVERRIDE_DIR = "/etc/systemd/system/systemd-hibernate.service.d"
HIBERNATE_OVERRIDE = "/etc/systemd/system/systemd-hibernate.service.d/oxp-hibernate.conf"
SLEEP_HOOK = "/usr/lib/systemd/system-sleep/99-oxp-hibernate"

# BMI260 IMU (accelerometer/gyroscope) on GPIO 90 generates an interrupt storm
# during hibernate that hangs the kernel image write. This hook unbinds the
# driver before hibernate and rebinds it after resume.
SLEEP_HOOK_CONTENT = """#!/bin/bash
# OXP Apex Tools — disable BMI260 IMU before hibernate
# GPIO 90 (BMI260 data-ready interrupt) fires continuously during hibernate,
# preventing the kernel from completing the image write.

DRIVER_PATH="/sys/bus/i2c/drivers/bmi260_i2c"
DEVICE="i2c-BMI0160:00"

case "$1/$2" in
    pre/hibernate|pre/suspend-then-hibernate)
        if [ -e "$DRIVER_PATH/$DEVICE" ]; then
            echo "$DEVICE" > "$DRIVER_PATH/unbind" 2>/dev/null
        fi
        ;;
    post/hibernate|post/suspend-then-hibernate)
        if [ ! -e "$DRIVER_PATH/$DEVICE" ]; then
            echo "$DEVICE" > "$DRIVER_PATH/bind" 2>/dev/null
        fi
        ;;
esac
"""


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


def _run(cmd, timeout=120):
    """Run a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, env=_clean_env()
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _atomic_write(path, content):
    """Atomically write content to a file.

    Writes to a temp file in the same directory, fsyncs, then renames.
    This prevents empty/partial files if the process is killed mid-write.
    """
    target_dir = os.path.dirname(path)
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".oxp-tmp-")
    try:
        os.write(fd, content.encode())
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.rename(tmp_path, path)
    except BaseException:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _get_ram_gb():
    """Get total RAM in GB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
    except Exception:
        pass
    return 0


def _get_cmdline():
    """Read current kernel command line."""
    try:
        with open("/proc/cmdline") as f:
            return f.read().strip()
    except Exception:
        return ""


def _has_karg(name):
    """Check if a kernel argument (prefix) is present in cmdline."""
    cmdline = _get_cmdline()
    return any(arg.startswith(name) for arg in cmdline.split())


def _get_karg_value(prefix):
    """Extract the value of a kernel arg from /proc/cmdline.

    E.g. _get_karg_value("resume_offset=") → "400039168"
    Returns None if the arg is not present.
    """
    cmdline = _get_cmdline()
    for arg in cmdline.split():
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _get_swap_uuid():
    """Get the UUID of the filesystem containing the swapfile.
    Uses findmnt -T to target the swap file directly — works on BTRFS/ostree."""
    if os.path.exists(SWAP_FILE):
        rc, out, _ = _run(["findmnt", "-no", "UUID", "-T", SWAP_FILE])
        if rc == 0 and out:
            return out
    # Fallback: try /var (Bazzite/ostree real disk)
    for mount in ["/var", "/"]:
        rc, out, _ = _run(["findmnt", "-no", "UUID", mount])
        if rc == 0 and out:
            return out
    return None


def _get_swap_offset():
    """Get the BTRFS swapfile offset for the resume_offset kernel arg.

    Must use 'btrfs inspect-internal map-swapfile -r' — filefrag gives
    physical extents which are WRONG for BTRFS resume.
    """
    if not os.path.exists(SWAP_FILE):
        return None
    rc, out, _ = _run(["btrfs", "inspect-internal", "map-swapfile", "-r", SWAP_FILE])
    if rc == 0 and out:
        try:
            return int(out.strip())
        except ValueError:
            _log_error(f"Could not parse btrfs map-swapfile output: {out}")
    return None


def _swap_file_size_gb():
    """Get the size of the swap file in GB, or 0 if it doesn't exist."""
    if not os.path.exists(SWAP_FILE):
        return 0
    try:
        return os.path.getsize(SWAP_FILE) / (1024 ** 3)
    except Exception:
        return 0


def _is_swap_active():
    """Check if our swap file is currently active."""
    try:
        with open("/proc/swaps") as f:
            return SWAP_FILE in f.read()
    except Exception:
        return False


def _is_zram_disabled():
    """Check if zram is disabled (config file empty or absent)."""
    if not os.path.exists(ZRAM_CONF):
        return True
    try:
        with open(ZRAM_CONF) as f:
            content = f.read().strip()
        # Empty or only comments = disabled
        lines = [l for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
        return len(lines) == 0
    except Exception:
        return False


def _has_dracut_resume():
    """Check if dracut resume module is configured."""
    if not os.path.exists(DRACUT_RESUME_CONF):
        return False
    try:
        with open(DRACUT_RESUME_CONF) as f:
            return "resume" in f.read()
    except Exception:
        return False


def _has_fstab_swap():
    """Check if our swap entry is in fstab."""
    try:
        with open(FSTAB) as f:
            content = f.read()
        return SWAP_FILE in content
    except Exception:
        return False


def _has_polkit_rule():
    """Check if our polkit hibernate rule is installed."""
    return os.path.exists(POLKIT_RULE)


def _has_sleep_conf():
    """Check if our systemd sleep.conf override is installed and clean.

    Returns False if the file contains deprecated HibernateState (systemd 258
    warns about it), which forces a rewrite with clean content.
    """
    if not os.path.exists(SLEEP_CONF):
        return False
    try:
        with open(SLEEP_CONF) as f:
            content = f.read()
        if "HibernateState" in content:
            return False  # Stale — needs rewrite
        return "AllowHibernation=yes" in content
    except Exception:
        return False


def _has_systemd_overrides():
    """Check if systemd service overrides for BTRFS hibernate bypass are installed.

    On BTRFS, systemd can't match the swap file's anonymous device number to
    the resume block device, causing CanHibernate to return 'na'. The override
    sets SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK=1 to skip this broken check.
    """
    for path in (LOGIND_OVERRIDE, HIBERNATE_OVERRIDE):
        if not os.path.exists(path):
            return False
        try:
            with open(path) as f:
                if "SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK" not in f.read():
                    return False
        except Exception:
            return False
    return True


def _has_sleep_hook():
    """Check if the BMI260 hibernate sleep hook is installed."""
    if not os.path.exists(SLEEP_HOOK):
        return False
    try:
        with open(SLEEP_HOOK) as f:
            return "bmi260" in f.read().lower()
    except Exception:
        return False


def _install_sleep_hook():
    """Install the systemd sleep hook that unbinds BMI260 before hibernate."""
    hook_dir = os.path.dirname(SLEEP_HOOK)
    if not os.path.isdir(hook_dir):
        _log_warning(f"Sleep hook directory {hook_dir} not found")
        return False
    try:
        _atomic_write(SLEEP_HOOK, SLEEP_HOOK_CONTENT)
        os.chmod(SLEEP_HOOK, 0o755)
        _log_info(f"Installed BMI260 hibernate sleep hook at {SLEEP_HOOK}")
        return True
    except Exception as e:
        _log_error(f"Failed to install sleep hook: {e}")
        return False


def get_status():
    """Check the current state of hibernate configuration.

    Returns a dict with the status of each requirement.
    Validates that resume= and resume_offset= karg VALUES match the live
    swap file, not just that the kargs exist.
    """
    ram_gb = _get_ram_gb()
    swap_gb = _swap_file_size_gb()
    swap_sufficient = swap_gb >= ram_gb if ram_gb > 0 else False

    has_resume_karg = _has_karg("resume=")
    has_offset_karg = _has_karg("resume_offset=")

    # Get live values from the actual swap file
    live_uuid = _get_swap_uuid()
    live_offset = _get_swap_offset()

    # Get cmdline values
    cmdline_resume = _get_karg_value("resume=")
    cmdline_offset = _get_karg_value("resume_offset=")

    # Validate correctness (karg exists AND matches live value)
    expected_resume = f"UUID={live_uuid}" if live_uuid else None
    expected_offset = str(live_offset) if live_offset is not None else None

    resume_correct = has_resume_karg and cmdline_resume == expected_resume
    offset_correct = has_offset_karg and cmdline_offset == expected_offset

    # Mismatch = karg exists but value is wrong (stale)
    kargs_mismatch = (
        (has_resume_karg and not resume_correct) or
        (has_offset_karg and not offset_correct)
    )

    return {
        "ram_gb": round(ram_gb, 1),
        "swap_exists": os.path.exists(SWAP_FILE),
        "swap_gb": round(swap_gb, 1),
        "swap_sufficient": swap_sufficient,
        "swap_active": _is_swap_active(),
        "zram_disabled": _is_zram_disabled(),
        "has_resume_karg": has_resume_karg,
        "has_offset_karg": has_offset_karg,
        "resume_correct": resume_correct,
        "offset_correct": offset_correct,
        "kargs_mismatch": kargs_mismatch,
        "expected_offset": expected_offset,
        "cmdline_offset": cmdline_offset,
        "has_dracut_resume": _has_dracut_resume(),
        "has_fstab_entry": _has_fstab_swap(),
        "has_polkit_rule": _has_polkit_rule(),
        "has_sleep_conf": _has_sleep_conf(),
        "has_systemd_overrides": _has_systemd_overrides(),
        "has_sleep_hook": _has_sleep_hook(),
        "ready": all([
            swap_sufficient,
            _is_swap_active(),
            _is_zram_disabled(),
            resume_correct,
            offset_correct,
            _has_dracut_resume(),
            _has_systemd_overrides(),
            _has_sleep_hook(),
        ]),
    }


def setup(swap_size_gb=None):
    """Set up hibernate on Bazzite. This is a one-shot operation that:

    1. Creates BTRFS /swap subvolume
    2. Creates swap file (size = RAM or specified)
    3. Disables zram
    4. Adds fstab entry
    5. Adds resume= and resume_offset= kernel params
    6. Adds dracut resume module config
    7. Enables initramfs regeneration

    Returns dict with success status and details. Reboot is required after.
    """
    steps = []
    errors = []

    # Determine swap size
    ram_gb = _get_ram_gb()
    if swap_size_gb is None:
        swap_size_gb = int(ram_gb) + 2  # RAM + 2GB headroom
    swap_size_gb = max(swap_size_gb, int(ram_gb))

    # Check available disk space
    # Use /var for the check — on ostree/Bazzite, "/" is a tiny read-only
    # composefs image (always 100% full), but /var is on the real disk.
    try:
        disk_check_path = "/var" if os.path.exists("/var") else "/"
        stat = os.statvfs(disk_check_path)
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        if free_gb < swap_size_gb + 5:  # 5GB buffer
            return {
                "success": False,
                "error": f"Not enough disk space. Need {swap_size_gb + 5:.0f}GB, have {free_gb:.0f}GB free.",
            }
    except Exception as e:
        errors.append(f"Could not check disk space: {e}")

    # Step 1: Create BTRFS swap subvolume
    if not os.path.exists(SWAP_SUBVOL):
        rc, out, err = _run(["btrfs", "subvolume", "create", SWAP_SUBVOL])
        if rc == 0:
            steps.append("Created /swap BTRFS subvolume")
        else:
            # Maybe not BTRFS, try mkdir
            try:
                os.makedirs(SWAP_SUBVOL, exist_ok=True)
                steps.append("Created /swap directory (non-BTRFS)")
            except Exception as e:
                errors.append(f"Could not create /swap: {e}")
                return {"success": False, "error": "; ".join(errors), "steps": steps}
    else:
        steps.append("/swap already exists")

    # Step 1b: Fix SELinux context — var_t on directory, swapfile_t on swapfile
    rc, _, _ = _run(["semanage", "fcontext", "-a", "-t", "var_t", SWAP_SUBVOL])
    if rc == 0:
        _run(["restorecon", SWAP_SUBVOL])
        steps.append("Set SELinux context (var_t) on swap directory")
    # Not fatal if semanage isn't available

    # Step 2: Create swap file
    if not os.path.exists(SWAP_FILE):
        _log_info(f"Creating {swap_size_gb}GB swap file (this may take a while)...")

        # Try btrfs mkswapfile first (preferred for BTRFS)
        rc, out, err = _run(
            ["btrfs", "filesystem", "mkswapfile", "--size", f"{swap_size_gb}g", SWAP_FILE],
            timeout=600  # Large files take time
        )
        if rc == 0:
            steps.append(f"Created {swap_size_gb}GB swap file (btrfs mkswapfile)")
        else:
            # Fallback: manual creation
            _log_info("btrfs mkswapfile failed, trying manual creation...")
            try:
                # Create with fallocate (fast) or dd (slow fallback)
                rc2, _, _ = _run(
                    ["fallocate", "-l", f"{swap_size_gb}G", SWAP_FILE],
                    timeout=600
                )
                if rc2 != 0:
                    rc2, _, _ = _run(
                        ["dd", "if=/dev/zero", f"of={SWAP_FILE}",
                         "bs=1G", f"count={swap_size_gb}"],
                        timeout=600
                    )
                if rc2 != 0:
                    errors.append("Could not create swap file")
                    return {"success": False, "error": "; ".join(errors), "steps": steps}

                os.chmod(SWAP_FILE, 0o600)
                rc3, _, _ = _run(["mkswap", SWAP_FILE])
                if rc3 != 0:
                    errors.append("mkswap failed")
                    return {"success": False, "error": "; ".join(errors), "steps": steps}
                steps.append(f"Created {swap_size_gb}GB swap file (manual)")
            except Exception as e:
                errors.append(f"Swap file creation failed: {e}")
                return {"success": False, "error": "; ".join(errors), "steps": steps}
    else:
        current_gb = _swap_file_size_gb()
        if current_gb < ram_gb:
            steps.append(f"WARNING: Existing swap file ({current_gb:.0f}GB) is smaller than RAM ({ram_gb:.0f}GB)")
        else:
            steps.append(f"Swap file already exists ({current_gb:.0f}GB)")

    # Step 2b: Set SELinux context on swapfile
    rc, _, _ = _run(["semanage", "fcontext", "-a", "-t", "swapfile_t", SWAP_FILE])
    if rc == 0:
        _run(["restorecon", SWAP_FILE])
        steps.append("Set SELinux context (swapfile_t) on swapfile")

    # Step 3: Disable zram
    if not _is_zram_disabled():
        try:
            # Backup original
            if os.path.exists(ZRAM_CONF):
                backup = ZRAM_CONF + ".oxp-backup"
                if not os.path.exists(backup):
                    with open(ZRAM_CONF) as f:
                        orig = f.read()
                    with open(backup, "w") as f:
                        f.write(orig)

            # Write empty config to disable zram
            _atomic_write(ZRAM_CONF, "# zram disabled by OXP Apex Tools for hibernate support\n")
            steps.append("Disabled zram (backed up original config)")
        except Exception as e:
            errors.append(f"Could not disable zram: {e}")
    else:
        steps.append("zram already disabled")

    # Step 4: Add fstab entry
    if not _has_fstab_swap():
        try:
            with open(FSTAB, "a") as f:
                f.write(f"\n{FSTAB_MARKER}\n")
                f.write(f"{SWAP_FILE} none swap defaults,nofail 0 0\n")
            steps.append("Added swap entry to fstab")
        except Exception as e:
            errors.append(f"Could not update fstab: {e}")
    else:
        steps.append("fstab swap entry already exists")

    # Step 5: Activate swap now
    if not _is_swap_active():
        rc, _, err = _run(["swapon", SWAP_FILE])
        if rc == 0:
            steps.append("Activated swap file")
        else:
            _log_warning(f"swapon failed (will work after reboot): {err}")
            steps.append("Swap activation deferred to reboot")
    else:
        steps.append("Swap already active")

    # Step 6: Get resume parameters
    root_uuid = _get_swap_uuid()
    swap_offset = _get_swap_offset()

    if not root_uuid:
        errors.append("Could not determine root filesystem UUID")
        return {"success": False, "error": "; ".join(errors), "steps": steps}

    if swap_offset is None:
        errors.append("Could not determine swap file offset (filefrag failed)")
        return {"success": False, "error": "; ".join(errors), "steps": steps}

    steps.append(f"Resume params: UUID={root_uuid}, offset={swap_offset}")

    # Step 7: Add dracut resume module (BEFORE kargs — so initramfs rebuild
    # includes the resume module when rpm-ostree creates the new deployment)
    if not _has_dracut_resume():
        try:
            _atomic_write(
                DRACUT_RESUME_CONF,
                '# Added by OXP Apex Tools for hibernate support\n'
                'add_dracutmodules+=" resume "\n',
            )
            steps.append("Added dracut resume module config")
        except Exception as e:
            errors.append(f"Could not create dracut config: {e}")
    else:
        steps.append("dracut resume module already configured")

    # Step 8: Enable initramfs regeneration (BEFORE kargs — ensures the
    # deployment created by rpm-ostree kargs gets a fresh initramfs)
    rc, _, err = _run(["rpm-ostree", "initramfs", "--enable"])
    if rc == 0:
        steps.append("Enabled initramfs regeneration")
    else:
        if "already enabled" in err.lower() or "initramfs" in err.lower():
            steps.append("initramfs regeneration already enabled")
        else:
            _log_warning(f"rpm-ostree initramfs --enable: {err}")
            steps.append(f"initramfs regeneration warning: {err}")

    # Step 9: Add/update kernel parameters (LAST rpm-ostree step — creates
    # the final deployment with both kargs and regenerated initramfs)
    kargs_added = False
    cmdline = _get_cmdline()

    expected_resume = f"resume=UUID={root_uuid}"
    expected_offset = f"resume_offset={swap_offset}"

    # Build a single rpm-ostree kargs command to delete old + append new
    kargs_cmd = ["rpm-ostree", "kargs"]
    need_kargs = False

    # Handle resume= param
    current_resume = None
    for arg in cmdline.split():
        if arg.startswith("resume="):
            current_resume = arg

    if current_resume == expected_resume:
        steps.append("resume= karg already correct")
    else:
        if current_resume:
            kargs_cmd.append(f"--delete={current_resume}")
            steps.append(f"Replacing wrong resume karg: {current_resume}")
        kargs_cmd.append(f"--append={expected_resume}")
        need_kargs = True

    # Handle resume_offset= param
    current_offset = None
    for arg in cmdline.split():
        if arg.startswith("resume_offset="):
            current_offset = arg

    if current_offset == expected_offset:
        steps.append("resume_offset= karg already correct")
    else:
        if current_offset:
            kargs_cmd.append(f"--delete={current_offset}")
            steps.append(f"Replacing wrong resume_offset karg: {current_offset}")
        kargs_cmd.append(f"--append={expected_offset}")
        need_kargs = True

    if need_kargs:
        rc, _, err = _run(kargs_cmd)
        if rc == 0:
            steps.append(f"Set kernel params: {expected_resume}, {expected_offset}")
            kargs_added = True
        else:
            errors.append(f"rpm-ostree kargs failed: {err}")
    else:
        steps.append("Kernel parameters already correct")

    # Step 10: Install polkit rule (allows hibernate in desktop power menu)
    if not _has_polkit_rule():
        try:
            _atomic_write(
                POLKIT_RULE,
                '// Allow hibernate for all users — installed by OXP Apex Tools\n'
                'polkit.addRule(function(action, subject) {\n'
                '    if (action.id == "org.freedesktop.login1.hibernate" ||\n'
                '        action.id == "org.freedesktop.login1.handle-hibernate-key" ||\n'
                '        action.id == "org.freedesktop.login1.hibernate-multiple-sessions" ||\n'
                '        action.id == "org.freedesktop.login1.hibernate-ignore-inhibit") {\n'
                '        return polkit.Result.YES;\n'
                '    }\n'
                '});\n',
            )
            steps.append("Installed polkit hibernate rule")
        except Exception as e:
            _log_warning(f"Could not install polkit rule: {e}")
            steps.append(f"Polkit rule install failed: {e}")
    else:
        steps.append("Polkit hibernate rule already installed")

    # Step 11: Install systemd sleep.conf override (enable hibernate)
    if not _has_sleep_conf():
        try:
            _atomic_write(
                SLEEP_CONF,
                '# OXP Apex Tools — enable hibernate\n'
                '[Sleep]\n'
                'AllowHibernation=yes\n'
                'HibernateMode=shutdown\n',
            )
            steps.append("Installed systemd sleep.conf override")
        except Exception as e:
            _log_warning(f"Could not install sleep.conf: {e}")
            steps.append(f"sleep.conf install failed: {e}")
    else:
        steps.append("systemd sleep.conf already configured")

    # Step 12: Install systemd service overrides for BTRFS hibernate bypass.
    # On BTRFS, systemd can't match the swap file's anonymous btrfs device
    # number (e.g. 0:50) to /sys/power/resume (e.g. 252:0 for dm-0), so
    # CanHibernate returns "na". This override bypasses that broken check.
    if not _has_systemd_overrides():
        override_content = (
            '# OXP Apex Tools — bypass BTRFS swap device check for hibernate\n'
            '[Service]\n'
            'Environment=SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK=1\n'
        )
        try:
            for override_path in (LOGIND_OVERRIDE, HIBERNATE_OVERRIDE):
                _atomic_write(override_path, override_content)

            # Verify overrides were written correctly (guard against truncation)
            for override_path in (LOGIND_OVERRIDE, HIBERNATE_OVERRIDE):
                with open(override_path) as f:
                    written = f.read()
                if "SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK" not in written:
                    raise RuntimeError(f"Override file {override_path} verification failed — content: {written!r}")

            # Reload systemd and restart logind so the override takes effect now
            _run(["systemctl", "daemon-reload"])
            _run(["systemctl", "restart", "systemd-logind"])
            steps.append("Installed systemd BTRFS hibernate overrides + restarted logind")
        except Exception as e:
            _log_error(f"Could not install systemd overrides: {e}")
            errors.append(f"systemd override install failed: {e}")
    else:
        steps.append("systemd BTRFS hibernate overrides already installed")

    # Step 13: Install BMI260 hibernate sleep hook
    if not _has_sleep_hook():
        if _install_sleep_hook():
            steps.append("Installed BMI260 hibernate sleep hook")
        else:
            errors.append("Could not install BMI260 sleep hook")
    else:
        steps.append("BMI260 hibernate sleep hook already installed")

    if errors:
        return {
            "success": False,
            "error": "; ".join(errors),
            "steps": steps,
        }

    msg = "Hibernate setup complete. Reboot required for changes to take effect."
    if kargs_added:
        msg += " Note: button fix patches will need to be re-applied after reboot."

    _log_info(msg)
    return {
        "success": True,
        "reboot_needed": True,
        "message": msg,
        "steps": steps,
    }


def repair_kargs():
    """Fix stale resume=/resume_offset= kernel args to match the live swap file.

    This is a focused repair — only touches kargs, not the full setup.
    Use when the swap file offset has changed (e.g. after recreating the swap file)
    but the kernel cmdline still has the old values.
    """
    steps = []

    live_uuid = _get_swap_uuid()
    live_offset = _get_swap_offset()

    if not live_uuid:
        return {"success": False, "error": "Could not determine swap UUID — is the swap file present?"}
    if live_offset is None:
        return {"success": False, "error": "Could not determine swap offset — is the swap file present?"}

    expected_resume = f"resume=UUID={live_uuid}"
    expected_offset = f"resume_offset={live_offset}"

    cmdline = _get_cmdline()

    kargs_cmd = ["rpm-ostree", "kargs"]
    need_update = False

    # Check resume=
    current_resume = None
    for arg in cmdline.split():
        if arg.startswith("resume="):
            current_resume = arg

    if current_resume != expected_resume:
        if current_resume:
            kargs_cmd.append(f"--delete={current_resume}")
            steps.append(f"Remove stale: {current_resume}")
        kargs_cmd.append(f"--append={expected_resume}")
        steps.append(f"Set: {expected_resume}")
        need_update = True
    else:
        steps.append("resume= already correct")

    # Check resume_offset=
    current_offset = None
    for arg in cmdline.split():
        if arg.startswith("resume_offset="):
            current_offset = arg

    if current_offset != expected_offset:
        if current_offset:
            kargs_cmd.append(f"--delete={current_offset}")
            steps.append(f"Remove stale: {current_offset}")
        kargs_cmd.append(f"--append={expected_offset}")
        steps.append(f"Set: {expected_offset}")
        need_update = True
    else:
        steps.append("resume_offset= already correct")

    if not need_update:
        return {
            "success": True,
            "reboot_needed": False,
            "message": "Kernel args already match the live swap file.",
            "steps": steps,
        }

    _log_info(f"Repairing kargs: {' '.join(kargs_cmd)}")
    rc, _, err = _run(kargs_cmd)
    if rc != 0:
        return {"success": False, "error": f"rpm-ostree kargs failed: {err}", "steps": steps}

    msg = "Kernel args repaired. Reboot required. Re-apply button fix after reboot."
    _log_info(msg)
    return {
        "success": True,
        "reboot_needed": True,
        "message": msg,
        "steps": steps,
    }


def run_diagnostics():
    """Collect comprehensive hibernate diagnostic info and log it all.

    Returns the diagnostics as a dict and logs everything to the plugin log
    so "Save Logs" captures it for debugging.
    """
    diag = {}
    _log_info("=== HIBERNATE DIAGNOSTICS START ===")

    # 1. Status check
    status = get_status()
    diag["status"] = status
    _log_info(f"[DIAG] Status: {status}")

    # 2. /proc/cmdline
    cmdline = _get_cmdline()
    diag["cmdline"] = cmdline
    _log_info(f"[DIAG] /proc/cmdline: {cmdline}")

    # 3. /proc/swaps
    try:
        with open("/proc/swaps") as f:
            swaps = f.read().strip()
        diag["proc_swaps"] = swaps
        _log_info(f"[DIAG] /proc/swaps:\n{swaps}")
    except Exception as e:
        diag["proc_swaps"] = f"ERROR: {e}"
        _log_error(f"[DIAG] /proc/swaps read failed: {e}")

    # 4. swapon --show
    rc, out, err = _run(["swapon", "--show"])
    diag["swapon_show"] = out or err
    _log_info(f"[DIAG] swapon --show:\n{out or err}")

    # 5. Swap file details
    if os.path.exists(SWAP_FILE):
        try:
            st = os.stat(SWAP_FILE)
            diag["swap_file_size"] = st.st_size
            diag["swap_file_mode"] = oct(st.st_mode)
            _log_info(f"[DIAG] Swap file: size={st.st_size}, mode={oct(st.st_mode)}")
        except Exception as e:
            _log_error(f"[DIAG] Swap file stat failed: {e}")

        # SELinux context
        rc, out, err = _run(["ls", "-lZ", SWAP_FILE])
        diag["swap_selinux"] = out or err
        _log_info(f"[DIAG] Swap SELinux: {out or err}")

        # btrfs map-swapfile (correct offset for BTRFS)
        rc, out, err = _run(["btrfs", "inspect-internal", "map-swapfile", "-r", SWAP_FILE])
        diag["swap_btrfs_offset"] = out or err
        _log_info(f"[DIAG] btrfs map-swapfile: {out or err}")
    else:
        diag["swap_file_exists"] = False
        _log_warning(f"[DIAG] Swap file {SWAP_FILE} does not exist")

    # 6. findmnt (filesystem info)
    for mount in ["/var", "/"]:
        rc, out, err = _run(["findmnt", "-no", "SOURCE,UUID,FSTYPE,OPTIONS", mount])
        diag[f"findmnt_{mount.replace('/', '_') or 'root'}"] = out or err
        _log_info(f"[DIAG] findmnt {mount}: {out or err}")

    # 7. Root UUID
    root_uuid = _get_swap_uuid()
    diag["root_uuid"] = root_uuid
    _log_info(f"[DIAG] Root UUID: {root_uuid}")

    # 8. Swap offset
    swap_offset = _get_swap_offset()
    diag["swap_offset"] = swap_offset
    _log_info(f"[DIAG] Swap offset: {swap_offset}")

    # 9. Kernel resume= values vs actual
    cmdline_parts = cmdline.split()
    for part in cmdline_parts:
        if part.startswith("resume="):
            diag["cmdline_resume"] = part
            _log_info(f"[DIAG] Kernel resume param: {part}")
            # Check if the UUID device exists
            uuid_val = part.replace("resume=UUID=", "").replace("resume=", "")
            dev_path = f"/dev/disk/by-uuid/{uuid_val}"
            exists = os.path.exists(dev_path)
            if exists:
                real = os.path.realpath(dev_path)
                diag["resume_device_exists"] = True
                diag["resume_device_real"] = real
                _log_info(f"[DIAG] Resume UUID device {dev_path} -> {real} (EXISTS)")
            else:
                diag["resume_device_exists"] = False
                _log_error(f"[DIAG] Resume UUID device {dev_path} DOES NOT EXIST!")
        if part.startswith("resume_offset="):
            diag["cmdline_resume_offset"] = part
            _log_info(f"[DIAG] Kernel resume_offset param: {part}")

    # 10. /etc/fstab content
    try:
        with open(FSTAB) as f:
            fstab = f.read().strip()
        diag["fstab"] = fstab
        _log_info(f"[DIAG] /etc/fstab:\n{fstab}")
    except Exception as e:
        diag["fstab"] = f"ERROR: {e}"
        _log_error(f"[DIAG] fstab read failed: {e}")

    # 11. dracut config
    if os.path.exists(DRACUT_RESUME_CONF):
        try:
            with open(DRACUT_RESUME_CONF) as f:
                dracut = f.read().strip()
            diag["dracut_conf"] = dracut
            _log_info(f"[DIAG] {DRACUT_RESUME_CONF}:\n{dracut}")
        except Exception as e:
            diag["dracut_conf"] = f"ERROR: {e}"
    else:
        diag["dracut_conf"] = "NOT FOUND"
        _log_warning(f"[DIAG] {DRACUT_RESUME_CONF} does not exist")

    # 12. Check /sys/power/resume and resume_offset (what systemd actually uses)
    for sysfs in ["/sys/power/resume", "/sys/power/resume_offset", "/sys/power/state", "/sys/power/disk"]:
        try:
            with open(sysfs) as f:
                val = f.read().strip()
            diag[sysfs] = val
            _log_info(f"[DIAG] {sysfs}: {val}")
        except Exception as e:
            diag[sysfs] = f"ERROR: {e}"
            _log_warning(f"[DIAG] {sysfs}: {e}")

    # Also log the swap file's device number (to show the BTRFS mismatch)
    if os.path.exists(SWAP_FILE):
        try:
            st = os.stat(SWAP_FILE)
            swap_dev = f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
            diag["swap_file_devno"] = swap_dev
            _log_info(f"[DIAG] Swap file st_dev: {swap_dev} (BTRFS anonymous — won't match /sys/power/resume)")
        except Exception:
            pass

    # 13. Check if resume module is in initramfs
    rc, out, err = _run(["lsinitrd", "-m"], timeout=30)
    if rc == 0:
        resume_in_initrd = "resume" in out.lower()
        diag["resume_in_initramfs"] = resume_in_initrd
        if resume_in_initrd:
            resume_lines = [l for l in out.split("\n") if "resume" in l.lower()]
            _log_info(f"[DIAG] Resume module in initramfs: YES ({', '.join(resume_lines[:5])})")
        else:
            _log_warning("[DIAG] Resume module in initramfs: NO — hibernate won't resume after reboot!")
    else:
        diag["resume_in_initramfs"] = "lsinitrd failed"
        _log_warning(f"[DIAG] lsinitrd failed: {err}")

    # 13. systemd sleep config
    if os.path.exists(SLEEP_CONF):
        try:
            with open(SLEEP_CONF) as f:
                sleep_conf = f.read().strip()
            diag["sleep_conf"] = sleep_conf
            _log_info(f"[DIAG] {SLEEP_CONF}:\n{sleep_conf}")
        except Exception as e:
            diag["sleep_conf"] = f"ERROR: {e}"
    else:
        diag["sleep_conf"] = "NOT FOUND"
        _log_warning(f"[DIAG] {SLEEP_CONF} not found — hibernate may not show in desktop settings")

    # 14. polkit rule
    diag["polkit_rule_exists"] = os.path.exists(POLKIT_RULE)
    _log_info(f"[DIAG] Polkit rule installed: {os.path.exists(POLKIT_RULE)}")

    # 15. zram config
    if os.path.exists(ZRAM_CONF):
        try:
            with open(ZRAM_CONF) as f:
                zram = f.read().strip()
            diag["zram_conf"] = zram
            _log_info(f"[DIAG] {ZRAM_CONF}:\n{zram}")
        except Exception as e:
            diag["zram_conf"] = f"ERROR: {e}"
    else:
        diag["zram_conf"] = "NOT FOUND"
        _log_info(f"[DIAG] {ZRAM_CONF} not found (zram disabled)")

    # 16. List /dev/disk/by-uuid/ to help debug
    rc, out, err = _run(["ls", "-la", "/dev/disk/by-uuid/"])
    diag["disk_by_uuid"] = out or err
    _log_info(f"[DIAG] /dev/disk/by-uuid/:\n{out or err}")

    # 17. systemctl status for hibernate-related services
    for svc in ["systemd-hibernate.service", "systemd-logind.service"]:
        rc, out, err = _run(["systemctl", "status", svc, "--no-pager", "-l"])
        diag[f"systemctl_{svc}"] = out or err
        _log_info(f"[DIAG] systemctl status {svc}:\n{out or err}")

    # 19. Systemd BTRFS hibernate overrides
    diag["systemd_overrides"] = _has_systemd_overrides()
    _log_info(f"[DIAG] BTRFS hibernate overrides installed: {_has_systemd_overrides()}")

    # 20. Can hibernate? (D-Bus method call)
    rc, out, err = _run(["busctl", "call", "org.freedesktop.login1",
                         "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                         "CanHibernate"])
    diag["can_hibernate"] = out or err
    _log_info(f"[DIAG] CanHibernate: {out or err}")

    # 19. rpm-ostree status (deployment info)
    rc, out, err = _run(["rpm-ostree", "status", "--json"], timeout=30)
    if rc == 0:
        # Just log the kargs from the current deployment
        try:
            import json
            rpm_status = json.loads(out)
            deployments = rpm_status.get("deployments", [])
            if deployments:
                booted = [d for d in deployments if d.get("booted")]
                if booted:
                    kargs = booted[0].get("kernel-arguments", [])
                    diag["rpm_ostree_kargs"] = kargs
                    _log_info(f"[DIAG] rpm-ostree booted kargs: {kargs}")
                    initramfs_enabled = booted[0].get("regenerate-initramfs", False)
                    diag["initramfs_enabled"] = initramfs_enabled
                    _log_info(f"[DIAG] initramfs regeneration enabled: {initramfs_enabled}")
        except Exception as e:
            _log_warning(f"[DIAG] Could not parse rpm-ostree status: {e}")
    else:
        _log_warning(f"[DIAG] rpm-ostree status failed: {err}")

    _log_info("=== HIBERNATE DIAGNOSTICS END ===")
    return diag


def hibernate():
    """Trigger hibernate (S4 suspend to disk)."""
    status = get_status()
    if not status["ready"]:
        missing = []
        if not status["swap_exists"]:
            missing.append("no swap file")
        elif not status["swap_sufficient"]:
            missing.append(f"swap too small ({status['swap_gb']:.0f}GB for {status['ram_gb']:.0f}GB RAM)")
        if not status["swap_active"]:
            missing.append("swap not active")
        if not status["zram_disabled"]:
            missing.append("zram still enabled")
        if not status["has_resume_karg"]:
            missing.append("missing resume= karg")
        elif not status.get("resume_correct"):
            missing.append("resume= karg is stale — run Repair Kargs")
        if not status["has_offset_karg"]:
            missing.append("missing resume_offset= karg")
        elif not status.get("offset_correct"):
            missing.append(f"resume_offset is stale (cmdline={status.get('cmdline_offset')}, actual={status.get('expected_offset')}) — run Repair Kargs")
        if not status["has_dracut_resume"]:
            missing.append("missing dracut resume module")
        if not status["has_systemd_overrides"]:
            missing.append("missing systemd BTRFS hibernate overrides")

        _log_error(f"Hibernate not ready: {', '.join(missing)}")
        _log_info("Running diagnostics to help debug...")
        run_diagnostics()

        return {
            "success": False,
            "error": f"Hibernate not ready: {', '.join(missing)}. Run setup first.",
        }

    _log_info("Initiating hibernate (S4)...")
    rc, out, err = _run(["systemctl", "hibernate"], timeout=30)
    if rc == 0:
        return {"success": True, "message": "Hibernate initiated"}
    else:
        _log_error(f"systemctl hibernate failed: {err}")
        _log_info("Running diagnostics after hibernate failure...")
        run_diagnostics()
        return {"success": False, "error": f"systemctl hibernate failed: {err}"}


def remove():
    """Remove all hibernate configuration and restore defaults.

    WARNING: rpm-ostree kargs creates a new deployment, losing hotfix overlays.
    """
    steps = []
    reboot_needed = False
    cmdline = _get_cmdline()

    # Remove kernel params
    for prefix in ["resume=", "resume_offset="]:
        for arg in cmdline.split():
            if arg.startswith(prefix):
                rc, _, err = _run(["rpm-ostree", "kargs", f"--delete={arg}"])
                if rc == 0:
                    steps.append(f"Removed karg: {arg}")
                    reboot_needed = True
                else:
                    _log_warning(f"Could not remove karg {arg}: {err}")

    # Remove dracut config
    if os.path.exists(DRACUT_RESUME_CONF):
        try:
            os.remove(DRACUT_RESUME_CONF)
            steps.append("Removed dracut resume config")
            reboot_needed = True
        except Exception as e:
            _log_warning(f"Could not remove dracut config: {e}")

    # Deactivate swap
    if _is_swap_active():
        rc, _, _ = _run(["swapoff", SWAP_FILE])
        if rc == 0:
            steps.append("Deactivated swap file")

    # Remove fstab entry
    if _has_fstab_swap():
        try:
            with open(FSTAB) as f:
                lines = f.readlines()
            # Remove our marker and the swap line
            new_lines = []
            skip_next = False
            for line in lines:
                if FSTAB_MARKER in line:
                    skip_next = True
                    continue
                if skip_next and SWAP_FILE in line:
                    skip_next = False
                    continue
                skip_next = False
                new_lines.append(line)
            with open(FSTAB, "w") as f:
                f.writelines(new_lines)
            steps.append("Removed fstab swap entry")
        except Exception as e:
            _log_warning(f"Could not clean fstab: {e}")

    # Remove swap file
    if os.path.exists(SWAP_FILE):
        try:
            os.remove(SWAP_FILE)
            steps.append("Removed swap file")
        except Exception as e:
            _log_warning(f"Could not remove swap file: {e}")

    # Remove swap subvolume (only if we created it and it's empty)
    if os.path.exists(SWAP_SUBVOL):
        try:
            if not os.listdir(SWAP_SUBVOL):
                rc, _, _ = _run(["btrfs", "subvolume", "delete", SWAP_SUBVOL])
                if rc == 0:
                    steps.append("Removed /swap subvolume")
                else:
                    os.rmdir(SWAP_SUBVOL)
                    steps.append("Removed /swap directory")
        except Exception:
            pass  # Not empty or not removable — leave it

    # Remove polkit rule
    if os.path.exists(POLKIT_RULE):
        try:
            os.remove(POLKIT_RULE)
            steps.append("Removed polkit hibernate rule")
        except Exception as e:
            _log_warning(f"Could not remove polkit rule: {e}")

    # Remove sleep.conf override
    if os.path.exists(SLEEP_CONF):
        try:
            os.remove(SLEEP_CONF)
            steps.append("Removed systemd sleep.conf override")
        except Exception as e:
            _log_warning(f"Could not remove sleep.conf: {e}")

    # Remove systemd service overrides
    for override_path, override_dir in [
        (LOGIND_OVERRIDE, LOGIND_OVERRIDE_DIR),
        (HIBERNATE_OVERRIDE, HIBERNATE_OVERRIDE_DIR),
    ]:
        if os.path.exists(override_path):
            try:
                os.remove(override_path)
                # Remove dir if empty
                if os.path.isdir(override_dir) and not os.listdir(override_dir):
                    os.rmdir(override_dir)
            except Exception as e:
                _log_warning(f"Could not remove {override_path}: {e}")
    steps.append("Removed systemd hibernate overrides")
    _run(["systemctl", "daemon-reload"])

    # Restore zram
    backup = ZRAM_CONF + ".oxp-backup"
    if os.path.exists(backup):
        try:
            with open(backup) as f:
                orig = f.read()
            with open(ZRAM_CONF, "w") as f:
                f.write(orig)
            os.remove(backup)
            steps.append("Restored original zram config")
            reboot_needed = True
        except Exception as e:
            _log_warning(f"Could not restore zram config: {e}")

    if reboot_needed:
        msg = "Hibernate removed. Reboot required. Re-apply button fix after reboot."
    elif steps:
        msg = "Hibernate configuration cleaned up."
    else:
        msg = "No hibernate configuration found to remove."

    return {
        "success": True,
        "reboot_needed": reboot_needed,
        "message": msg,
        "steps": steps,
    }
