#!/usr/bin/env python3
"""Quick evtest replacement — reads input events from an evdev device."""
import struct
import sys

dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/event25"
FMT = "llHHi"
SIZE = struct.calcsize(FMT)

print(f"Monitoring {dev} — press buttons (Ctrl+C to quit)\n")
with open(dev, "rb") as f:
    while True:
        data = f.read(SIZE)
        sec, usec, ev_type, code, value = struct.unpack(FMT, data)
        if ev_type == 0:  # SYN_REPORT
            continue
        print(f"type={ev_type} code={code} (0x{code:04x}) value={value}")
