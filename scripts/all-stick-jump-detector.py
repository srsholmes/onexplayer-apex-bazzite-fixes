#!/usr/bin/env python3
"""All-axis stick jump detector.

Captures both analog sticks (LX, LY, RX, RY) during a slow circle and
flags any sudden jumps on any axis. Logs raw + converted values on jumps,
prints min/max ranges at end.

Run as root: sudo python3 all-stick-jump-detector.py
"""
import glob
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


JUMP_THRESHOLD = 0.3
AXIS_NAMES = ["LX", "LY", "RX", "RY"]

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

print("\nSlowly rotate BOTH STICKS in full circles.")
print("Press ENTER to start, ENTER again to stop.\n")
input("Ready? Press ENTER...")

samples = []
prev = {"LX": None, "LY": None, "RX": None, "RY": None}
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
                        print(f"  ** BUTTON EVENT: code=0x{btn:02x} state=0x{state:02x}")

                    elif pkt_type == 0x02:
                        lx_raw = struct.unpack_from("<h", data, 17)[0]
                        ly_raw = struct.unpack_from("<h", data, 19)[0]
                        rx_raw = struct.unpack_from("<h", data, 21)[0]
                        ry_raw = struct.unpack_from("<h", data, 23)[0]

                        # Convert (no overflow correction — we want to see raw behavior)
                        lx = lx_raw / 32768.0
                        ly = -(ly_raw / 32768.0)
                        rx = rx_raw / 32768.0
                        ry = -(ry_raw / 32768.0)

                        sample = {
                            "t": time.perf_counter(),
                            "LX_raw": lx_raw, "LY_raw": ly_raw,
                            "RX_raw": rx_raw, "RY_raw": ry_raw,
                            "LX": lx, "LY": ly, "RX": rx, "RY": ry,
                            "raw_hex": data[15:26].hex(),
                        }
                        samples.append(sample)

                        # Detect jumps on each axis
                        for axis in AXIS_NAMES:
                            val = sample[axis]
                            raw = sample[f"{axis}_raw"]
                            if prev[axis] is not None:
                                delta = abs(val - prev[axis])
                                if delta > JUMP_THRESHOLD:
                                    jump = {
                                        "idx": len(samples) - 1,
                                        "axis": axis,
                                        "delta": delta,
                                        "prev": prev[axis],
                                        "cur": val,
                                        "raw": raw,
                                        "raw_hex": data[15:26].hex(),
                                    }
                                    jumps.append(jump)
                                    print(f"  ** JUMP {axis} at sample {len(samples)-1}: "
                                          f"{prev[axis]:.3f}->{val:.3f} (d={delta:.3f})  "
                                          f"raw={raw}")
                            prev[axis] = val
            except BlockingIOError:
                pass
finally:
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

print(f"\n{'='*60}")
print(f"  Captured {len(samples)} samples, {len(jumps)} jumps detected")
print(f"{'='*60}")

if jumps:
    print(f"\n  JUMPS (threshold > {JUMP_THRESHOLD} change per sample):")
    for j in jumps:
        idx = j["idx"]
        axis = j["axis"]
        print(f"\n  Sample {idx} — {axis}:")
        print(f"    {axis}: {j['prev']:.4f} -> {j['cur']:.4f}  (delta={j['delta']:.4f})")
        print(f"    raw {axis}={j['raw']:6d}")
        print(f"    bytes[15:26]: {j['raw_hex']}")
        # Show surrounding samples
        for k in range(max(0, idx - 2), min(len(samples), idx + 3)):
            s = samples[k]
            marker = " >>>" if k == idx else "    "
            print(f"  {marker} [{k:3d}] "
                  f"LX_raw={s['LX_raw']:6d} LY_raw={s['LY_raw']:6d} "
                  f"RX_raw={s['RX_raw']:6d} RY_raw={s['RY_raw']:6d}  "
                  f"LX={s['LX']:.4f} LY={s['LY']:.4f} "
                  f"RX={s['RX']:.4f} RY={s['RY']:.4f}")
else:
    print("\n  No jumps detected! Raw data looks clean.")

# Show ranges per axis
if samples:
    print(f"\n  Axis ranges:")
    for axis in AXIS_NAMES:
        raw_key = f"{axis}_raw"
        vals = [s[raw_key] for s in samples]
        print(f"    {axis} raw range: [{min(vals):6d}, {max(vals):6d}]")

print("\nCleaning up...")
os.write(vendor_fd, gen_cmd_v1(0xB2, [0x00, 0x01, 0x02]))
time.sleep(0.1)
os.close(vendor_fd)
os.system("systemctl restart hhd@$(logname) 2>/dev/null; systemctl restart hhd 2>/dev/null")
print("Done.")
