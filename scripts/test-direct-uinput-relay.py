#!/usr/bin/env python3
"""Test: Intercept mode with DIRECT uinput relay — bypass HHD entirely.

This tests whether stick sticking/lagging is caused by:
  a) Python parsing speed (unlikely but possible)
  b) HHD's pipeline (delta filtering, multiplexer, event routing)
  c) The intercept protocol itself (inherent to vendor HID polling rate)

Approach:
  - Enables full intercept on vendor HID (Xbox gamepad goes silent)
  - Creates TWO uinput devices:
    1. "OXP Paddles" — BTN_TRIGGER_HAPPY1/2 for L4/R4
    2. "OXP Gamepad Relay" — full Xbox-like gamepad (sticks, triggers, buttons, dpad)
  - Parses 64-byte vendor packets and emits uinput events directly
  - No HHD, no delta filtering, no multiplexer — raw packet → uinput

Run: sudo python3 scripts/test-direct-uinput-relay.py

If sticks feel good: the bottleneck is HHD's pipeline → we can write a lean daemon.
If sticks still stick: the bottleneck is the vendor HID protocol itself → need native C.
"""

import ctypes
import fcntl
import glob
import os
import select
import signal
import struct
import sys
import time

# ── Device detection ──

TARGET_VID = 0x1A86
TARGET_PID = 0xFE00


def find_vendor_hidraw():
    """Find the vendor HID interface (usage page 0xFF00) for 1a86:fe00."""
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

    for name, sysfs_path in candidates:
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        if not os.path.exists(rd_path):
            continue
        try:
            with open(rd_path, "rb") as f:
                rd = f.read(3)
            if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                dev_path = f"/dev/{name}"
                if os.path.exists(dev_path):
                    return dev_path
        except OSError:
            continue
    return None


# ── HID v1 command generation ──

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])

# ── Keyboard evdev for Home/QAM ──
# Home = KEY_G (with modifiers), QAM = KEY_O (with modifiers)
# These come through the keyboard evdev interface of 1a86:fe00
KEY_G = 34    # 0x22
KEY_O = 24    # 0x18
INPUT_EVENT_SIZE = struct.calcsize("llHHi")


def find_kbd_evdev():
    """Find the keyboard evdev device for 1a86:fe00 that has KEY_G capability."""
    for entry in sorted(os.listdir("/dev/input")):
        if not entry.startswith("event"):
            continue
        num = int(entry.replace("event", ""))
        sysfs = f"/sys/class/input/event{num}/device"

        # Check name
        try:
            with open(f"{sysfs}/name") as f:
                name = f.read().strip()
        except Exception:
            continue

        if "1a86:fe00" not in name and "HID 1a86" not in name:
            continue

        # Check it's the keyboard interface (has KEY_G)
        try:
            with open(f"{sysfs}/capabilities/key") as f:
                caps = f.read().strip()
            # KEY_G = 34, bit 34 in the capabilities bitmask
            # Capabilities are hex words, LSB first in each word
            words = caps.split()
            # Reverse to get word 0 first
            words.reverse()
            flat = 0
            for i, w in enumerate(words):
                flat |= int(w, 16) << (i * 64)
            if not (flat & (1 << KEY_G)):
                continue
        except Exception:
            continue

        return f"/dev/input/{entry}", name

    return None, None


# ── Linux input constants ──

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0x00

# Buttons
BTN_A = 0x130
BTN_B = 0x131
BTN_X = 0x133
BTN_Y = 0x134
BTN_TL = 0x136  # LB
BTN_TR = 0x137  # RB
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C  # Home/Guide
BTN_THUMBL = 0x13D  # LS click
BTN_THUMBR = 0x13E  # RS click
BTN_TRIGGER_HAPPY1 = 0x2C0  # L4
BTN_TRIGGER_HAPPY2 = 0x2C1  # R4

# Keyboard keys for QAM — Steam uses KEY_F16 (0xCB) for QAM overlay on some devices
# On Steam Deck the "..." button sends KEY_F14. We'll try KEY_F16 which is common.
KEY_F14 = 0x68  # Sometimes used for Steam QAM
KEY_F16 = 0x6B  # Also used for QAM on some controllers

# Axes
ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02     # LT analog
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05    # RT analog
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

# uinput ioctl
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
UI_DEV_SETUP = 0x405C5503
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

