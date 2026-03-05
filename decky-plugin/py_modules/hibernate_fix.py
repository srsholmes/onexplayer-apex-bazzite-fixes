"""Hibernate on Power Button fix for OneXPlayer Apex on Bazzite.

Patches HHD's powerbutton plugin so the power button triggers hibernate
instead of suspend. S0i3 suspend is broken on Strix Halo with kernel <6.18,
so this forces hibernate as the sleep action.

Also installs a systemd sleep hook that logs hibernate/wake events
for debugging.

Requires ostree unlock + HHD restart.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import stat
import time

logger = logging.getLogger("OXP-HibernateFix")

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
PATCH_DIR = os.path.join(os.path.dirname(__file__), "hhd_patches")
VANILLA_DIR = os.path.join(PATCH_DIR, "powerbutton_vanilla")
PATCHED_DIR = os.path.join(PATCH_DIR, "powerbutton_patched")

# Target file
FILE = "base.py"

# Systemd sleep hook
SLEEP_HOOK_PATH = "/usr/lib/systemd/system-sleep/99-oxp-hibernate-log"
HIBERNATE_LOG = "/var/log/oxp-hibernate.log"

SLEEP_HOOK_CONTENT = """#!/bin/bash
# OXP Apex Tools — hibernate/wake event logger
# Installed by the Decky plugin hibernate fix.

LOG="{log_path}"

case "$1" in
    pre)
        echo "$(date -Iseconds) PRE-HIBERNATE" >> "$LOG"
        echo "  mem_available: $(grep MemAvailable /proc/meminfo | awk '{{print $2, $3}}')" >> "$LOG"
        echo "  swap_free: $(grep SwapFree /proc/meminfo | awk '{{print $2, $3}}')" >> "$LOG"
        echo "  swap_total: $(grep SwapTotal /proc/meminfo | awk '{{print $2, $3}}')" >> "$LOG"
        ;;
    post)
        echo "$(date -Iseconds) POST-WAKE" >> "$LOG"
        echo "  mem_available: $(grep MemAvailable /proc/meminfo | awk '{{print $2, $3}}')" >> "$LOG"
        echo "  last_dmesg: $(dmesg --time-format iso | tail -5 | tr '\\n' ' ')" >> "$LOG"
        echo "" >> "$LOG"
        ;;
esac
""".format(log_path=HIBERNATE_LOG)


def _find_target_dir():
    """Locate the HHD powerbutton plugin directory on the system."""
    target = "/usr/lib/python3.14/site-packages/hhd/plugins/powerbutton"
    if os.path.isdir(target):
        return target
    import glob as _glob
    results = sorted(_glob.glob("/usr/lib/python3*/site-packages/hhd/plugins/powerbutton"))
    if results:
        return results[-1]
    return None


def _file_hash(path):
    """SHA256 hash of a file's contents."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _sleep_hook_installed():
    """Check if our systemd sleep hook is installed."""
    return os.path.exists(SLEEP_HOOK_PATH)


def is_applied():
    """Check if the hibernate fix is currently applied."""
    target_dir = _find_target_dir()
    if not target_dir:
        return {"applied": False, "error": "HHD powerbutton directory not found"}

    try:
        target = os.path.join(target_dir, FILE)
        patched = os.path.join(PATCHED_DIR, FILE)
        if not os.path.exists(target):
            return {"applied": False, "error": f"{FILE} not found at {target_dir}"}
        if not os.path.exists(patched):
            return {"applied": False, "error": f"Bundled patched {FILE} missing"}

        file_patched = _file_hash(target) == _file_hash(patched)
        hook_installed = _sleep_hook_installed()

        if file_patched and hook_installed:
            return {"applied": True}
        elif file_patched:
            return {"applied": True, "warning": "Sleep hook not installed"}
        else:
            return {"applied": False}
    except Exception as e:
        return {"applied": False, "error": str(e)}


def check_compatibility():
    """Check if installed HHD powerbutton file matches our expected vanilla or patched version."""
    target_dir = _find_target_dir()
    if not target_dir:
        return {"compatible": False, "message": "HHD powerbutton directory not found"}

    target = os.path.join(target_dir, FILE)
    vanilla = os.path.join(VANILLA_DIR, FILE)
    patched = os.path.join(PATCHED_DIR, FILE)

    if not os.path.exists(target):
        return {"compatible": False, "message": f"{FILE} not found"}

    h = _file_hash(target)
    if h == _file_hash(patched):
        return {"compatible": True}  # already patched
    if h == _file_hash(vanilla):
        return {"compatible": True}  # vanilla, ready to patch
    return {
        "compatible": False,
        "message": (
            f"HHD powerbutton {FILE} has been modified by a different version. "
            f"The system HHD may have been updated. "
            f"Patches need to be regenerated for the new version."
        ),
    }


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
    """Restart all HHD service instances so they pick up new files."""
    _log_info("Restarting HHD...")
    try:
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


