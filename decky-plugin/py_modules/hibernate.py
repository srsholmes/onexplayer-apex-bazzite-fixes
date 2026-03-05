"""Hibernate (S4) support for OneXPlayer Apex on Bazzite.

S0i3 deep sleep is broken on Strix Halo with kernel 6.17 (needs 6.18+).
Hibernate is a viable alternative — writes RAM to disk, powers off completely.
Zero power drain, ~6-7 second wake.

This module handles:
- Creating a BTRFS swapfile for hibernate
- Configuring fstab, disabling zram
- Setting kernel resume parameters via rpm-ostree kargs
- Full teardown/removal
"""

import logging
import os
import re
import shutil
import subprocess

logger = logging.getLogger("OXP-Hibernate")

# Pluggable log callbacks — set by main.py
_log_info_cb = None
_log_error_cb = None
_log_warning_cb = None


def set_log_callbacks(info_fn, error_fn, warning_fn):
    global _log_info_cb, _log_error_cb, _log_warning_cb
    _log_info_cb = info_fn
    _log_error_cb = error_fn
    _log_warning_cb = warning_fn


def _log_info(msg):
    if _log_info_cb:
        _log_info_cb(msg)
    else:
        logger.info(msg)


def _log_error(msg):
    if _log_error_cb:
        _log_error_cb(msg)
    else:
        logger.error(msg)


def _log_warning(msg):
    if _log_warning_cb:
        _log_warning_cb(msg)
    else:
        logger.warning(msg)


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


# --- Constants ---
SWAP_SUBVOLUME = "/var/swap"
SWAP_FILE = "/var/swap/swapfile"
FSTAB_PATH = "/etc/fstab"
FSTAB_BACKUP = "/etc/fstab.oxp-hibernate-backup"
FSTAB_MARKER = "# OXP Hibernate"
ZRAM_CONF = "/etc/systemd/zram-generator.conf"
ZRAM_BACKUP = "/etc/systemd/zram-generator.conf.oxp-hibernate-backup"

CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "hibernate_configs")

# Mapping: bundled filename -> system install path
HIBERNATE_CONFIGS = {
    "sleep.conf": "/etc/systemd/sleep.conf.d/10-oxp-hibernate.conf",
    "logind-override.conf": "/etc/systemd/system/systemd-logind.service.d/10-oxp-hibernate.conf",
    "hibernate-override.conf": "/etc/systemd/system/systemd-hibernate.service.d/10-oxp-hibernate.conf",
    "polkit-hibernate.rules": "/etc/polkit-1/rules.d/10-oxp-hibernate.rules",
    "imu-fix.service": "/etc/systemd/system/oxp-hibernate-imu-fix.service",
}
IMU_SERVICE_NAME = "oxp-hibernate-imu-fix.service"


