#!/usr/bin/env python3
"""Test: simulate what d_kbd_vol does — open AT keyboard via GenericGamepadEvdev,
grab it, and read volume events through HHD's event processing.

Run as root with HHD STOPPED:
    sudo systemctl stop hhd@$(whoami)
    sudo python3 scripts/test-volume-grab.py
"""

import select
import sys
import time

# Use HHD's own evdev module to match exactly what d_kbd_vol does
from hhd.controller.physical.evdev import B, GenericGamepadEvdev

KBD_VID = 0x0001
KBD_PID = 0x0001

def main():
    print("=== Volume Grab Test ===")
    print("Opening AT keyboard (0x0001:0x0001) with GenericGamepadEvdev...")
    print("This simulates exactly what d_kbd_vol does in our patched base.py\n")

    d_kbd_vol = GenericGamepadEvdev(
        vid=[KBD_VID],
        pid=[KBD_PID],
        required=False,
        grab=True,
        btn_map={
            B("KEY_VOLUMEUP"): "key_volumeup",
            B("KEY_VOLUMEDOWN"): "key_volumedown",
        },
        capabilities={B("EV_KEY"): [B("KEY_VOLUMEUP")]},
    )

    fds = d_kbd_vol.open()
    if not fds:
        print("FAILED: d_kbd_vol.open() returned empty — device not found!")
        print(f"  Device: {d_kbd_vol.dev}")
        sys.exit(1)

    print(f"SUCCESS: Opened device fd={fds[0]}")
    if d_kbd_vol.dev:
        print(f"  Path: {d_kbd_vol.dev.path}")
        print(f"  Name: {d_kbd_vol.dev.name}")
        print(f"  VID:PID: {d_kbd_vol.dev.info.vendor:#06x}:{d_kbd_vol.dev.info.product:#06x}")
    print(f"\nPress volume up/down... (Ctrl+C to stop)\n")

    try:
        while True:
            r, _, _ = select.select(fds, [], [], 1.0)
            if r:
                evs = d_kbd_vol.produce(r)
                for ev in evs:
                    print(f"  Event: {ev}")
            else:
                # Also check for delayed events
                evs = d_kbd_vol.produce([])
                for ev in evs:
                    print(f"  Delayed event: {ev}")
    except KeyboardInterrupt:
        print("\nDone.")
    finally:
        d_kbd_vol.close(True)

if __name__ == "__main__":
    main()
