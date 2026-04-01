# HHD to InputPlumber Transition Guide

This document captures the full research and migration plan for transitioning the OneXPlayer Apex from HHD (Handheld Daemon) to InputPlumber for input handling. Bazzite is deprecating HHD in favour of InputPlumber via the Open Gaming Collective (OGC).

## Background

The current Decky plugin patches HHD v4.1.5 Python files (`const.py`, `base.py`, `hid_v2.py`) to add Apex-specific input support. This approach dies when Bazzite removes HHD. Two external efforts replace it:

1. **InputPlumber PR #567** (by honjow) — Rust driver that reads vendor HID button events
2. **Kernel `hid-oxp` patches** (by pastaq / Derek J. Clark, Valve) — kernel driver that handles all firmware-level init, RGB, and resume recovery

Together these two pieces fully replace everything this plugin does for input.

## Architecture: Current (HHD) vs Future (InputPlumber + Kernel)

### Current Stack

```
back_paddle.py          → Sends B4 remap + B2 report mode activation
button_fix.py           → Patches HHD Python files on disk
home_button.py          → Monitors hidraw for home button combo
hhd_patches/patched/    → Modified const.py, base.py, hid_v2.py
    const.py            → APEX_BTN_MAPPINGS (KEY_G→mode, KEY_O→share, etc.)
    base.py             → Dual device routing (1A86:FE00 buttons, 1A2C:B001 RGB)
    hid_v2.py           → B2 packet parser + OxpHidrawV2Rgb class
```

### Future Stack

```
Kernel hid-oxp.ko       → B4 remap, B2 mode cycle, B3 vibration, RGB LED class
                           Auto re-applies on resume. Exposes sysfs attrs.
InputPlumber            → Reads B2 packets, routes to virtual gamepad
                           Device profile + capability map + OXP HID driver
Steam                   → Standard controller from InputPlumber's virtual gamepad
```

## InputPlumber PR #567

- **PR**: https://github.com/ShadowBlip/InputPlumber/pull/567
- **Author**: honjow
- **Credits**: This repo's PR #8 for HID protocol reverse engineering
- **Status**: Open (as of 2026-04-01)

### What it does

The PR adds an OXP HID Rust driver that **reads** button events. It sends no init commands — it is purely a consumer that expects the kernel driver to handle initialization.

**Files added/modified:**

| File | Purpose |
|------|---------|
| `50-onexplayer_apex.yaml` | Device profile — DMI match, combines Xbox gamepad + AT keyboard + HID 1a86:fe00 + IMU into composite device |
| `onexplayer_type8.yaml` | Capability map — paddle swap, button mappings for Turbo/KB/Orange |
| `src/drivers/oxp_hid/driver.rs` | Opens 1a86:fe00 interface 2, reads B2 packets, validates frames, debounces |
| `src/drivers/oxp_hid/event.rs` | Event types: M1, M2, Keyboard, Guide |
| `src/drivers/oxp_hid/hid_report.rs` | 64-byte input report struct with packed_struct |
| `src/input/source/hidraw/oxp_hid.rs` | Source device — translates OXP events to NativeEvents |

**Device profile** (`50-onexplayer_apex.yaml`):

```yaml
source_devices:
  - group: gamepad
    evdev:
      name: Microsoft X-Box 360 pad
      phys_path: usb-0000:65:00.4-1.3/input0
  - group: keyboard
    evdev:
      name: AT Translated Set 2 keyboard
      phys_path: isa0060/serio0/input0
  - group: keyboard
    evdev:
      name: "HID 1a86:fe00"
      phys_path: usb-0000:65:00.4-1.2/input0
  - group: gamepad
    hidraw:
      vendor_id: 0x1a86
      product_id: 0xfe00
      interface_num: 2
  - group: imu
    iio:
      name: "{i2c-BMI0160:00,bmi260}"
target_devices:
  - xbox-series
  - mouse
  - keyboard
capability_map_id: oxp8
```

**Capability map** (`onexplayer_type8.yaml`):

| Source | Target | Notes |
|--------|--------|-------|
| LeftPaddle1 | RightPaddle1 | Physical L/R swap correction for Apex |
| RightPaddle1 | LeftPaddle1 | Physical L/R swap correction for Apex |
| Ctrl+Alt+Meta | QuickAccess | Turbo button |
| Ctrl+Meta+O | Keyboard | KB button (short press) |
| Meta+G | Guide | Orange button (short press) |
| Meta+D | QuickAccess2 | Orange button (long press) |
| Meta+Sysrq | Screenshot | Turbo + Orange combo |
| RCtrl+RAlt+Delete | KeyF13 | KB + Orange combo |