BUS_USB = 0x03

# struct uinput_setup: input_id(8 bytes) + name(80 bytes) + ff_effects_max(u32)
UINPUT_SETUP_FMT = "HHHh80sI"

# struct input_event
INPUT_EVENT_FMT = "llHHi"

# struct uinput_abs_setup: code(u16) + pad(u16) + absinfo(min,max,fuzz,flat,res = 5*i32)
# Actually: __u16 code, struct input_absinfo { __s32 value, min, max, fuzz, flat, resolution }
UINPUT_ABS_SETUP = 0x401C5504  # _IOW('U', 4, struct uinput_abs_setup) — 28 bytes


# ── Button code mapping: vendor HID → evdev ──

VENDOR_BTN_MAP = {
    0x01: BTN_A,
    0x02: BTN_B,
    0x03: BTN_X,
    0x04: BTN_Y,
    0x05: BTN_TL,       # LB
    0x06: BTN_TR,       # RB
    0x09: BTN_START,
    0x0A: BTN_SELECT,
    0x0B: BTN_THUMBL,   # LS click
    0x0C: BTN_THUMBR,   # RS click
    0x21: BTN_MODE,     # Home
    0x22: BTN_TRIGGER_HAPPY2,  # Physical RIGHT paddle → R4
    0x23: BTN_TRIGGER_HAPPY1,  # Physical LEFT paddle → L4
    0x24: KEY_F16,             # KB/QAM button
}

VENDOR_BTN_NAMES = {
    0x01: "A", 0x02: "B", 0x03: "X", 0x04: "Y",
    0x05: "LB", 0x06: "RB", 0x09: "Start", 0x0A: "Select",
    0x0B: "LS", 0x0C: "RS", 0x21: "Home",
    0x22: "R4", 0x23: "L4", 0x24: "QAM",
}

# D-pad codes
DPAD_UP = 0x0D
DPAD_DOWN = 0x0E
DPAD_LEFT = 0x0F
DPAD_RIGHT = 0x10


def write_event(fd, ev_type, code, value):
    """Write a single input_event to a uinput fd."""
    now = time.time()
    sec = int(now)
    usec = int((now - sec) * 1_000_000)
    os.write(fd, struct.pack(INPUT_EVENT_FMT, sec, usec, ev_type, code, value))


def syn(fd):
    write_event(fd, EV_SYN, SYN_REPORT, 0)


def create_gamepad_uinput():
    """Create a full Xbox-like gamepad uinput device."""
    fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)

    # Enable event types
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
    fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)

    # Enable all buttons
    all_btns = list(VENDOR_BTN_MAP.values())
    for btn in all_btns:
        fcntl.ioctl(fd, UI_SET_KEYBIT, btn)

    # Enable axes
    axes_config = {
        ABS_X:     (-32768, 32767, 16, 128),    # LX
        ABS_Y:     (-32768, 32767, 16, 128),    # LY
        ABS_RX:    (-32768, 32767, 16, 128),    # RX
        ABS_RY:    (-32768, 32767, 16, 128),    # RY
        ABS_Z:     (0, 255, 0, 0),              # LT
        ABS_RZ:    (0, 255, 0, 0),              # RT
        ABS_HAT0X: (-1, 1, 0, 0),               # D-pad X
        ABS_HAT0Y: (-1, 1, 0, 0),               # D-pad Y
    }

    for axis_code, (amin, amax, fuzz, flat) in axes_config.items():
        fcntl.ioctl(fd, UI_SET_ABSBIT, axis_code)
        # struct uinput_abs_setup: __u16 code, __u16 pad, struct input_absinfo (value, min, max, fuzz, flat, resolution)
        abs_setup = struct.pack("HH6i", axis_code, 0, 0, amin, amax, fuzz, flat, 0)
        fcntl.ioctl(fd, UINPUT_ABS_SETUP, abs_setup)

    # Device setup
    name = b"OXP Gamepad Relay"
    name_padded = name + b"\x00" * (80 - len(name))
    setup_data = struct.pack(
        UINPUT_SETUP_FMT,
        BUS_USB,
        0x045E,   # Microsoft VID (so Steam recognizes it as Xbox)
        0x028F,   # Slightly different PID to distinguish from real
        1,
        name_padded,
        0,
    )
    fcntl.ioctl(fd, UI_DEV_SETUP, setup_data)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    time.sleep(0.3)  # Wait for device node
    return fd


