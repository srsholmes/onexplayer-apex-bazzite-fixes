# HHD to InputPlumber Transition Guide

This document captures the full research and migration plan for transitioning the OneXPlayer Apex from HHD (Handheld Daemon) to InputPlumber for input handling. Bazzite is deprecating HHD in favour of InputPlumber via the Open Gaming Collective (OGC).

**Last updated:** 2026-04-13
**Kernel:** 6.17.7-ba29.fc43.x86_64
**hid-oxp version:** v3 (from pastaq/7.1/hid/hid-oxp)

## Quick Start: Testing These Changes

> These instructions assume you are on a OneXPlayer Apex running Bazzite with kernel 6.17.7-ba29.

### Prerequisites

- This repo cloned to your device
- Root access (`sudo`)
- For building from source: `kernel-devel` package matching your kernel, Rust toolchain (`cargo`), `libiio-devel`, `systemd-devel`
- A pre-built `hid-oxp.ko` is included for ba29 — no build needed for that kernel

### Option A: Test kernel module only (no InputPlumber switch)

This loads `hid-oxp.ko` alongside your existing HHD setup to verify the kernel driver works. HHD continues handling input.

```bash
cd /var/home/srsholmes/Work/onexplayer-apex-bazzite-fixes

# Load the pre-built module
sudo insmod kernel-patches/hid-oxp/6.17.7-ba29.fc43.x86_64/hid-oxp.ko

# Run the automated test suite
sudo ./scripts/test-hid-oxp-v3.sh load    # Verify module binds to devices
sudo ./scripts/test-hid-oxp-v3.sh led     # Test RGB LED sysfs
sudo ./scripts/test-hid-oxp-v3.sh buttons # Test button remapping sysfs
sudo ./scripts/test-hid-oxp-v3.sh gamepad # Test gamepad mode switching
sudo ./scripts/test-hid-oxp-v3.sh rumble  # Test rumble intensity
sudo ./scripts/test-hid-oxp-v3.sh info    # Show device info

# Or run all tests interactively
sudo ./scripts/test-hid-oxp-v3.sh interactive
```

**Known issue:** `rmmod hid-oxp` causes a kernel oops because v3 doesn't cancel delayed work items on remove. Reported to pastaq. **Do not unload the module** — reboot instead if you need to remove it.

### Option B: Full migration (HHD → InputPlumber)

This stops HHD, loads `hid-oxp.ko`, builds InputPlumber from PR #567, and switches input handling completely. **Reversible** via the rollback script.

```bash
cd /var/home/srsholmes/Work/onexplayer-apex-bazzite-fixes

# On Bazzite, unlock the filesystem for build deps
sudo ostree admin unlock --hotfix

# Run the migration (builds everything, stops HHD, starts InputPlumber)
sudo ./scripts/migrate-to-inputplumber.sh

# The script will:
# 1. Build or use pre-built hid-oxp.ko
# 2. Install module + create boot service
# 3. Clone and build InputPlumber from PR #567
# 4. Stop and mask all HHD services
# 5. Start InputPlumber
# 6. Run verification checks

# Then run the verification checklist below in Steam Gaming Mode
```

### Rolling back to HHD

```bash
# Undo everything — stops InputPlumber, unloads hid-oxp, restarts HHD
sudo ./scripts/rollback-to-hhd.sh

# Note: you may need to re-apply the Decky plugin button fix after rollback
```

### Building from source (different kernel)

If you're on a kernel other than 6.17.7-ba29, build the module yourself:

```bash
# Install kernel headers (Bazzite)
sudo ostree admin unlock --hotfix
sudo dnf install kernel-devel-$(uname -r)

# Build the module
./scripts/build-hid-oxp.sh

# Output: kernel-patches/hid-oxp/$(uname -r)/hid-oxp.ko
```

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

## Kernel `hid-oxp` Driver

