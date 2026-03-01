# Back Paddle (L4/R4) Support — Findings & Implementation

## Discovery

The OXP Apex back paddles (L4/R4) are **NOT** hardwired duplicates of B/Y. They fire as separate events on the `1a86:fe00` vendor HID device — but ONLY after sending an HID v1 intercept enable command. Without this command, the firmware falls back to mirroring B/Y on the Xbox gamepad.

Previous assumption (wrong): "L4/R4 back paddles are hardwired as duplicate B/Y on the Xbox controller chip" — this was based on testing without the intercept command.

## Hardware Details

- **Device**: `1a86:fe00` (QinHeng Electronics) — exposes multiple hidraw interfaces
- **Interfaces**:
  - Keyboard interface (8-byte reports) — used by home button monitor
  - Mouse interface
  - **Vendor interface** (64-byte reports, usage page `0xFF00`) — this is the one we need
- **Identifying the vendor interface**: Report descriptor starts with `0x06 0x00 0xFF` (usage page 0xFF00)
  - Check `/sys/class/hidraw/hidrawN/device/report_descriptor` for each matching VID:PID

## HID v1 Protocol

### Command Format

```python
def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])
```

### Intercept Commands

| Command | Bytes | Effect |
|---------|-------|--------|
| Enable intercept | `gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])` | Back paddles send separate events instead of mirroring B/Y |
| Disable intercept | `gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])` | Back paddles revert to mirroring B/Y on Xbox gamepad |

### Button Report Format (64 bytes)

When intercept is enabled, back paddle presses arrive as 64-byte reports:

| Byte | Value | Meaning |
|------|-------|---------|
| 0 | `0xB2` | Command ID (button report) |
| 3 | `0x01` | Report type: button event (vs `0x03`=command ack, `0x02`=gamepad state) |
| 6 | `0x22` or `0x23` | Button code: `0x22`=L4, `0x23`=R4 |
| 12 | `0x01` or `0x02` | State: `0x01`=pressed, `0x02`=released |

### Filtering

- Only process reports where `byte[0] == 0xB2` (button report command ID)
- Skip reports where `byte[3] != 0x01` (only want button events, not acks or gamepad state)

## Implementation

### Module: `decky-plugin/py_modules/back_paddle.py`

- `BackPaddleMonitor` class — same async daemon pattern as `HomeButtonMonitor`
- Finds correct hidraw device by VID:PID + report descriptor check
- Opens device `O_RDWR | O_NONBLOCK`
- Sends `INTERCEPT_ON` on start, `INTERCEPT_OFF` on stop
- Creates uinput virtual device "OXP Apex Back Paddles" with:
  - `BTN_TRIGGER_HAPPY1` → L4
  - `BTN_TRIGGER_HAPPY2` → R4
- Steam Input recognizes these as back paddle buttons (like Steam Deck L4/R4)
- Users can remap per-game through Steam's controller configuration UI

### Lifecycle

- Auto-starts when button fix is applied
- Auto-stops when button fix is reverted
- Stops on plugin unload
- Retries device discovery every 5s if not found
- Reconnects on device read errors

### Files Changed

| File | Change |
|------|--------|
| `decky-plugin/py_modules/back_paddle.py` | **New** — back paddle monitor daemon |
| `decky-plugin/main.py` | Import, wire lifecycle, add `back_paddle_running` to status |
| `decky-plugin/src/index.tsx` | Show paddle status in UI, replaced hardware limitation note |

## Testing

1. Apply button fix in plugin → back paddle monitor starts
2. Press L4 → `BTN_TRIGGER_HAPPY1` fires (verify with `sudo evtest`, look for "OXP Apex Back Paddles")
3. Press R4 → `BTN_TRIGGER_HAPPY2` fires
4. In Steam Input controller settings, L4/R4 appear as remappable back paddle buttons
5. Revert button fix → monitor stops, paddles revert to B/Y mirroring
6. Check plugin logs for "Back paddle monitor started" / press events

## Quick Manual Test (without plugin)

```python
import os

def gen_cmd_v1(cid, cmd, idx=0x01, size=64):
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])

# Open the vendor hidraw device (find correct one first)
fd = os.open("/dev/hidraw5", os.O_RDWR | os.O_NONBLOCK)

# Enable intercept
os.write(fd, gen_cmd_v1(0xB2, [0x03, 0x01, 0x02]))

# Read reports in a loop
while True:
    try:
        data = os.read(fd, 64)
        if data[0] == 0xB2 and data[3] == 0x01:
            btn = "L4" if data[6] == 0x22 else "R4" if data[6] == 0x23 else f"0x{data[6]:02x}"
            state = "pressed" if data[12] == 0x01 else "released"
            print(f"{btn} {state}")
    except BlockingIOError:
        import time; time.sleep(0.02)

# Disable intercept when done
os.write(fd, gen_cmd_v1(0xB2, [0x00, 0x01, 0x02]))
os.close(fd)
```

Note: The actual hidraw number may vary — use `find_vendor_hidraw()` from `back_paddle.py` to find the correct one dynamically.
