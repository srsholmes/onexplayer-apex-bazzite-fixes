"""Button fix for OneXPlayer Apex on Bazzite.

Patches HHD (Handheld Daemon) by replacing its OXP device files with
Apex-compatible versions. Uses bundled vanilla/patched file copies instead
of fragile string replacement.

Requires ostree unlock + HHD restart.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time

logger = logging.getLogger("OXP-ButtonFix")

# Pluggable log callbacks — set by main.py to route logs to the plugin log file.
_log_info_cb = None
_log_error_cb = None
_log_warning_cb = None


def set_log_callbacks(info_fn, error_fn, warning_fn):
    """Set external log callbacks (called by main.py to wire into plugin logging)."""
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


# --- Paths ---
_PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))
PATCH_DIR = os.path.join(os.path.dirname(__file__), "hhd_patches")
VANILLA_DIR = os.path.join(PATCH_DIR, "vanilla")
PATCHED_DIR = os.path.join(PATCH_DIR, "patched")

# Target HHD version these patches are built for
HHD_VERSION = "4.1.5"

# Files we manage
FILES = ["const.py", "base.py", "hid_v2.py"]


def _get_hhd_version():
    """Get the installed HHD version from package metadata."""
    try:
        import importlib.metadata
        return importlib.metadata.version("hhd")
    except Exception:
        return None


def _find_target_dir():
    """Locate the HHD oxp directory on the system."""
    # Try hardcoded path first (most common on Bazzite)
    target = "/usr/lib/python3.14/site-packages/hhd/device/oxp"
    if os.path.isdir(target):
        return target
    # Fallback: search for any Python version
    import glob as _glob
    results = sorted(_glob.glob("/usr/lib/python3*/site-packages/hhd/device/oxp"))
    if results:
        return results[-1]
    return None


def _file_hash(path):
    """SHA256 hash of a file's contents."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _const_patched_hashes():
    """SHA256 hashes of patched const.py with both apex_intercept modes.

    The deployed const.py may have apex_intercept set to True or False
    depending on the user's toggle choice. Both are valid "applied" states.
    """
    path = os.path.join(PATCHED_DIR, "const.py")
    with open(path, "rb") as f:
        content = f.read()
    h_true = hashlib.sha256(content).hexdigest()
    h_false = hashlib.sha256(
        content.replace(b'"apex_intercept": True', b'"apex_intercept": False')
    ).hexdigest()
    return {h_true, h_false}


def is_applied():
    """Check if the Apex button fix is currently applied."""
    target_dir = _find_target_dir()
    if not target_dir:
        return {"applied": False, "error": "HHD oxp directory not found"}

    try:
        for name in FILES:
            target = os.path.join(target_dir, name)
            patched = os.path.join(PATCHED_DIR, name)
            if not os.path.exists(target):
                return {"applied": False, "error": f"{name} not found at {target_dir}"}
            if not os.path.exists(patched):
                return {"applied": False, "error": f"Bundled patched {name} missing"}
            if name == "const.py":
                # const.py may have apex_intercept toggled — accept both states
                if _file_hash(target) not in _const_patched_hashes():
                    return {"applied": False}
            else:
                if _file_hash(target) != _file_hash(patched):
                    return {"applied": False}
        result = {"applied": True}
        # Include version info for the frontend
        installed_ver = _get_hhd_version()
        if installed_ver:
            result["hhd_version"] = installed_ver
            result["expected_hhd_version"] = HHD_VERSION
        return result
    except Exception as e:
        return {"applied": False, "error": str(e)}


