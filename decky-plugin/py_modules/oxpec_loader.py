"""EC platform driver (oxpec) loader for OneXPlayer Apex.

Installs and loads the oxpec kernel module which provides hwmon sensors
and enables HHD native fan curves. Bundled .ko files are organized per
kernel version in py_modules/oxpec/<kernel>/oxpec.ko.

Loading strategy:
  1. modprobe oxpec  (works when upstream kernel ships APEX DMI entry)
  2. insmod with bundled .ko matching the running kernel
  3. insmod from /var/lib/oxpec/oxpec.ko (previously installed copy)
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
_OXPEC_DIR = os.path.join(os.path.dirname(__file__), "oxpec")
_INSTALL_DIR = "/var/lib/oxpec"
_INSTALL_KO = os.path.join(_INSTALL_DIR, "oxpec.ko")
_SERVICE_NAME = "oxpec-load.service"
_SERVICE_PATH = f"/etc/systemd/system/{_SERVICE_NAME}"


def _make_service_content(ko_path):
    """Generate systemd service content with modprobe-first, insmod fallback."""
    return f"""[Unit]
Description=Load oxpec EC platform driver for OneXPlayer
DefaultDependencies=no
After=systemd-modules-load.service
Before=hhd@.service hhd.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'modprobe oxpec 2>/dev/null || insmod {ko_path}'
ExecStop=/sbin/rmmod oxpec

