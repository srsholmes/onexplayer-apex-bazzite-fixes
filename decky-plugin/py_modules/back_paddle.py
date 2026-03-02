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

Uses raw /dev/uinput ioctl calls — no external dependencies needed.

Steam Input recognizes BTN_TRIGGER_HAPPY1/2 as back paddle buttons
(like the Steam Deck's L4/R4) and lets users remap them per-game.

HID v1 protocol:
  gen_cmd_v1(cid, cmd) -> 64-byte packet
  INTERCEPT_ON  = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
  INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])

Button report (64 bytes, byte[0]==0xB2):
  byte[3] == 0x01 -> button event (vs 0x03=ack, 0x02=gamepad state)
  byte[6] -> button code: 0x22=L4, 0x23=R4
  byte[12] -> state: 0x01=pressed, 0x02=released
"""

import asyncio
import ctypes
import fcntl
import glob
import logging
import os
import struct
import time

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


# ── Raw uinput constants and helpers ──
# No external dependencies — uses ioctl directly against /dev/uinput.

# Linux input event types / codes
EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0x00
BTN_TRIGGER_HAPPY1 = 0x2C0
BTN_TRIGGER_HAPPY2 = 0x2C1
BUS_VIRTUAL = 0x06

# uinput ioctl numbers
UI_SET_EVBIT = 0x40045564   # _IOW('U', 100, int)
UI_SET_KEYBIT = 0x40045565  # _IOW('U', 101, int)
UI_DEV_SETUP = 0x405C5503   # _IOW('U', 3, struct uinput_setup)
UI_DEV_CREATE = 0x5501       # _IO('U', 1)
UI_DEV_DESTROY = 0x5502      # _IO('U', 2)

# struct input_id: bustype(u16), vendor(u16), product(u16), version(u16)
# struct uinput_setup: input_id(8 bytes), name(80 bytes), ff_effects_max(u32) = 92 bytes
UINPUT_SETUP_FMT = "HHHh80sI"

# struct input_event: tv_sec(long), tv_usec(long), type(u16), code(u16), value(i32)
INPUT_EVENT_FMT = "llHHi"


class RawUinputDevice:
    """Minimal uinput wrapper using raw ioctl — no python-evdev needed."""

    def __init__(self):
        self._fd = -1

    def create(self):
        self._fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)

        # Enable EV_KEY
        fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
        # Enable the two button codes
        fcntl.ioctl(self._fd, UI_SET_KEYBIT, BTN_TRIGGER_HAPPY1)
        fcntl.ioctl(self._fd, UI_SET_KEYBIT, BTN_TRIGGER_HAPPY2)

        # Setup device identity
        name = b"OXP Apex Back Paddles"
        name_padded = name + b"\x00" * (80 - len(name))
        setup_data = struct.pack(
            UINPUT_SETUP_FMT,
            BUS_VIRTUAL,   # bustype
            0x1A86,        # vendor
            0xFE01,        # product (distinct from real device)
            1,             # version
            name_padded,   # name
            0,             # ff_effects_max
        )
        fcntl.ioctl(self._fd, UI_DEV_SETUP, setup_data)
        fcntl.ioctl(self._fd, UI_DEV_CREATE)
        # Small delay for device node to appear
        time.sleep(0.1)

    def emit(self, ev_type, code, value):
        """Write a single input event."""
        now = time.time()
        sec = int(now)
        usec = int((now - sec) * 1_000_000)
        event = struct.pack(INPUT_EVENT_FMT, sec, usec, ev_type, code, value)
        os.write(self._fd, event)

    def syn(self):
        """Send a SYN_REPORT to flush the event."""
        self.emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        if self._fd >= 0:
            try:
                fcntl.ioctl(self._fd, UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1


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

                uinput_dev = RawUinputDevice()
                uinput_dev.create()
                _log_info("Created uinput device: OXP Apex Back Paddles")

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
                        evdev_btn = BTN_TRIGGER_HAPPY1
                        label = "L4"
                    elif button_code == BTN_R4:
                        evdev_btn = BTN_TRIGGER_HAPPY2
                        label = "R4"
                    else:
                        continue

                    if state == STATE_PRESSED:
                        uinput_dev.emit(EV_KEY, evdev_btn, 1)
                        uinput_dev.syn()
                        _log_info(f"Back paddle {label} pressed")
                    elif state == STATE_RELEASED:
                        uinput_dev.emit(EV_KEY, evdev_btn, 0)
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
