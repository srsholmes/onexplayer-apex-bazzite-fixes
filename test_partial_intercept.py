#!/usr/bin/env python3
"""Test full intercept with detailed gamepad-state decoding.

Sends full intercept [0x03, 0x01, 0x02] and decodes ALL vendor packets:
- type 0x01: button press/release events
- type 0x02: gamepad state (sticks, triggers, dpad, face buttons)
- type 0x03: ACK responses

Run as root with HHD stopped: sudo python3 test_partial_intercept.py
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
    """Auto-detect the 64-byte vendor hidraw (1a86:fe00, usage page 0xFF00)."""
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


def find_xbox_evdev():
    """Auto-detect the Xbox 360 gamepad evdev (045e:028e)."""
    for sysfs_path in sorted(glob.glob("/sys/class/input/event*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        for line in content.splitlines():
            if line.startswith("PRODUCT="):
                parts = line.split("=")[1].split("/")
                if len(parts) >= 3:
                    vid = int(parts[1], 16)
                    pid = int(parts[2], 16)
                    if vid == 0x045E and pid == 0x028E:
                        name = os.path.basename(sysfs_path)
                        return f"/dev/input/{name}"
    return None


# Auto-detect devices
VENDOR_DEV = find_vendor_hidraw()
XBOX_DEV = find_xbox_evdev()

if not VENDOR_DEV:
    print("ERROR: Could not find vendor hidraw device (1a86:fe00)")
    exit(1)
if not XBOX_DEV:
    print("ERROR: Could not find Xbox gamepad evdev (045e:028e)")
    exit(1)

print(f"Vendor hidraw: {VENDOR_DEV}")
print(f"Xbox gamepad:  {XBOX_DEV}")

print("\nStopping HHD...")
os.system("systemctl stop hhd@$(logname) 2>/dev/null; systemctl stop hhd 2>/dev/null")
time.sleep(1)

vendor_fd = os.open(VENDOR_DEV, os.O_RDWR | os.O_NONBLOCK)
xbox_fd = os.open(XBOX_DEV, os.O_RDONLY | os.O_NONBLOCK)

# Full intercept
INTERCEPT_FULL = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
print(f"\nSending FULL intercept: {INTERCEPT_FULL[:8].hex()}...")
os.write(vendor_fd, INTERCEPT_FULL)
time.sleep(0.2)

# Drain ACK
try:
    while True:
        data = os.read(vendor_fd, 64)
        print(f"  ACK: {data[:16].hex()}...")
except BlockingIOError:
    pass

INPUT_EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(INPUT_EVENT_FMT)

BTN_NAMES = {
    0x01: "A", 0x02: "B", 0x03: "X", 0x04: "Y",
    0x05: "LB?", 0x06: "RB?",
    0x07: "LB", 0x08: "RB",
    0x09: "Start", 0x0A: "Select",
    0x0B: "LSClick?", 0x0C: "RSClick?",
    0x0D: "DpadUp", 0x0E: "DpadDown", 0x0F: "DpadLeft", 0x10: "DpadRight",
    0x21: "HOME", 0x22: "L4", 0x23: "R4", 0x24: "KB/QAM",
}

# Track previous gamepad state to only print changes
prev_state = None

print(f"\nFull intercept ON. Test everything!")
print(f"  Sticks, triggers, ABXY, dpad, LB/RB, Start/Select, L4/R4, Home, KB")
print(f"  Type 0x02 packets = gamepad state (sticks/triggers)")
print(f"\nCtrl+C to quit and clean up\n")

try:
    while True:
        readable, _, _ = select.select([vendor_fd, xbox_fd], [], [], 1.0)

        if vendor_fd in readable:
            try:
                while True:
                    try:
                        data = os.read(vendor_fd, 64)
                    except BlockingIOError:
                        break

                    if len(data) < 13:
                        continue

                    if data[0] != 0xB2:
                        print(f"  [VENDOR]  non-B2: cid=0x{data[0]:02x} {data[:20].hex()}")
                        continue

                    pkt_type = data[3]

                    if pkt_type == 0x01:  # Button event
                        btn = data[6]
                        state = data[12]
                        name = BTN_NAMES.get(btn, f"btn_0x{btn:02x}")
                        st = "PRESS" if state == 1 else "RELEASE"
                        print(f"  [BUTTON]  {name} {st}  (code=0x{btn:02x}, raw[12]=0x{state:02x})")

                    elif pkt_type == 0x02:  # Gamepad state
                        # Dump the full packet so we can figure out the layout
                        # Only print when state changes
                        state_bytes = data[4:30]  # likely the interesting part
                        if state_bytes != prev_state:
                            prev_state = state_bytes
                            # Print as hex with byte offsets
                            hex_parts = []
                            for i, b in enumerate(data[:32]):
                                hex_parts.append(f"{b:02x}")
                            print(f"  [STATE]   {' '.join(hex_parts)}")
                            # Also print byte-by-byte for the likely analog section
                            print(f"            bytes[4:20]: {' '.join(f'{data[i]:3d}' for i in range(4, 20))}")

                    elif pkt_type == 0x03:  # ACK
                        pass
                    else:
                        print(f"  [VENDOR]  unknown type=0x{pkt_type:02x}: {data[:20].hex()}")
            except BlockingIOError:
                pass

        if xbox_fd in readable:
            try:
                while True:
                    raw = os.read(xbox_fd, EVENT_SIZE)
                    if len(raw) < EVENT_SIZE:
                        break
                    sec, usec, ev_type, code, value = struct.unpack(INPUT_EVENT_FMT, raw)
                    if ev_type == 0:
                        continue
                    print(f"  [XBOX]    type={ev_type} code=0x{code:04x} value={value}")
            except BlockingIOError:
                pass

except KeyboardInterrupt:
    print("\n\nSending intercept OFF and restarting HHD...")
    INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
    os.write(vendor_fd, INTERCEPT_OFF)
    time.sleep(0.1)
    os.close(vendor_fd)
    os.close(xbox_fd)
    os.system("systemctl restart hhd@$(logname) 2>/dev/null; systemctl restart hhd 2>/dev/null")
    print("Done.")
