"""OneXPlayer Apex Tools — Decky Loader plugin backend.

Exposes methods for button fix, home button monitor, EC sensor driver,
resume recovery, sleep enablement, and speaker DSP to the frontend
via Decky's RPC bridge.

Each async method in the Plugin class becomes an RPC endpoint that
the React frontend can call via @decky/api's `callable()`.
"""

import asyncio
import os
import subprocess
import sys

# Official Decky module — injected by the loader at runtime.
# Provides DECKY_PLUGIN_DIR, DECKY_PLUGIN_LOG_DIR, and logger.
import decky

# Add py_modules to path so we can import our helper modules
sys.path.insert(0, os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules"))

# Import helper modules with error handling so a single broken module
# doesn't crash the entire plugin on load.

try:
    import button_fix as _button_fix_mod
    from button_fix import (
        apply as apply_button_fix_impl,
        revert as revert_button_fix_impl,
        is_applied as button_fix_status,
        get_intercept_mode as get_intercept_mode_impl,
        set_intercept_mode as set_intercept_mode_impl,
    )
except Exception as e:
    decky.logger.error(f"Failed to import button_fix: {e}")
    _button_fix_mod = None
    apply_button_fix_impl = None
    revert_button_fix_impl = None
    button_fix_status = None
    get_intercept_mode_impl = None
    set_intercept_mode_impl = None

try:
    import sleep_fix as _sleep_fix_mod
    from sleep_fix import (
        get_status as sleep_fix_status,
        apply as apply_light_sleep_impl,
        revert as revert_light_sleep_impl,
        remove as remove_sleep_fix_impl,
    )
except Exception as e:
    decky.logger.error(f"Failed to import sleep_fix: {e}")
    _sleep_fix_mod = None
    sleep_fix_status = None
    apply_light_sleep_impl = None
    revert_light_sleep_impl = None
    remove_sleep_fix_impl = None

try:
    import speaker_dsp as _speaker_dsp_mod
    from speaker_dsp import (
        enable as enable_speaker_dsp_impl,
        disable as disable_speaker_dsp_impl,
        set_profile as set_dsp_profile_impl,
        get_status as speaker_dsp_status,
        list_profiles as list_dsp_profiles_impl,
        get_preset_bands as get_preset_bands_impl,
        get_custom_profiles as get_custom_profiles_impl,
        save_custom_profile as save_custom_profile_impl,
        delete_custom_profile as delete_custom_profile_impl,
        play_test_sound as play_test_sound_impl,
        stop_test_sound as stop_test_sound_impl,
        bypass as bypass_speaker_dsp_impl,
        unbypass as unbypass_speaker_dsp_impl,
        is_bypassed as is_bypassed_speaker_dsp_impl,
    )
except Exception as e:
    decky.logger.error(f"Failed to import speaker_dsp: {e}")
    _speaker_dsp_mod = None
    enable_speaker_dsp_impl = None
    disable_speaker_dsp_impl = None
    set_dsp_profile_impl = None
    speaker_dsp_status = None
    list_dsp_profiles_impl = None
    get_preset_bands_impl = None
    get_custom_profiles_impl = None
    save_custom_profile_impl = None
    delete_custom_profile_impl = None
    play_test_sound_impl = None
    stop_test_sound_impl = None
    bypass_speaker_dsp_impl = None
    unbypass_speaker_dsp_impl = None
    is_bypassed_speaker_dsp_impl = None

try:
    import home_button as _home_button_mod
    from home_button import HomeButtonMonitor
except Exception as e:
    decky.logger.error(f"Failed to import home_button: {e}")
    _home_button_mod = None
    HomeButtonMonitor = None

try:
    import oxpec_loader as _oxpec_mod
    from oxpec_loader import (
        apply as apply_oxpec_impl,
        revert as revert_oxpec_impl,
        is_applied as oxpec_status,
        ensure_loaded as ensure_oxpec_loaded,
    )
except Exception as e:
    decky.logger.error(f"Failed to import oxpec_loader: {e}")
    _oxpec_mod = None
    apply_oxpec_impl = None
    revert_oxpec_impl = None
    oxpec_status = None
    ensure_oxpec_loaded = None

try:
    import resume_fix as _resume_fix_mod
    from resume_fix import (
        apply as apply_resume_fix_impl,
        revert as revert_resume_fix_impl,
        is_applied as resume_fix_status,
    )
except Exception as e:
    decky.logger.error(f"Failed to import resume_fix: {e}")
    _resume_fix_mod = None
    apply_resume_fix_impl = None
    revert_resume_fix_impl = None
    resume_fix_status = None

try:
    import sleep_enable as _sleep_enable_mod
    from sleep_enable import (
        apply as apply_sleep_enable_impl,
        revert as revert_sleep_enable_impl,
        is_applied as sleep_enable_status,
    )
except Exception as e:
    decky.logger.error(f"Failed to import sleep_enable: {e}")
    _sleep_enable_mod = None
    apply_sleep_enable_impl = None
    revert_sleep_enable_impl = None
    sleep_enable_status = None

# back_paddle.py is no longer used as a separate monitor — the button fix
# patches HHD's hid_v2.py with full v1 intercept mode (apex_v1=True).
# OxpHidrawV2 handles ALL gamepad input: sticks, triggers, buttons, and
# back paddles (L4/R4) natively through HHD's virtual Steam Controller.

def _get_user_home():
    """Get the real (non-root) user's home directory.

    Decky runs as root, so os.path.expanduser("~") returns /root.
    We find the actual user by checking SUDO_USER, the plugin dir path,
    or falling back to the first user in /home.
    """
    # Check SUDO_USER first
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        home = f"/home/{sudo_user}"
        if os.path.isdir(home):
            return home

    # Infer from Decky plugin dir path (e.g. /home/srsholmes/homebrew/plugins/...)
    plugin_dir = decky.DECKY_PLUGIN_DIR
    if plugin_dir.startswith("/home/"):
        parts = plugin_dir.split("/")
        if len(parts) >= 3:
            home = f"/home/{parts[2]}"
            if os.path.isdir(home):
                return home

    # Fallback: first non-root user in /home
    try:
        for name in sorted(os.listdir("/home")):
            path = f"/home/{name}"
            if os.path.isdir(path) and name != "root":
                return path
    except OSError:
        pass

    return os.path.expanduser("~")


# Log file path — write to Decky's plugin log directory
LOG_FILE = os.path.join(decky.DECKY_PLUGIN_LOG_DIR, "oxp-apex.log")


def _log_to_file(msg: str):
    """Append a message to our log file (in addition to decky.logger)."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            from datetime import datetime
            f.write(f"{datetime.now().isoformat()} [OXP-Apex] {msg}\n")
    except Exception:
        pass


def _log_info(msg: str):
    decky.logger.info(msg)
    _log_to_file(msg)


def _log_error(msg: str):
    decky.logger.error(msg)
    _log_to_file(f"ERROR: {msg}")


def _log_warning(msg: str):
    decky.logger.warning(msg)
    _log_to_file(f"WARN: {msg}")


# Wire log callbacks into helper modules so their logs appear in oxp-apex.log
if _button_fix_mod:
    _button_fix_mod.set_log_callbacks(_log_info, _log_error, _log_warning)
if _home_button_mod:
    _home_button_mod.set_log_callbacks(_log_info, _log_error, _log_warning)
if _speaker_dsp_mod:
    _speaker_dsp_mod.set_log_callbacks(_log_info, _log_error, _log_warning)
if _oxpec_mod:
    _oxpec_mod.set_log_callbacks(_log_info, _log_error, _log_warning)
if _resume_fix_mod:
    _resume_fix_mod.set_log_callbacks(_log_info, _log_error, _log_warning)
if _sleep_fix_mod:
    _sleep_fix_mod.set_log_callbacks(_log_info, _log_error, _log_warning)
if _sleep_enable_mod:
    _sleep_enable_mod.set_log_callbacks(_log_info, _log_error, _log_warning)


def _clean_env():
    """Strip Decky's LD_LIBRARY_PATH/LD_PRELOAD for subprocess calls."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


def _restart_hhd():
    """Restart HHD so it re-detects hardware (e.g. after loading oxpec)."""
    _log_info("Restarting HHD to pick up new hardware...")
    try:
        r = subprocess.run(
            ["systemctl", "list-units", "--plain", "--no-legend", "--type=service", "hhd*"],
            capture_output=True, text=True, timeout=10, env=_clean_env()
        )
        units = []
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if parts:
                units.append(parts[0])
        if not units:
            units = ["hhd"]
        for unit in units:
            r = subprocess.run(
                ["systemctl", "restart", unit],
                capture_output=True, text=True, timeout=30, env=_clean_env()
            )
            if r.returncode == 0:
                _log_info(f"Restarted {unit}")
            else:
                _log_error(f"Failed to restart {unit}: {r.stderr.strip()}")
    except Exception as e:
        _log_error(f"HHD restart failed: {e}")


class Plugin:
    # Home button HID monitor instance
    home_monitor = None

    async def _main(self):
        """Plugin entry point — called by Decky on load."""
        try:
            from build_info import BUILD_ID
        except ImportError:
            BUILD_ID = "unknown"
        _log_info(f"OneXPlayer Apex Tools starting ({BUILD_ID})")
        _log_info(f"Plugin dir: {decky.DECKY_PLUGIN_DIR}")
        _log_info(f"Log dir: {decky.DECKY_PLUGIN_LOG_DIR}")

        # Create home monitor instance (started automatically with button fix)
        if HomeButtonMonitor:
            self.home_monitor = HomeButtonMonitor()
        else:
            _log_warning("home_button module not available")

        # Auto-load oxpec driver if not already loaded (survives reboots
        # even when hotfix overlay is lost since plugin runs on every boot)
        if ensure_oxpec_loaded:
            try:
                result = ensure_oxpec_loaded()
                if result.get("success") and result.get("loaded"):
                    _log_info("oxpec auto-loaded — restarting HHD for fan control")
                    _restart_hhd()
                elif result.get("already_loaded"):
                    _log_info("oxpec already loaded")
            except Exception as e:
                _log_error(f"oxpec auto-load failed: {e}")

        # Auto-start home monitor if button fix is already applied
        if button_fix_status:
            status = button_fix_status()
            if status.get("applied"):
                _log_info("Button fix already applied — auto-starting home monitor")
                self._start_home_monitor()

    async def _unload(self):
        """Plugin teardown — called by Decky on unload."""
        _log_info("OneXPlayer Apex Tools unloading")
        # Stop test sound if playing
        if stop_test_sound_impl:
            try:
                stop_test_sound_impl()
            except Exception:
                pass
        # Stop monitors if active
        if self.home_monitor:
            await self.home_monitor.stop()

    # -- Status overview --

    async def get_status(self):
        """Get combined status of all features — called by the frontend on load."""
        bf_status = button_fix_status() if button_fix_status else {"applied": False, "error": "module not loaded"}
        bf_status["home_monitor_running"] = self.home_monitor.is_running if self.home_monitor else False
        if bf_status.get("applied") and get_intercept_mode_impl:
            bf_status["intercept_enabled"] = get_intercept_mode_impl().get("enabled", True)
        return {
            "button_fix": bf_status,
            "light_sleep": sleep_fix_status() if sleep_fix_status else {"applied": False, "has_problematic_kargs": False, "problematic_kargs": [], "light_sleep_present": [], "light_sleep_missing": []},
            "speaker_dsp": speaker_dsp_status() if speaker_dsp_status else {"enabled": False, "profile": None, "speaker_node": None},
            "oxpec": oxpec_status() if oxpec_status else {"applied": False, "error": "module not loaded"},
            "resume_fix": resume_fix_status() if resume_fix_status else {"applied": False, "error": "module not loaded"},
            "sleep_enable": sleep_enable_status() if sleep_enable_status else {"applied": False, "error": "module not loaded"},
        }

    # -- Logs --

    async def get_logs(self, lines=20):
        """Return the last N lines from the log file."""
        try:
            with open(LOG_FILE) as f:
                all_lines = f.readlines()
            tail = [l.rstrip("\n") for l in all_lines[-lines:]]
            return {"lines": tail, "log_file": LOG_FILE}
        except Exception as e:
            return {"lines": [], "log_file": LOG_FILE, "error": str(e)}

    async def save_logs(self):
        """Copy the log file to the user's ~/Downloads/ with a timestamp."""
        import shutil
        from datetime import datetime
        try:
            user_home = _get_user_home()
            downloads = os.path.join(user_home, "Downloads")
            os.makedirs(downloads, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = os.path.join(downloads, f"oxp-apex-logs_{ts}.log")
            shutil.copy2(LOG_FILE, dest)
            _log_info(f"Logs saved to {dest}")
            return {"success": True, "path": dest}
        except Exception as e:
            _log_error(f"Failed to save logs: {e}")
            return {"success": False, "error": str(e)}

    # -- Button Fix --
    # Patches HHD (Handheld Daemon) to recognize Apex face buttons.
    # Requires ostree filesystem unlock since Bazzite is immutable.

    async def get_button_fix_status(self):
        if not button_fix_status:
            return {"applied": False, "error": "module not loaded"}
        return button_fix_status()

    async def apply_button_fix(self):
        if not apply_button_fix_impl:
            return {"success": False, "error": "button_fix module not loaded"}
        _log_info("Applying button fix...")
        try:
            result = await asyncio.to_thread(apply_button_fix_impl)
            if result.get("success"):
                _log_info(f"Button fix applied: {result.get('message', 'OK')}")
                self._start_home_monitor()
            else:
                _log_error(f"Button fix failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Button fix exception: {e}")
            return {"success": False, "error": str(e)}

    async def revert_button_fix(self):
        if not revert_button_fix_impl:
            return {"success": False, "error": "button_fix module not loaded"}
        _log_info("Reverting button fix...")
        try:
            await self._stop_home_monitor()
            result = await asyncio.to_thread(revert_button_fix_impl)
            if result.get("success"):
                _log_info(f"Button fix reverted: {result.get('message', 'OK')}")
            else:
                _log_error(f"Button fix revert failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Button fix revert exception: {e}")
            return {"success": False, "error": str(e)}

    # -- Intercept Mode --

    async def get_intercept_mode(self):
        if not get_intercept_mode_impl:
            return {"enabled": True, "error": "module not loaded"}
        return get_intercept_mode_impl()

    async def set_intercept_mode(self, enabled):
        if not set_intercept_mode_impl:
            return {"success": False, "error": "button_fix module not loaded"}
        _log_info(f"Setting intercept mode: {'full' if enabled else 'face buttons only'}")
        try:
            result = await asyncio.to_thread(set_intercept_mode_impl, enabled)
            if result.get("success"):
                _log_info(f"Intercept mode set: {result.get('message', 'OK')}")
            else:
                _log_error(f"Intercept mode failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Intercept mode exception: {e}")
            return {"success": False, "error": str(e)}

    # -- Light Sleep (s2idle kargs) --

    async def get_light_sleep_status(self):
        if not sleep_fix_status:
            return {"applied": False, "has_problematic_kargs": False, "problematic_kargs": [], "light_sleep_present": [], "light_sleep_missing": []}
        return sleep_fix_status()

    async def apply_light_sleep(self):
        if not apply_light_sleep_impl:
            return {"success": False, "error": "sleep_fix module not loaded"}
        _log_info("Applying light sleep kargs...")
        try:
            result = await asyncio.to_thread(apply_light_sleep_impl)
            if result.get("success"):
                _log_info(f"Light sleep applied: {result.get('message', 'OK')}")
            else:
                _log_error(f"Light sleep failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Light sleep exception: {e}")
            return {"success": False, "error": str(e)}

    async def revert_light_sleep(self):
        if not revert_light_sleep_impl:
            return {"success": False, "error": "sleep_fix module not loaded"}
        _log_info("Reverting light sleep kargs...")
        try:
            result = await asyncio.to_thread(revert_light_sleep_impl)
            if result.get("success"):
                _log_info(f"Light sleep reverted: {result.get('message', 'OK')}")
            else:
                _log_error(f"Light sleep revert failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Light sleep revert exception: {e}")
            return {"success": False, "error": str(e)}

    # Legacy compat — old frontend called remove_sleep_fix
    async def remove_sleep_fix(self):
        if not remove_sleep_fix_impl:
            return {"success": False, "error": "sleep_fix module not loaded"}
        try:
            return await asyncio.to_thread(remove_sleep_fix_impl)
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -- Speaker DSP --

    async def get_speaker_dsp_status(self):
        if not speaker_dsp_status:
            return {"enabled": False, "profile": None, "speaker_node": None}
        return speaker_dsp_status()

    async def enable_speaker_dsp(self, profile="balanced"):
        if not enable_speaker_dsp_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        _log_info(f"Enabling speaker DSP ({profile})...")
        try:
            result = await asyncio.to_thread(enable_speaker_dsp_impl, profile)
            if result.get("success"):
                _log_info(f"Speaker DSP enabled: {result.get('message', 'OK')}")
            else:
                _log_error(f"Speaker DSP enable failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Speaker DSP enable exception: {e}")
            return {"success": False, "error": str(e)}

    async def disable_speaker_dsp(self):
        if not disable_speaker_dsp_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        _log_info("Disabling speaker DSP...")
        try:
            result = await asyncio.to_thread(disable_speaker_dsp_impl)
            if result.get("success"):
                _log_info(f"Speaker DSP disabled: {result.get('message', 'OK')}")
            else:
                _log_error(f"Speaker DSP disable failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Speaker DSP disable exception: {e}")
            return {"success": False, "error": str(e)}

    async def set_dsp_profile(self, profile):
        if not set_dsp_profile_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        _log_info(f"Switching speaker DSP profile to {profile}...")
        try:
            result = await asyncio.to_thread(set_dsp_profile_impl, profile)
            if result.get("success"):
                _log_info(f"Speaker DSP profile set: {result.get('message', 'OK')}")
            else:
                _log_error(f"Speaker DSP profile switch failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Speaker DSP profile exception: {e}")
            return {"success": False, "error": str(e)}

    async def list_dsp_profiles(self):
        if not list_dsp_profiles_impl:
            return {}
        return list_dsp_profiles_impl()

    async def get_preset_bands(self, profile_name):
        if not get_preset_bands_impl:
            return {"error": "speaker_dsp module not loaded"}
        return get_preset_bands_impl(profile_name)

    async def get_custom_profiles(self):
        if not get_custom_profiles_impl:
            return {"profiles": {}}
        return get_custom_profiles_impl()

    async def save_custom_profile(self, name, gains):
        if not save_custom_profile_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        _log_info(f"Saving custom EQ profile: {name}")
        try:
            result = await asyncio.to_thread(save_custom_profile_impl, name, gains)
            return result
        except Exception as e:
            _log_error(f"Save custom profile exception: {e}")
            return {"success": False, "error": str(e)}

    async def delete_custom_profile(self, name):
        if not delete_custom_profile_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        _log_info(f"Deleting custom EQ profile: {name}")
        try:
            result = await asyncio.to_thread(delete_custom_profile_impl, name)
            return result
        except Exception as e:
            _log_error(f"Delete custom profile exception: {e}")
            return {"success": False, "error": str(e)}

    async def play_test_sound(self):
        if not play_test_sound_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        try:
            return await asyncio.to_thread(play_test_sound_impl)
        except Exception as e:
            _log_error(f"Play test sound exception: {e}")
            return {"success": False, "error": str(e)}

    async def stop_test_sound(self):
        if not stop_test_sound_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        try:
            return await asyncio.to_thread(stop_test_sound_impl)
        except Exception as e:
            _log_error(f"Stop test sound exception: {e}")
            return {"success": False, "error": str(e)}

    async def bypass_speaker_dsp(self):
        if not bypass_speaker_dsp_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        try:
            return await asyncio.to_thread(bypass_speaker_dsp_impl)
        except Exception as e:
            _log_error(f"Bypass speaker DSP exception: {e}")
            return {"success": False, "error": str(e)}

    async def unbypass_speaker_dsp(self):
        if not unbypass_speaker_dsp_impl:
            return {"success": False, "error": "speaker_dsp module not loaded"}
        try:
            return await asyncio.to_thread(unbypass_speaker_dsp_impl)
        except Exception as e:
            _log_error(f"Unbypass speaker DSP exception: {e}")
            return {"success": False, "error": str(e)}

    async def is_bypassed_speaker_dsp(self):
        if not is_bypassed_speaker_dsp_impl:
            return {"bypassed": False, "error": "speaker_dsp module not loaded"}
        try:
            return await asyncio.to_thread(is_bypassed_speaker_dsp_impl)
        except Exception as e:
            _log_error(f"Is bypassed speaker DSP exception: {e}")
            return {"bypassed": False, "error": str(e)}

    # -- Home Button Monitor (private — managed by button fix lifecycle) --

    def _start_home_monitor(self):
        if not self.home_monitor:
            if HomeButtonMonitor:
                self.home_monitor = HomeButtonMonitor()
            else:
                _log_warning("Cannot start home monitor — module not loaded")
                return
        if not self.home_monitor.is_running:
            loop = asyncio.get_event_loop()
            self.home_monitor.start(loop)
            _log_info("Home button monitor started")

    async def _stop_home_monitor(self):
        if self.home_monitor and self.home_monitor.is_running:
            await self.home_monitor.stop()
            _log_info("Home button monitor stopped")

    # -- oxpec EC Sensor Driver --

    async def get_oxpec_status(self):
        if not oxpec_status:
            return {"applied": False, "error": "module not loaded"}
        return oxpec_status()

    async def apply_oxpec(self):
        if not apply_oxpec_impl:
            return {"success": False, "error": "oxpec_loader module not loaded"}
        _log_info("Installing oxpec driver...")
        try:
            result = await asyncio.to_thread(apply_oxpec_impl)
            if result.get("success"):
                _log_info(f"oxpec applied: {result.get('message', 'OK')}")
                # Restart HHD so it detects the new hwmon for fan control
                await asyncio.to_thread(_restart_hhd)
            else:
                _log_error(f"oxpec failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"oxpec exception: {e}")
            return {"success": False, "error": str(e)}

    async def revert_oxpec(self):
        if not revert_oxpec_impl:
            return {"success": False, "error": "oxpec_loader module not loaded"}
        _log_info("Removing oxpec driver...")
        try:
            result = await asyncio.to_thread(revert_oxpec_impl)
            if result.get("success"):
                _log_info(f"oxpec reverted: {result.get('message', 'OK')}")
            else:
                _log_error(f"oxpec revert failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"oxpec revert exception: {e}")
            return {"success": False, "error": str(e)}

    # -- Resume Recovery --

    async def get_resume_fix_status(self):
        if not resume_fix_status:
            return {"applied": False, "error": "module not loaded"}
        return resume_fix_status()

    async def apply_resume_fix(self):
        if not apply_resume_fix_impl:
            return {"success": False, "error": "resume_fix module not loaded"}
        _log_info("Installing resume recovery fix...")
        try:
            result = await asyncio.to_thread(apply_resume_fix_impl)
            if result.get("success"):
                _log_info(f"Resume fix applied: {result.get('message', 'OK')}")
            else:
                _log_error(f"Resume fix failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Resume fix exception: {e}")
            return {"success": False, "error": str(e)}

    async def revert_resume_fix(self):
        if not revert_resume_fix_impl:
            return {"success": False, "error": "resume_fix module not loaded"}
        _log_info("Removing resume recovery fix...")
        try:
            result = await asyncio.to_thread(revert_resume_fix_impl)
            if result.get("success"):
                _log_info(f"Resume fix reverted: {result.get('message', 'OK')}")
            else:
                _log_error(f"Resume fix revert failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Resume fix revert exception: {e}")
            return {"success": False, "error": str(e)}

    # -- Sleep Enable --

    async def get_sleep_enable_status(self):
        if not sleep_enable_status:
            return {"applied": False, "error": "module not loaded"}
        return sleep_enable_status()

    async def apply_sleep_enable(self):
        if not apply_sleep_enable_impl:
            return {"success": False, "error": "sleep_enable module not loaded"}
        _log_info("Applying sleep enablement fix...")
        try:
            result = await asyncio.to_thread(apply_sleep_enable_impl)
            if result.get("success"):
                _log_info(f"Sleep enable applied: {result.get('message', 'OK')}")
            else:
                _log_error(f"Sleep enable failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Sleep enable exception: {e}")
            return {"success": False, "error": str(e)}

    async def revert_sleep_enable(self):
        if not revert_sleep_enable_impl:
            return {"success": False, "error": "sleep_enable module not loaded"}
        _log_info("Reverting sleep enablement fix...")
        try:
            result = await asyncio.to_thread(revert_sleep_enable_impl)
            if result.get("success"):
                _log_info(f"Sleep enable reverted: {result.get('message', 'OK')}")
            else:
                _log_error(f"Sleep enable revert failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Sleep enable revert exception: {e}")
            return {"success": False, "error": str(e)}
