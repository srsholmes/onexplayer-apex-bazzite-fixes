"""Light sleep (s2idle) kargs manager for OneXPlayer Apex on Bazzite.

Light sleep works on Strix Halo when "ACPI Auto configuration" is enabled
in the BIOS. This module applies the required kernel parameters and removes
any known-problematic legacy kargs from previous fix attempts.

IMPORTANT: rpm-ostree kargs creates a new ostree deployment. Any hotfix
overlay (e.g. button fix patches) will be lost on reboot. Re-apply the
button fix after rebooting.
"""

import logging
import os
import subprocess

logger = logging.getLogger("OXP-SleepFix")

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


# Kargs that enable light sleep (s2idle)
LIGHT_SLEEP_KARGS = [
    "mem_sleep_default=s2idle",
    "amd_iommu=off",             # Required — IOMMU must be off for sleep on Strix Halo
]

# Legacy kargs that should be removed — broken or counterproductive
PROBLEMATIC_KARGS = [
    "amd_iommu=on",              # Invalid AMD parameter, silently ignored
    "acpi.ec_no_wakeup=1",       # Prevents EC-based wakeup
    "amdgpu.cwsr_enable=0",      # Compute-specific, not needed for sleep
    "amdgpu.gttsize=126976",     # Not sleep-related
    "ttm.pages_limit=32505856",  # Not sleep-related
]


def _read_cmdline():
    try:
        with open("/proc/cmdline") as f:
            return f.read()
    except Exception:
        return ""


def get_status():
    """Check light sleep kargs and problematic legacy kargs."""
    cmdline = _read_cmdline()

    light_sleep_present = [k for k in LIGHT_SLEEP_KARGS if k in cmdline]
    light_sleep_missing = [k for k in LIGHT_SLEEP_KARGS if k not in cmdline]
    problematic_found = [k for k in PROBLEMATIC_KARGS if k in cmdline]

    applied = len(light_sleep_missing) == 0 and len(problematic_found) == 0

    return {
        "applied": applied,
        "light_sleep_present": light_sleep_present,
        "light_sleep_missing": light_sleep_missing,
        "problematic_kargs": problematic_found,
        "has_problematic_kargs": len(problematic_found) > 0,
    }


def apply():
    """Apply light sleep kargs and remove problematic legacy kargs.

    Uses a single rpm-ostree kargs call to minimize deployment churn.
    """
    cmdline = _read_cmdline()
    steps = []

    _log_info("=== Light Sleep Apply Start ===")

    # Build rpm-ostree kargs command with all changes at once
    args = ["rpm-ostree", "kargs"]

    for karg in LIGHT_SLEEP_KARGS:
        if karg not in cmdline:
            args.append(f"--append={karg}")
            steps.append(f"Adding {karg}")

    for karg in PROBLEMATIC_KARGS:
        if karg in cmdline:
            args.append(f"--delete={karg}")
            steps.append(f"Removing {karg}")

    if len(args) == 2:
        _log_info("Light sleep kargs already correct, no changes needed")
        return {
            "success": True,
            "reboot_needed": False,
            "message": "Light sleep kargs already applied. No changes needed.",
            "steps": ["All kargs already correct"],
        }

    _log_info(f"Running: {' '.join(args)}")
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=60,
            env=_clean_env()
        )
        if r.returncode != 0:
            error_msg = r.stderr.strip() or "Unknown error"
            _log_error(f"rpm-ostree kargs failed: {error_msg}")
            return {
                "success": False,
                "error": f"rpm-ostree kargs failed: {error_msg}",
                "steps": steps,
            }
    except subprocess.TimeoutExpired:
        _log_error("rpm-ostree kargs timed out")
        return {
            "success": False,
            "error": "rpm-ostree kargs timed out (60s)",
            "steps": steps,
        }
    except Exception as e:
        _log_error(f"rpm-ostree kargs exception: {e}")
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
        }

    msg = "Light sleep kargs applied. Reboot required."
    msg += " Note: button fix patches will need to be re-applied after reboot."
    _log_info(f"Light sleep apply complete: {msg}")
    return {
        "success": True,
        "reboot_needed": True,
        "message": msg,
        "steps": steps,
    }


def revert():
    """Remove light sleep kargs."""
    cmdline = _read_cmdline()
    steps = []

    _log_info("=== Light Sleep Revert Start ===")

    args = ["rpm-ostree", "kargs"]

    for karg in LIGHT_SLEEP_KARGS:
        if karg in cmdline:
            args.append(f"--delete={karg}")
            steps.append(f"Removing {karg}")

    if len(args) == 2:
        _log_info("No light sleep kargs to remove")
        return {
            "success": True,
            "reboot_needed": False,
            "message": "No light sleep kargs to remove.",
            "steps": ["No kargs to remove"],
        }

    _log_info(f"Running: {' '.join(args)}")
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=60,
            env=_clean_env()
        )
        if r.returncode != 0:
            error_msg = r.stderr.strip() or "Unknown error"
            _log_error(f"rpm-ostree kargs failed: {error_msg}")
            return {
                "success": False,
                "error": f"rpm-ostree kargs failed: {error_msg}",
                "steps": steps,
            }
    except Exception as e:
        _log_error(f"rpm-ostree kargs exception: {e}")
        return {
            "success": False,
            "error": str(e),
            "steps": steps,
        }

    msg = "Light sleep kargs removed. Reboot required."
    msg += " Note: button fix patches will need to be re-applied after reboot."
    _log_info(f"Light sleep revert complete: {msg}")
    return {
        "success": True,
        "reboot_needed": True,
        "message": msg,
        "steps": steps,
    }


# Legacy compat — old frontend called remove() for cleanup
def remove():
    """Remove problematic kargs (legacy compat, delegates to apply)."""
    return apply()
