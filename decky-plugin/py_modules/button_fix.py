"""Button fix for OneXPlayer Apex on Bazzite.

Patches HHD (Handheld Daemon) to add Apex device support with correct
button mappings and keyboard VID:PID. Requires ostree unlock + HHD restart.
"""

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import time

logger = logging.getLogger("OXP-ButtonFix")

# Pluggable log callbacks — set by main.py to route logs to the plugin log file.
# Default to standard logger so the module works standalone too.
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
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH.

    Decky Loader runs Python from a PyInstaller bundle which sets LD_LIBRARY_PATH
    to its temp extraction dir (e.g. /tmp/_MEIxxxxxx/). This dir contains bundled
    OpenSSL libs that are incompatible with system binaries like ostree, systemctl,
    rpm-ostree, etc. Stripping these vars lets subprocesses use the correct system libs.
    """
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backups")
BACKUP_META = os.path.join(BACKUP_DIR, "button_fix_meta.json")

# Patch content for const.py — Apex button mappings
APEX_MAPPINGS_BLOCK = '''
# Apex-specific: Home button sends KEY_G instead of KEY_D
APEX_BTN_MAPPINGS = {
    B("KEY_VOLUMEUP"): "key_volumeup",
    B("KEY_VOLUMEDOWN"): "key_volumedown",
    # Turbo Button: KEY_LEFTCTRL + KEY_LEFTALT + KEY_LEFTMETA
    B("KEY_LEFTALT"): "share",
    # Home/Orange Button: KEY_G + KEY_LEFTMETA (Apex uses KEY_G, not KEY_D)
    B("KEY_G"): "mode",
    # KB Button: KEY_O + KEY_RIGHTCTRL + KEY_LEFTMETA
    B("KEY_O"): "keyboard",
}
'''

APEX_DEVICE_ENTRY = '''    "ONEXPLAYER APEX": {
        "name": "ONEXPLAYER APEX",
        **ONEX_DEFAULT_CONF,
        "protocol": "hid_v2",
        "apex_kbd": True,
    },'''

# Placeholder byte values for Apex back paddles in hid_v2.py OXP_BUTTONS.
# These need to be updated after capturing raw HID reports from the device:
#   sudo systemctl stop hhd
#   sudo cat /dev/hidrawN | xxd   (press each paddle, note the byte)
# On other OXP devices, L4=0x22 and R4=0x23. The Apex sends different values.
APEX_L4_BYTE = 0x22  # TODO: replace with actual Apex L4 paddle byte
APEX_R4_BYTE = 0x23  # TODO: replace with actual Apex R4 paddle byte


def _find_hhd_files():
    """Locate HHD oxp const.py, base.py, and hid_v2.py."""
    # Try hardcoded path first (most common on Bazzite)
    const_file = "/usr/lib/python3.14/site-packages/hhd/device/oxp/const.py"
    base_file = "/usr/lib/python3.14/site-packages/hhd/device/oxp/base.py"
    hid_v2_file = "/usr/lib/python3.14/site-packages/hhd/device/oxp/hid_v2.py"
    if os.path.exists(const_file) and os.path.exists(base_file):
        if not os.path.exists(hid_v2_file):
            hid_v2_file = None
        return const_file, base_file, hid_v2_file
    # Fallback: search for any Python version
    results = sorted(glob.glob("/usr/lib/python3*/site-packages/hhd/device/oxp/const.py"))
    if results:
        const_file = results[-1]
        base_file = const_file.replace("const.py", "base.py")
        hid_v2_file = const_file.replace("const.py", "hid_v2.py")
        if os.path.exists(base_file):
            if not os.path.exists(hid_v2_file):
                hid_v2_file = None
            return const_file, base_file, hid_v2_file
    return None, None, None


def is_applied():
    """Check if the Apex button fix is already applied."""
    const_file, base_file, hid_v2_file = _find_hhd_files()
    if not const_file or not base_file:
        return {"applied": False, "error": "HHD oxp files not found"}
    try:
        with open(const_file) as f:
            const_content = f.read()
        with open(base_file) as f:
            base_content = f.read()
        const_ok = "ONEXPLAYER APEX" in const_content and "APEX_BTN_MAPPINGS" in const_content
        base_ok = "APEX_BTN_MAPPINGS" in base_content
        # hid_v2 patch is optional — only check if the file exists
        hid_v2_ok = True
        if hid_v2_file:
            with open(hid_v2_file) as f:
                hid_v2_content = f.read()
            hid_v2_ok = "# Apex back paddles" in hid_v2_content
        # "applied" is based on const+base only — these are the core patches.
        # hid_v2 is an additive enhancement; its absence shouldn't flip the
        # toggle to "Not applied" for users who already have the old patch.
        return {
            "applied": const_ok and base_ok,
            "const_patched": const_ok,
            "base_patched": base_ok,
            "hid_v2_patched": hid_v2_ok,
        }
    except Exception as e:
        return {"applied": False, "error": str(e)}


def _save_backups(const_file, base_file, hid_v2_file=None):
    """Save original HHD files before patching."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    const_backup = os.path.join(BACKUP_DIR, "const.py.bak")
    base_backup = os.path.join(BACKUP_DIR, "base.py.bak")
    shutil.copy2(const_file, const_backup)
    shutil.copy2(base_file, base_backup)
    meta = {"const_file": const_file, "base_file": base_file}
    if hid_v2_file:
        hid_v2_backup = os.path.join(BACKUP_DIR, "hid_v2.py.bak")
        shutil.copy2(hid_v2_file, hid_v2_backup)
        meta["hid_v2_file"] = hid_v2_file
    with open(BACKUP_META, "w") as f:
        json.dump(meta, f)
    _log_info(f"Backups saved to {BACKUP_DIR}")