**Critical design decision**: InputPlumber reads raw button IDs (byte[6]: 0x22=M1, 0x23=M2) from the B2 report, NOT the remapped keycode from B4. The B4 remap just ensures buttons produce independent HID events. The PR description confirms: *"our input parser reads the button's own identifier from the report, not the configured keycode."*

### What it does NOT do

- No B4 firmware remap commands
- No B2 report mode activation
- No B3 vibration init
- No RGB control
- No output capabilities (rumble, LED)

All of these are delegated to the kernel driver.

## Kernel `hid-oxp` Patches

Five patches from Derek J. Clark (pastaq, Valve) in `~/Downloads/`:

```
v2-0001-HID-hid-oxp-Add-OneXPlayer-configuration-driver.patch
v2-0002-HID-hid-oxp-Add-Second-Generation-RGB-Control.patch
v2-0003-HID-hid-oxp-Add-Second-Generation-Gamepad-Mode-Sw.patch
v2-0004-HID-hid-oxp-Add-Button-Mapping-Interface.patch
v2-0005-HID-hid-oxp-Add-Virbation-Intenstity-Attributes.patch
```

### Patch 1: Base driver + Gen1 RGB

- Creates `drivers/hid/hid-oxp.c`
- Binds to `1A2C:B001` (USB_VENDOR_ID_CRSC) — Gen1 usage page `0xFF01`
- LED multicolor class: `oxp:rgb:joystick_rings`
- 19 animated effects + monocolor with per-LED RGB intensity
- Sysfs attributes: `effect`, `effect_index`, `enabled`, `enabled_index`, `speed`, `speed_range`
- 200ms MCU delay between commands + 50ms debounced work queue for rapid writes
- Brightness: hardware accepts 0-4, driver scales to 0-100 for userspace

### Patch 2: Gen2 RGB + Hybrid MCU Detection

- Adds `1A86:FE00` (USB_VENDOR_ID_WCH) — Gen2 usage page `0xFF00`
- DMI-based hybrid MCU quirk for devices that have Gen1 RGB + Gen2 buttons:
  - OneXPlayer Apex (`ONE-NETBOOK` / `ONEXPLAYER APEX`)
  - OneXPlayer G1 AMD (`ONEXPLAYER G1 A`)
  - OneXPlayer G1 Intel (`ONEXPLAYER G1 i`)
- Hybrid devices: skip RGB setup on Gen2 interface (use Gen1 for RGB)
- Gen2 message framing: `[fid, 0x3F, 0x01, ...data..., 0x3F, fid]`

### Patch 3: Gamepad Mode Switch (B2 — replaces `back_paddle.py` report mode)

- `OXP_FID_GEN2_TOGGLE_MODE = 0xB2`
- Sysfs attribute: `gamepad_mode` — `xinput` (0x00) or `debug` (0x03)
- **On probe**: `oxp_mcu_init_fn` sends B2 with debug mode (0x03), then switches back to xinput. This is the same cycle our `back_paddle.py` does to activate report mode.
- **On resume**: detects MCU reset event (`data[3] == 0xFE` in B8 status packet), auto-triggers `oxp_mcu_init_fn` via delayed work queue. No manual per-boot init needed.

### Patch 4: Button Mapping (B4 — replaces `back_paddle.py` firmware remap)

- `OXP_FID_GEN2_KEY_STATE = 0xB4`
- Complete button table: all gamepad buttons (A/B/X/Y/LB/RB/LT/RT/Start/Select/L3/R3/DPad) + keyboard keys (F1-F24)
- Two-page mapping sent via 59-byte packets:
  - Page 1: A, B, X, Y, LB, RB, LT, RT, Start
  - Page 2: Select, L3, R3, DUp, DDown, DLeft, DRight, M1, M2
- **Default M1 = KEY_F15 (index 48), M2 = KEY_F16 (index 49)** — differs from our `back_paddle.py` which uses F14/F13, but this difference is irrelevant because InputPlumber reads raw button IDs, not keycodes.
- Per-button sysfs attributes: `button_a`, `button_b`, ..., `button_m1`, `button_m2`
- `reset_buttons` sysfs write attribute to restore defaults
- `button_mapping_options` lists all valid mapping targets
- Debounced 50ms delayed work queue for rapid writes
- **Re-applied on resume** via `oxp_mcu_init_fn`

