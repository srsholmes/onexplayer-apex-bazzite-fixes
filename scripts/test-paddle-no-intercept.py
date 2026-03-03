#!/usr/bin/env python3
"""Diagnostic: Do L4/R4 back paddles report via vendor HID WITHOUT intercept?

This is the critical experiment. If button reports arrive without sending
the intercept command, we can read L4/R4 as separate buttons while leaving
sticks on the native Xbox gamepad driver — no latency, no sticking.

Run: sudo python3 scripts/test-paddle-no-intercept.py

What to do:
  1. Press L4 (left back paddle)
  2. Press R4 (right back paddle)
  3. Press A, B, Home, QAM for comparison
  4. Move sticks
  5. Watch output — any packets = we can read without intercept
"""

import glob
import os
import select
import signal
import sys
import time

# ── Device detection (same logic as back_paddle.py) ──

TARGET_VID = 0x1A86
TARGET_PID = 0xFE00


def find_vendor_hidraw():
    """Find the vendor HID interface (usage page 0xFF00) for 1a86:fe00."""
    candidates = []
    for sysfs_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sysfs_path, "device", "uevent")
        if not os.path.exists(uevent_path):
            continue
        with open(uevent_path) as f:
            content = f.read()
        for line in content.splitlines():
            if not line.startswith("HID_ID="):
                continue
            parts = line.split(":")
            if len(parts) < 3:
                continue
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
            if vid == TARGET_VID and pid == TARGET_PID:
                name = os.path.basename(sysfs_path)
                candidates.append((name, sysfs_path))
                break

    for name, sysfs_path in candidates:
        rd_path = os.path.join(sysfs_path, "device", "report_descriptor")
        if not os.path.exists(rd_path):
            continue
        try:
            with open(rd_path, "rb") as f:
                rd = f.read(3)
            if len(rd) >= 3 and rd[0] == 0x06 and rd[1] == 0x00 and rd[2] == 0xFF:
                dev_path = f"/dev/{name}"
                if os.path.exists(dev_path):
                    return dev_path
        except OSError:
            continue
    return None


# ── Known button codes for reference ──

BUTTON_NAMES = {
    0x01: "A", 0x02: "B", 0x03: "X", 0x04: "Y",
    0x05: "LB", 0x06: "RB", 0x07: "LT(dig)", 0x08: "RT(dig)",
    0x09: "Start", 0x0A: "Select", 0x0B: "LS", 0x0C: "RS",
    0x0D: "DpadUp", 0x0E: "DpadDown", 0x0F: "DpadLeft", 0x10: "DpadRight",
    0x21: "Home", 0x22: "R4(phys_right)", 0x23: "L4(phys_left)", 0x24: "KB/QAM",
}

PKT_TYPES = {0x01: "BUTTON", 0x02: "GAMEPAD_STATE", 0x03: "ACK"}


