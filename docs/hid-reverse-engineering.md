# HID Reverse Engineering — OneXPlayer Apex Vendor Protocol

A guide to how the OneXPlayer Apex's vendor HID protocol was reverse-engineered, how the full intercept approach works, and how to use the diagnostic scripts for similar devices.

## Hardware Overview

The OneXPlayer Apex exposes multiple USB HID devices:

| VID:PID | Name | Purpose |
|---------|------|---------|
| `045e:028e` | Microsoft X-Box 360 pad | Standard Xbox gamepad (handled by `xpad` kernel driver) |
| `1a86:fe00` | QinHeng Electronics | Vendor HID — special buttons, back paddles, full intercept |
| `1a2c:b001` | XFLY keyboard | Keyboard events (volume, Home/Turbo/KB combo keys) |

### Vendor Device (1a86:fe00) — hidraw interfaces

This single USB device creates **multiple hidraw interfaces**:

| Interface | Usage Page | Report Size | Purpose |
|-----------|-----------|-------------|---------|
| hidraw3 | Generic Desktop (0x01) | 8 bytes | Keyboard HID |
| hidraw4 | Generic Desktop (0x01) | Variable | Mouse/consumer control |
| **hidraw5** | **Vendor (0xFF00)** | **64 bytes** | **Vendor command/response channel** |

The vendor interface (usage page `0xFF00`) is what we need. HHD identifies it by scanning report descriptors for the `0x06 0x00 0xFF` prefix.

### evdev devices from 1a86:fe00

The vendor device also creates 4 evdev devices:

| Device | Type | Notes |
|--------|------|-------|
| event6 | Keyboard | Standard key events |
| event9 | Mouse | Relative axes |
| event10 | Consumer Control | Media keys — **grabbing this breaks dpad/sticks** |
| event11 | System Control | Power/sleep |

**Important**: Earlier attempts to grab the vendor device's evdev (via HHD's `d_kbd_1`) captured event10 (Consumer Control), which broke dpad and analog sticks. The fix was to use hidraw directly instead of evdev.

## Full Intercept Mode

### Discovery Process

1. **Initial assumption (wrong)**: L4/R4 back paddles are hardwired duplicates of B/Y
2. **Key insight**: The vendor HID device supports an "intercept" command that takes over ALL controller input
3. **Testing with `monitor-hidraw.py`**: Opened all hidraw devices, pressed buttons, found vendor reports on hidraw5
4. **Intercept command found**: `gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])` — silences the Xbox gamepad entirely and routes all input through vendor HID

### What happens when intercept is enabled

```
Before intercept:
  Xbox gamepad (045e:028e) → evdev → xpad → ABXY, sticks, triggers
  Vendor HID (1a86:fe00)   → nothing (silent unless polled)
  Back paddles              → mirrored as B/Y on Xbox gamepad

After intercept:
  Xbox gamepad (045e:028e) → SILENT (no events)
  Vendor HID (1a86:fe00)   → ALL input: buttons, sticks, triggers, dpad
  Back paddles              → separate L4/R4 codes (0x22/0x23)
```

### HID v1 Command Format

```python
def gen_cmd_v1(cid: int, cmd: list[int], idx: int = 0x01, size: int = 64):
    """Generate an HID v1 command packet with 0x3F framing."""
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])
```

| Command | Code | Effect |
|---------|------|--------|
| Enable intercept | `gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])` | Takes over entire controller |
| Disable intercept | `gen_cmd_v1(0xB2, [0x00, 0x01, 0x02])` | Xbox gamepad resumes |

