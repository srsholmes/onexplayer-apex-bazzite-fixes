"""Sleep/suspend fix cleanup for OneXPlayer Apex (Strix Halo) on Bazzite.

S0i3 deep sleep does NOT work on Strix Halo with kernel 6.17 — ACPI C4
support (required for VDD OFF / S0i3) is missing until kernel 6.18+.

Previous fix attempts applied various kernel parameters that either didn't
help or made things worse (device hangs on sleep, requiring hard power off).
This module provides cleanup: removing all previously applied kargs and
udev rules so the system is in a clean state.

No sleep fix is applied — there is no working fix until kernel 6.18+.
Use hibernate (S4) instead — see hibernate_setup.py.
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


# ALL kargs we've ever applied across any version — remove() cleans all of them
ALL_KARGS = [
    # test/sleep branch
    "iommu=pt",
    "acpi.ec_no_wakeup=1",
    # main branch
    "amd_iommu=off",
    "amd_iommu=on",
    # ancient attempts
    "amdgpu.cwsr_enable=0",
    "amdgpu.gttsize=126976",
    "ttm.pages_limit=32505856",
]

# Udev rules we've ever created
UDEV_RULES = [
    "/etc/udev/rules.d/91-oxp-fingerprint-no-wakeup.rules",
    "/etc/udev/rules.d/99-disable-spurious-wake.rules",
]


def get_status():
    """Check which sleep fix kargs are currently present in the boot cmdline."""
    try:
        with open("/proc/cmdline") as f:
            cmdline = f.read()
    except Exception:
        cmdline = ""

    kargs_found = [k for k in ALL_KARGS if k in cmdline]

    return {
        "has_kargs": len(kargs_found) > 0,
        "kargs_found": kargs_found,
    }


def remove():
    """Remove ALL sleep fix kargs and udev rules we've ever applied.

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

    # Remove all sleep fix kargs
    for karg in ALL_KARGS:
        if karg in cmdline:
            logger.info(f"Removing sleep fix karg: {karg}")
            try:
                subprocess.run(
                    ["rpm-ostree", "kargs", f"--delete={karg}"],
                    capture_output=True, timeout=60,
                    env=_clean_env()
                )
                reboot_needed = True
            except Exception as e:
                logger.warning(f"Could not remove karg {karg}: {e}")

    # Remove udev rules
    reload_udev = False
    for rule_path in UDEV_RULES:
        if os.path.exists(rule_path):
            try:
                os.remove(rule_path)
                logger.info(f"Removed udev rule: {rule_path}")
                reload_udev = True
            except Exception as e:
                logger.warning(f"Could not remove udev rule {rule_path}: {e}")

    if reload_udev:
        try:
            subprocess.run(
                ["udevadm", "control", "--reload-rules"],
                capture_output=True, timeout=10,
                env=_clean_env()
            )
        except Exception as e:
            logger.warning(f"Could not reload udev rules: {e}")

    if reboot_needed:
        msg = "Sleep fix kargs removed. Reboot required."
        msg += " Note: button fix patches will need to be re-applied after reboot."
    else:
        msg = "No sleep fix kargs found to remove."

    return {
        "success": True,
        "reboot_needed": reboot_needed,
        "message": msg,
    }
