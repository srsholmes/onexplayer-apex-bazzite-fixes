"""EC platform driver (oxpec) loader for OneXPlayer Apex.

Installs and loads the oxpec kernel module which provides hwmon sensors
and enables HHD native fan curves. The .ko is bundled with the plugin
and installed as a systemd oneshot service so it persists across reboots.

Built for kernel 6.17.7-ba25.fc43.x86_64.
"""

import logging
import os
import subprocess

logger = logging.getLogger("OXP-OxpecLoader")

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
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


# Paths
_PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
_BUNDLED_KO = os.path.join(os.path.dirname(__file__), "oxpec", "oxpec.ko")
_INSTALL_DIR = "/var/lib/oxpec"
_INSTALL_KO = os.path.join(_INSTALL_DIR, "oxpec.ko")
_SERVICE_NAME = "oxpec-load.service"
_SERVICE_PATH = f"/etc/systemd/system/{_SERVICE_NAME}"
_TARGET_KERNEL = "6.17.7-ba25.fc43.x86_64"

_SERVICE_CONTENT = f"""[Unit]
Description=Load oxpec EC platform driver for OneXPlayer
DefaultDependencies=no
After=systemd-modules-load.service
Before=hhd@.service hhd.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/insmod {_INSTALL_KO}
ExecStop=/sbin/rmmod oxpec

[Install]
WantedBy=multi-user.target
"""


def _find_hwmon():
    """Find the oxpec hwmon device path, if loaded."""
    hwmon_base = "/sys/class/hwmon"
    if not os.path.isdir(hwmon_base):
        return None
    for entry in os.listdir(hwmon_base):
        name_path = os.path.join(hwmon_base, entry, "name")
        try:
            with open(name_path) as f:
                if f.read().strip() == "oxpec":
                    return os.path.join(hwmon_base, entry)
        except (OSError, IOError):
            continue
    return None


def _is_module_loaded():
    """Check if oxpec module is currently loaded."""
    try:
        with open("/proc/modules") as f:
            for line in f:
                if line.startswith("oxpec "):
                    return True
    except OSError:
        pass
    return False


def _get_running_kernel():
    """Get the running kernel version."""
    try:
        r = subprocess.run(["uname", "-r"], capture_output=True, text=True, timeout=5, env=_clean_env())
        return r.stdout.strip()
    except Exception:
        return None


def is_applied():
    """Check current status of oxpec driver installation."""
    kernel = _get_running_kernel()
    kernel_compatible = kernel == _TARGET_KERNEL if kernel else None

    module_loaded = _is_module_loaded()
    hwmon_path = _find_hwmon()

    service_enabled = False
    service_exists = os.path.exists(_SERVICE_PATH)
    if service_exists:
        try:
            r = subprocess.run(
                ["systemctl", "is-enabled", _SERVICE_NAME],
                capture_output=True, text=True, timeout=10, env=_clean_env()
            )
            service_enabled = r.stdout.strip() == "enabled"
        except Exception:
            pass

    applied = module_loaded and service_enabled

    return {
        "applied": applied,
        "module_loaded": module_loaded,
        "service_enabled": service_enabled,
        "hwmon_path": hwmon_path,
        "kernel_compatible": kernel_compatible,
        "running_kernel": kernel,
        "target_kernel": _TARGET_KERNEL,
    }


def ensure_loaded():
    """Load the oxpec module if not already loaded.

    Lightweight startup check — skips service creation so it works
    even when the ostree hotfix overlay is gone after reboot.
    """
    if _is_module_loaded():
        return {"success": True, "already_loaded": True}

    # Prefer installed copy (/var persists), fall back to bundled
    ko_path = _INSTALL_KO if os.path.exists(_INSTALL_KO) else _BUNDLED_KO
    if not os.path.exists(ko_path):
        _log_error("No oxpec.ko found to load")
        return {"success": False, "error": "oxpec.ko not found"}

    _log_info(f"Auto-loading oxpec from {ko_path}")
    try:
        r = subprocess.run(
            ["insmod", ko_path],
            capture_output=True, text=True, timeout=10, env=_clean_env()
        )
        if r.returncode != 0:
            _log_error(f"insmod failed: {r.stderr.strip()}")
            return {"success": False, "error": r.stderr.strip()}
    except Exception as e:
        _log_error(f"insmod exception: {e}")
        return {"success": False, "error": str(e)}

    if _is_module_loaded():
        _log_info("oxpec module loaded on startup")
        return {"success": True, "loaded": True}
    else:
        return {"success": False, "error": "Module did not load"}


