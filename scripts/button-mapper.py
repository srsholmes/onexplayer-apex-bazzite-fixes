#!/usr/bin/env python3
"""Interactive button mapper — press each button when prompted.

Maps vendor HID button codes (0x01-0x10) to standard gamepad buttons.
Run as root: sudo python3 test_button_map.py
"""
import os
import time

VENDOR_DEV = "/dev/hidraw5"

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])

print("Stopping HHD...")
os.system("systemctl stop hhd")
time.sleep(1)

fd = os.open(VENDOR_DEV, os.O_RDWR)
INTERCEPT_ON = gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])
os.write(fd, INTERCEPT_ON)
time.sleep(0.2)

# Drain ACK
os.set_blocking(fd, False)
try:
    while True:
        os.read(fd, 64)
except BlockingIOError:
    pass
os.set_blocking(fd, True)

BUTTONS = [
    "A", "B", "X", "Y",
    "LB (left bumper)", "RB (right bumper)",
    "LT (left trigger click)", "RT (right trigger click)",
    "LS (left stick click)", "RS (right stick click)",
    "D-pad UP", "D-pad DOWN", "D-pad LEFT", "D-pad RIGHT",
    "START/Menu", "SELECT/View/Back",
]

mapping = {}

print("\n=== BUTTON MAPPING ===")
print("Press each button ONCE when prompted.\n")

for btn_name in BUTTONS:
    input(f"Press {btn_name} then hit Enter here: ")

    # Read the button press event
    os.set_blocking(fd, False)
    codes = set()
    time.sleep(0.05)
    try:
        while True:
            data = os.read(fd, 64)
            if data[0] == 0xB2 and len(data) >= 13 and data[3] == 0x01:
                codes.add(data[6])
    except BlockingIOError:
        pass

    if codes:
        code_str = ", ".join(f"0x{c:02x}" for c in sorted(codes))
        print(f"  -> {btn_name} = {code_str}")
        for c in codes:
            mapping[c] = btn_name
    else:
        print(f"  -> No button event detected!")

print("\n\n=== COMPLETE MAPPING ===")
for code in sorted(mapping.keys()):
    print(f"  0x{code:02x} -> {mapping[code]}")

print("\nSending intercept OFF and restarting HHD...")
INTERCEPT_OFF = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])
os.set_blocking(fd, True)
os.write(fd, INTERCEPT_OFF)
time.sleep(0.1)
os.close(fd)
os.system("systemctl restart hhd")
print("Done.")