**Source:** [pastaq/linux — 7.1/hid/hid-oxp](https://github.com/pastaq/linux/tree/pastaq/7.1/hid/hid-oxp) (v3)
**Author:** Derek J. Clark (pastaq, Valve)
**Copyright:** Valve Corporation

The combined driver source is at `kernel-patches/hid-oxp/build/hid-oxp.c` (1579 lines). A pre-built `.ko` for ba29 is at `kernel-patches/hid-oxp/6.17.7-ba29.fc43.x86_64/hid-oxp.ko`. The original v2 patch series is preserved alongside for reference:

```
kernel-patches/hid-oxp/
├── build/
│   ├── hid-oxp.c          ← Combined v3 source (standalone, builds out-of-tree)
│   └── Makefile
├── 6.17.7-ba29.fc43.x86_64/
│   └── hid-oxp.ko         ← Pre-built module for ba29
├── v2-0001-*.patch         ← Original patch series (reference only)
├── v2-0002-*.patch
├── v2-0003-*.patch
├── v2-0004-*.patch
└── v2-0005-*.patch
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
- Single intensity control (v3 simplified from v2's separate left/right), range 0-5
- Defaults to max (5) on probe
- Sysfs: `rumble_intensity`, `rumble_intensity_range`
- **Re-applied on resume** via `oxp_mcu_init_fn`
- Rumble also re-applied after gamepad mode switch (v3 fix)

### v3 Changes from v2

- `oxp_reset_buttons()` separated from `oxp_set_buttons()` — reset is now a void function
- Removed dead `sysfs_match_string` call in `gamepad_mode_store`
- Probe now resets buttons and sets xinput mode on init
- Rumble simplified to single `rumble_intensity` attribute (was `rumble_intensity_left`/`rumble_intensity_right`)
- Rumble re-applied after mode switch

### Known v3 Bug

`oxp_hid_remove()` does not cancel delayed work items (`oxp_rgb_queue`, `oxp_mcu_init`), which causes a **kernel oops on `rmmod`**. This has been reported to pastaq. **Workaround:** reboot instead of unloading the module.

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
| Rumble intensity | Not implemented | Kernel sysfs `rumble_intensity` (0-5) | **UI needed** |
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

### Scripts

All migration and testing is automated via scripts in `scripts/`:

| Script | Purpose | Run as |
|--------|---------|--------|
| `build-hid-oxp.sh` | Build `hid-oxp.ko` for current kernel | user |
| `build-inputplumber.sh` | Clone PR #567 + cargo build | user |
| `migrate-to-inputplumber.sh` | Full migration (builds, installs, switches services) | root |
| `rollback-to-hhd.sh` | Full rollback (stops InputPlumber, restarts HHD) | root |
| `test-hid-oxp-v3.sh` | Automated kernel driver test suite (8 modes) | root |

### Phase 1: Test kernel module (non-destructive)

Load `hid-oxp.ko` alongside HHD to verify sysfs interfaces. This does NOT disrupt input — HHD continues handling the gamepad.

```bash
# Use pre-built module for ba29, or build for your kernel
sudo insmod kernel-patches/hid-oxp/6.17.7-ba29.fc43.x86_64/hid-oxp.ko

# Verify binding
ls /sys/class/leds/oxp:rgb:joystick_rings/
# Check for gamepad_mode, button_m1, button_m2, rumble_intensity attrs

# Run automated tests
sudo ./scripts/test-hid-oxp-v3.sh load
sudo ./scripts/test-hid-oxp-v3.sh led
sudo ./scripts/test-hid-oxp-v3.sh buttons
sudo ./scripts/test-hid-oxp-v3.sh gamepad
sudo ./scripts/test-hid-oxp-v3.sh rumble
```

### Phase 2: Full migration

```bash
# One command does everything:
sudo ./scripts/migrate-to-inputplumber.sh

# What it does:
# 1. Builds/installs hid-oxp.ko + creates boot service
# 2. Builds InputPlumber from PR #567 (clones to vendor/InputPlumber/)
# 3. Stops and masks all HHD services
# 4. Installs and starts InputPlumber
# 5. Runs verification checks

# Test in Steam Gaming Mode — see verification checklist below
```

### Phase 3: Rollback (if needed)

```bash
sudo ./scripts/rollback-to-hhd.sh

# Cleans up everything: stops InputPlumber, unloads hid-oxp,
# deletes installed files, unmasks and restarts HHD
# Note: Decky plugin button fix may need re-application
```

### Phase 4: Final plugin state (when Bazzite ships both natively)

When Bazzite includes `hid-oxp` in the kernel and ships InputPlumber by default, the plugin shrinks to: speaker DSP, sleep fixes, xHCI recovery, oxpec loader. At that point:

1. Remove `button_fix.py`, `home_button.py`, `back_paddle.py`, `hhd_patches/`
2. Update `main.py`: replace `_restart_hhd()` with InputPlumber restart
3. Remove HHD-specific UI elements from frontend

## Steam Loader Integration

The sibling project (`linux-gaming-plugin-manager` / Steam Loader) needs these input-related features:

| Feature | Implementation |
|---------|---------------|
| RGB control | Read/write sysfs `/sys/class/leds/oxp:rgb:joystick_rings/` — kernel exposes effect, brightness, color, speed, enabled |
| Rumble intensity | Write sysfs `rumble_intensity` (0-5) |
| Button remapping | Expose kernel's per-button sysfs attrs (`button_a` through `button_m2`) in UI |
| Overlay trigger | Implement "Pluggable Input Trigger Backend" (P2 TODO) — listen for InputPlumber D-Bus events or virtual gamepad evdev |

## Verification Checklist

### Automated (via test script)

Run `sudo ./scripts/test-hid-oxp-v3.sh <mode>` for each:

- [ ] `load` — Module binds to Gen1 (1A2C:B001) and Gen2 (1A86:FE00) devices
- [ ] `led` — RGB sysfs: brightness, effects, colors, speed all read/write
- [ ] `buttons` — All 20 button sysfs attrs readable, remapping works, reset works
- [ ] `gamepad` — Mode switching between `debug` and `xinput`
- [ ] `rumble` — Intensity control (0-5 range, writes accepted)
- [ ] `info` — Device attribute walk shows expected VID/PID

### Manual (in Steam Gaming Mode, after full migration)

- [ ] Left stick, right stick — full range, no drift
- [ ] All face buttons (A/B/X/Y)
- [ ] Triggers and bumpers (LB/RB/LT/RT)
- [ ] D-pad (all 4 directions)
- [ ] Back paddles L4/R4 — correct positions (physical left = L4, physical right = R4)
- [ ] Home/Orange button opens Steam menu
- [ ] KB button functions (short press)
- [ ] Turbo button functions
- [ ] Volume up/down
- [ ] Rumble/vibration works in a game
- [ ] Controller appears correctly in Steam Input (single device, no phantoms)
- [ ] Sleep/wake: put device to sleep, wake, verify paddles and buttons still work (kernel auto re-inits via B8 MCU reset detection)
- [ ] xHCI recovery: if USB dies after wake, run "Recover Gamepad" from Decky plugin (should restart InputPlumber instead of HHD)
- [ ] Gyro/IMU (if used in games — BMI260 source device in InputPlumber)

## References

- InputPlumber PR #567: https://github.com/ShadowBlip/InputPlumber/pull/567
- InputPlumber repo: https://github.com/ShadowBlip/InputPlumber
- Kernel driver source (v3): https://github.com/pastaq/linux/tree/pastaq/7.1/hid/hid-oxp
- Combined driver source: `kernel-patches/hid-oxp/build/hid-oxp.c` (this repo)
- Pre-built module (ba29): `kernel-patches/hid-oxp/6.17.7-ba29.fc43.x86_64/hid-oxp.ko`
- Original v2 patch series: `kernel-patches/hid-oxp/v2-000{1..5}-*.patch` (reference)
- HID protocol docs: `docs/hid-reverse-engineering.md` (this repo)
- Back paddle implementation: `docs/back-paddle-findings.md` (this repo)
- OGC announcement: https://github.com/OpenGamingCollective