def check_compatibility():
    """Check if installed HHD files match our expected vanilla or patched versions.

    Returns {"compatible": True} if safe to apply/revert.
    Returns {"compatible": False, ...} if HHD version is wrong or files changed.
    """
    # Check HHD package version first
    installed_ver = _get_hhd_version()
    if installed_ver:
        _log_info(f"Installed HHD version: {installed_ver}")
        if installed_ver < HHD_VERSION:
            return {
                "compatible": False,
                "message": (
                    f"HHD {installed_ver} is too old. Please update to "
                    f"HHD {HHD_VERSION} or later before applying patches. "
                    f"Run: ujust update"
                ),
            }
    else:
        _log_warning("Could not detect HHD version — proceeding with file hash check")

    target_dir = _find_target_dir()
    if not target_dir:
        return {"compatible": False, "message": "HHD oxp directory not found"}

    for name in FILES:
        target = os.path.join(target_dir, name)
        vanilla = os.path.join(VANILLA_DIR, name)
        patched = os.path.join(PATCHED_DIR, name)

        if not os.path.exists(target):
            return {"compatible": False, "file": name, "message": f"{name} not found"}

        h = _file_hash(target)
        if name == "const.py":
            # const.py may have apex_intercept toggled — accept both states
            if h in _const_patched_hashes():
                continue  # already patched (either intercept mode)
        else:
            if h == _file_hash(patched):
                continue  # already patched
        if h == _file_hash(vanilla):
            continue  # vanilla, ready to patch
        return {
            "compatible": False,
            "file": name,
            "message": (
                f"HHD version mismatch — {name} has been modified. "
                f"Expected HHD {HHD_VERSION}. The system HHD may have been "
                f"updated. Patches need to be regenerated for the new version."
            ),
        }
    return {"compatible": True}


def _is_filesystem_writable(test_path):
    """Check if the immutable filesystem is writable."""
    test_dir = os.path.dirname(test_path)
    probe = os.path.join(test_dir, ".oxp_write_test")
    try:
        with open(probe, "w") as f:
            f.write("test")
        os.remove(probe)
        return True
    except OSError:
        return False


def _unlock_filesystem(test_path, steps):
    """Unlock the ostree immutable filesystem with retries."""
    _log_info("Unlocking filesystem...")

    if _is_filesystem_writable(test_path):
        _log_info("Filesystem already writable — skipping ostree unlock")
        steps.append("Filesystem already writable")
        return True

    try:
        _log_info("Running: ostree admin unlock --hotfix")
        r = subprocess.run(
            ["ostree", "admin", "unlock", "--hotfix"],
            capture_output=True, text=True, timeout=120,
            env=_clean_env()
        )
        _log_info(f"ostree unlock exit code: {r.returncode}")
        if r.stdout.strip():
            _log_info(f"ostree unlock stdout: {r.stdout.strip()}")
        if r.stderr.strip():
            _log_info(f"ostree unlock stderr: {r.stderr.strip()}")

        if r.returncode == 0:
            steps.append("Unlocked filesystem")
        else:
            _log_warning(f"ostree unlock returned {r.returncode}: {r.stderr.strip()}")
            steps.append(f"ostree unlock returned {r.returncode} (may already be unlocked)")
    except subprocess.TimeoutExpired:
        _log_error("ostree unlock timed out after 120s")
        steps.append("ostree unlock timed out")
        return False
    except Exception as e:
        _log_error(f"ostree unlock exception: {e}")
        steps.append(f"ostree unlock failed: {e}")
        return False

    # Wait for the overlay mount to become writable (retry with backoff)
    max_retries = 6
    for attempt in range(1, max_retries + 1):
        if _is_filesystem_writable(test_path):
            _log_info(f"Filesystem writable after attempt {attempt}")
            steps.append("Filesystem confirmed writable")
            return True
        wait = min(attempt * 0.5, 2.0)
        _log_info(f"Filesystem not yet writable, waiting {wait}s (attempt {attempt}/{max_retries})...")
        time.sleep(wait)

    _log_error("Filesystem still not writable after all retries")
    steps.append("Filesystem not writable after retries")
    return False


def _restart_hhd(steps):
    """Restart all HHD service instances so they pick up new files.

    Bazzite runs HHD as a per-user service (hhd@<user>) which is the instance
    that actually holds the lock and manages the controller. The system-level
    hhd.service may also be present. We restart all active instances.
    """
    _log_info("Restarting HHD...")
    try:
        # Find all active HHD service units (hhd.service, hhd@user.service, etc.)
        r = subprocess.run(
            ["systemctl", "list-units", "--plain", "--no-legend", "--type=service", "hhd*"],
            capture_output=True, text=True, timeout=10,
            env=_clean_env()
        )
        units = []
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if parts:
                units.append(parts[0])

        if not units:
            units = ["hhd"]
            _log_warning("No active HHD units found, falling back to 'hhd'")

        _log_info(f"Restarting HHD units: {units}")

        success = False
        for unit in units:
            try:
                r = subprocess.run(
                    ["systemctl", "restart", unit],
                    capture_output=True, text=True, timeout=30,
                    env=_clean_env()
                )
                if r.returncode == 0:
                    steps.append(f"Restarted {unit}")
                    _log_info(f"{unit} restarted successfully")
                    success = True
                else:
                    _log_warning(f"{unit} restart returned {r.returncode}: {r.stderr.strip()}")
            except Exception as e:
                _log_warning(f"{unit} restart failed: {e}")

        if not success:
            _log_error("Failed to restart any HHD service")
            steps.append("HHD restart failed")
        return success
    except Exception as e:
        _log_error(f"HHD restart exception: {e}")
        steps.append("HHD restart failed")
        return False