def apply():
    """Install and load the oxpec kernel module."""
    steps = []
    _log_info("=== oxpec Apply Start ===")

    # Check if already applied
    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    # Warn on kernel mismatch but don't block
    if status.get("kernel_compatible") is False:
        _log_warning(
            f"Kernel mismatch: running {status['running_kernel']}, "
            f"module built for {_TARGET_KERNEL}"
        )
        steps.append(f"Warning: kernel mismatch ({status['running_kernel']} vs {_TARGET_KERNEL})")

    # Check bundled .ko exists
    if not os.path.exists(_BUNDLED_KO):
        return {"success": False, "error": f"Bundled oxpec.ko not found at {_BUNDLED_KO}", "steps": steps}

    # Copy .ko to install location
    try:
        os.makedirs(_INSTALL_DIR, exist_ok=True)
        import shutil
        shutil.copy2(_BUNDLED_KO, _INSTALL_KO)
        _log_info(f"Copied oxpec.ko to {_INSTALL_KO}")
        steps.append(f"Copied oxpec.ko to {_INSTALL_DIR}")
    except Exception as e:
        return {"success": False, "error": f"Failed to copy oxpec.ko: {e}", "steps": steps}

    # Set SELinux context
    try:
        r = subprocess.run(
            ["chcon", "-t", "modules_object_t", _INSTALL_KO],
            capture_output=True, text=True, timeout=10, env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Set SELinux context")
            _log_info("Set SELinux context on oxpec.ko")
        else:
            _log_warning(f"chcon returned {r.returncode}: {r.stderr.strip()}")
            steps.append("SELinux context set failed (may not be enforcing)")
    except Exception as e:
        _log_warning(f"chcon failed: {e}")

    # Write systemd service
    try:
        with open(_SERVICE_PATH, "w") as f:
            f.write(_SERVICE_CONTENT)
        _log_info(f"Created {_SERVICE_PATH}")
        steps.append("Created systemd service")
    except Exception as e:
        return {"success": False, "error": f"Failed to write service file: {e}", "steps": steps}

    # Reload systemd and enable+start
    try:
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=30, env=_clean_env()
        )
        r = subprocess.run(
            ["systemctl", "enable", "--now", _SERVICE_NAME],
            capture_output=True, text=True, timeout=30, env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Enabled and started oxpec-load service")
            _log_info("oxpec service enabled and started")
        else:
            _log_error(f"systemctl enable --now failed: {r.stderr.strip()}")
            # Try loading manually as fallback
            r2 = subprocess.run(
                ["insmod", _INSTALL_KO],
                capture_output=True, text=True, timeout=10, env=_clean_env()
            )
            if r2.returncode == 0:
                steps.append("Loaded module manually (service failed)")
                _log_warning("Service failed but manual insmod succeeded")
            else:
                return {"success": False, "error": f"Failed to load module: {r2.stderr.strip()}", "steps": steps}
    except Exception as e:
        return {"success": False, "error": f"systemctl failed: {e}", "steps": steps}

    # Verify
    if _is_module_loaded():
        steps.append("Module loaded successfully")
        hwmon = _find_hwmon()
        if hwmon:
            steps.append(f"hwmon device at {hwmon}")
        _log_info("oxpec applied successfully")
        return {"success": True, "message": "oxpec driver loaded", "steps": steps}
    else:
        return {"success": False, "error": "Module did not load after insmod", "steps": steps}


def revert():
    """Unload oxpec and remove service."""
    steps = []
    _log_info("=== oxpec Revert Start ===")

    # Disable and stop service
    if os.path.exists(_SERVICE_PATH):
        try:
            subprocess.run(
                ["systemctl", "disable", "--now", _SERVICE_NAME],
                capture_output=True, text=True, timeout=30, env=_clean_env()
            )
            steps.append("Disabled oxpec-load service")
        except Exception as e:
            _log_warning(f"Failed to disable service: {e}")

    # Unload module
    if _is_module_loaded():
        try:
            r = subprocess.run(
                ["rmmod", "oxpec"],
                capture_output=True, text=True, timeout=10, env=_clean_env()
            )
            if r.returncode == 0:
                steps.append("Unloaded oxpec module")
                _log_info("oxpec module unloaded")
            else:
                _log_warning(f"rmmod failed: {r.stderr.strip()}")
                steps.append(f"rmmod failed: {r.stderr.strip()}")
        except Exception as e:
            _log_warning(f"rmmod exception: {e}")

    # Remove service file
    if os.path.exists(_SERVICE_PATH):
        try:
            os.remove(_SERVICE_PATH)
            steps.append("Removed service file")
        except Exception as e:
            _log_warning(f"Failed to remove service file: {e}")

    # Remove installed .ko
    if os.path.isdir(_INSTALL_DIR):
        try:
            import shutil
            shutil.rmtree(_INSTALL_DIR)
            steps.append("Removed /var/lib/oxpec/")
        except Exception as e:
            _log_warning(f"Failed to remove install dir: {e}")

    # Reload systemd
    try:
        subprocess.run(
            ["systemctl", "daemon-reload"],
            capture_output=True, text=True, timeout=30, env=_clean_env()
        )
    except Exception:
        pass

    _log_info("oxpec reverted")
    return {"success": True, "message": "oxpec driver unloaded and service removed", "steps": steps}