def main():
    dev_path = find_vendor_hidraw()
    if not dev_path:
        print("ERROR: Vendor HID device not found!")
        sys.exit(1)

    print(f"Found vendor HID: {dev_path}")
    print("Stopping HHD...")
    os.system("systemctl stop hhd@$(whoami) 2>/dev/null; systemctl stop hhd 2>/dev/null")
    time.sleep(1)

    hid_fd = -1
    gamepad_fd = -1

    def cleanup(*_):
        print("\n\nCleaning up...")
        # Release and close keyboard evdev
        if kbd_fd >= 0:
            try:
                EVIOCGRAB = 0x40044590
                fcntl.ioctl(kbd_fd, EVIOCGRAB, 0)
            except OSError:
                pass
            try:
                os.close(kbd_fd)
            except OSError:
                pass
        # Disable intercept
        if hid_fd >= 0:
            try:
                os.write(hid_fd, INTERCEPT_OFF)
                print("Intercept disabled")
            except OSError:
                pass
            try:
                os.close(hid_fd)
            except OSError:
                pass
        # Destroy uinput
        if gamepad_fd >= 0:
            try:
                fcntl.ioctl(gamepad_fd, UI_DEV_DESTROY)
            except OSError:
                pass
            try:
                os.close(gamepad_fd)
            except OSError:
                pass
        print("Restarting HHD...")
        os.system("systemctl restart hhd@$(whoami) 2>/dev/null; systemctl restart hhd 2>/dev/null")
        print("Done.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Open vendor HID for read+write (need write for intercept command)
    hid_fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)
    print(f"Opened {dev_path}")

    # Open keyboard evdev for Home/QAM
    kbd_path, kbd_name = find_kbd_evdev()
    kbd_fd = -1
    if kbd_path:
        kbd_fd = os.open(kbd_path, os.O_RDONLY | os.O_NONBLOCK)
        # Grab the device so keypresses don't leak to desktop
        import ctypes
        EVIOCGRAB = 0x40044590
        fcntl.ioctl(kbd_fd, EVIOCGRAB, 1)
        print(f"Opened keyboard: {kbd_path} ({kbd_name}) [grabbed]")
    else:
        print("WARNING: Keyboard evdev not found — Home/QAM won't work")

    # Create gamepad uinput
    print("Creating uinput gamepad...")
    gamepad_fd = create_gamepad_uinput()
    print("Created 'OXP Gamepad Relay' uinput device")

    # Enable intercept
    os.write(hid_fd, INTERCEPT_ON)
    print("Intercept mode ENABLED")
    print("=" * 60)
    print("All input via vendor HID → uinput. Home/QAM via keyboard evdev.")
    print("Test sticks, buttons, triggers, dpad, Home, QAM.")
    print("Ctrl+C to stop and restore HHD.")
    print("=" * 60)

    # D-pad state tracking
    dpad = {"up": False, "down": False, "left": False, "right": False}
    prev_buttons = {}
    prev_axes = {}
    pkt_count = 0
    btn_count = 0
    state_count = 0

    # Map keyboard evdev keys to gamepad buttons
    KBD_TO_BTN = {
        KEY_G: ("Home", BTN_MODE),
        KEY_O: ("QAM", KEY_F16),
    }

    fds_to_poll = [hid_fd]
    if kbd_fd >= 0:
        fds_to_poll.append(kbd_fd)

    try:
        while True:
            ready = select.select(fds_to_poll, [], [], 0.001)[0]
            if not ready:
                continue

            # ── Process keyboard evdev events (Home/QAM) ──
            if kbd_fd >= 0 and kbd_fd in ready:
                try:
                    while True:
                        evdata = os.read(kbd_fd, INPUT_EVENT_SIZE)
                        if len(evdata) < INPUT_EVENT_SIZE:
                            break
                        _, _, ev_type, ev_code, ev_value = struct.unpack("llHHi", evdata)
                        # Only KEY events, skip repeats (value=2)
                        if ev_type != EV_KEY or ev_value == 2:
                            continue
                        if ev_code in KBD_TO_BTN:
                            name, btn = KBD_TO_BTN[ev_code]
                            write_event(gamepad_fd, EV_KEY, btn, ev_value)
                            syn(gamepad_fd)
                            if ev_value == 1:
                                print(f"  BTN {name} pressed (via kbd)")
                except BlockingIOError:
                    pass

            # ── Process vendor HID packets (sticks/buttons/paddles) ──
            if hid_fd not in ready:
                continue

            try:
                data = os.read(hid_fd, 64)
            except BlockingIOError:
                continue
            except OSError:
                print("Device read error!")
                break

            if len(data) < 4:
                continue

            pkt_count += 1

            # Log ALL non-0xB2 packets — Home/QAM may use different framing
            if data[0] != 0xB2:
                nonzero = [(i, f"0x{b:02x}") for i, b in enumerate(data[:20]) if b != 0]
                print(f"  NON-0xB2 packet: cid=0x{data[0]:02x} raw={data[:20].hex()} nonzero={nonzero}")
                continue

            pkt_type = data[3]

            if pkt_type == 0x01 and len(data) >= 13:
                # Button event
                btn_code = data[6]
                pressed = data[12] == 0x01

                # D-pad
                if btn_code in (DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT):
                    direction = {DPAD_UP: "up", DPAD_DOWN: "down", DPAD_LEFT: "left", DPAD_RIGHT: "right"}[btn_code]
                    dpad[direction] = pressed
                    hat_x = int(dpad["right"]) - int(dpad["left"])
                    hat_y = int(dpad["down"]) - int(dpad["up"])
                    write_event(gamepad_fd, EV_ABS, ABS_HAT0X, hat_x)
                    write_event(gamepad_fd, EV_ABS, ABS_HAT0Y, hat_y)
                    syn(gamepad_fd)
                    btn_count += 1
                    if pressed:
                        print(f"  DPAD {direction}")


                elif btn_code in VENDOR_BTN_MAP:
                    evdev_btn = VENDOR_BTN_MAP[btn_code]
                    name = VENDOR_BTN_NAMES.get(btn_code, f"0x{btn_code:02x}")

                    # Debounce
                    if prev_buttons.get(btn_code) == pressed:
                        continue
                    prev_buttons[btn_code] = pressed

                    write_event(gamepad_fd, EV_KEY, evdev_btn, 1 if pressed else 0)
                    syn(gamepad_fd)
                    btn_count += 1

                    if pressed:
                        print(f"  BTN {name} pressed")

            elif pkt_type == 0x02 and len(data) >= 25:
                # Gamepad state — sticks + triggers
                lt = data[16]
                rt = data[15]
                lx = struct.unpack_from("<h", data, 17)[0]
                ly = -struct.unpack_from("<h", data, 19)[0]  # Invert Y

                # RX/RY with overflow correction
                rx_raw = struct.unpack_from("<h", data, 21)[0]
                if rx_raw == 32767:
                    rx = -32768  # Overflowed from left
                elif rx_raw == -32768:
                    rx = 32767   # Overflowed from right
                else:
                    rx = rx_raw

                ry_raw = struct.unpack_from("<h", data, 23)[0]
                if ry_raw == 32767:
                    ry = 32767   # Overflowed from up (inverted)
                elif ry_raw == -32768:
                    ry = -32768  # Overflowed from down (inverted)
                else:
                    ry = -ry_raw  # Invert Y

                # Emit all axes — no delta filtering, just raw → uinput
                write_event(gamepad_fd, EV_ABS, ABS_X, lx)
                write_event(gamepad_fd, EV_ABS, ABS_Y, ly)
                write_event(gamepad_fd, EV_ABS, ABS_RX, rx)
                write_event(gamepad_fd, EV_ABS, ABS_RY, ry)
                write_event(gamepad_fd, EV_ABS, ABS_Z, lt)
                write_event(gamepad_fd, EV_ABS, ABS_RZ, rt)
                syn(gamepad_fd)
                state_count += 1

                # Print first few + periodic status
                if state_count <= 3 or state_count % 500 == 0:
                    print(f"  STICK LX={lx:6d} LY={ly:6d} RX={rx:6d} RY={ry:6d} LT={lt:3d} RT={rt:3d} [#{state_count}]")

            # type 0x03 = ACK, ignore

    except OSError as e:
        print(f"Error: {e}")

    cleanup()


if __name__ == "__main__":
    main()
