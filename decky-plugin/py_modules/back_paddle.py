"""Back paddle monitor for OneXPlayer Apex.

The Apex has L4/R4 back paddles connected through a vendor HID device
(VID:PID 1a86:fe00). By default, the firmware mirrors these as B/Y on
the Xbox gamepad. Sending an HID v1 intercept command enables separate
button reports for L4 (0x22) and R4 (0x23).

This module:
1. Finds the correct hidraw interface (64-byte vendor, usage page 0xFF00)
2. Sends the intercept-enable command
3. Reads 64-byte button reports in a loop
4. Emits BTN_TRIGGER_HAPPY1 (L4) and BTN_TRIGGER_HAPPY2 (R4) via uinput

Steam Input recognizes BTN_TRIGGER_HAPPY1/2 as back paddle buttons
(like the Steam Deck's L4/R4) and lets users remap them per-game.

HID v1 protocol:
  gen_cmd_v1(cid, cmd) → 64-byte packet
  INTERCEPT_ON  = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
  INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])

Button report (64 bytes, byte[0]==0xB2):
  byte[3] == 0x01 → button event (vs 0x03=ack, 0x02=gamepad state)
  byte[6] → button code: 0x22=L4, 0x23=R4
  byte[12] → state: 0x01=pressed, 0x02=released
"""

import asyncio
import glob
import logging
import os

logger = logging.getLogger("OXP-BackPaddle")

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


# USB VID:PID for the Apex's vendor HID device
TARGET_VID = 0x1A86
TARGET_PID = 0xFE00

# Button codes in the intercept reports
BTN_L4 = 0x22
BTN_R4 = 0x23

# Button states
STATE_PRESSED = 0x01
STATE_RELEASED = 0x02


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    """Generate an HID v1 command packet."""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])


def find_vendor_hidraw():
    """Find the 64-byte vendor HID interface for the Apex (1a86:fe00).

    The device exposes multiple hidraw interfaces (keyboard, mouse, vendor).
    We need the one with usage page 0xFF00 — its report descriptor starts
    with bytes 0x06 0x00 0xFF.
    """
    candidates = []
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        for line in content.splitlines():
            if not line.startswith("HID_ID="):
                continue
            parts = line.split(":")
            if len(parts) < 3:
                continue
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
            if vid == TARGET_VID and pid == TARGET_PID:
                name = os.path.basename(sysfs_path)
                candidates.append((name, sysfs_path))
                break

    # Filter to the vendor-defined interface (usage page 0xFF00)
    for name, sysfs_path in candidates:
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        if not os.path.exists(rd_path):
            continue
        try:
            with open(rd_path, "rb") as f:
                rd = f.read(3)
            # Usage page 0xFF00 starts with: 0x06 0x00 0xFF
            if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                dev_path = f"/dev/{name}"
                if os.path.exists(dev_path):
                    return dev_path
        except OSError:
            continue

    return None


# Try to import evdev for uinput virtual device creation
try:
    import evdev
    from evdev import UInput, ecodes
    _HAS_EVDEV = True
except ImportError:
    _HAS_EVDEV = False


def _create_uinput_device():
    """Create a virtual gamepad with BTN_TRIGGER_HAPPY1 and BTN_TRIGGER_HAPPY2."""
    if not _HAS_EVDEV:
        return None

    capabilities = {
        ecodes.EV_KEY: [
            ecodes.BTN_TRIGGER_HAPPY1,
            ecodes.BTN_TRIGGER_HAPPY2,
        ],
    }
    device = UInput(
        capabilities,
        name="OXP Apex Back Paddles",
        bustype=ecodes.BUS_VIRTUAL,
        vendor=0x1A86,
        product=0xFE01,  # Distinct from the real device
    )
    return device


class BackPaddleMonitor:
    """Async monitor that intercepts back paddle HID reports and emits uinput events."""

    def __init__(self):
        self._task = None
        self._running = False

    @property
    def is_running(self):
        return self._task is not None and not self._task.done()

    async def _monitor_loop(self):
        """Main monitoring loop — enables intercept and reads paddle reports."""
        self._running = True

        if not _HAS_EVDEV:
            _log_error("python-evdev not available — back paddle monitor cannot start")
            return

        while self._running:
            dev_path = find_vendor_hidraw()
            if not dev_path:
                _log_warning("Vendor hidraw device not found, retrying in 5s...")
                await asyncio.sleep(5)
                continue

            _log_info(f"Back paddle monitor opening {dev_path}")
            uinput_dev = None
            fd = -1
            try:
                fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)
                uinput_dev = _create_uinput_device()
                if not uinput_dev:
                    _log_error("Failed to create uinput device")
                    os.close(fd)
                    fd = -1
                    await asyncio.sleep(5)
                    continue

                # Enable intercept mode
                os.write(fd, INTERCEPT_ON)
                _log_info("Back paddle intercept mode enabled")

                while self._running:
                    try:
                        data = os.read(fd, 64)
                    except BlockingIOError:
                        await asyncio.sleep(0.02)
                        continue
                    except OSError:
                        _log_warning("Device read error, will reconnect...")
                        break

                    if not data or len(data) < 13:
                        await asyncio.sleep(0.02)
                        continue

                    # Only process button report packets
                    if data[0] != 0xB2:
                        continue
                    if data[3] != 0x01:
                        continue

                    button_code = data[6]
                    state = data[12]

                    if button_code == BTN_L4:
                        evdev_btn = ecodes.BTN_TRIGGER_HAPPY1
                        label = "L4"
                    elif button_code == BTN_R4:
                        evdev_btn = ecodes.BTN_TRIGGER_HAPPY2
                        label = "R4"
                    else:
                        continue

                    if state == STATE_PRESSED:
                        uinput_dev.write(ecodes.EV_KEY, evdev_btn, 1)
                        uinput_dev.syn()
                        _log_info(f"Back paddle {label} pressed")
                    elif state == STATE_RELEASED:
                        uinput_dev.write(ecodes.EV_KEY, evdev_btn, 0)
                        uinput_dev.syn()
                        _log_info(f"Back paddle {label} released")

            except OSError as e:
                _log_error(f"Error with back paddle device: {e}")
            finally:
                # Disable intercept mode before closing
                if fd >= 0:
                    try:
                        os.write(fd, INTERCEPT_OFF)
                        _log_info("Back paddle intercept mode disabled")
                    except OSError:
                        pass
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if uinput_dev:
                    try:
                        uinput_dev.close()
                    except Exception:
                        pass

            if self._running:
                await asyncio.sleep(5)

    def start(self, loop):
        """Start the monitor as an async task on the given event loop."""
        if not self.is_running:
            self._running = True
            self._task = loop.create_task(self._monitor_loop())

    async def stop(self):
        """Stop the monitor and clean up."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
