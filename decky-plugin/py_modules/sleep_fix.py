"""Sleep/suspend fix for OneXPlayer Apex (Strix Halo) on Bazzite.

The Apex uses an AMD Strix Halo APU which has a known issue where
suspend/resume fails due to IOMMU conflicts. Adding amd_iommu=off
as a kernel parameter fixes wake-from-sleep.

This module applies the kernel parameter via rpm-ostree (Bazzite's atomic
update system). The karg requires a reboot to take effect.
"""

import logging
import os
import subprocess

logger = logging.getLogger("OXP-SleepFix")


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


KARG = "amd_iommu=off"

# Old kargs/udev from the previous sleep fix — cleaned up if found
OLD_KARGS = [
    "amdgpu.cwsr_enable=0",
    "iommu=pt",
    "amdgpu.gttsize=126976",
    "ttm.pages_limit=32505856",
]
OLD_UDEV_RULE_PATH = "/etc/udev/rules.d/99-disable-spurious-wake.rules"


def get_status():
    """Check whether the sleep fix kernel param is active."""
    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    applied = KARG in cmdline

    return {
        "applied": applied,
        "karg": KARG,
        "karg_set": applied,
    }


def apply():
    """Apply the sleep fix. Returns status dict with reboot_needed flag.

    Adds amd_iommu=off via rpm-ostree kargs. Also cleans up any old
    sleep fix kargs/udev rules if present.

    WARNING: rpm-ostree kargs creates a new ostree deployment. Any
    ostree admin unlock --hotfix overlay (e.g. button fix patches)
    will be lost on reboot into the new deployment. Re-apply the
    button fix after rebooting.
    """
    reboot_needed = False

    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    # Clean up old kargs if present
    for old_karg in OLD_KARGS:
        if old_karg in cmdline:
            logger.info(f"Removing old karg: {old_karg}")
            try:
                subprocess.run(
                    ["rpm-ostree", "kargs", f"--delete={old_karg}"],
                    capture_output=True, timeout=60,
                    env=_clean_env()
                )
                reboot_needed = True
            except Exception as e:
                logger.warning(f"Could not remove old karg {old_karg}: {e}")

    # Clean up old udev rule if present
    if os.path.exists(OLD_UDEV_RULE_PATH):
        try:
            os.remove(OLD_UDEV_RULE_PATH)
            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                capture_output=True, timeout=10,
                env=_clean_env()
            )
            logger.info("Removed old udev rule")
        except Exception as e:
            logger.warning(f"Could not remove old udev rule: {e}")

    # Apply the actual fix
    if KARG in cmdline:
        logger.info(f"Already set: {KARG}")
    else:
        logger.info(f"Adding karg: {KARG}")
        try:
            subprocess.run(
                ["rpm-ostree", "kargs", f"--append-if-missing={KARG}"],
                capture_output=True, timeout=60,
                env=_clean_env()
            )
            reboot_needed = True
        except Exception as e:
            logger.error(f"Failed to add karg {KARG}: {e}")
            return {"success": False, "error": f"Failed to add karg {KARG}: {e}"}

    msg = "Reboot required for kernel param" if reboot_needed else "Sleep fix already applied"
    if reboot_needed:
        msg += ". Note: button fix patches will need to be re-applied after reboot."

    return {
        "success": True,
        "reboot_needed": reboot_needed,
        "message": msg,
    }
