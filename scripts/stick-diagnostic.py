#!/usr/bin/env python3
"""Stick diagnostic — captures raw packets while rotating sticks.

Analyzes byte patterns to determine correct axis mapping.
Run as root with HHD stopped: sudo python3 test_stick_diagnostic.py
"""
import glob
import math
import os
import select
import struct
import time


def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def find_vendor_hidraw():
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        vid = pid = 0
        for line in content.splitlines():
            if line.startswith("HID_ID="):
                parts = line.split(":")
                if len(parts) >= 3:
                    vid = int(parts[1], 16)
                    pid = int(parts[2], 16)
        if vid != 0x1A86 or pid != 0xFE00:
            continue
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        if not os.path.exists(rd_path):
            continue
        with open(rd_path, "rb") as f:
            rd = f.read(3)
        if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
            name = os.path.basename(sysfs_path)
            return f"/dev/{name}"
    return None


def analyze_samples(samples, label):
    """Analyze captured samples for a single axis movement."""
    if not samples:
        print(f"  No samples captured for {label}!")
        return

    print(f"\n{'='*60}")
    print(f"  {label}: {len(samples)} samples captured")
    print(f"{'='*60}")

    # For each pair of bytes (as signed 16-bit LE), show min/max/range
    print(f"\n  Signed 16-bit LE pairs (bytes[i:i+2]):")
    print(f"  {'Offset':>6} {'Min':>8} {'Max':>8} {'Range':>8} {'Active':>8}")
    print(f"  {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")

    active_pairs = []
    for i in range(4, 26):
        vals = []
        for s in samples:
            if i + 1 < len(s):
                v = struct.unpack_from("<h", s, i)[0]
                vals.append(v)
        if vals:
            mn, mx = min(vals), max(vals)
            rng = mx - mn
            active = "***" if rng > 1000 else ""
            print(f"  [{i:2d}:{i+2:2d}] {mn:8d} {mx:8d} {rng:8d} {active:>8}")
            if rng > 1000:
                active_pairs.append((i, mn, mx, rng))

    # Also check individual bytes
    print(f"\n  Individual bytes:")
    print(f"  {'Offset':>6} {'Min':>5} {'Max':>5} {'Range':>5} {'Active':>8}")
    print(f"  {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*8}")
    for i in range(4, 26):
        vals = [s[i] for s in samples if i < len(s)]
        if vals:
            mn, mx = min(vals), max(vals)
            rng = mx - mn
            active = "***" if rng > 50 else ""
            print(f"  [{i:2d}]    {mn:5d} {mx:5d} {rng:5d} {active:>8}")

    if active_pairs:
        print(f"\n  Active s16 pairs for {label}:")
        for off, mn, mx, rng in active_pairs:
            center = (mn + mx) / 2
            half_range = rng / 2
            print(f"    bytes[{off}:{off+2}]: range [{mn}, {mx}], center={center:.0f}, half_range={half_range:.0f}")

    # Show first and last raw hex
    print(f"\n  First sample bytes[4:26]: {samples[0][4:26].hex()}")
    print(f"  Last  sample bytes[4:26]: {samples[-1][4:26].hex()}")
    # Show the sample with maximum absolute value in the active range
    if active_pairs:
        off = active_pairs[0][0]
        max_s = max(samples, key=lambda s: abs(struct.unpack_from("<h", s, off)[0]))
        print(f"  Peak  sample bytes[4:26]: {max_s[4:26].hex()}")


VENDOR_DEV = find_vendor_hidraw()
if not VENDOR_DEV:
    print("ERROR: Could not find vendor hidraw device (1a86:fe00)")
    exit(1)

print(f"Vendor hidraw: {VENDOR_DEV}")
print("\nStopping HHD...")
os.system("systemctl stop hhd@$(logname) 2>/dev/null; systemctl stop hhd 2>/dev/null")
time.sleep(1)

vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
INTERCEPT_FULL = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
print(f"Sending FULL intercept...")
os.write(vendor_fd, INTERCEPT_FULL)
time.sleep(0.5)

# Drain
try:
    while True:
        os.read(vendor_fd, 64)
except BlockingIOError:
    pass

tests = [
    ("LEFT STICK — full slow clockwise circle", "L-stick circle"),
    ("RIGHT STICK — full slow clockwise circle", "R-stick circle"),
    ("LEFT TRIGGER — full press and release", "L-trigger"),
    ("RIGHT TRIGGER — full press and release", "R-trigger"),
]

all_results = {}

for instruction, label in tests:
    input(f"\n>>> {instruction}\n    Press ENTER to start recording, then do the motion, press ENTER when done.")

    samples = []
    print(f"    Recording... (do the motion now)")

    # Record until user presses enter (non-blocking stdin check)
    import sys
    import termios
    import tty

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        recording = True
        while recording:
            readable, _, _ = select.select([vendor_fd, sys.stdin], [], [], 0.01)

            if sys.stdin in readable:
                sys.stdin.read(1)
                recording = False
                break

            if vendor_fd in readable:
                try:
                    while True:
                        try:
                            data = os.read(vendor_fd, 64)
                        except BlockingIOError:
                            break
                        if len(data) >= 25 and data[0] == 0xB2 and data[3] == 0x02:
                            samples.append(bytes(data))
                except BlockingIOError:
                    pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    print(f"    Captured {len(samples)} state packets.")
    all_results[label] = samples
    analyze_samples(samples, label)

# Final summary
print(f"\n\n{'='*60}")
print(f"  SUMMARY")
print(f"{'='*60}")

for label, samples in all_results.items():
    if not samples:
        continue
    print(f"\n  {label}:")
    for i in range(4, 26):
        vals = []
        for s in samples:
            if i + 1 < len(s):
                v = struct.unpack_from("<h", s, i)[0]
                vals.append(v)
        if vals:
            rng = max(vals) - min(vals)
            if rng > 1000:
                mn, mx = min(vals), max(vals)
                print(f"    s16 @ [{i}:{i+2}]: [{mn:6d} .. {mx:6d}] range={rng}")

    for i in range(4, 26):
        vals = [s[i] for s in samples if i < len(s)]
        if vals:
            rng = max(vals) - min(vals)
            if rng > 50 and rng < 1000:
                mn, mx = min(vals), max(vals)
                print(f"    u8  @ [{i}]:     [{mn:3d} .. {mx:3d}] range={rng}")

print("\n\nCleaning up...")
INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
os.write(vendor_fd, INTERCEPT_OFF)
time.sleep(0.1)
os.close(vendor_fd)
os.system("systemctl restart hhd@$(logname) 2>/dev/null; systemctl restart hhd 2>/dev/null")
print("Done.")
