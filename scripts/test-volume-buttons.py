#!/usr/bin/env python3
"""Diagnostic: find which evdev device produces volume button events.

Run as root:
    sudo python3 scripts/test-volume-buttons.py

Press volume up/down buttons while running. The script monitors ALL input
devices simultaneously and reports which ones produce volume key events.

Also checks if HHD's virtual volume keyboard (event24 etc.) receives events,
which tells us whether HHD is capturing and forwarding volume buttons.
"""

import select
import sys
import evdev

KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115

def main():
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    if not devices:
        print("No input devices found. Run as root?")
        sys.exit(1)

    print("=== Volume Button Diagnostic ===")
    print(f"Found {len(devices)} input devices\n")

    # Show devices that claim to support volume keys
    print("Devices with volume key capability:")
    vol_capable = []
    for dev in devices:
        caps = dev.capabilities(verbose=False)
        keys = caps.get(evdev.ecodes.EV_KEY, [])
        has_vol_up = KEY_VOLUMEUP in keys
        has_vol_down = KEY_VOLUMEDOWN in keys
        if has_vol_up or has_vol_down:
            vol_capable.append(dev)
            print(f"  {dev.path}: {dev.name} (vid={dev.info.vendor:#06x} pid={dev.info.product:#06x})")

    if not vol_capable:
        print("  (none found)")

    print(f"\nMonitoring ALL {len(devices)} devices for volume key events.")
    print("Press volume up/down buttons now... (Ctrl+C to stop)\n")

    fd_to_dev = {dev.fd: dev for dev in devices}

    try:
        while True:
            r, _, _ = select.select(list(fd_to_dev.keys()), [], [], 1.0)
            for fd in r:
                dev = fd_to_dev[fd]
                try:
                    for event in dev.read():
                        if event.type == evdev.ecodes.EV_KEY and event.code in (KEY_VOLUMEUP, KEY_VOLUMEDOWN):
                            key_name = "VOLUME_UP" if event.code == KEY_VOLUMEUP else "VOLUME_DOWN"
                            state = "press" if event.value == 1 else "release" if event.value == 0 else f"repeat({event.value})"
                            is_hhd = "Handheld Daemon" in dev.name
                            marker = " <-- HHD virtual output" if is_hhd else " <-- SOURCE"
                            print(f"  [{key_name} {state}] {dev.path}: {dev.name} (vid={dev.info.vendor:#06x} pid={dev.info.product:#06x}){marker}")
                except OSError:
                    # Device was grabbed or removed
                    del fd_to_dev[fd]
    except KeyboardInterrupt:
        print("\nDone.")
    finally:
        for dev in devices:
            try:
                dev.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