**Timing**: A 4-second delay after device open is required before sending the intercept command (matches HHD's startup timing). Sending too early is silently ignored.

### Packet Types

All packets from the vendor device start with `0xB2` (command ID). Byte 3 indicates the type:

| Type | Byte[3] | Description | Min Length |
|------|---------|-------------|------------|
| `0x01` | Button event | Discrete press/release | 13 bytes |
| `0x02` | Gamepad state | Continuous analog data | 25 bytes |
| `0x03` | ACK response | Command acknowledgment | — |

### Button Event Format (type 0x01)

```
Byte[0]  = 0xB2 (command ID)
Byte[3]  = 0x01 (button event)
Byte[6]  = button code (see table below)
Byte[12] = 0x01 (pressed) or 0x02 (released)
```

### Complete Button Map

Discovered using `scripts/button-mapper.py` — an interactive tool that prompts for each button press:

| Code | Button | Code | Button |
|------|--------|------|--------|
| 0x01 | A | 0x09 | Start |
| 0x02 | B | 0x0A | Select |
| 0x03 | X | 0x0B | LS Click |
| 0x04 | Y | 0x0C | RS Click |
| 0x05 | LB | 0x0D | D-pad Up |
| 0x06 | RB | 0x0E | D-pad Down |
| 0x07 | LT (digital) | 0x0F | D-pad Left |
| 0x08 | RT (digital) | 0x10 | D-pad Right |
| 0x21 | Home | 0x22 | R4 (physical RIGHT paddle) |
| 0x24 | KB/QAM | 0x23 | L4 (physical LEFT paddle) |

**Physical swap**: HID code `0x22` is the **right** paddle, `0x23` is the **left** paddle. They are physically swapped relative to what you'd expect.

### Gamepad State Format (type 0x02)

Continuous packets at ~125 Hz containing analog stick and trigger data:

```
Byte[0]     = 0xB2 (command ID)
Byte[3]     = 0x02 (gamepad state)
Byte[15]    = RT analog (0-255)
Byte[16]    = LT analog (0-255)
Byte[17:19] = LX (signed 16-bit LE, -32768 to +32767)
Byte[19:21] = LY (signed 16-bit LE, inverted)
Byte[21:23] = RX (signed 16-bit LE, inverted)
Byte[23:25] = RY (signed 16-bit LE, inverted)
```

Python parsing:
```python
import struct

lt = cmd[16] / 255.0
rt = cmd[15] / 255.0
lx = struct.unpack_from("<h", cmd, 17)[0] / 32768.0
ly = -(struct.unpack_from("<h", cmd, 19)[0] / 32768.0)  # inverted
rx = struct.unpack_from("<h", cmd, 21)[0] / 32768.0
ry = -(struct.unpack_from("<h", cmd, 23)[0] / 32768.0)  # inverted
```

**Note on LT/RT byte positions**: They are swapped relative to convention — byte 15 is RT, byte 16 is LT.

### Stick Edge Case — Signed Overflow

At full stick deflection, the signed 16-bit value can wrap from +32767 to -32768 in a single sample. The HHD patch handles this:

```python
if prev_val is not None:
    delta = abs(value - prev_val)
    if delta > 1.5:
        # Wrap — clamp to the previous direction's extreme
        value = 1.0 if prev_val > 0 else -1.0
```

## HHD Integration (Patched Files)

Three HHD files are patched to add Apex support:

### 1. `const.py` — Device Registration

Adds the Apex to HHD's device table with `"apex": True` and `"protocol": "hid_v2"`. Also adds `APEX_BTN_MAPPINGS` for the keyboard device (Home = `KEY_G` instead of `KEY_D`).

### 2. `base.py` — Device Wiring

`find_vendor()` gains an `apex` parameter. When true, `OxpHidrawV2` is instantiated with `apex_v1=True`, which enables the full intercept code path. The vendor hidraw device uses `X1_MINI` VID/PID constants.

### 3. `hid_v2.py` — Full Intercept Implementation

- `OxpHidrawV2.__init__()`: New `apex_v1` flag, `prev_axes` dict, `dpad` state
- `open()`: Queues intercept-enable command
- `close()`: Sends intercept-disable command
- `consume()`: Skips RGB handling for Apex (no RGB support)
- `_produce_apex()`: New method — parses button events and gamepad state packets, emits HHD-compatible events
- `APEX_V1_BUTTONS`: Full button map for intercept mode
- `APEX_V1_DPAD`: D-pad codes emitted as `hat_x`/`hat_y` axis events

## Diagnostic Scripts

All scripts are in the `scripts/` directory.

### For device discovery

| Script | Purpose |
|--------|---------|
| `monitor-hidraw.py` | Monitor all hidraw devices — see raw HID reports from every device |
| `evtest.py` | Lightweight evdev event reader — `sudo python scripts/evtest.py /dev/input/eventN` |
| `monitor-inputs.py` | Monitor multiple evdev devices simultaneously |

### For protocol analysis

| Script | Purpose |
|--------|---------|
| `monitor-vendor-hid.py` | Full intercept monitor with byte-level diffs between consecutive packets |
| `monitor-intercept.py` | All-in-one: monitors both vendor HID and Xbox evdev during intercept |
| `test-no-intercept.py` | Verify behavior without intercept (negative test) |

### For mapping

| Script | Purpose |
|--------|---------|
| `button-mapper.py` | Interactive guided button mapper — prompts for each button, builds code table |
| `stick-diagnostic.py` | Comprehensive axis diagnostic — captures all stick/trigger data, statistical analysis |
| `stick-jump-detector.py` | Detects anomalous value jumps in analog sticks (edge case debugging) |

### Typical workflow for a new device

1. Run `monitor-hidraw.py` to find which hidraw device produces vendor reports
2. Run `test-no-intercept.py` to see what data arrives without commands
3. Run `button-mapper.py` to map all button codes
4. Run `stick-diagnostic.py` to map analog axis byte offsets
5. Run `monitor-intercept.py` to verify full integration
6. If stick issues appear, use `stick-jump-detector.py` to analyze edge cases

## Lessons Learned

1. **Don't assume hardware limitations** — The back paddles were NOT hardwired as B/Y. The firmware has a full intercept mode that was undocumented.

2. **evdev grabs can break things silently** — Grabbing the wrong evdev device (Consumer Control instead of keyboard) broke dpad and sticks without any error messages.

3. **Timing matters** — The 4-second init delay before sending intercept is critical. Without it, the command is silently dropped.

4. **Physical vs logical can be swapped** — HID codes 0x22/0x23 are swapped relative to physical L4/R4 positions. Always verify with physical testing.

5. **Signed integer overflow at stick extremes** — The controller sends raw signed 16-bit values that can wrap at full deflection. Must clamp, not trust the raw value.

6. **Y-axis inversion varies by axis** — LY, RX, and RY need inversion; LX does not. Discovered empirically with `stick-diagnostic.py`.

7. **Test on the actual hardware** — USB device numbers, hidraw numbers, and evdev numbers all change between boots. Scripts should auto-detect where possible.