def _install_sleep_hook(steps):
    """Install the systemd sleep hook for hibernate logging."""
    try:
        hook_dir = os.path.dirname(SLEEP_HOOK_PATH)
        if not os.path.isdir(hook_dir):
            _log_warning(f"Sleep hook directory {hook_dir} not found — skipping hook install")
            steps.append("Sleep hook directory missing (skipped)")
            return True  # non-fatal

        with open(SLEEP_HOOK_PATH, "w") as f:
            f.write(SLEEP_HOOK_CONTENT)
        os.chmod(SLEEP_HOOK_PATH, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        _log_info(f"Installed sleep hook at {SLEEP_HOOK_PATH}")
        steps.append("Installed sleep hook")
        return True
    except Exception as e:
        _log_warning(f"Failed to install sleep hook: {e}")
        steps.append(f"Sleep hook install failed: {e}")
        return True  # non-fatal


def _remove_sleep_hook(steps):
    """Remove the systemd sleep hook."""
    try:
        if os.path.exists(SLEEP_HOOK_PATH):
            os.remove(SLEEP_HOOK_PATH)
            _log_info(f"Removed sleep hook at {SLEEP_HOOK_PATH}")
            steps.append("Removed sleep hook")
        else:
            steps.append("Sleep hook already absent")
        return True
    except Exception as e:
        _log_warning(f"Failed to remove sleep hook: {e}")
        steps.append(f"Sleep hook removal failed: {e}")
        return True  # non-fatal


def apply():
    """Apply the hibernate fix by copying patched powerbutton file. Idempotent."""
    steps = []

    _log_info("=== Hibernate Fix Apply Start ===")

    status = is_applied()
    if status.get("applied") and not status.get("warning"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    # Check compatibility before touching anything
    compat = check_compatibility()
    if not compat.get("compatible"):
        msg = compat.get("message", "HHD version mismatch")
        _log_error(f"Compatibility check failed: {msg}")
        return {"success": False, "error": msg, "steps": steps}

    target_dir = _find_target_dir()
    if not target_dir:
        return {"success": False, "error": "HHD powerbutton directory not found", "steps": steps}

    test_path = os.path.join(target_dir, FILE)

    # Unlock immutable filesystem
    if not _unlock_filesystem(test_path, steps):
        return {"success": False, "error": "Filesystem is not writable. ostree unlock failed.", "steps": steps}

    # Copy patched file
    try:
        src = os.path.join(PATCHED_DIR, FILE)
        dst = os.path.join(target_dir, FILE)
        shutil.copy2(src, dst)
        _log_info(f"Copied patched {FILE}")
        steps.append(f"Copied patched {FILE}")
    except Exception as e:
        _log_error(f"Failed to copy file: {e}")
        # Attempt rollback
        _log_warning("Rolling back to vanilla...")
        try:
            vanilla = os.path.join(VANILLA_DIR, FILE)
            dst = os.path.join(target_dir, FILE)
            shutil.copy2(vanilla, dst)
            steps.append("Rolled back to vanilla after error")
        except Exception as rb_err:
            _log_error(f"Rollback failed: {rb_err}")
        return {"success": False, "error": f"Failed to copy file: {e}", "steps": steps}

    # Install sleep hook
    _install_sleep_hook(steps)

    # Restart HHD
    if not _restart_hhd(steps):
        return {"success": True, "warning": "Patched but HHD restart may have failed", "steps": steps}

    _log_info("Hibernate fix applied successfully")
    return {"success": True, "message": "Hibernate fix applied and HHD restarted", "steps": steps}


def revert():
    """Revert the hibernate fix by restoring vanilla powerbutton file."""
    steps = []

    _log_info("=== Hibernate Fix Revert Start ===")

    target_dir = _find_target_dir()
    if not target_dir:
        return {"success": False, "error": "HHD powerbutton directory not found", "steps": steps}

    # Check if already vanilla
    target = os.path.join(target_dir, FILE)
    vanilla = os.path.join(VANILLA_DIR, FILE)
    if os.path.exists(target) and os.path.exists(vanilla):
        if _file_hash(target) == _file_hash(vanilla) and not _sleep_hook_installed():
            return {"success": True, "message": "Already reverted (vanilla)", "steps": ["Already vanilla"]}

    test_path = os.path.join(target_dir, FILE)

    # Unlock immutable filesystem
    if not _unlock_filesystem(test_path, steps):
        return {"success": False, "error": "Filesystem is not writable. ostree unlock failed.", "steps": steps}

    # Copy vanilla file back
    try:
        src = os.path.join(VANILLA_DIR, FILE)
        dst = os.path.join(target_dir, FILE)
        shutil.copy2(src, dst)
        _log_info(f"Restored vanilla {FILE}")
        steps.append(f"Restored vanilla {FILE}")
    except Exception as e:
        _log_error(f"Failed to restore vanilla file: {e}")
        return {"success": False, "error": f"Failed to restore file: {e}", "steps": steps}

    # Remove sleep hook
    _remove_sleep_hook(steps)

    # Restart HHD
    if not _restart_hhd(steps):
        return {"success": True, "warning": "Reverted but HHD restart may have failed", "steps": steps}

    _log_info("Hibernate fix reverted successfully")
    return {"success": True, "message": "Hibernate fix reverted and HHD restarted", "steps": steps}


def get_hibernate_logs(lines=30):
    """Read the last N lines from the hibernate event log."""
    try:
        if not os.path.exists(HIBERNATE_LOG):
            return {"lines": [], "log_file": HIBERNATE_LOG}
        with open(HIBERNATE_LOG) as f:
            all_lines = f.readlines()
        tail = [l.rstrip("\n") for l in all_lines[-lines:]]
        return {"lines": tail, "log_file": HIBERNATE_LOG}
    except Exception as e:
        return {"lines": [], "log_file": HIBERNATE_LOG, "error": str(e)}


if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    usage = "Usage: sudo python3 hibernate_fix.py [status|apply|revert|compat|logs]"

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

    elif cmd == "logs":
        result = get_hibernate_logs()
        print(_json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print(usage)
        sys.exit(1)
