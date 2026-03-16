"""Button fix for OneXPlayer Apex on Bazzite.

Replaces the system HHD (Handheld Daemon) with a local fork that includes
Apex-specific fixes (device recognition, button mappings, intercept mode).

Delegates all work to hhd_fork.py which manages the git clone, venv, and
systemd service. No ostree unlock required — everything lives in /home/.
"""

import logging
import os

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

    # Forward to hhd_fork
    try:
        import hhd_fork
        hhd_fork.set_log_callbacks(info_fn, error_fn, warning_fn)
    except ImportError:
        pass


import hhd_fork


def is_applied():
    """Check if the Apex button fix is currently applied (fork installed + running)."""
    status = hhd_fork.is_installed()
    return {
        "applied": status.get("installed", False) and status.get("running", False),
        "fork_installed": status.get("installed", False),
        "fork_running": status.get("running", False),
    }


def apply():
    """Apply the Apex button fix by installing and starting the HHD fork."""
    return hhd_fork.install()


def revert():
    """Revert the Apex button fix by uninstalling the fork and restoring system HHD."""
    return hhd_fork.uninstall()


def get_intercept_mode():
    """Check if full intercept mode is enabled."""
    return hhd_fork.get_intercept_mode()


def set_intercept_mode(enabled):
    """Toggle full intercept mode and restart HHD."""
    return hhd_fork.set_intercept_mode(enabled)


def update():
    """Pull latest changes from the HHD fork and restart."""
    return hhd_fork.update()


def get_fork_status():
    """Get detailed HHD fork status (commit, branch, etc.)."""
    return hhd_fork.get_status()


def check_compatibility():
    """Compatibility check — always compatible with fork approach."""
    return {"compatible": True}


if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    usage = "Usage: sudo python3 button_fix.py [status|apply|revert|update|fork-status]"

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

    elif cmd == "update":
        result = update()
        print(_json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)

    elif cmd == "fork-status":
        result = get_fork_status()
        print(_json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print(usage)
        sys.exit(1)
