#!/usr/bin/env python3
"""Monitor ALL hidraw interfaces of 1a86:fe00 with intercept active.

Checks if Home/QAM come through a different hidraw interface
even when intercept is enabled on the vendor (0xFF00) interface.

Run: sudo python3 scripts/find-home-all-hidraw.py
"""

import glob
import os
import select
import sys
import time

TARGET_VID = 0x1A86
TARGET_PID = 0xFE00


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])


def find_all_hidraw():
    """Find ALL hidraw interfaces for 1a86:fe00, noting which is vendor."""
    results = []
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        is_target = False
        for line in content.splitlines():
            if not line.startswith("HID_ID="):
                continue
            parts = line.split(":")
            if len(parts) < 3:
                continue
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
            if vid == TARGET_VID and pid == TARGET_PID:
                is_target = True
                break
        if not is_target:
            continue

        name = os.path.basename(sysfs_path)
        dev_path = f"/dev/{name}"

        # Check report descriptor for usage page
        is_vendor = False
        rd_hex = ""
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        if os.path.exists(rd_path):
            try:
                with open(rd_path, "rb") as f:
                    rd = f.read()
                rd_hex = rd[:6].hex()
                if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                    is_vendor = True
            except OSError:
                pass

        results.append((dev_path, is_vendor, rd_hex))
    return results


def main():
    print("Stopping HHD...")
    os.system("systemctl stop hhd@$(whoami) 2>/dev/null; systemctl stop hhd 2>/dev/null")
    time.sleep(1)

    interfaces = find_all_hidraw()
    if not interfaces:
        print("No 1a86:fe00 hidraw devices found!")
        sys.exit(1)

    print(f"\nFound {len(interfaces)} hidraw interfaces for 1a86:fe00:")
    fds = {}
    vendor_fd = -1
    vendor_path = None

    for dev_path, is_vendor, rd_hex in interfaces:
        label = "VENDOR (0xFF00)" if is_vendor else f"other (rd={rd_hex})"
        print(f"  {dev_path}: {label}")
        try:
            fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)
            fds[fd] = (dev_path, is_vendor)
            if is_vendor:
                vendor_fd = fd
                vendor_path = dev_path
        except OSError as e:
            print(f"    Failed to open: {e}")

    if vendor_fd < 0:
        print("ERROR: Could not open vendor interface!")
        sys.exit(1)

    # Enable intercept on vendor interface
    os.write(vendor_fd, INTERCEPT_ON)
    print(f"\nIntercept ENABLED on {vendor_path}")
    print("=" * 60)
    print("Press HOME and QAM. Monitoring ALL interfaces for data.")
    print("Ctrl+C to stop.")
    print("=" * 60)

    try:
        while True:
            ready = select.select(list(fds.keys()), [], [], 0.1)[0]
            for fd in ready:
                try:
                    data = os.read(fd, 256)
                except BlockingIOError:
                    continue
                except OSError:
                    continue

                dev_path, is_vendor = fds[fd]
                label = "VENDOR" if is_vendor else dev_path

                # Show first 20 bytes hex + any recognizable structure
                hex_str = data[:24].hex()
                nonzero = [(i, f"0x{b:02x}") for i, b in enumerate(data[:24]) if b != 0]
                print(f"  [{label}] {len(data)}b: {hex_str}")
                print(f"           nonzero: {nonzero}")

    except KeyboardInterrupt:
        pass

    print("\n\nDisabling intercept...")
    try:
        os.write(vendor_fd, INTERCEPT_OFF)
    except OSError:
        pass

    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass

    print("Restarting HHD...")
    os.system("systemctl restart hhd@$(whoami) 2>/dev/null; systemctl restart hhd 2>/dev/null")
    print("Done.")


if __name__ == "__main__":
    main()
