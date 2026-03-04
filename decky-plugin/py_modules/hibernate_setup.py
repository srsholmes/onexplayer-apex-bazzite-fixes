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
SWAP_SUBVOL = "/swap"
SWAP_FILE = "/swap/swapfile"
DRACUT_RESUME_CONF = "/etc/dracut.conf.d/resume.conf"
ZRAM_CONF = "/etc/systemd/zram-generator.conf"
FSTAB = "/etc/fstab"
FSTAB_MARKER = "# OXP-Apex hibernate swap"


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


def _get_root_uuid():
    """Get UUID of the root filesystem."""
    rc, out, _ = _run(["findmnt", "-no", "UUID", "/"])
    return out if rc == 0 else None


def _get_swap_offset():
    """Get the resume_offset for the swap file on BTRFS."""
    if not os.path.exists(SWAP_FILE):
        return None
    rc, out, _ = _run(["filefrag", "-v", SWAP_FILE])
    if rc != 0 or not out:
        return None
    # Parse filefrag output — the offset is the first physical offset
    # Format: "ext: logical_offset: physical_offset: length: ..."
    for line in out.split("\n"):
        line = line.strip()
        if line and line[0].isdigit():
            parts = line.split()
            if len(parts) >= 4:
                # physical_offset is the 3rd field (index 2), strip trailing ".."
                offset_str = parts[2].rstrip(".").rstrip(".")
                try:
                    return int(offset_str)
                except ValueError:
                    continue
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


def get_status():
    """Check the current state of hibernate configuration.

    Returns a dict with the status of each requirement.
    """
    ram_gb = _get_ram_gb()
    swap_gb = _swap_file_size_gb()
    swap_sufficient = swap_gb >= ram_gb if ram_gb > 0 else False

    has_resume_karg = _has_karg("resume=")
    has_offset_karg = _has_karg("resume_offset=")

    return {
        "ram_gb": round(ram_gb, 1),
        "swap_exists": os.path.exists(SWAP_FILE),
        "swap_gb": round(swap_gb, 1),
        "swap_sufficient": swap_sufficient,
        "swap_active": _is_swap_active(),
        "zram_disabled": _is_zram_disabled(),
        "has_resume_karg": has_resume_karg,
        "has_offset_karg": has_offset_karg,
        "has_dracut_resume": _has_dracut_resume(),
        "has_fstab_entry": _has_fstab_swap(),
        "ready": all([
            swap_sufficient,
            _is_swap_active(),
            _is_zram_disabled(),
            has_resume_karg,
            has_offset_karg,
            _has_dracut_resume(),
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
    try:
        stat = os.statvfs("/")
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

    # Step 1b: Fix SELinux context on swap directory
    rc, _, _ = _run(["semanage", "fcontext", "-a", "-t", "swapfile_t", f"{SWAP_SUBVOL}(/.*)?"])
    if rc == 0:
        _run(["restorecon", "-R", SWAP_SUBVOL])
        steps.append("Set SELinux context on /swap")
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
            with open(ZRAM_CONF, "w") as f:
                f.write("# zram disabled by OXP Apex Tools for hibernate support\n")
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
                f.write(f"{SWAP_FILE} none swap sw 0 0\n")
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
    root_uuid = _get_root_uuid()
    swap_offset = _get_swap_offset()

    if not root_uuid:
        errors.append("Could not determine root filesystem UUID")
        return {"success": False, "error": "; ".join(errors), "steps": steps}

    if swap_offset is None:
        errors.append("Could not determine swap file offset (filefrag failed)")
        return {"success": False, "error": "; ".join(errors), "steps": steps}

    steps.append(f"Resume params: UUID={root_uuid}, offset={swap_offset}")

    # Step 7: Add kernel parameters
    kargs_added = False
    if not _has_karg("resume="):
        rc, _, err = _run([
            "rpm-ostree", "kargs",
            f"--append=resume=UUID={root_uuid}"
        ])
        if rc == 0:
            steps.append("Added resume= kernel parameter")
            kargs_added = True
        else:
            errors.append(f"Could not add resume karg: {err}")
    else:
        steps.append("resume= karg already present")

    if not _has_karg("resume_offset="):
        rc, _, err = _run([
            "rpm-ostree", "kargs",
            f"--append=resume_offset={swap_offset}"
        ])
        if rc == 0:
            steps.append("Added resume_offset= kernel parameter")
            kargs_added = True
        else:
            errors.append(f"Could not add resume_offset karg: {err}")
    else:
        steps.append("resume_offset= karg already present")

    # Step 8: Add dracut resume module
    if not _has_dracut_resume():
        try:
            os.makedirs(os.path.dirname(DRACUT_RESUME_CONF), exist_ok=True)
            with open(DRACUT_RESUME_CONF, "w") as f:
                f.write('# Added by OXP Apex Tools for hibernate support\n')
                f.write('add_dracutmodules+=" resume "\n')
            steps.append("Added dracut resume module config")
        except Exception as e:
            errors.append(f"Could not create dracut config: {e}")
    else:
        steps.append("dracut resume module already configured")

    # Step 9: Enable initramfs regeneration (needed for dracut changes)
    rc, _, err = _run(["rpm-ostree", "initramfs", "--enable"])
    if rc == 0:
        steps.append("Enabled initramfs regeneration")
    else:
        # May already be enabled or may fail — not critical if kargs were set
        if "already enabled" in err.lower() or "initramfs" in err.lower():
            steps.append("initramfs regeneration already enabled")
        else:
            _log_warning(f"rpm-ostree initramfs --enable: {err}")
            steps.append(f"initramfs regeneration warning: {err}")

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
        if not status["has_offset_karg"]:
            missing.append("missing resume_offset= karg")
        if not status["has_dracut_resume"]:
            missing.append("missing dracut resume module")
        return {
            "success": False,
            "error": f"Hibernate not ready: {', '.join(missing)}. Run setup first.",
        }

    _log_info("Initiating hibernate (S4)...")
    rc, out, err = _run(["systemctl", "hibernate"], timeout=30)
    if rc == 0:
        return {"success": True, "message": "Hibernate initiated"}
    else:
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
