# Research: Replacing Custom Fan Control with HHD's Native Fan Control

## Context

The OneXPlayer Apex Bazzite Fixes plugin includes a custom fan control implementation (`fan_control.py`, ~495 lines) with three fallback backends (hwmon, EC debugfs, port I/O), predefined fan curves, and a Decky UI. The README itself states these fixes become obsolete when HHD adds Apex support upstream and Bazzite's kernel includes the `oxpec` driver.

**Antheas (HHD author) recently submitted kernel patches adding OneXPlayer Apex support to the oxpec driver.** This research evaluates whether the custom fan control can now be replaced by HHD's built-in fan control.

---

## Key Findings

### 1. Kernel Driver Status (oxpec)

**Antheas submitted oxpec patches for the Apex in February 2026:**
- v1 patch series: Feb 18, 2026 ([LKML](https://lkml.org/lkml/2026/2/18/1173))
- v2 patch series: Feb 23, 2026 ([LKML](https://lkml.org/lkml/2026/2/23/988))
- The Apex uses the **same EC registers as OneXPlayer Fly** devices, so support was straightforward to add
- The oxpec driver exposes a standard **hwmon sysfs interface**: `pwm1`, `pwm1_enable`, `fan1_input` — the exact same interface our `HwmonFanController` already uses
- Also adds turbo button/LED control, charge threshold management, and battery monitoring

**Driver consolidation:** The older `oxp-sensors` hwmon driver is being refactored and moved to `platform/x86` as `oxpec`, consolidating fan control, turbo buttons, and power management into one driver (v7 patchset: [patchwork](https://patchwork.kernel.org/project/linux-pm/cover/20250319181044.392235-1-lkml@antheas.dev/)).

### 2. HHD Fan Control Architecture

HHD v4+ has **integrated fan curve management** (previously in the separate "adjustor" project, now archived and merged into HHD):
- Provides fan curve UI accessible through **gamescope overlay** (in-game) and **desktop app**
- Supports multiple cooling profiles with customizable temperature breakpoints
- Works through **standard hwmon sysfs interfaces** — exactly what the oxpec driver exposes
- No special per-device fan code needed; HHD discovers hwmon fans automatically

**This means:** Once the oxpec driver loads and exposes hwmon for the Apex, HHD's fan control UI "just works" — no HHD patches needed for fan control specifically.

### 3. Bazzite Kernel Status

**Current state: NOT YET READY**
- Bazzite 41 ships kernel 6.11 (kernel-bazzite, based on Fedora's kernel-ark)
- The oxpec Apex patches target kernel 6.14+ (submitted Feb 2026)
- Current stable mainline kernel is 6.19 (released Feb 8, 2026)
- **Bazzite needs a kernel update** to include the Apex oxpec patches

**When will it land?** The patches are very recent (Feb 2026). They need to:
1. Be accepted into mainline Linux (likely targeting 6.15 or later merge window)
2. Be backported/included in Bazzite's kernel-bazzite package

Alternatively, Bazzite could cherry-pick the patches into their custom kernel before mainline inclusion — this is common practice for handheld device support.

### 4. What Our Plugin Does vs What HHD Provides

| Feature | Our Plugin | HHD (with oxpec) |
|---------|-----------|-------------------|
| Fan curves (silent/balanced/performance) | ✓ Custom profiles | ✓ Built-in UI with customizable curves |
| Manual fan speed slider | ✓ | ✓ |
| Auto mode (EC control) | ✓ | ✓ |
| Live RPM/temp readout | ✓ | ✓ |
| Fallback to EC debugfs | ✓ | ✗ (hwmon only) |
| Fallback to port I/O | ✓ | ✗ (hwmon only) |
| Works without oxpec driver | ✓ (3 backends) | ✗ (requires oxpec driver) |

### 5. GitHub Issues in Bazzite

- No Apex-specific fan issues found (the device is very new)
- General fan control discussion: [bazzite#999](https://github.com/ublue-os/bazzite/issues/999)
- OneXPlayer support requests exist for X1, F1 Pro models but not Apex specifically
- Bazzite's Jan 2025 update mentioned fan curve improvements for handhelds

---

## Feasibility Assessment

### Can we replace our fan control with HHD's? **YES, but not yet.**

**Prerequisites that must be met first:**
1. **oxpec driver with Apex support must be in Bazzite's kernel** — this is the critical blocker. Without the driver, there's no hwmon interface, and HHD's fan control won't detect the fan.
2. **HHD must recognize the Apex as a device** — our button fix patches are still needed for controller input. However, fan control is separate from device recognition in HHD; it works through hwmon discovery, not device-specific code.

### Recommended Approach

**Phase 1 (Now): Keep custom fan control, monitor upstream**
- The oxpec patches are brand new (Feb 2026) and not in Bazzite yet
- Our 3-backend fallback system works today without any kernel driver
- No action needed on fan control code

**Phase 2 (When oxpec lands in Bazzite's kernel): Test HHD fan control**
- Once Bazzite ships a kernel with the Apex oxpec patches:
  - Verify `oxpec` driver loads and creates hwmon interface
  - Test HHD's fan curve UI works with the Apex's fan
  - Compare behavior (curve responsiveness, safety on sleep/wake)
- If HHD works well: remove `fan_control.py` and fan UI from the Decky plugin
- If HHD has issues: contribute fixes upstream to HHD rather than maintaining our own

**Phase 3 (When HHD adds Apex upstream): Remove button fix too**
- Once HHD natively recognizes the Apex, the button fix patches also become unnecessary
- At that point, the plugin reduces to just Speaker DSP + Home Button + Sleep Fix

### What to Contribute Upstream (to HHD) Instead of Patching

Rather than maintaining our own fan control, we could contribute to HHD:
- **Fan curve profiles** tuned for the Apex's dual-fan setup (our silent/balanced/performance curves)
- **EC register documentation** (registers 0x4A, 0x4B, 0x78) for the Apex
- **Testing feedback** on the oxpec driver's Apex support

---

## Summary

| Question | Answer |
|----------|--------|
| Can HHD control the Apex fan natively? | **Yes**, once the oxpec kernel driver is available |
| Is the oxpec driver ready? | **Patches submitted** (Feb 2026), not yet in mainline or Bazzite |
| Should we remove our fan control now? | **No** — keep it until Bazzite ships the oxpec driver |
| Should we patch HHD for fan control? | **No** — HHD's fan control is generic (hwmon-based), no HHD patches needed for fans |
| What's the timeline? | Likely a few months — depends on kernel merge window and Bazzite's update cycle |

**Bottom line:** The path to removing our custom fan control is clear and inevitable, but the kernel driver hasn't landed in Bazzite yet. Keep our implementation for now, plan to remove it when oxpec ships in Bazzite, and consider contributing our fan curve tuning data upstream to HHD.