### Patch 5: Vibration Intensity (B3)

- `OXP_FID_GEN2_RUMBLE_SET = 0xB3`
- Left/right independent intensity control, range 0-5
- Defaults to max (5) on probe
- Sysfs: `rumble_intensity_left`, `rumble_intensity_right`, `rumble_intensity_range`
- **Re-applied on resume** via `oxp_mcu_init_fn`

### Resume Recovery Flow

The `oxp_mcu_init_fn` work function handles all post-resume re-initialization:

```
MCU sends B8 status with byte3=0xFE (~6s after wake)
  → kernel detects in oxp_hid_raw_event_gen_2()
  → schedules oxp_mcu_init via mod_delayed_work (50ms)
  → oxp_mcu_init_fn runs:
      1. Reset button mapping (B4 page1 + page2)
      2. Cycle gamepad mode (B2 debug → xinput)
      3. Set rumble intensity (B3)
```

This eliminates the need for any userspace resume handling for input.

## Feature Coverage Matrix

| Feature | Current (HHD + Plugin) | Future (Kernel + InputPlumber) | Gap? |
|---------|----------------------|-------------------------------|------|
| Face buttons, sticks, triggers | HHD full intercept on vendor HID | Xbox gamepad evdev (standard) | No |
| Back paddles M1/M2 | `back_paddle.py` B4+B2 + HHD reads B2 | Kernel B4+B2 init + InputPlumber reads B2 | No |
| Home/Orange button | `home_button.py` monitors hidraw | InputPlumber maps Meta+G → Guide | No |
| KB button | HHD `APEX_BTN_MAPPINGS` KEY_O → share | InputPlumber maps Ctrl+Meta+O → Keyboard | No |
| Turbo/QAM button | HHD KEY_LEFTALT → keyboard → steam_qam | InputPlumber maps Ctrl+Alt+Meta → QuickAccess | No |
| Volume buttons | HHD captures from AT keyboard | AT keyboard in InputPlumber source devices | No |
| RGB control | `OxpHidrawV2Rgb` on 1A2C:B001 | Kernel LED class `oxp:rgb:joystick_rings` via sysfs | **UI needed** |
| Rumble | Xbox gamepad (no intercept) | Xbox gamepad (no intercept) | No |
| Rumble intensity | Not implemented | Kernel sysfs `rumble_intensity_left/right` (0-5) | **UI needed** |
| Button remapping | Not implemented | Kernel sysfs per-button attrs | **UI needed** |
| Resume recovery (input) | `back_paddle.py` re-run + HHD restart | Kernel auto-detects MCU reset, re-applies all | No (better) |
| Resume recovery (USB) | `xhci_recovery.py` rebinds PCI + restarts HHD | `xhci_recovery.py` rebinds PCI + restarts InputPlumber | Minor update |
| IMU/Gyro | HHD CombinedImu + HrtimerTrigger | InputPlumber BMI260 source device | No |
| Overlay trigger | `home_button.py` calls HHD REST API | Need new mechanism (D-Bus or evdev listener) | **Steam Loader** |

## What Stays vs Goes from Current Plugin

| Module | Status | Notes |
|--------|--------|-------|
| `xhci_recovery.py` | **STAYS** | Change `_restart_hhd()` → `systemctl restart inputplumber` |
| `oxpec_loader.py` | **STAYS** | EC sensor driver, independent of input daemon |
| `speaker_dsp.py` | **STAYS** | PipeWire filter chain, no input dependency |
| `sleep_fix.py` | **STAYS** | Kernel params (`mem_sleep_default=s2idle`, `amd_iommu=off`) |
| `resume_fix.py` | **STAYS** | xHCI rebind after sleep, update to restart InputPlumber |
| `button_fix.py` | **REMOVE** | Replaced entirely by InputPlumber device profile |
| `home_button.py` | **REMOVE** | InputPlumber maps Home → Guide natively |
| `back_paddle.py` | **REMOVE** | Kernel driver handles B4/B2/B3 on probe + resume |
| `hhd_patches/` | **REMOVE** | No more HHD file patching |

Frontend changes:
- Remove "Button and RGB Fix" toggle from `FixesSection.tsx`
- Remove paddle monitor status display
- Update "Recover Gamepad" button to restart InputPlumber instead of HHD
- Remove all references to HHD throughout UI