def _has_backups():
    """Check if backups exist from a previous apply."""
    return os.path.exists(BACKUP_META)


def revert():
    """Restore original HHD files from backup and restart HHD."""
    steps = []

    if not _has_backups():
        return {"success": False, "error": "No backups found — nothing to revert", "steps": steps}

    try:
        with open(BACKUP_META) as f:
            meta = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"Failed to read backup metadata: {e}", "steps": steps}

    const_file = meta["const_file"]
    base_file = meta["base_file"]
    hid_v2_file = meta.get("hid_v2_file")
    const_backup = os.path.join(BACKUP_DIR, "const.py.bak")
    base_backup = os.path.join(BACKUP_DIR, "base.py.bak")
    hid_v2_backup = os.path.join(BACKUP_DIR, "hid_v2.py.bak")

    if not os.path.exists(const_backup) or not os.path.exists(base_backup):
        return {"success": False, "error": "Backup files missing", "steps": steps}

    # Unlock immutable filesystem (with retries for mount propagation)
    if not _unlock_filesystem(const_file, steps):
        return {"success": False, "error": "Filesystem is not writable. ostree unlock failed — check logs for details.", "steps": steps}

    try:
        shutil.copy2(const_backup, const_file)
        shutil.copy2(base_backup, base_file)
        if hid_v2_file and os.path.exists(hid_v2_backup):
            shutil.copy2(hid_v2_backup, hid_v2_file)
        steps.append("Restored original files from backup")
    except Exception as e:
        return {"success": False, "error": f"Failed to restore files: {e}", "steps": steps}

    # Restart HHD
    _log_info("Restarting HHD...")
    try:
        r = subprocess.run(
            ["systemctl", "restart", "hhd"],
            capture_output=True, text=True, timeout=30,
            env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Restarted HHD")
            _log_info("HHD restarted successfully")
        else:
            _log_error(f"systemctl restart hhd returned {r.returncode}: {r.stderr.strip()}")
            steps.append(f"HHD restart returned {r.returncode}")
            return {"success": True, "warning": f"Reverted but HHD restart may have failed (exit {r.returncode})", "steps": steps}
    except Exception as e:
        steps.append("HHD restart failed")
        _log_error(f"HHD restart exception: {e}")
        return {"success": True, "warning": f"Reverted but HHD restart failed: {e}", "steps": steps}

    _log_info("Button fix reverted from backup")
    return {"success": True, "message": "Button fix reverted and HHD restarted", "steps": steps}


def _is_filesystem_writable(test_path):
    """Check if the immutable filesystem is writable by testing the directory."""
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
    """Unlock the ostree immutable filesystem with retries.

    After `ostree admin unlock --hotfix`, the overlay mount can take a moment
    to propagate — especially when running as root under gamescope (Gaming Mode).
    We retry the writable check several times with delays before giving up.

    Returns True if filesystem is writable, False otherwise.
    """
    _log_info("Unlocking filesystem...")

    # Step 1: Check if already writable (previous hotfix unlock persists across reboots)
    if _is_filesystem_writable(test_path):
        _log_info("Filesystem already writable — skipping ostree unlock")
        steps.append("Filesystem already writable")
        return True

    # Step 2: Run ostree admin unlock --hotfix
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

    # Step 3: Wait for the overlay mount to become writable (retry with backoff)
    max_retries = 6
    for attempt in range(1, max_retries + 1):
        if _is_filesystem_writable(test_path):
            _log_info(f"Filesystem writable after attempt {attempt}")
            steps.append("Filesystem confirmed writable")
            return True
        wait = min(attempt * 0.5, 2.0)  # 0.5s, 1s, 1.5s, 2s, 2s, 2s
        _log_info(f"Filesystem not yet writable, waiting {wait}s (attempt {attempt}/{max_retries})...")
        time.sleep(wait)

    _log_error("Filesystem still not writable after all retries")
    steps.append("Filesystem not writable after retries")
    return False


def apply():
    """Apply the Apex button fix. Idempotent — safe to re-run."""
    steps = []

    # Log environment context for debugging
    _log_info("=== Button Fix Apply Start ===")
    _log_info(f"Running as UID={os.getuid()}, EUID={os.geteuid()}")
    _log_info(f"CWD: {os.getcwd()}")
    _log_info(f"PATH: {os.environ.get('PATH', 'not set')}")
    _log_info(f"DISPLAY: {os.environ.get('DISPLAY', 'not set')}")
    _log_info(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', 'not set')}")

    # Check ostree status before attempting unlock
    try:
        r = subprocess.run(
            ["ostree", "admin", "status"],
            capture_output=True, text=True, timeout=30,
            env=_clean_env()
        )
        _log_info(f"ostree admin status (exit {r.returncode}):\n{r.stdout.strip()}")
        if r.stderr.strip():
            _log_info(f"ostree admin status stderr: {r.stderr.strip()}")
    except Exception as e:
        _log_warning(f"Could not get ostree status: {e}")

    # Check current mount state of the target directory
    try:
        r = subprocess.run(
            ["mount"],
            capture_output=True, text=True, timeout=10,
            env=_clean_env()
        )
        overlay_mounts = [line for line in r.stdout.splitlines() if "overlay" in line.lower() or "/usr" in line]
        if overlay_mounts:
            _log_info(f"Relevant mounts:\n" + "\n".join(overlay_mounts))
        else:
            _log_info("No overlay or /usr mounts found")
    except Exception as e:
        _log_warning(f"Could not check mounts: {e}")

    status = is_applied()
    _log_info(f"Current patch status: {status}")
    if status.get("applied") and status.get("hid_v2_patched"):
        return {"success": True, "message": "Already applied", "steps": ["Already applied"]}

    const_file, base_file, hid_v2_file = _find_hhd_files()
    _log_info(f"HHD files: const={const_file}, base={base_file}, hid_v2={hid_v2_file}")
    if not const_file or not base_file:
        return {"success": False, "error": "HHD oxp files not found", "steps": steps}

    # Unlock immutable filesystem (with retries for mount propagation)
    if not _unlock_filesystem(const_file, steps):
        return {"success": False, "error": "Filesystem is not writable. ostree unlock failed — check logs for details.", "steps": steps}

    # Save backups for user-facing revert (always refresh if files changed)
    try:
        _save_backups(const_file, base_file, hid_v2_file)
        steps.append("Saved backups")
    except Exception as e:
        return {"success": False, "error": f"Failed to save backups: {e}", "steps": steps}

    # Read originals for rollback on partial failure
    const_backup = None
    base_backup = None
    hid_v2_backup = None
    try:
        with open(const_file) as f:
            const_backup = f.read()
        with open(base_file) as f:
            base_backup = f.read()
        if hid_v2_file:
            with open(hid_v2_file) as f:
                hid_v2_backup = f.read()
    except Exception as e:
        return {"success": False, "error": f"Failed to read files for backup: {e}", "steps": steps}

    errors = []

    # Patch const.py
    if not status.get("const_patched"):
        try:
            _patch_const(const_file)
            steps.append("Patched const.py")
        except Exception as e:
            errors.append(f"const.py: {e}")

    # Patch base.py
    if not status.get("base_patched"):
        try:
            _patch_base(base_file)
            steps.append("Patched base.py")
        except Exception as e:
            errors.append(f"base.py: {e}")

    # Patch hid_v2.py (back paddle remapping)
    if hid_v2_file and not status.get("hid_v2_patched"):
        try:
            _patch_hid_v2(hid_v2_file)
            steps.append("Patched hid_v2.py (back paddles → L4/R4)")
        except Exception as e:
            _log_warning(f"hid_v2.py patch failed (non-fatal): {e}")
            steps.append(f"hid_v2.py patch skipped: {e}")

    if errors:
        # Rollback on partial failure
        _log_warning("Rolling back due to errors")
        steps.append("Rolling back changes")
        try:
            if const_backup is not None:
                with open(const_file, "w") as f:
                    f.write(const_backup)
            if base_backup is not None:
                with open(base_file, "w") as f:
                    f.write(base_backup)
            if hid_v2_backup is not None and hid_v2_file:
                with open(hid_v2_file, "w") as f:
                    f.write(hid_v2_backup)
        except Exception as rollback_err:
            _log_error(f"Rollback failed: {rollback_err}")
        return {"success": False, "error": "; ".join(errors), "steps": steps}

    # Restart HHD so it picks up the patched code
    _log_info("Restarting HHD...")
    try:
        r = subprocess.run(
            ["systemctl", "restart", "hhd"],
            capture_output=True, text=True, timeout=30,
            env=_clean_env()
        )
        if r.returncode == 0:
            steps.append("Restarted HHD")
            _log_info("HHD restarted successfully")
        else:
            _log_error(f"systemctl restart hhd returned {r.returncode}: {r.stderr.strip()}")
            steps.append(f"HHD restart returned {r.returncode}")
            return {"success": True, "warning": f"Patched but HHD restart may have failed (exit {r.returncode})", "steps": steps}
    except Exception as e:
        steps.append("HHD restart failed")
        _log_error(f"HHD restart exception: {e}")
        return {"success": True, "warning": f"Patched but HHD restart failed: {e}", "steps": steps}

    return {"success": True, "message": "Button fix applied and HHD restarted", "steps": steps}


def _patch_const(const_file):
    """Patch const.py to add Apex device entry and button mappings."""
    with open(const_file) as f:
        content = f.read()

    # Remove partial Apex entries from previous attempts
    content = re.sub(r'    "ONEXPLAYER APEX".*?\n(?:.*?\n)*?    \},?\n', '', content)

    # Add Apex button mappings before ONEX_DEFAULT_CONF
    marker = 'ONEX_DEFAULT_CONF = {'
    if marker in content and 'APEX_BTN_MAPPINGS' not in content:
        content = content.replace(marker, APEX_MAPPINGS_BLOCK + '\n' + marker)

    # Add Apex device entry
    if 'ONEXPLAYER APEX' not in content:
        f1_marker = '"ONEXPLAYER F1 EVA-02": OXP_F1_CONF,'
        if f1_marker in content:
            content = content.replace(f1_marker, f1_marker + '\n' + APEX_DEVICE_ENTRY)
        else:
            oxp2_marker = '    # OXP 2'
            if oxp2_marker in content:
                content = content.replace(
                    oxp2_marker,
                    '    # Apex\n' + APEX_DEVICE_ENTRY + '\n' + oxp2_marker
                )

    with open(const_file, 'w') as f:
        f.write(content)
    _log_info("const.py patched")


def _patch_base(base_file):
    """Patch base.py to use Apex keyboard VID:PID and button mappings."""
    with open(base_file) as f:
        content = f.read()

    if 'APEX_BTN_MAPPINGS' in content:
        return

    original = content

    # Update import
    old_import = 'from .const import BTN_MAPPINGS, BTN_MAPPINGS_NONTURBO, DEFAULT_MAPPINGS'
    new_import = 'from .const import APEX_BTN_MAPPINGS, BTN_MAPPINGS, BTN_MAPPINGS_NONTURBO, DEFAULT_MAPPINGS'
    if old_import in content:
        content = content.replace(old_import, new_import)
        _log_info("Patched import line in base.py")
    else:
        _log_warning("Could not find expected import line in base.py — HHD version may differ")

    # Patch turbo_loop keyboard device
    old_turbo = '''    d_kbd_1 = OxpAtKbd(
        vid=[KBD_VID],
        pid=[KBD_PID],
        required=False,
        grab=True,
        btn_map=BTN_MAPPINGS,
    )

    share_reboots = False
    last_controller_check = 0'''

    new_turbo = '''    if dconf.get("apex_kbd", False):
        d_kbd_1 = OxpAtKbd(
            vid=[X1_MINI_VID],
            pid=[X1_MINI_PID],
            required=False,
            grab=True,
            btn_map=APEX_BTN_MAPPINGS,
        )
    else:
        d_kbd_1 = OxpAtKbd(
            vid=[KBD_VID],
            pid=[KBD_PID],
            required=False,
            grab=True,
            btn_map=BTN_MAPPINGS,
        )

    share_reboots = False
    last_controller_check = 0'''

    if old_turbo in content:
        content = content.replace(old_turbo, new_turbo)
        _log_info("Patched turbo_loop keyboard block")
    else:
        _log_warning("Could not find turbo_loop keyboard block in base.py — HHD version may differ")

    # Patch controller_loop keyboard device
    old_ctrl = '''    if turbo:
        # Switch buttons if turbo is enabled.
        # This only affects AOKZOE and OneXPlayer devices with
        # that button that have the nonturbo mapping as default
        mappings = BTN_MAPPINGS
    else:
        mappings = BTN_MAPPINGS_NONTURBO

    d_kbd_1 = OxpAtKbd(
        vid=[KBD_VID],
        pid=[KBD_PID],
        required=False,
        grab=True,
        btn_map=mappings,
    )'''

    new_ctrl = '''    if turbo:
        # Switch buttons if turbo is enabled.
        # This only affects AOKZOE and OneXPlayer devices with
        # that button that have the nonturbo mapping as default
        mappings = BTN_MAPPINGS
    else:
        mappings = BTN_MAPPINGS_NONTURBO

    if dconf.get("apex_kbd", False):
        d_kbd_1 = OxpAtKbd(
            vid=[X1_MINI_VID],
            pid=[X1_MINI_PID],
            required=False,
            grab=True,
            btn_map=APEX_BTN_MAPPINGS,
        )
    else:
        d_kbd_1 = OxpAtKbd(
            vid=[KBD_VID],
            pid=[KBD_PID],
            required=False,
            grab=True,
            btn_map=mappings,
        )'''

    if old_ctrl in content:
        content = content.replace(old_ctrl, new_ctrl)
        _log_info("Patched controller_loop keyboard block")
    else:
        _log_warning("Could not find controller_loop keyboard block in base.py — HHD version may differ")

    if content == original:
        raise RuntimeError("No patches could be applied to base.py — HHD version may be incompatible")

    with open(base_file, 'w') as f:
        f.write(content)
    _log_info("base.py patched")


def _patch_hid_v2(hid_v2_file):
    """Patch hid_v2.py to add Apex back paddle byte values to OXP_BUTTONS.

    The Apex back paddles appear as gamepad B/Y because their HID byte values
    aren't in OXP_BUTTONS. We add entries so HHD maps them to extra_l1/extra_r1
    (L4/R4) instead.
    """
    with open(hid_v2_file) as f:
        content = f.read()

    if "# Apex back paddles" in content:
        return  # Already patched

    # Find the OXP_BUTTONS dict and add Apex entries
    # The dict looks like:
    #   OXP_BUTTONS = {
    #       0x24: KBD_NAME,
    #       0x21: HOME_NAME,
    #       0x22: "extra_l1",
    #       0x23: "extra_r1",
    #   }
    # We insert Apex-specific entries before the closing brace.
    apex_entries = (
        f'    # Apex back paddles\n'
        f'    {hex(APEX_L4_BYTE)}: "extra_l1",\n'
        f'    {hex(APEX_R4_BYTE)}: "extra_r1",\n'
    )

    # Strategy: find the last entry in OXP_BUTTONS and append after it
    oxp_buttons_pattern = r'(OXP_BUTTONS\s*=\s*\{[^}]*)(})'
    match = re.search(oxp_buttons_pattern, content, re.DOTALL)
    if not match:
        raise RuntimeError("Could not find OXP_BUTTONS dict in hid_v2.py")

    # Check if our byte values are already present (avoid duplicates)
    existing_block = match.group(1)
    l4_hex = hex(APEX_L4_BYTE)
    r4_hex = hex(APEX_R4_BYTE)
    if l4_hex in existing_block and r4_hex in existing_block:
        _log_info("OXP_BUTTONS already contains Apex byte values — adding marker only")
        # Add a comment marker so is_applied() detects it
        content = content.replace(
            match.group(0),
            match.group(1) + "    # Apex back paddles\n" + match.group(2),
        )
    else:
        content = content.replace(
            match.group(0),
            match.group(1) + apex_entries + match.group(2),
        )

    with open(hid_v2_file, 'w') as f:
        f.write(content)
    _log_info("hid_v2.py patched — added Apex back paddle byte mappings")


if __name__ == "__main__":
    import sys
    import json as _json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    usage = "Usage: sudo python3 button_fix.py [status|apply|revert]"

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

    else:
        print(f"Unknown command: {cmd}")
        print(usage)
        sys.exit(1)
