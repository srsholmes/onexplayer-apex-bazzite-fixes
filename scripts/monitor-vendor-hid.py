#!/usr/bin/env python3
"""Debug d-pad in full intercept mode.

Shows ALL vendor HID data to find where d-pad is encoded.
Run as root with HHD stopped: sudo python3 test_dpad_debug.py
"""
import glob
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


VENDOR_DEV = find_vendor_hidraw()
if not VENDOR_DEV:
    print("ERROR: Could not find vendor hidraw device (1a86:fe00)")
    exit(1)

print(f"Vendor hidraw: {VENDOR_DEV}")

print("\nStopping HHD...")
os.system("systemctl stop hhd@$(logname) 2>/dev/null; systemctl stop hhd 2>/dev/null")
time.sleep(1)

vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)

# Full intercept
INTERCEPT_FULL = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
print(f"Sending FULL intercept: {INTERCEPT_FULL[:8].hex()}...")
os.write(vendor_fd, INTERCEPT_FULL)
time.sleep(0.2)

# Drain ACK
try:
    while True:
        data = os.read(vendor_fd, 64)
        print(f"  ACK: {data[:16].hex()}...")
except BlockingIOError:
    pass

BTN_NAMES = {
    0x01: "A", 0x02: "B", 0x03: "X", 0x04: "Y",
    0x05: "LB", 0x06: "RB", 0x07: "LT_dig", 0x08: "RT_dig",
    0x09: "Start", 0x0A: "Select",
    0x0B: "LSClick", 0x0C: "RSClick",
    0x0D: "DpadUp", 0x0E: "DpadDown", 0x0F: "DpadLeft", 0x10: "DpadRight",
    0x21: "HOME", 0x22: "R4", 0x23: "L4", 0x24: "KB/QAM",
}

prev_state = None
prev_state_full = None

print(f"\n=== D-PAD DEBUG MODE ===")
print(f"Press d-pad directions one at a time.")
print(f"Also try sticks/triggers to verify fixes.")
print(f"Watching for type 0x01 (buttons) and type 0x02 (state) changes.")
print(f"Ctrl+C to quit\n")

try:
    while True:
        readable, _, _ = select.select([vendor_fd], [], [], 1.0)

        if vendor_fd in readable:
            try:
                while True:
                    try:
                        data = os.read(vendor_fd, 64)
                    except BlockingIOError:
                        break

                    if len(data) < 4 or data[0] != 0xB2:
                        print(f"  [OTHER]   {data[:20].hex()}")
                        continue

                    pkt_type = data[3]

                    if pkt_type == 0x01 and len(data) >= 13:
                        btn = data[6]
                        state = data[12]
                        name = BTN_NAMES.get(btn, f"UNKNOWN_0x{btn:02x}")
                        st = "PRESS" if state == 1 else "RELEASE"
                        print(f"  [BUTTON]  {name:12s} {st}  (code=0x{btn:02x})")

                    elif pkt_type == 0x02:
                        # Show full state packet with byte indices
                        state_key = data[4:30]
                        if state_key != prev_state:
                            # Show what changed
                            changes = []
                            if prev_state_full is not None:
                                for i in range(min(len(data), len(prev_state_full))):
                                    if data[i] != prev_state_full[i]:
                                        changes.append(f"[{i}]:0x{prev_state_full[i]:02x}->0x{data[i]:02x}")

                            prev_state = state_key
                            prev_state_full = bytes(data)

                            # Parse known analog values
                            if len(data) >= 25:
                                b15 = data[15]
                                b16 = data[16]
                                lx = struct.unpack_from("<h", data, 17)[0]
                                ly = struct.unpack_from("<h", data, 19)[0]
                                rx = struct.unpack_from("<h", data, 21)[0]
                                ry = struct.unpack_from("<h", data, 23)[0]

                                print(f"  [STATE]   b15={b15:3d} b16={b16:3d}  "
                                      f"LX={lx:6d} LY={ly:6d}  RX={rx:6d} RY={ry:6d}")
                                # Show bytes 4-14 which might contain d-pad/buttons
                                early = " ".join(f"{data[i]:02x}" for i in range(4, 15))
                                print(f"            bytes[4:15]: {early}")
                                if changes:
                                    print(f"            changed: {' '.join(changes)}")

                    elif pkt_type == 0x03:
                        pass
                    else:
                        print(f"  [TYPE_{pkt_type:02x}] {data[:20].hex()}")
            except BlockingIOError:
                pass

except KeyboardInterrupt:
    print("\n\nSending intercept OFF and restarting HHD...")
    INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    os.write(vendor_fd, INTERCEPT_OFF)
    time.sleep(0.1)
    os.close(vendor_fd)
    os.system("systemctl restart hhd@$(logname) 2>/dev/null; systemctl restart hhd 2>/dev/null")
    print("Done.")