## Migration Path

### Constraint: HHD and InputPlumber CANNOT coexist

Both grab input devices exclusively (EVIOCGRAB). Running both simultaneously will cause one to fail to acquire devices.

### Phase 1: Build and test kernel module

```bash
# Apply all 5 patches to reconstruct final hid-oxp.c
# Similar process to building oxpec.ko for ba29 kernel
# Need kernel headers for 6.17.7-ba29.fc43.x86_64

# Build
make -C /lib/modules/$(uname -r)/build M=$(pwd) modules

# Load
sudo insmod hid-oxp.ko

# Verify — should bind to both devices
ls /sys/class/leds/oxp:rgb:joystick_rings/
# Check for gamepad_mode, button_m1, button_m2, rumble_intensity_* attrs
```

### Phase 2: Test with InputPlumber

```bash
# Stop HHD
sudo systemctl stop hhd.service hhd@srsholmes.service

# Load kernel module (handles all firmware init)
sudo insmod hid-oxp.ko

# Build and start InputPlumber from PR #567 branch
git clone https://github.com/ShadowBlip/InputPlumber.git
cd InputPlumber
git fetch origin pull/567/head:pr-567
git checkout pr-567
cargo build --release
sudo ./target/release/inputplumber

# Test in Steam Gaming Mode — see verification checklist below
```

### Phase 3: Full switchover (when Bazzite ships both)

1. Revert button fix in Decky plugin
2. Disable and mask HHD services:
   ```bash
   sudo systemctl disable --now hhd@srsholmes.service
   sudo systemctl disable --now hhd.service
   sudo systemctl mask hhd.service hhd@srsholmes.service
   ```
3. Ensure `hid-oxp.ko` loads at boot (modprobe config or built-in to Bazzite kernel)
4. Enable InputPlumber service
5. Strip Decky plugin: remove `button_fix.py`, `home_button.py`, `back_paddle.py`, `hhd_patches/`
6. Update `main.py`: replace `_restart_hhd()` with InputPlumber restart
7. Update frontend: remove HHD-specific UI elements

### Phase 4: Final plugin state

Plugin shrinks to: speaker DSP, sleep fixes, xHCI recovery, oxpec loader.

## Steam Loader Integration

The sibling project (`linux-gaming-plugin-manager` / Steam Loader) needs these input-related features:

| Feature | Implementation |
|---------|---------------|
| RGB control | Read/write sysfs `/sys/class/leds/oxp:rgb:joystick_rings/` — kernel exposes effect, brightness, color, speed, enabled |
| Rumble intensity | Write sysfs `rumble_intensity_left`/`rumble_intensity_right` (0-5) |
| Button remapping | Expose kernel's per-button sysfs attrs (`button_a` through `button_m2`) in UI |
| Overlay trigger | Implement "Pluggable Input Trigger Backend" (P2 TODO) — listen for InputPlumber D-Bus events or virtual gamepad evdev |

## Verification Checklist

After stopping HHD and starting kernel module + InputPlumber:

- [ ] Left stick, right stick — full range, no drift
- [ ] All face buttons (A/B/X/Y)
- [ ] Triggers and bumpers (LB/RB/LT/RT)
- [ ] D-pad (all 4 directions)
- [ ] Back paddles L4/R4 — correct positions (physical left = L4, physical right = R4)
- [ ] Home/Orange button opens Steam menu
- [ ] KB button functions (short press)
- [ ] Turbo button functions
- [ ] Volume up/down
- [ ] Rumble/vibration works
- [ ] Controller appears correctly in Steam Input (single device, no phantoms)
- [ ] RGB sysfs accessible: `cat /sys/class/leds/oxp:rgb:joystick_rings/brightness`
- [ ] Sleep/wake: paddles still work after resume (kernel auto re-inits)
- [ ] xHCI recovery works with InputPlumber restart
- [ ] Gyro/IMU (if used in games)

## References

- InputPlumber PR #567: https://github.com/ShadowBlip/InputPlumber/pull/567
- InputPlumber repo: https://github.com/ShadowBlip/InputPlumber
- Kernel patches: `~/Downloads/v2-000{1..5}-HID-hid-oxp-*.patch` (from pastaq)
- HID protocol docs: `docs/hid-reverse-engineering.md` (this repo)
- Back paddle implementation: `docs/back-paddle-findings.md` (this repo)
- OGC announcement: https://github.com/OpenGamingCollective
