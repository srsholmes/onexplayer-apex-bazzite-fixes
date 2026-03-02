#!/usr/bin/env python3
"""Test: does the vendor device send any data WITHOUT intercept?

If we can detect L4/R4 in GAMEPAD_STATE packets without enabling intercept,
we can avoid killing the Xbox gamepad entirely.
"""
import os
import time

VENDOR_DEV = "/dev/hidraw5"

print("Stopping HHD...")
os.system("systemctl stop hhd")
time.sleep(1)

fd = os.open(VENDOR_DEV, os.O_RDWR)

# Do NOT send intercept command
print(f"Opened {VENDOR_DEV} - NO intercept sent")
print(f"Press L4, R4, A, B, move stick. Looking for ANY packets...")
print(f"Wait 10 seconds. Ctrl+C to quit early.\n")

os.set_blocking(fd, True)

import select
try:
    for i in range(100):  # ~10 seconds
        ready = select.select([fd], [], [], 0.1)[0]
        if ready:
            data = os.read(fd, 64)
            pkt_type = data[3] if len(data) > 3 else -1
            nonzero = [(i, f"0x{b:02x}") for i, b in enumerate(data[6:30], 6) if b != 0]
            print(f"  Got {len(data)} bytes: cid=0x{data[0]:02x} type=0x{pkt_type:02x} nonzero_data={nonzero}")
            print(f"  raw: {data[:32].hex()}...")
except KeyboardInterrupt:
    pass

print("\nRestarting HHD...")
os.close(fd)
os.system("systemctl restart hhd")
print("Done.")
