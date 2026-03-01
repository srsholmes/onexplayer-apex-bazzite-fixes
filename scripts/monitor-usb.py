#!/usr/bin/env python3
"""Monitor raw USB packets from Xbox 360 gamepad on bus 1."""

import sys

USBMON_PATH = "/sys/kernel/debug/usb/usbmon/1u"

def main():
    print(f"Reading raw USB from {USBMON_PATH}")
    print(f"Press back buttons, then face B, then face Y.")
    print(f"Press Ctrl+C to stop.\n")

    try:
        with open(USBMON_PATH, "r") as f:
            for line in f:
                line = line.strip()
                # Only show incoming data (Ci = control in, Bi = bulk in)
                # and only lines with actual data payload
                parts = line.split()
                if len(parts) < 5:
                    continue
                direction = parts[3]
                # 'Ci' or 'Bi' = data coming FROM device, 'Co'/'Bo' = to device
                if direction not in ("Ci", "Bi", "Ii"):
                    continue
                # Only show lines with hex data (status = 0)
                if len(parts) > 7 and parts[7] != "=":
                    # Check if there's data
                    has_data = False
                    for p in parts:
                        if p == "=":
                            has_data = True
                            break
                    if not has_data:
                        continue
                # Find data after '='
                try:
                    eq_idx = parts.index("=")
                    data = " ".join(parts[eq_idx + 1:])
                    if data:
                        print(f"IN: {data}")
                except ValueError:
                    pass
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