def apply():
    """Apply the Apex button fix by copying patched files. Idempotent."""
    steps = []

    _log_info("=== Button Fix Apply Start ===")

    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    # Check compatibility before touching anything
    compat = check_compatibility()
    if not compat.get("compatible"):
        # Files don't match vanilla or patched — could be old-style string patches.
        # Try restoring vanilla first, then re-check compatibility.
        _log_warning(f"Compatibility check failed: {compat.get('message')}. "
                     "Attempting to restore vanilla files first (old patch migration).")

        target_dir_pre = _find_target_dir()
        if target_dir_pre and _is_filesystem_writable(os.path.join(target_dir_pre, "const.py")):
            migrated = True
        elif target_dir_pre:
            migrated = _unlock_filesystem(os.path.join(target_dir_pre, "const.py"), steps)
        else:
            migrated = False

        if migrated and target_dir_pre:
            try:
                for name in FILES:
                    src = os.path.join(VANILLA_DIR, name)
                    dst = os.path.join(target_dir_pre, name)
                    shutil.copy2(src, dst)
                    _log_info(f"Migration: restored vanilla {name}")
                steps.append("Restored vanilla files (old patch migration)")
            except Exception as e:
                _log_error(f"Migration restore failed: {e}")
                migrated = False

        if migrated:
            compat = check_compatibility()

        if not compat.get("compatible"):
            msg = compat.get("message", "HHD version mismatch")
            _log_error(f"Compatibility check failed after migration attempt: {msg}")
            return {"success": False, "error": msg, "steps": steps}

    target_dir = _find_target_dir()
    if not target_dir:
        return {"success": False, "error": "HHD oxp directory not found", "steps": steps}

    test_path = os.path.join(target_dir, "const.py")

    # Unlock immutable filesystem
    if not _unlock_filesystem(test_path, steps):
        return {"success": False, "error": "Filesystem is not writable. ostree unlock failed.", "steps": steps}

    # Copy patched files
    try:
        for name in FILES:
            src = os.path.join(PATCHED_DIR, name)
            dst = os.path.join(target_dir, name)
            shutil.copy2(src, dst)
            _log_info(f"Copied patched {name}")
            steps.append(f"Copied patched {name}")
    except Exception as e:
        _log_error(f"Failed to copy files: {e}")
        # Attempt rollback
        _log_warning("Rolling back to vanilla...")
        try:
            for name in FILES:
                vanilla = os.path.join(VANILLA_DIR, name)
                dst = os.path.join(target_dir, name)
                if os.path.exists(vanilla):
                    shutil.copy2(vanilla, dst)
            steps.append("Rolled back to vanilla after error")
        except Exception as rb_err:
            _log_error(f"Rollback failed: {rb_err}")
        return {"success": False, "error": f"Failed to copy files: {e}", "steps": steps}

    # Restart HHD
    if not _restart_hhd(steps):
        return {"success": True, "warning": "Patched but HHD restart may have failed", "steps": steps}

    _log_info("Button fix applied successfully")
    return {"success": True, "message": "Button fix applied and HHD restarted", "steps": steps}


