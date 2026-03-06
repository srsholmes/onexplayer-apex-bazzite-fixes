"""Power button hibernate fix for OneXPlayer Apex on Bazzite.

Patches HHD's power button handler so that pressing the power button in
Steam Game Mode triggers hibernate instead of (broken) S0i3 sleep.

Patches hhd/plugins/powerbutton/base.py — the run_steam_shortpress()
function is replaced to always call emergency_hibernate().

Requires ostree unlock + HHD restart.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import time

logger = logging.getLogger("OXP-PowerButtonFix")

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


PATCH_DIR = os.path.join(os.path.dirname(__file__), "hhd_patches")
VANILLA_DIR = os.path.join(PATCH_DIR, "powerbutton_vanilla")
PATCHED_DIR = os.path.join(PATCH_DIR, "powerbutton_patched")

FILES = ["base.py"]


def _find_target_dir():
    target = "/usr/lib/python3.14/site-packages/hhd/plugins/powerbutton"
    if os.path.isdir(target):
        return target
    import glob as _glob
    results = sorted(_glob.glob("/usr/lib/python3*/site-packages/hhd/plugins/powerbutton"))
    if results:
        return results[-1]
    return None


def _file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def is_applied():
    target_dir = _find_target_dir()
    if not target_dir:
        return {"applied": False, "error": "HHD powerbutton directory not found"}
    try:
        for name in FILES:
            target = os.path.join(target_dir, name)
            patched = os.path.join(PATCHED_DIR, name)
            if not os.path.exists(target) or not os.path.exists(patched):
                return {"applied": False}
            if _file_hash(target) != _file_hash(patched):
                return {"applied": False}
        return {"applied": True}
    except Exception as e:
        return {"applied": False, "error": str(e)}


def check_compatibility():
    target_dir = _find_target_dir()
    if not target_dir:
        return {"compatible": False, "message": "HHD powerbutton directory not found"}
    for name in FILES:
        target = os.path.join(target_dir, name)
        vanilla = os.path.join(VANILLA_DIR, name)
        patched = os.path.join(PATCHED_DIR, name)
        if not os.path.exists(target):
            return {"compatible": False, "message": f"{name} not found"}
        h = _file_hash(target)
        if h == _file_hash(patched) or h == _file_hash(vanilla):
            continue
        return {
            "compatible": False,
            "message": f"HHD powerbutton {name} has been modified — version mismatch.",
        }
    return {"compatible": True}


def _is_filesystem_writable(test_path):
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
    if _is_filesystem_writable(test_path):
        steps.append("Filesystem already writable")
        return True
    try:
        r = subprocess.run(
            ["ostree", "admin", "unlock", "--hotfix"],
            capture_output=True, text=True, timeout=120,
            env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Unlocked filesystem")
        else:
            steps.append(f"ostree unlock returned {r.returncode}")
    except Exception as e:
        steps.append(f"ostree unlock failed: {e}")
        return False

    for attempt in range(1, 7):
        if _is_filesystem_writable(test_path):
            steps.append("Filesystem confirmed writable")
            return True
        time.sleep(min(attempt * 0.5, 2.0))

    steps.append("Filesystem not writable after retries")
    return False


def _restart_hhd(steps):
    _log_info("Restarting HHD...")
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "--plain", "--no-legend", "--type=service", "hhd*"],
            capture_output=True, text=True, timeout=10,
            env=_clean_env()
        )
        units = [line.split()[0] for line in r.stdout.strip().splitlines() if line.split()]
        if not units:
            units = ["hhd"]

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
                    success = True
            except Exception:
                pass
        return success
    except Exception as e:
        steps.append(f"HHD restart failed: {e}")
        return False


def apply():
    steps = []
    _log_info("=== Power Button Fix Apply Start ===")

    status = is_applied()
    if status.get("applied"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    compat = check_compatibility()
    if not compat.get("compatible"):
        return {"success": False, "error": compat.get("message", "Incompatible"), "steps": steps}

    target_dir = _find_target_dir()
    if not target_dir:
        return {"success": False, "error": "HHD powerbutton directory not found", "steps": steps}

    test_path = os.path.join(target_dir, "base.py")
    if not _unlock_filesystem(test_path, steps):
        return {"success": False, "error": "Filesystem is not writable", "steps": steps}

    try:
        for name in FILES:
            src = os.path.join(PATCHED_DIR, name)
            dst = os.path.join(target_dir, name)
            shutil.copy2(src, dst)
            steps.append(f"Copied patched {name}")
    except Exception as e:
        # Rollback
        try:
            for name in FILES:
                vanilla = os.path.join(VANILLA_DIR, name)
                dst = os.path.join(target_dir, name)
                if os.path.exists(vanilla):
                    shutil.copy2(vanilla, dst)
        except Exception:
            pass
        return {"success": False, "error": f"Failed to copy files: {e}", "steps": steps}

    if not _restart_hhd(steps):
        return {"success": True, "warning": "Patched but HHD restart may have failed", "steps": steps}

    _log_info("Power button fix applied — short press now hibernates")
    return {"success": True, "message": "Power button now triggers hibernate instead of sleep", "steps": steps}


def revert():
    steps = []
    _log_info("=== Power Button Fix Revert Start ===")

    target_dir = _find_target_dir()
    if not target_dir:
        return {"success": False, "error": "HHD powerbutton directory not found", "steps": steps}

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
        return {"success": True, "message": "Already reverted", "steps": ["Already vanilla"]}

    test_path = os.path.join(target_dir, "base.py")
    if not _unlock_filesystem(test_path, steps):
        return {"success": False, "error": "Filesystem is not writable", "steps": steps}

    try:
        for name in FILES:
            src = os.path.join(VANILLA_DIR, name)
            dst = os.path.join(target_dir, name)
            shutil.copy2(src, dst)
            steps.append(f"Restored vanilla {name}")
    except Exception as e:
        return {"success": False, "error": f"Failed to restore files: {e}", "steps": steps}

    if not _restart_hhd(steps):
        return {"success": True, "warning": "Reverted but HHD restart may have failed", "steps": steps}

    _log_info("Power button fix reverted — short press restored to default behavior")
    return {"success": True, "message": "Power button restored to default sleep behavior", "steps": steps}
