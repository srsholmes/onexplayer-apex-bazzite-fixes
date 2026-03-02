#!/usr/bin/env python3
"""Right stick jump detector.

Captures R-stick data during a slow circle and flags any sudden jumps.
Run as root: sudo python3 test_rstick_jump.py
"""
import glob
import math
import os
import select
import struct
import sys
import termios
import time
import tty


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


VENDOR_DEV = find_vendor_hidraw()
if not VENDOR_DEV:
    print("ERROR: Could not find vendor hidraw")
    exit(1)

print(f"Vendor: {VENDOR_DEV}")
print("Stopping HHD...")
os.system("systemctl stop hhd@$(logname) 2>/dev/null; systemctl stop hhd 2>/dev/null")
time.sleep(1)

vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
os.write(vendor_fd, gen_cmd_v1(0xB2, [0x03, 0x01, 0x02]))
time.sleep(0.5)

# Drain ACK
try:
    while True:
        os.read(vendor_fd, 64)
except BlockingIOError:
    pass

print("\nSlowly rotate the RIGHT STICK in a full circle.")
print("Press ENTER to start, ENTER again to stop.\n")
input("Ready? Press ENTER...")

samples = []
prev_rx = prev_ry = None
jumps = []

print("Recording... rotate slowly. Press ENTER when done.\n")

old_settings = termios.tcgetattr(sys.stdin)
try:
    tty.setcbreak(sys.stdin.fileno())
    while True:
        readable, _, _ = select.select([vendor_fd, sys.stdin], [], [], 0.005)

        if sys.stdin in readable:
            sys.stdin.read(1)
            break

        if vendor_fd in readable:
            try:
                while True:
                    try:
                        data = os.read(vendor_fd, 64)
                    except BlockingIOError:
                        break

                    if len(data) < 25 or data[0] != 0xB2:
                        continue

                    pkt_type = data[3]

                    if pkt_type == 0x01:
                        btn = data[6]
                        state = data[12]
                        print(f"  ** BUTTON EVENT during stick: code=0x{btn:02x} state=0x{state:02x}")

                    elif pkt_type == 0x02:
                        rx_raw = struct.unpack_from("<h", data, 21)[0]
                        ry_raw = struct.unpack_from("<h", data, 23)[0]
                        rx = rx_raw / 32768.0
                        ry = -(ry_raw / 32768.0)

                        sample = {
                            "t": time.perf_counter(),
                            "rx_raw": rx_raw, "ry_raw": ry_raw,
                            "rx": rx, "ry": ry,
                            "raw_hex": data[15:26].hex(),
                        }
                        samples.append(sample)

                        # Detect jump
                        if prev_rx is not None:
                            dx = abs(rx - prev_rx)
                            dy = abs(ry - prev_ry)
                            if dx > 0.3 or dy > 0.3:
                                jumps.append({
                                    "idx": len(samples) - 1,
                                    "dx": dx, "dy": dy,
                                    "prev_rx": prev_rx, "prev_ry": prev_ry,
                                    "rx": rx, "ry": ry,
                                    "rx_raw": rx_raw, "ry_raw": ry_raw,
                                    "raw_hex": data[15:26].hex(),
                                })
                                print(f"  ** JUMP at sample {len(samples)-1}: "
                                      f"rx={prev_rx:.3f}->{rx:.3f} (d={dx:.3f})  "
                                      f"ry={prev_ry:.3f}->{ry:.3f} (d={dy:.3f})  "
                                      f"raw_rx={rx_raw} raw_ry={ry_raw}")

                        prev_rx = rx
                        prev_ry = ry
            except BlockingIOError:
                pass
finally:
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

print(f"\n{'='*60}")
print(f"  Captured {len(samples)} samples, {len(jumps)} jumps detected")
print(f"{'='*60}")

if jumps:
    print(f"\n  JUMPS (threshold > 0.3 change per sample):")
    for j in jumps:
        idx = j["idx"]
        print(f"\n  Sample {idx}:")
        print(f"    rx: {j['prev_rx']:.4f} -> {j['rx']:.4f}  (delta={j['dx']:.4f})")
        print(f"    ry: {j['prev_ry']:.4f} -> {j['ry']:.4f}  (delta={j['dy']:.4f})")
        print(f"    raw rx={j['rx_raw']:6d}  raw ry={j['ry_raw']:6d}")
        print(f"    bytes[15:26]: {j['raw_hex']}")
        # Show surrounding samples
        for k in range(max(0, idx-2), min(len(samples), idx+3)):
            s = samples[k]
            marker = " >>>" if k == idx else "    "
            print(f"  {marker} [{k:3d}] rx_raw={s['rx_raw']:6d} ry_raw={s['ry_raw']:6d}  "
                  f"rx={s['rx']:.4f} ry={s['ry']:.4f}  bytes={s['raw_hex']}")
else:
    print("\n  No jumps detected! Raw data looks clean.")
    print("  If you still see jumps in-game, the issue is in HHD's processing chain.")

# Show rx/ry ranges
if samples:
    rx_vals = [s["rx_raw"] for s in samples]
    ry_vals = [s["ry_raw"] for s in samples]
    print(f"\n  RX raw range: [{min(rx_vals):6d}, {max(rx_vals):6d}]")
    print(f"  RY raw range: [{min(ry_vals):6d}, {max(ry_vals):6d}]")

print("\nCleaning up...")
os.write(vendor_fd, gen_cmd_v1(0xB2, [0x00, 0x01, 0x02]))
time.sleep(0.1)
os.close(vendor_fd)
os.system("systemctl restart hhd@$(logname) 2>/dev/null; systemctl restart hhd 2>/dev/null")
print("Done.")