def revert():
    """Revert the Apex button fix by copying vanilla files back."""
    steps = []

    _log_info("=== Button Fix Revert Start ===")

    target_dir = _find_target_dir()
    if not target_dir:
        return {"success": False, "error": "HHD oxp directory not found", "steps": steps}

    # Check if already vanilla
    all_vanilla = True
    for name in FILES:
        target = os.path.join(target_dir, name)
        vanilla = os.path.join(VANILLA_DIR, name)
        if os.path.exists(target) and os.path.exists(vanilla):
            if _file_hash(target) != _file_hash(vanilla):
                all_vanilla = False
                break
    if all_vanilla:
        return {"success": True, "message": "Already reverted (vanilla)", "steps": ["Already vanilla"]}

    test_path = os.path.join(target_dir, "const.py")

    # Unlock immutable filesystem
    if not _unlock_filesystem(test_path, steps):
        return {"success": False, "error": "Filesystem is not writable. ostree unlock failed.", "steps": steps}

    # Copy vanilla files
    try:
        for name in FILES:
            src = os.path.join(VANILLA_DIR, name)
            dst = os.path.join(target_dir, name)
            shutil.copy2(src, dst)
            _log_info(f"Restored vanilla {name}")
            steps.append(f"Restored vanilla {name}")
    except Exception as e:
        _log_error(f"Failed to restore vanilla files: {e}")
        return {"success": False, "error": f"Failed to restore files: {e}", "steps": steps}

    # Restart HHD
    if not _restart_hhd(steps):
        return {"success": True, "warning": "Reverted but HHD restart may have failed", "steps": steps}

    _log_info("Button fix reverted successfully")
    return {"success": True, "message": "Button fix reverted and HHD restarted", "steps": steps}


def get_intercept_mode():
    """Check if full intercept mode is enabled in the deployed const.py.

    Returns {"enabled": True/False} indicating whether apex_intercept is on.
    Full intercept = all input via vendor HID (back paddles as separate buttons).
    Face buttons only = just Home + QAM, Xbox gamepad works normally.
    """
    target_dir = _find_target_dir()
    if not target_dir:
        return {"enabled": True, "error": "HHD oxp directory not found"}

    target = os.path.join(target_dir, "const.py")
    if not os.path.exists(target):
        return {"enabled": True, "error": "const.py not found"}

    try:
        with open(target) as f:
            content = f.read()
        if '"apex_intercept": False' in content:
            return {"enabled": False}
        return {"enabled": True}
    except Exception as e:
        return {"enabled": True, "error": str(e)}


def set_intercept_mode(enabled):
    """Toggle full intercept mode in the deployed const.py and restart HHD.

    enabled=True: full intercept (back paddles + everything via vendor HID)
    enabled=False: face buttons only (just Home + QAM, Xbox gamepad normal)
    """
    steps = []

    _log_info(f"=== Set Intercept Mode: {'Full' if enabled else 'Face buttons only'} ===")

    target_dir = _find_target_dir()
    if not target_dir:
        return {"success": False, "error": "HHD oxp directory not found", "steps": steps}

    target = os.path.join(target_dir, "const.py")

    # Check that patches are applied first
    status = is_applied()
    if not status.get("applied"):
        return {"success": False, "error": "Button fix must be applied first", "steps": steps}

    # Read current content
    try:
        with open(target) as f:
            content = f.read()
    except Exception as e:
        return {"success": False, "error": f"Failed to read const.py: {e}", "steps": steps}

    # Toggle the value
    if enabled:
        new_content = content.replace('"apex_intercept": False', '"apex_intercept": True')
    else:
        new_content = content.replace('"apex_intercept": True', '"apex_intercept": False')

    if new_content == content:
        mode_str = "Full intercept" if enabled else "Face buttons only"
        return {"success": True, "message": f"Already in {mode_str} mode", "steps": ["No change needed"]}

    # Unlock filesystem if needed
    if not _unlock_filesystem(target, steps):
        return {"success": False, "error": "Filesystem is not writable", "steps": steps}

    # Write modified content
    try:
        with open(target, "w") as f:
            f.write(new_content)
        mode_str = "Full intercept" if enabled else "Face buttons only"
        steps.append(f"Set intercept mode: {mode_str}")
        _log_info(f"Set apex_intercept={enabled} in deployed const.py")
    except Exception as e:
        return {"success": False, "error": f"Failed to write const.py: {e}", "steps": steps}

    # Restart HHD
    if not _restart_hhd(steps):
        return {"success": True, "warning": "Mode changed but HHD restart may have failed", "steps": steps}

    mode_str = "Full intercept" if enabled else "Face buttons only"
    _log_info(f"Intercept mode switched to: {mode_str}")
    return {"success": True, "message": f"Switched to {mode_str} mode", "steps": steps}


if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    usage = "Usage: sudo python3 button_fix.py [status|apply|revert|compat]"

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        result = is_applied()
        print(_json.dumps(result, indent=2))

    elif cmd == "apply":
        result = apply()
        print(_json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)

    elif cmd == "revert":
        result = revert()
        print(_json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)

    elif cmd == "compat":
        result = check_compatibility()
        print(_json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print(usage)
        sys.exit(1)
