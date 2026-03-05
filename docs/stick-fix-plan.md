# Fix Stick Sticking in Intercept Mode

## Context

The direct uinput relay (`scripts/test-direct-uinput-relay.py`) proved that sticks feel native when bypassing HHD's pipeline. The relay reads vendor HID packets and writes raw int16 values directly to uinput — no filtering, no batching delays, no float conversions. Sticks were smooth with no sticking.

Meanwhile, HHD's pipeline with `_produce_apex()` has sticking/lagging. After deep analysis of both codepaths, there are three specific differences that likely cause the sticking:

### Root Cause Analysis

| Factor | Relay (works) | HHD pipeline (sticks) |
|--------|--------------|----------------------|
| **Delta filter** | None — every value written | `delta < 0.002` skips small changes (~65 raw units) |
| **Overflow wrap filter** | None — only boundary correction | `delta > 1.5` clamps value to ±1.0 (could misfire on fast flicks) |
| **Poll rate** | ~1000Hz (1ms select timeout) | 125Hz max (8ms sleep between iterations) |
| **Value format** | Raw int16 direct to uinput | float [-1.0,1.0] → dict → multiplexer → UInputDevice denormalize |

The **delta filter** (line 387 of hid_v2.py) is the most suspicious. It suppresses axis events when the change is < 0.002 of full range. During continuous slow movement or small corrections near extremes, legitimate position changes get swallowed. The relay has zero filtering and works perfectly.

The **overflow wrap filter** (line 383) is redundant — the boundary overflow corrections at lines 351-368 already handle the real overflow cases (exact values ±32768/32767). The secondary delta > 1.5 check could misclassify fast legitimate flicks as overflow and clamp them.

The **125Hz cap** means the loop sleeps 8ms between iterations even when data is available faster. The relay processes at the vendor HID's native rate.

## Approach: Remove Filters + Increase Poll Rate

All changes are in files we already patch. No new daemons, no new architecture.

### Step 1: Strip the delta filter and overflow wrap from `_produce_apex()`

**File**: `decky-plugin/py_modules/hhd_patches/patched/hid_v2.py`

In `_produce_apex()`, the axis event loop (lines 379-396) currently:
```python
for code, value in axes.items():
    prev_val = self.prev_axes.get(code)
    if prev_val is not None:
        delta = abs(value - prev_val)
        if delta > 1.5:                    # ← REMOVE (overflow wrap filter)
            value = 1.0 if prev_val > 0 else -1.0
        elif delta < 0.002:                # ← REMOVE (delta filter)
            continue
    evs.append({"type": "axis", "code": code, "value": value})
    self.prev_axes[code] = value
```

Change to emit every value unconditionally (keep boundary overflow correction above, remove delta/wrap filters):
```python
for code, value in axes.items():
    evs.append({"type": "axis", "code": code, "value": value})
```

Remove `self.prev_axes` usage for stick/trigger axes entirely (still needed for dpad hat_x/hat_y dedup).

### Step 2: Increase poll rate for Apex intercept mode

**File**: `decky-plugin/py_modules/hhd_patches/patched/base.py`

In `controller_loop()`, after line 731:
```python
REPORT_FREQ_MAX = 125
```

Add Apex intercept override:
```python
if dconf.get("apex_intercept", False):
    REPORT_FREQ_MAX = 500  # 2ms — match native vendor HID rate
```

This reduces the minimum loop time from 8ms to 2ms when intercept is active (all input via vendor HID, so we need the faster polling).

### Step 3: Update patch version and hashes

**File**: `decky-plugin/py_modules/hhd_patches/patched/hid_v2.py`
- Update `_PATCH_VERSION` to `"apex-stick-v8-no-filter"`

**File**: `decky-plugin/py_modules/button_fix.py`
- Update `_const_patched_hashes()` / `_hid_v2_patched_hash()` etc. to include new SHA256 hashes for modified files

## Files to Modify

| File | Change |
|------|--------|
| `decky-plugin/py_modules/hhd_patches/patched/hid_v2.py` | Remove delta filter + overflow wrap from axis loop, update patch version |
| `decky-plugin/py_modules/hhd_patches/patched/base.py` | Increase REPORT_FREQ_MAX to 500 when apex intercept active |
| `decky-plugin/py_modules/button_fix.py` | Update SHA256 hashes for modified patched files |

## Verification

1. `cd decky-plugin && bun run deploy` — build and package
2. Install plugin, apply button fix, enable back paddle support (intercept mode)
3. **Stick test**: Move both sticks continuously — should feel identical to when intercept is OFF
4. **Extreme test**: Push sticks to full deflection and release — should snap back to center without sticking
5. **Fast flick test**: Rapid flicks in all directions — no momentary sticking or wrong-direction jumps
6. **Back paddle test**: L4/R4 still register as separate buttons
7. **Home/QAM test**: Still work via keyboard evdev path
8. **Non-intercept mode test**: Toggle back paddle support OFF — sticks still work normally via native Xbox gamepad

## Fallback

If removing filters + faster polling doesn't fully fix sticking, the next step is the standalone relay approach: integrate `test-direct-uinput-relay.py` into `back_paddle.py` as a systemd service that replaces HHD's controller handling while keeping HHD alive for TDP/RGB. This would require solving Steam's virtual gamepad recognition (VID/PID matching, proper capabilities).

## Key Reference: HHD Pipeline Deep Dive

### Event flow in HHD's controller_loop (base.py):
```
select.select(fds, timeout=40ms)
  → d_hidraw_v2.produce(r)  [calls _produce_apex()]
  → d_xinput.produce(r)     [Xbox gamepad - silent during intercept]
  → d_kbd_1.produce(r)      [keyboard evdev - Home/QAM as KEY_G/KEY_O]
  → multiplexer.process(evs) [trigger/dpad conversion, button mapping]
  → d_outs.consume(evs)     [UInputDevice writes to virtual controller]
  → sleep(max(0, 8ms - elapsed))
```

### UInputDevice.consume() behavior:
- Iterates events in **reverse order**
- **Deduplicates**: if same (type, code) appears twice, only the last value gets written
- Single `syn()` at end of batch
- No additional filtering on axis values

### Multiplexer.process() behavior:
- **No delta filtering** on axis values
- Converts analog triggers → discrete buttons (additive, doesn't remove axis)
- Converts dpad axes → buttons
- Button remapping (Home/QAM/share)
- All axis events pass through unchanged

### Key constants:
- Vendor HID: `1a86:fe00`, usage page `0xFF00` (hidraw5)
- Xbox gamepad: `045e:028e` (event16)
- Keyboard evdev: `1a86:fe00` event6 (KEY_G=Home, KEY_O=QAM)
- HHD virtual controller: VID `0x5335`, PID `0x01`, "Handheld Daemon Controller"
