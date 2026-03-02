"""OneXPlayer Apex Tools — Decky Loader plugin backend.

Exposes methods for button fix, home button monitor, and fan control
to the frontend via Decky's RPC bridge.

Each async method in the Plugin class becomes an RPC endpoint that
the React frontend can call via @decky/api's `callable()`.
"""

import asyncio
import os
import sys

# Official Decky module — injected by the loader at runtime.
# Provides DECKY_PLUGIN_DIR, DECKY_PLUGIN_LOG_DIR, and logger.
import decky

# Add py_modules to path so we can import our helper modules
sys.path.insert(0, os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules"))

# Import helper modules with error handling so a single broken module
# doesn't crash the entire plugin on load.
try:
    from fan_control import (
        FanCurveRunner,
        PROFILES,
        find_temp_sensor,
        get_controller,
    )
except Exception as e:
    decky.logger.error(f"Failed to import fan_control: {e}")
    FanCurveRunner = None
    PROFILES = {}
    find_temp_sensor = None
    get_controller = None

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
    from sleep_fix import apply as apply_sleep_fix_impl, get_status as sleep_fix_status
except Exception as e:
    decky.logger.error(f"Failed to import sleep_fix: {e}")
    apply_sleep_fix_impl = None
    sleep_fix_status = None

try:
    import home_button as _home_button_mod
    from home_button import HomeButtonMonitor
except Exception as e:
    decky.logger.error(f"Failed to import home_button: {e}")
    _home_button_mod = None
    HomeButtonMonitor = None

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


class Plugin:
    # Fan controller instance (HwmonFanController, ECFanController, or PortIOFanController)
    fan_ctrl = None
    # Active fan curve runner (async task that adjusts fan speed based on temp)
    fan_curve_runner = None
    # Current fan mode: "auto" (EC controls fan) or "manual" (we control fan)
    fan_mode = "auto"
    # Active fan profile name: "silent", "balanced", "performance", or "custom"
    fan_profile = "custom"
    # Manual slider value (0-100%) — only used when profile is "custom"
    fan_speed = 50
    # Home button HID monitor instance
    home_monitor = None

    async def _main(self):
        """Plugin entry point — called by Decky on load."""
        _log_info("OneXPlayer Apex Tools starting")
        _log_info(f"Plugin dir: {decky.DECKY_PLUGIN_DIR}")
        _log_info(f"Log dir: {decky.DECKY_PLUGIN_LOG_DIR}")

        # Init fan controller (best-effort — may fail if no backend)
        if get_controller:
            try:
                self.fan_ctrl = get_controller()
            except RuntimeError as e:
                _log_error(f"Fan control init failed: {e}")
                self.fan_ctrl = None
        else:
            _log_warning("fan_control module not available")

        # Safety: always restore fan to auto on plugin startup.
        # If the system crashed while fan was in manual mode (e.g. 0%),
        # the EC stays in that state across reboots. This prevents
        # thermal shutdowns from a stuck-off fan.
        if self.fan_ctrl:
            try:
                self.fan_ctrl.set_auto()
                _log_info("Fan restored to auto mode on startup")
            except Exception as e:
                _log_warning(f"Failed to restore fan auto mode: {e}")

        # Create home monitor instance (started automatically with button fix)
        if HomeButtonMonitor:
            self.home_monitor = HomeButtonMonitor()
        else:
            _log_warning("home_button module not available")

        # Auto-start home monitor if button fix is already applied
        if button_fix_status:
            status = button_fix_status()
            if status.get("applied"):
                _log_info("Button fix already applied — auto-starting home monitor")
                self._start_home_monitor()

    async def _unload(self):
        """Plugin teardown — called by Decky on unload."""
        _log_info("OneXPlayer Apex Tools unloading")
        # Stop monitors if active
        if self.home_monitor:
            await self.home_monitor.stop()
        # Stop any running fan curve task
        if self.fan_curve_runner:
            await self.fan_curve_runner.stop()
        # Restore fan to auto so it doesn't stay stuck in manual after unload
        if self.fan_ctrl:
            try:
                self.fan_ctrl.set_auto()
            except Exception:
                pass

    # -- Status overview --

    async def get_status(self):
        """Get combined status of all features — called by the frontend on load."""
        fan_status = await self.get_fan_status()
        bf_status = button_fix_status() if button_fix_status else {"applied": False, "error": "module not loaded"}
        bf_status["home_monitor_running"] = self.home_monitor.is_running if self.home_monitor else False
        if bf_status.get("applied") and get_intercept_mode_impl:
            bf_status["intercept_enabled"] = get_intercept_mode_impl().get("enabled", True)
        return {
            "button_fix": bf_status,
            "sleep_fix": sleep_fix_status() if sleep_fix_status else {"applied": False},
            "fan": fan_status,
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
            # Decky runs as root, so ~ would resolve to /root.
            # Find the real user's home directory instead.
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
    # Toggles between full controller intercept (back paddles + everything)
    # and face-buttons-only mode (just Home + QAM, Xbox gamepad normal).

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

    # -- Sleep Fix --
    # Adds amd_iommu=off kernel param via rpm-ostree to fix suspend/resume.
    # Creates a new ostree deployment — button fix patches need re-applying after reboot.

    async def get_sleep_fix_status(self):
        if not sleep_fix_status:
            return {"applied": False}
        return sleep_fix_status()

    async def apply_sleep_fix(self):
        if not apply_sleep_fix_impl:
            return {"success": False, "error": "sleep_fix module not loaded"}
        _log_info("Applying sleep fix (amd_iommu=off)...")
        try:
            result = await asyncio.to_thread(apply_sleep_fix_impl)
            if result.get("success"):
                _log_info(f"Sleep fix result: {result.get('message', 'OK')}")
            else:
                _log_error(f"Sleep fix failed: {result.get('error', 'unknown')}")
            return result
        except Exception as e:
            _log_error(f"Sleep fix exception: {e}")
            return {"success": False, "error": str(e)}

    # -- Home Button Monitor (private — managed by button fix lifecycle) --

    def _start_home_monitor(self):
        """Start the home button monitor (called after button fix apply)."""
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
        """Stop the home button monitor (called before button fix revert)."""
        if self.home_monitor and self.home_monitor.is_running:
            await self.home_monitor.stop()
            _log_info("Home button monitor stopped")

    # -- Fan Control --
    # Three modes of operation:
    #   1. Auto — EC firmware controls the fan (default, safest)
    #   2. Manual + custom — user sets a fixed fan speed via slider
    #   3. Manual + profile — FanCurveRunner adjusts speed based on temp curve

    async def get_fan_status(self):
        """Read current fan state from hardware and return to frontend."""
        if not self.fan_ctrl:
            return {"available": False, "error": "No fan control backend"}
        try:
            rpm = self.fan_ctrl.get_rpm()
            percent = self.fan_ctrl.get_percent()
            mode = self.fan_ctrl.get_mode()
            # Read CPU temp from the best available sensor
            temp_path = find_temp_sensor() if find_temp_sensor else None
            temp = None
            if temp_path:
                with open(temp_path) as f:
                    temp = int(f.read().strip()) / 1000  # millidegrees to degrees
            return {
                "available": True,
                "rpm": rpm,
                "percent": round(percent, 1),
                "hw_mode": mode,         # actual EC mode (auto/manual)
                "temp": round(temp, 1) if temp is not None else None,
                "mode": self.fan_mode,   # our tracked mode
                "profile": self.fan_profile,
                "speed": self.fan_speed,
                "backend": self.fan_ctrl.backend_name,
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    async def set_fan_mode(self, mode):
        """Set fan mode: 'auto' or 'manual'."""
        if not self.fan_ctrl:
            return {"success": False, "error": "No fan control backend"}
        self.fan_mode = mode
        if mode == "auto":
            # Stop any running fan curve and hand control back to the EC
            if self.fan_curve_runner:
                await self.fan_curve_runner.stop()
                self.fan_curve_runner = None
            self.fan_ctrl.set_auto()
            return {"success": True, "mode": "auto"}
        else:
            # Switch to manual and apply the current slider speed
            self.fan_ctrl.set_manual(self.fan_speed)
            return {"success": True, "mode": "manual"}

    async def set_fan_speed(self, percent):
        """Set manual fan speed (0-100). Stops any active curve."""
        if not self.fan_ctrl:
            return {"success": False, "error": "No fan control backend"}
        self.fan_speed = max(0, min(100, int(percent)))
        self.fan_profile = "custom"  # explicit speed overrides any profile
        # Stop fan curve if one is running — user wants direct control
        if self.fan_curve_runner:
            await self.fan_curve_runner.stop()
            self.fan_curve_runner = None
        if self.fan_mode == "manual":
            self.fan_ctrl.set_manual(self.fan_speed)
        return {"success": True, "speed": self.fan_speed}

    async def set_fan_profile(self, name):
        """Set fan profile: 'silent', 'balanced', 'performance', 'custom'.

        Profiles other than 'custom' start a FanCurveRunner that periodically
        reads the CPU temp and adjusts fan speed according to the curve.
        """
        if not self.fan_ctrl:
            return {"success": False, "error": "No fan control backend"}
        self.fan_profile = name

        # Always stop the existing curve before switching
        if self.fan_curve_runner:
            await self.fan_curve_runner.stop()
            self.fan_curve_runner = None

        if name == "custom":
            # Custom = direct slider control, no curve
            if self.fan_mode == "manual":
                self.fan_ctrl.set_manual(self.fan_speed)
            return {"success": True, "profile": "custom"}

        # Look up the predefined curve for this profile
        curve = PROFILES.get(name)
        if not curve:
            return {"success": False, "error": f"Unknown profile: {name}"}

        temp_sensor = find_temp_sensor() if find_temp_sensor else None
        if not temp_sensor:
            return {"success": False, "error": "No temperature sensor found"}

        # Start the curve runner — it will adjust fan speed every 2 seconds
        self.fan_mode = "manual"
        self.fan_curve_runner = FanCurveRunner(
            self.fan_ctrl, temp_sensor, curve, interval=2.0
        )
        loop = asyncio.get_event_loop()
        self.fan_curve_runner.start(loop)
        return {"success": True, "profile": name}