def _get_ram_gb():
    """Get total RAM in GB (rounded up) from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    gb = -(-kb // (1024 * 1024))  # ceiling division
                    return gb
    except Exception as e:
        _log_error(f"Failed to read RAM size: {e}")
    return None


def _get_free_space_gb(path):
    """Get free space in GB at the given path."""
    try:
        st = os.statvfs(path)
        return (st.f_bavail * st.f_frsize) / (1024 ** 3)
    except Exception as e:
        _log_error(f"Failed to get free space at {path}: {e}")
        return None


def _is_swap_active():
    """Check if our swapfile is active in /proc/swaps."""
    try:
        with open("/proc/swaps") as f:
            return SWAP_FILE in f.read()
    except Exception:
        return False


def _has_fstab_entry():
    """Check if our fstab entry exists."""
    try:
        with open(FSTAB_PATH) as f:
            content = f.read()
        return FSTAB_MARKER in content and SWAP_FILE in content
    except Exception:
        return False


def _is_zram_disabled():
    """Check if zram is disabled (empty config or config doesn't exist)."""
    if not os.path.exists(ZRAM_CONF):
        return True
    try:
        with open(ZRAM_CONF) as f:
            content = f.read().strip()
        # Empty or only comments = disabled
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return False
        return True
    except Exception:
        return False


def _are_configs_installed():
    """Check if all bundled config files are installed at system paths."""
    return all(os.path.isfile(dest) for dest in HIBERNATE_CONFIGS.values())


def _is_imu_service_enabled():
    """Check if the IMU fix service is enabled."""
    try:
        r = subprocess.run(
            ["systemctl", "is-enabled", IMU_SERVICE_NAME],
            capture_output=True, text=True, timeout=10,
            env=_clean_env()
        )
        return r.stdout.strip() == "enabled"
    except Exception:
        return False


def _get_resume_params():
    """Get resume UUID and offset from current cmdline."""
    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        return None, None

    uuid_match = re.search(r"resume=UUID=([^\s]+)", cmdline)
    offset_match = re.search(r"resume_offset=(\d+)", cmdline)

    return (
        uuid_match.group(1) if uuid_match else None,
        offset_match.group(1) if offset_match else None,
    )


def _get_swap_uuid():
    """Get the UUID of the filesystem containing the swapfile."""
    try:
        r = subprocess.run(
            ["findmnt", "-no", "UUID", "-T", SWAP_FILE],
            capture_output=True, text=True, timeout=10,
            env=_clean_env()
        )
        return r.stdout.strip() or None
    except Exception as e:
        _log_error(f"Failed to get swap UUID: {e}")
        return None


def _get_swap_offset():
    """Get the BTRFS swapfile offset for the resume_offset karg."""
    try:
        r = subprocess.run(
            ["btrfs", "inspect-internal", "map-swapfile", "-r", SWAP_FILE],
            capture_output=True, text=True, timeout=10,
            env=_clean_env()
        )
        return r.stdout.strip() or None
    except Exception as e:
        _log_error(f"Failed to get swap offset: {e}")
        return None


def get_status():
    """Detect current hibernate state and return status dict.

    Returns phase: "none" | "swap_ready" | "complete"
    """
    ram_gb = _get_ram_gb()
    subvol_exists = os.path.isdir(SWAP_SUBVOLUME)
    swapfile_exists = os.path.isfile(SWAP_FILE)
    swap_active = _is_swap_active()
    fstab_entry = _has_fstab_entry()
    zram_disabled = _is_zram_disabled()
    resume_uuid, resume_offset = _get_resume_params()
    configs_installed = _are_configs_installed()
    imu_service_enabled = _is_imu_service_enabled()

    has_kargs = resume_uuid is not None and resume_offset is not None
    swap_ready = swapfile_exists and (swap_active or fstab_entry)

    if swap_ready and has_kargs and zram_disabled and configs_installed and imu_service_enabled:
        phase = "complete"
    elif swap_ready:
        phase = "swap_ready"
    else:
        phase = "none"

    # Get swapfile size if it exists
    swap_size_gb = None
    if swapfile_exists:
        try:
            swap_size_gb = round(os.path.getsize(SWAP_FILE) / (1024 ** 3), 1)
        except Exception:
            pass

    return {
        "phase": phase,
        "ram_gb": ram_gb,
        "swap_size_gb": swap_size_gb,
        "subvol_exists": subvol_exists,
        "swapfile_exists": swapfile_exists,
        "swap_active": swap_active,
        "fstab_entry": fstab_entry,
        "zram_disabled": zram_disabled,
        "resume_uuid": resume_uuid,
        "resume_offset": resume_offset,
        "configs_installed": configs_installed,
        "imu_service_enabled": imu_service_enabled,
    }


def setup():
    """Set up hibernate — creates swapfile, configures fstab/zram/kargs.

    Idempotent: skips steps that are already done.
    May require 1-2 reboots depending on whether swapon works immediately.

    Returns dict with success, steps taken, and whether reboot is needed.
    """
    steps = []
    reboot_needed = False

    _log_info("=== Hibernate Setup Start ===")

    ram_gb = _get_ram_gb()
    if not ram_gb:
        return {"success": False, "error": "Could not detect RAM size", "steps": steps}

    _log_info(f"RAM size: {ram_gb}GB")

    # Step 1: Check disk space
    var_free = _get_free_space_gb("/var")
    if var_free is not None and var_free < ram_gb:
        return {
            "success": False,
            "error": f"Not enough disk space on /var. Need {ram_gb}GB, have {var_free:.1f}GB free.",
            "steps": steps,
        }

    # Step 2: Create BTRFS subvolume
    if not os.path.isdir(SWAP_SUBVOLUME):
        _log_info("Creating BTRFS subvolume for swap...")
        try:
            r = subprocess.run(
                ["btrfs", "subvolume", "create", SWAP_SUBVOLUME],
                capture_output=True, text=True, timeout=30,
                env=_clean_env()
            )
            if r.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to create subvolume: {r.stderr.strip()}",
                    "steps": steps,
                }
            steps.append("Created BTRFS subvolume /var/swap")
            _log_info("BTRFS subvolume created")
        except Exception as e:
            return {"success": False, "error": f"Failed to create subvolume: {e}", "steps": steps}
    else:
        steps.append("Subvolume already exists")

    # Step 3: SELinux contexts (graceful failure)
    try:
        subprocess.run(
            ["semanage", "fcontext", "-a", "-t", "swapfile_t", f"{SWAP_SUBVOLUME}(/.*)?"],
            capture_output=True, text=True, timeout=30,
            env=_clean_env()
        )
        subprocess.run(
            ["restorecon", "-R", SWAP_SUBVOLUME],
            capture_output=True, text=True, timeout=30,
            env=_clean_env()
        )
        steps.append("SELinux contexts set")
    except Exception as e:
        _log_warning(f"SELinux context setup skipped: {e}")
        steps.append("SELinux contexts skipped (not critical)")

    # Step 4: Create swapfile
    if not os.path.isfile(SWAP_FILE):
        _log_info(f"Creating {ram_gb}GB swapfile (this may take a while)...")
        try:
            r = subprocess.run(
                ["btrfs", "filesystem", "mkswapfile", "--size", f"{ram_gb}G", SWAP_FILE],
                capture_output=True, text=True, timeout=600,  # 10 min for large files
                env=_clean_env()
            )
            if r.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to create swapfile: {r.stderr.strip()}",
                    "steps": steps,
                }
            steps.append(f"Created {ram_gb}GB swapfile")
            _log_info("Swapfile created")
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Swapfile creation timed out (10 min)", "steps": steps}
        except Exception as e:
            return {"success": False, "error": f"Failed to create swapfile: {e}", "steps": steps}
    else:
        steps.append("Swapfile already exists")

    # Step 5: Activate swap
    swapon_ok = _is_swap_active()
    if not swapon_ok:
        _log_info("Activating swapfile...")
        try:
            r = subprocess.run(
                ["swapon", SWAP_FILE],
                capture_output=True, text=True, timeout=30,
                env=_clean_env()
            )
            if r.returncode == 0:
                swapon_ok = True
                steps.append("Activated swap")
                _log_info("Swap activated")
            else:
                _log_warning(f"swapon failed: {r.stderr.strip()}")
                steps.append(f"swapon failed: {r.stderr.strip()} — reboot may be needed")
        except Exception as e:
            _log_warning(f"swapon exception: {e}")
            steps.append(f"swapon failed: {e} — reboot may be needed")
    else:
        steps.append("Swap already active")

    # Step 6: Add fstab entry
    if not _has_fstab_entry():
        _log_info("Adding fstab entry...")
        try:
            # Backup fstab
            if os.path.exists(FSTAB_PATH) and not os.path.exists(FSTAB_BACKUP):
                shutil.copy2(FSTAB_PATH, FSTAB_BACKUP)
                steps.append("Backed up fstab")

            with open(FSTAB_PATH, "a") as f:
                f.write(f"\n{FSTAB_MARKER}\n")
                f.write(f"{SWAP_FILE} none swap defaults,nofail 0 0\n")
            steps.append("Added fstab entry")
            _log_info("fstab entry added")
        except Exception as e:
            _log_error(f"Failed to modify fstab: {e}")
            return {"success": False, "error": f"Failed to modify fstab: {e}", "steps": steps}
    else:
        steps.append("fstab entry already present")

    # Step 7: Disable zram
    if not _is_zram_disabled():
        _log_info("Disabling zram...")
        try:
            # Backup original config
            if os.path.exists(ZRAM_CONF) and not os.path.exists(ZRAM_BACKUP):
                shutil.copy2(ZRAM_CONF, ZRAM_BACKUP)
                steps.append("Backed up zram config")

            with open(ZRAM_CONF, "w") as f:
                f.write(f"{FSTAB_MARKER} — zram disabled for hibernate\n")
            steps.append("Disabled zram")
            _log_info("zram disabled")
            reboot_needed = True
        except Exception as e:
            _log_error(f"Failed to disable zram: {e}")
            return {"success": False, "error": f"Failed to disable zram: {e}", "steps": steps}
    else:
        steps.append("zram already disabled")

    # Step 8: Install bundled config files
    if not _are_configs_installed():
        _log_info("Installing hibernate config files...")
        try:
            for src_name, dest_path in HIBERNATE_CONFIGS.items():
                src_path = os.path.join(CONFIGS_DIR, src_name)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(src_path, dest_path)
                _log_info(f"Installed {src_name} -> {dest_path}")
            # Fix SELinux contexts — shutil.copy2 preserves source labels
            # (e.g. user_home_t from Decky's home dir), which prevents
            # systemd from recognizing the unit files.
            dest_paths = list(HIBERNATE_CONFIGS.values())
            subprocess.run(
                ["restorecon", "-v"] + dest_paths,
                capture_output=True, text=True, timeout=30,
                env=_clean_env()
            )
            _log_info("Restored SELinux contexts on config files")
            steps.append("Installed hibernate config files")
        except Exception as e:
            _log_error(f"Failed to install config files: {e}")
            return {"success": False, "error": f"Failed to install config files: {e}", "steps": steps}
    else:
        steps.append("Config files already installed")

    # Step 9: Enable IMU fix service + daemon-reload
    if not _is_imu_service_enabled():
        _log_info("Enabling IMU hibernate fix service...")
        try:
            subprocess.run(
                ["systemctl", "daemon-reload"],
                capture_output=True, text=True, timeout=30,
                env=_clean_env()
            )
            r = subprocess.run(
                ["systemctl", "enable", IMU_SERVICE_NAME],
                capture_output=True, text=True, timeout=30,
                env=_clean_env()
            )
            if r.returncode != 0:
                _log_warning(f"Failed to enable IMU service: {r.stderr.strip()}")
                steps.append(f"Failed to enable IMU service: {r.stderr.strip()}")
            else:
                steps.append("Enabled IMU hibernate fix service")
                _log_info("IMU service enabled")
        except Exception as e:
            _log_warning(f"Failed to enable IMU service: {e}")
            steps.append(f"Failed to enable IMU service: {e}")
        reboot_needed = True
    else:
        steps.append("IMU service already enabled")

    # If swapon failed, we need a reboot before we can get UUID/offset
    if not swapon_ok:
        _log_info("Swap not active — reboot needed before kargs can be set")
        return {
            "success": True,
            "phase_completed": "swap",
            "reboot_needed": True,
            "message": "Swap configured. Reboot, then run setup again to set kernel parameters.",
            "steps": steps,
        }

    # Step 10-11: Get UUID and offset
    uuid = _get_swap_uuid()
    offset = _get_swap_offset()

    if not uuid or not offset:
        _log_error(f"Could not determine resume params (UUID={uuid}, offset={offset})")
        return {
            "success": False,
            "error": "Could not determine resume UUID/offset. Swap may need a reboot to activate.",
            "steps": steps,
        }

    _log_info(f"Resume params: UUID={uuid}, offset={offset}")

    # Step 12: Set kernel parameters
    current_uuid, current_offset = _get_resume_params()
    kargs_needed = current_uuid != uuid or current_offset != offset

    if kargs_needed:
        _log_info("Setting kernel resume parameters via rpm-ostree...")

        # Remove old resume kargs first if they exist with wrong values
        try:
            with open("/proc/cmdline") as f:
                cmdline = f.read()
        except Exception:
            cmdline = ""

        kargs_cmd = ["rpm-ostree", "kargs"]

        # Delete existing resume params if present (they might have wrong values)
        if "resume=" in cmdline:
            old_resume = re.search(r"(resume=\S+)", cmdline)
            if old_resume:
                kargs_cmd.append(f"--delete={old_resume.group(1)}")
        if "resume_offset=" in cmdline:
            old_offset = re.search(r"(resume_offset=\S+)", cmdline)
            if old_offset:
                kargs_cmd.append(f"--delete={old_offset.group(1)}")

        kargs_cmd.append(f"--append=resume=UUID={uuid}")
        kargs_cmd.append(f"--append=resume_offset={offset}")

        try:
            r = subprocess.run(
                kargs_cmd,
                capture_output=True, text=True, timeout=120,
                env=_clean_env()
            )
            if r.returncode != 0:
                return {
                    "success": False,
                    "error": f"rpm-ostree kargs failed: {r.stderr.strip()}",
                    "steps": steps,
                }
            steps.append(f"Set resume kargs (UUID={uuid}, offset={offset})")
            reboot_needed = True
            _log_info("Kernel resume parameters set")
        except Exception as e:
            return {"success": False, "error": f"rpm-ostree kargs failed: {e}", "steps": steps}
    else:
        steps.append("Kernel resume parameters already set")

    msg = "Hibernate setup complete."
    if reboot_needed:
        msg += " Reboot required to activate. Button fix patches will need re-applying after reboot."

    _log_info(f"=== Hibernate Setup Complete (reboot_needed={reboot_needed}) ===")

    return {
        "success": True,
        "reboot_needed": reboot_needed,
        "message": msg,
        "steps": steps,
    }


