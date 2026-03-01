#!/usr/bin/env python3
"""Monitor hidraw devices for raw HID reports — to find back button signals."""

import os
import select
import sys

def get_hidraw_name(num):
    try:
        path = f"/sys/class/hidraw/hidraw{num}/device/uevent"
        with open(path) as f:
            for line in f:
                if line.startswith("HID_NAME="):
                    return line.strip().split("=", 1)[1]
    except:
        pass
    return "unknown"

def main():
    devices = {}

    for i in range(20):
        path = f"/dev/hidraw{i}"
        if not os.path.exists(path):
            continue
        name = get_hidraw_name(i)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            devices[fd] = (path, name)
            print(f"  Opened: hidraw{i} = {name}")
        except Exception as e:
            print(f"  SKIP: hidraw{i} = {name} ({e})")

    if not devices:
        print("No devices opened! Try running with sudo.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Monitoring {len(devices)} hidraw devices.")
    print(f"Press back buttons, then face B, then face Y.")
    print(f"Press Ctrl+C to stop.")
    print(f"{'='*60}\n")

    poll = select.poll()
    for fd in devices:
        poll.register(fd, select.POLLIN)

    # Track last report per device to only show changes
    last_report = {}

    try:
        while True:
            events = poll.poll(1000)
            for fd, mask in events:
                if mask & select.POLLIN:
                    try:
                        data = os.read(fd, 256)
                        path, name = devices[fd]
                        hex_data = data.hex()

                        # Only print if report changed (filters constant gyro noise)
                        key = fd
                        if last_report.get(key) != hex_data:
                            last_report[key] = hex_data
                            # Show as spaced hex bytes
                            pretty = " ".join(f"{b:02x}" for b in data)
                            print(f"[{name}] ({path}) len={len(data)}: {pretty}")
                    except BlockingIOError:
                        pass
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for fd in devices:
            os.close(fd)

if __name__ == "__main__":
    main()
