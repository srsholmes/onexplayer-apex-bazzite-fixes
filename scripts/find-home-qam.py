#!/usr/bin/env python3
"""Find which input device sends Home and QAM button events.

Opens ALL /dev/input/event* devices and logs KEY events.
Press Home and QAM to see which device they come through on.

Run: sudo python3 scripts/find-home-qam.py
"""

import os
import select
import struct
import sys

# Common key code names
KEY_NAMES = {
    1: "ESC", 28: "ENTER", 56: "LALT", 100: "RALT",
    125: "LEFTMETA", 126: "RIGHTMETA", 127: "COMPOSE",
    142: "SLEEP", 143: "WAKE",
    148: "PROG1", 149: "PROG2", 150: "PROG3",
    155: "CALC", 156: "SETUP",
    158: "BACK", 159: "FORWARD",
    163: "NEXTSONG", 164: "PLAYPAUSE", 165: "PREVIOUSSONG",
    166: "STOPCD", 171: "CONFIG",
    172: "HOMEPAGE", 173: "REFRESH",
    176: "EDIT", 177: "SCROLLUP", 178: "SCROLLDOWN",
    183: "F13", 184: "F14", 185: "F15", 186: "F16",
    187: "F17", 188: "F18", 189: "F19", 190: "F20",
    212: "CAMERA", 213: "ZOOMIN", 214: "ZOOMOUT",
    240: "UNKNOWN", 272: "BTN_LEFT",
    # Gamepad
    0x130: "BTN_A", 0x131: "BTN_B", 0x133: "BTN_X", 0x134: "BTN_Y",
    0x136: "BTN_TL", 0x137: "BTN_TR", 0x13A: "BTN_SELECT",
    0x13B: "BTN_START", 0x13C: "BTN_MODE",
    0x13D: "BTN_THUMBL", 0x13E: "BTN_THUMBR",
    0x2C0: "BTN_TRIGGER_HAPPY1", 0x2C1: "BTN_TRIGGER_HAPPY2",
}

EV_TYPES = {0: "SYN", 1: "KEY", 2: "REL", 3: "ABS", 4: "MSC", 17: "LED", 20: "REP"}


def get_device_name(event_num):
    try:
        with open(f"/sys/class/input/event{event_num}/device/name") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


def main():
    devices = {}

    print("Opening all input devices...\n")
    for entry in sorted(os.listdir("/dev/input"), key=lambda x: int(x.replace("event", "")) if x.startswith("event") else 999):
        if not entry.startswith("event"):
            continue
        num = int(entry.replace("event", ""))
        name = get_device_name(num)
        path = f"/dev/input/{entry}"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            devices[fd] = (entry, name)
            print(f"  {entry:10s} = {name}")
        except Exception:
            pass

    print(f"\n{'='*70}")
    print("Press HOME and QAM buttons. Only KEY events shown (SYN/MSC filtered).")
    print("Ctrl+C to stop.")
    print(f"{'='*70}\n")

    poll = select.poll()
    for fd in devices:
        poll.register(fd, select.POLLIN)

    try:
        while True:
            events = poll.poll(1000)
            for fd, mask in events:
                if not (mask & select.POLLIN):
                    continue
                try:
                    while True:
                        data = os.read(fd, 24)
                        if len(data) < 24:
                            break
                        _, _, ev_type, ev_code, ev_value = struct.unpack("llHHi", data)
                        # Only show KEY events (type 1) — skip SYN, MSC, REP, LED
                        if ev_type != 1:
                            continue
                        entry, name = devices[fd]
                        key_name = KEY_NAMES.get(ev_code, f"code_{ev_code}")
                        state = "DOWN" if ev_value == 1 else "UP" if ev_value == 0 else f"REPEAT({ev_value})"
                        print(f"  [{entry:10s}] {name:40s}  {key_name} ({ev_code:#06x}) {state}")
                except BlockingIOError:
                    pass
    except KeyboardInterrupt:
        print("\nDone.")
    finally:
        for fd in devices:
            os.close(fd)


if __name__ == "__main__":
    main()
