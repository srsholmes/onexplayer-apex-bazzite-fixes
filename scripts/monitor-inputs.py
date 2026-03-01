#!/usr/bin/env python3
"""Monitor all input devices for back button presses."""

import struct
import os
import select
import sys

# Event type names
EV_TYPES = {0: "SYN", 1: "KEY", 2: "REL", 3: "ABS", 4: "MSC", 21: "FF"}

def get_device_name(event_num):
    """Read device name from sysfs."""
    try:
        path = f"/sys/class/input/event{event_num}/device/name"
        with open(path) as f:
            return f.read().strip()
    except:
        return "unknown"

def main():
    # Devices to monitor
    devices = {}

    # Devices to skip (noisy or irrelevant)
    # Only monitor these specific event devices
    watch_events = {15, 24, 26, 5, 6}  # Xbox pad, HHD controller, Xbox pad 0, OXP, HID

    # Find all event devices
    for entry in sorted(os.listdir("/dev/input")):
        if entry.startswith("event"):
            num = int(entry.replace("event", ""))
            path = f"/dev/input/{entry}"
            name = get_device_name(num)
            if num not in watch_events:
                print(f"  SKIP: {entry} = {name}")
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                devices[fd] = (path, name)
                print(f"  Opened: {entry} = {name}")
            except PermissionError:
                print(f"  SKIP (no permission): {entry} = {name}")
            except Exception as e:
                print(f"  SKIP ({e}): {entry} = {name}")

    if not devices:
        print("No devices opened! Try running with sudo.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Monitoring {len(devices)} devices. Press back buttons now!")
    print(f"Press Ctrl+C to stop.")
    print(f"{'='*60}\n")

    poll = select.poll()
    for fd in devices:
        poll.register(fd, select.POLLIN)

    try:
        while True:
            events = poll.poll(1000)  # 1 second timeout
            for fd, mask in events:
                if mask & select.POLLIN:
                    try:
                        while True:
                            data = os.read(fd, 24)
                            if len(data) < 24:
                                break
                            tv_sec, tv_usec, ev_type, ev_code, ev_value = struct.unpack("llHHi", data)
                            # Skip SYN events (type 0) - they're just separators
                            if ev_type == 0:
                                continue
                            path, name = devices[fd]
                            type_name = EV_TYPES.get(ev_type, str(ev_type))
                            print(f"[{name}] ({path}) type={type_name}({ev_type}) code={ev_code} value={ev_value}")
                    except BlockingIOError:
                        pass
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for fd in devices:
            os.close(fd)

if __name__ == "__main__":
    main()