def remove():
    """Remove hibernate setup — teardown swapfile, fstab, zram, kargs.

    Idempotent: skips steps that are already clean.
    """
    steps = []
    reboot_needed = False

    _log_info("=== Hibernate Remove Start ===")

    # Step 1: Disable IMU fix service
    if _is_imu_service_enabled():
        _log_info("Disabling IMU hibernate fix service...")
        try:
            r = subprocess.run(
                ["systemctl", "disable", IMU_SERVICE_NAME],
                capture_output=True, text=True, timeout=30,
                env=_clean_env()
            )
            if r.returncode == 0:
                steps.append("Disabled IMU service")
                _log_info("IMU service disabled")
            else:
                _log_warning(f"Failed to disable IMU service: {r.stderr.strip()}")
                steps.append(f"Failed to disable IMU service: {r.stderr.strip()}")
        except Exception as e:
            _log_warning(f"Failed to disable IMU service: {e}")
            steps.append(f"Failed to disable IMU service: {e}")
    else:
        steps.append("IMU service not enabled")

    # Step 2: Remove installed config files
    removed_any = False
    for src_name, dest_path in HIBERNATE_CONFIGS.items():
        if os.path.isfile(dest_path):
            try:
                os.remove(dest_path)
                _log_info(f"Removed {dest_path}")
                removed_any = True
                # Remove empty parent dirs we created
                parent = os.path.dirname(dest_path)
                try:
                    os.removedirs(parent)
                except OSError:
                    pass  # dir not empty or is a system dir, fine
            except Exception as e:
                _log_warning(f"Failed to remove {dest_path}: {e}")
    if removed_any:
        steps.append("Removed hibernate config files")
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=30,
            env=_clean_env()
        )
    else:
        steps.append("No config files to remove")

    # Step 3: Remove kernel resume params (creates new deployment — do before file ops)
    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    kargs_cmd = ["rpm-ostree", "kargs"]
    has_kargs = False

    resume_match = re.search(r"(resume=\S+)", cmdline)
    if resume_match:
        kargs_cmd.append(f"--delete={resume_match.group(1)}")
        has_kargs = True
    offset_match = re.search(r"(resume_offset=\S+)", cmdline)
    if offset_match:
        kargs_cmd.append(f"--delete={offset_match.group(1)}")
        has_kargs = True

    if has_kargs:
        _log_info("Removing kernel resume parameters...")
        try:
            r = subprocess.run(
                kargs_cmd,
                capture_output=True, text=True, timeout=120,
                env=_clean_env()
            )
            if r.returncode == 0:
                steps.append("Removed resume kargs")
                reboot_needed = True
                _log_info("Resume kargs removed")
            else:
                _log_warning(f"rpm-ostree kargs delete failed: {r.stderr.strip()}")
                steps.append(f"Failed to remove kargs: {r.stderr.strip()}")
        except Exception as e:
            _log_warning(f"rpm-ostree kargs exception: {e}")
            steps.append(f"Failed to remove kargs: {e}")
    else:
        steps.append("No resume kargs to remove")

    # Step 4: Remove fstab entry
    if _has_fstab_entry():
        _log_info("Removing fstab entry...")
        try:
            with open(FSTAB_PATH) as f:
                lines = f.readlines()

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

            # Remove trailing blank lines
            while new_lines and new_lines[-1].strip() == "":
                new_lines.pop()
            if new_lines:
                new_lines.append("\n")

            with open(FSTAB_PATH, "w") as f:
                f.writelines(new_lines)
            steps.append("Removed fstab entry")
            _log_info("fstab entry removed")
        except Exception as e:
            _log_warning(f"Failed to clean fstab: {e}")
            steps.append(f"Failed to clean fstab: {e}")
    else:
        steps.append("No fstab entry to remove")

    # Step 5: Deactivate swap
    if _is_swap_active():
        _log_info("Deactivating swap...")
        try:
            r = subprocess.run(
                ["swapoff", SWAP_FILE],
                capture_output=True, text=True, timeout=60,
                env=_clean_env()
            )
            if r.returncode == 0:
                steps.append("Deactivated swap")
                _log_info("Swap deactivated")
            else:
                _log_warning(f"swapoff failed: {r.stderr.strip()}")
                steps.append(f"swapoff failed: {r.stderr.strip()}")
        except Exception as e:
            _log_warning(f"swapoff exception: {e}")
            steps.append(f"swapoff failed: {e}")
    else:
        steps.append("Swap not active")

    # Step 6: Remove swapfile
    if os.path.isfile(SWAP_FILE):
        _log_info("Removing swapfile...")
        try:
            os.remove(SWAP_FILE)
            steps.append("Removed swapfile")
            _log_info("Swapfile removed")
        except Exception as e:
            _log_warning(f"Failed to remove swapfile: {e}")
            steps.append(f"Failed to remove swapfile: {e}")
    else:
        steps.append("No swapfile to remove")

    # Step 7: Remove subvolume
    if os.path.isdir(SWAP_SUBVOLUME):
        _log_info("Removing BTRFS subvolume...")
        try:
            r = subprocess.run(
                ["btrfs", "subvolume", "delete", SWAP_SUBVOLUME],
                capture_output=True, text=True, timeout=30,
                env=_clean_env()
            )
            if r.returncode == 0:
                steps.append("Removed BTRFS subvolume")
                _log_info("Subvolume removed")
            else:
                _log_warning(f"subvolume delete failed: {r.stderr.strip()}")
                steps.append(f"Failed to remove subvolume: {r.stderr.strip()}")
        except Exception as e:
            _log_warning(f"subvolume delete exception: {e}")
            steps.append(f"Failed to remove subvolume: {e}")
    else:
        steps.append("No subvolume to remove")

    # Step 8: Restore zram config
    if os.path.exists(ZRAM_BACKUP):
        _log_info("Restoring zram config from backup...")
        try:
            shutil.copy2(ZRAM_BACKUP, ZRAM_CONF)
            os.remove(ZRAM_BACKUP)
            steps.append("Restored zram config")
            reboot_needed = True
            _log_info("zram config restored")
        except Exception as e:
            _log_warning(f"Failed to restore zram config: {e}")
            steps.append(f"Failed to restore zram config: {e}")
    elif _is_zram_disabled():
        # No backup but zram is disabled — can't restore automatically
        steps.append("No zram backup to restore (zram may need manual re-enable)")
    else:
        steps.append("zram already enabled")

    msg = "Hibernate removed."
    if reboot_needed:
        msg += " Reboot required. Button fix patches will need re-applying after reboot."

    _log_info(f"=== Hibernate Remove Complete (reboot_needed={reboot_needed}) ===")

    return {
        "success": True,
        "reboot_needed": reboot_needed,
        "message": msg,
        "steps": steps,
    }