def main():
    dev_path = find_vendor_hidraw()
    if not dev_path:
        print("ERROR: Vendor HID device (1a86:fe00, usage 0xFF00) not found!")
        print("Are you running on the OneXPlayer Apex?")
        sys.exit(1)

    print(f"Found vendor HID: {dev_path}")

    # Stop HHD so it doesn't grab the device
    print("Stopping HHD to get exclusive access...")
    os.system("systemctl stop hhd@$(whoami) 2>/dev/null; systemctl stop hhd 2>/dev/null")
    time.sleep(1)

    fd = os.open(dev_path, os.O_RDWR | os.O_NONBLOCK)

    # Send explicit "intercept OFF" — the device may have 3 states:
    #   default (silent), non-intercept (reports Home/QAM/L4/R4), full intercept
    # HHD's gen_intercept(False) uses gen_cmd format (0xFF framing):
    def gen_cmd(cid, cmd, size=64):
        c = bytes(cmd)
        base = bytes([cid, 0xFF, *c])
        return base + bytes([0] * (size - len(base)))

    # Also try v1 framing version
    def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
        base = bytes([cid, 0x3F, idx] + cmd)
        padding = bytes([0] * (size - len(base) - 2))
        return base + padding + bytes([0x3F, cid])

    intercept_off_v2 = gen_cmd(0xB2, [0x00, 0x01, 0x02])
    intercept_off_v1 = gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])

    print(f"\nOpened {dev_path} — sending explicit INTERCEPT OFF commands")
    os.write(fd, intercept_off_v2)
    time.sleep(0.05)
    os.write(fd, intercept_off_v1)
    time.sleep(0.05)
    print("Sent both v1 and v2 intercept-off commands")
    print("=" * 60)
    print("Press Home, QAM, L4, R4. Any output = non-intercept reporting works!")
    print("Waiting 30 seconds... Ctrl+C to stop early.")
    print("=" * 60)

    packet_count = 0
    start = time.time()
    timeout = 30

    def cleanup(*_):
        print(f"\n\nTotal packets received: {packet_count}")
        if packet_count == 0:
            print("RESULT: NO data without intercept — L4/R4 require intercept mode.")
            print("        Will need fallback approach (direct uinput relay).")
        else:
            print("RESULT: Data received WITHOUT intercept!")
            print("        Back paddles can work without killing Xbox gamepad.")
        os.close(fd)
        print("\nRestarting HHD...")
        os.system("systemctl restart hhd@$(whoami) 2>/dev/null; systemctl restart hhd 2>/dev/null")
        print("Done.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)

    try:
        while time.time() - start < timeout:
            ready = select.select([fd], [], [], 0.1)[0]
            if not ready:
                elapsed = int(time.time() - start)
                # Print a dot every 5 seconds to show we're alive
                if elapsed > 0 and elapsed % 5 == 0:
                    remaining = timeout - elapsed
                    sys.stdout.write(f"\r  [{elapsed}s] No data yet... ({remaining}s remaining)")
                    sys.stdout.flush()
                continue

            try:
                data = os.read(fd, 64)
            except BlockingIOError:
                continue

            packet_count += 1

            if len(data) < 4:
                print(f"  #{packet_count}: short packet ({len(data)} bytes): {data.hex()}")
                continue

            cid = data[0]
            pkt_type = data[3]
            pkt_name = PKT_TYPES.get(pkt_type, f"UNKNOWN(0x{pkt_type:02x})")

            if pkt_type == 0x01 and len(data) >= 13:
                # Button event
                btn_code = data[6]
                state = data[12]
                btn_name = BUTTON_NAMES.get(btn_code, f"UNKNOWN(0x{btn_code:02x})")
                state_str = "PRESSED" if state == 0x01 else "RELEASED" if state == 0x02 else f"0x{state:02x}"
                print(f"\n  #{packet_count}: BUTTON  {btn_name} = {state_str}")
                print(f"          raw: {data[:16].hex()}")

            elif pkt_type == 0x02 and len(data) >= 25:
                # Gamepad state
                import struct
                lt = data[16]
                rt = data[15]
                lx = struct.unpack_from("<h", data, 17)[0]
                ly = struct.unpack_from("<h", data, 19)[0]
                rx = struct.unpack_from("<h", data, 21)[0]
                ry = struct.unpack_from("<h", data, 23)[0]
                print(f"\n  #{packet_count}: STATE   LX={lx:6d} LY={ly:6d} RX={rx:6d} RY={ry:6d} LT={lt:3d} RT={rt:3d}")

            elif pkt_type == 0x03:
                print(f"\n  #{packet_count}: ACK     raw: {data[:16].hex()}")

            else:
                nonzero = [(i, f"0x{b:02x}") for i, b in enumerate(data[:32]) if b != 0]
                print(f"\n  #{packet_count}: {pkt_name} ({len(data)}b) nonzero: {nonzero}")
                print(f"          raw: {data[:32].hex()}")

    except OSError as e:
        print(f"\nDevice error: {e}")

    cleanup()


if __name__ == "__main__":
    main()