[Install]
WantedBy=multi-user.target
"""


def _find_bundled_ko(kernel=None):
    """Find bundled oxpec.ko matching a kernel version.

    Returns (ko_path, kernel_version) or (None, None).
    """
    if kernel is None:
        kernel = _get_running_kernel()
    if not kernel:
        return None, None
    ko = os.path.join(_OXPEC_DIR, kernel, "oxpec.ko")
    return (ko, kernel) if os.path.exists(ko) else (None, None)


def _list_bundled_kernels():
    """List kernel versions with bundled .ko files."""
    if not os.path.isdir(_OXPEC_DIR):
        return []
    return sorted(
        d for d in os.listdir(_OXPEC_DIR)
        if os.path.isfile(os.path.join(_OXPEC_DIR, d, "oxpec.ko"))
    )


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


def _try_modprobe():
    """Try modprobe oxpec. Returns {"success": bool, "error": str}."""
    try:
        r = subprocess.run(
            ["modprobe", "oxpec"],
            capture_output=True, text=True, timeout=10, env=_clean_env()
        )
        if r.returncode == 0:
            return {"success": True}
        return {"success": False, "error": r.stderr.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _try_insmod(ko_path):
    """Try insmod with specific .ko. Returns {"success": bool, "error": str}."""
    try:
        r = subprocess.run(
            ["insmod", ko_path],
            capture_output=True, text=True, timeout=10, env=_clean_env()
        )
        if r.returncode == 0:
            return {"success": True}
        return {"success": False, "error": r.stderr.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def is_applied():
    """Check current status of oxpec driver installation."""
    kernel = _get_running_kernel()
    bundled_kernels = _list_bundled_kernels()
    bundled_ko, _ = _find_bundled_ko(kernel)

    module_loaded = _is_module_loaded()
    hwmon_path = _find_hwmon()

    # Determine load method if loaded
    load_method = None
    if module_loaded:
        # Check if kernel has oxpec in its module tree (would mean modprobe works)
        try:
            r = subprocess.run(
                ["modprobe", "--dry-run", "oxpec"],
                capture_output=True, text=True, timeout=5, env=_clean_env()
            )
            load_method = "modprobe" if r.returncode == 0 else "insmod"
        except Exception:
            load_method = "insmod"

    # kernel_compatible: True if loaded, or matching bundled .ko exists, or modprobe works
    kernel_compatible = module_loaded or bundled_ko is not None
    if not kernel_compatible:
        try:
            r = subprocess.run(
                ["modprobe", "--dry-run", "oxpec"],
                capture_output=True, text=True, timeout=5, env=_clean_env()
            )
            if r.returncode == 0:
                kernel_compatible = True
        except Exception:
            pass

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
        "bundled_kernels": bundled_kernels,
        "load_method": load_method,
    }


def ensure_loaded():
    """Load the oxpec module if not already loaded.

    Lightweight startup check — tries modprobe first (future-proof),
    falls back to kernel-matched bundled .ko, then /var/lib/oxpec copy.
    """
    if _is_module_loaded():
        return {"success": True, "already_loaded": True}

    kernel = _get_running_kernel()

    # 1. Try modprobe (works when upstream kernel ships APEX DMI entry)
    result = _try_modprobe()
    if result["success"] and _is_module_loaded():
        _log_info("oxpec loaded via modprobe")
        return {"success": True, "loaded": True, "method": "modprobe"}

    modprobe_error = result.get("error", "unknown")

    # 2. Try bundled .ko for running kernel
    bundled_ko, matched_kernel = _find_bundled_ko(kernel)
    if bundled_ko:
        _log_info(f"Trying bundled oxpec.ko for {matched_kernel}")
        result = _try_insmod(bundled_ko)
        if result["success"] and _is_module_loaded():
            _log_info(f"oxpec loaded via insmod (bundled, {matched_kernel})")
            return {"success": True, "loaded": True, "method": "insmod"}
        _log_warning(f"Bundled insmod failed: {result.get('error', 'unknown')}")

    # 3. Try installed copy as last resort
    if os.path.exists(_INSTALL_KO):
        _log_info(f"Trying installed oxpec.ko from {_INSTALL_KO}")
        result = _try_insmod(_INSTALL_KO)
        if result["success"] and _is_module_loaded():
            _log_info("oxpec loaded via insmod (/var/lib/oxpec)")
            return {"success": True, "loaded": True, "method": "insmod"}

    # All methods failed
    bundled_kernels = _list_bundled_kernels()
    error_msg = (
        f"Failed to load oxpec. "
        f"modprobe: {modprobe_error}. "
        f"Running kernel: {kernel}. "
        f"Bundled .ko available for: {', '.join(bundled_kernels) if bundled_kernels else 'none'}"
    )
    _log_error(error_msg)
    return {"success": False, "error": error_msg}


def apply():
    """Install and load the oxpec kernel module."""
    steps = []
    _log_info("=== oxpec Apply Start ===")

    # Check if already applied
    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    kernel = _get_running_kernel()

    # Check if modprobe works (kernel has APEX DMI entry)
    modprobe_works = False
    try:
        r = subprocess.run(
            ["modprobe", "--dry-run", "oxpec"],
            capture_output=True, text=True, timeout=5, env=_clean_env()
        )
        modprobe_works = r.returncode == 0
    except Exception:
        pass

    if modprobe_works:
        _log_info("Kernel has oxpec module — using modprobe")
        steps.append("Kernel oxpec module available (modprobe)")
        ko_for_service = None  # service will just use modprobe
    else:
        # Find matching bundled .ko
        bundled_ko, matched_kernel = _find_bundled_ko(kernel)
        if not bundled_ko:
            bundled_kernels = _list_bundled_kernels()
            return {
                "success": False,
                "error": (
                    f"No oxpec.ko for kernel {kernel}. "
                    f"Available: {', '.join(bundled_kernels) if bundled_kernels else 'none'}. "
                    f"Update the plugin for new kernel support."
                ),
                "steps": steps,
            }

        _log_warning(
            f"Kernel mismatch for modprobe — using bundled .ko for {matched_kernel}"
        )
        steps.append(f"Using bundled .ko for {matched_kernel}")

        # Copy .ko to install location
        try:
            import shutil
            os.makedirs(_INSTALL_DIR, exist_ok=True)
            shutil.copy2(bundled_ko, _INSTALL_KO)
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

        ko_for_service = _INSTALL_KO

    # Write systemd service (modprobe-first, insmod fallback)
    try:
        service_content = _make_service_content(ko_for_service or "/dev/null")
        with open(_SERVICE_PATH, "w") as f:
            f.write(service_content)
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
            load_result = _try_modprobe() if modprobe_works else {"success": False}
            if not load_result["success"] and ko_for_service:
                load_result = _try_insmod(ko_for_service)
            if load_result["success"]:
                steps.append("Loaded module manually (service failed)")
                _log_warning("Service failed but manual load succeeded")
            else:
                return {"success": False, "error": f"Failed to load module: {r.stderr.strip()}", "steps": steps}
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
        return {"success": False, "error": "Module did not load after install", "steps": steps}


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
