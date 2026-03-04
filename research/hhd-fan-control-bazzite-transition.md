# HHD Fan Control & the Bazzite HHD→InputPlumber Transition

**Date:** 2026-03-04
**Context:** OneXFly Apex on Bazzite — understanding the HHD deprecation and its impact on fan/TDP control

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [What HHD Provided](#what-hhd-provided)
3. [Why Bazzite Is Dropping HHD](#why-bazzite-is-dropping-hhd)
4. [The Antheas Ban & Community Fallout](#the-antheas-ban--community-fallout)
5. [What InputPlumber + OGC Stack Replaces](#what-inputplumber--ogc-stack-replaces)
6. [The Gaps — What's Missing](#the-gaps--whats-missing)
7. [Can Users Just Install HHD Themselves?](#can-users-just-install-hhd-themselves)
8. [Bazzite Director's Cut (bazzite-dc)](#bazzite-directors-cut)
9. [Impact on OneXFly Apex Fan Control](#impact-on-onexfly-apex-fan-control)
10. [Recommendations](#recommendations)

---

## Executive Summary

Bazzite is replacing HHD (Handheld Daemon) with InputPlumber as part of joining the Open Gaming Collective (OGC) in January 2026. This is both a technical and political decision — HHD's creator (Antheas Kapenekakis) was banned from Bazzite for Code of Conduct violations, and InputPlumber is the shared input framework across SteamOS, ChimeraOS, Nobara, and others.

**The key finding for our project:** Fan control and TDP management are **independent** from the input management layer. The `oxpec`/`oxp-sensors` kernel driver talks directly to the EC (Embedded Controller) via hwmon sysfs. Our Decky plugin approach for fan control will work regardless of whether Bazzite uses HHD or InputPlumber.

---

## What HHD Provided

HHD (Handheld Daemon) by antheas was a single integrated Python daemon providing:

| Feature | Implementation |
|---------|---------------|
| **Controller emulation** | Xbox/DualSense virtual gamepads via uhid |
| **Gyro support** | IMU → virtual controller mapping |
| **Back button remapping** | HID device interception + remapping |
| **TDP control** | `adjustor` plugin, integrated into Steam QAM overlay |
| **Fan curve control** | Direct EC register access via platform drivers |
| **Per-game power profiles** | TDP + fan + GPU clock per-game |
| **RGB LED control** | Device-specific EC/HID commands |
| **Steam UI overlay** | Decky plugin + standalone overlay for QAM integration |
| **Charge limiting** | EC register control on supported devices |

The strength of HHD was that it was **one project** covering all of these features with a unified UI.

---

## Why Bazzite Is Dropping HHD

### Technical Reasons

1. **Ecosystem alignment:** InputPlumber is used by SteamOS, ChimeraOS, Nobara, Playtron, Manjaro Handheld, and CachyOS Handheld. Having one shared input framework reduces duplication.
2. **Language/performance:** InputPlumber is Rust-based vs HHD's Python — lower overhead and memory usage.
3. **Composability:** InputPlumber uses a pipeline model where input sources flow through configurable transformations.
4. **OGC membership:** The Open Gaming Collective (formed Jan 29, 2026) standardizes on InputPlumber across all member distros.

### Political Reasons

5. **Antheas was banned** from Bazzite/Universal Blue for "repeated violations of our Code of Conduct" by an overwhelming majority vote. This made continuing to maintain HHD within Bazzite untenable.
6. **GPD controversy:** GPD (hardware manufacturer) was caught in the crossfire — they had a collaboration via Antheas, which Bazzite's founder then disavowed.
7. **Trademark dispute:** Antheas claims partial ownership of the Bazzite brand.

### Skepticism from Others

CachyOS founder Peter Jung **declined** to join the OGC, saying: *"To us all this 'initiative' looked like an emergency, rushed thing, so that Bazzite finds new kernel maintainers after kicking the maintainer who basically made most integration work for them."*

---

## The Antheas Ban & Community Fallout

- Antheas Kapenekakis was the **primary developer** of HHD and a key contributor to Bazzite's kernel and handheld support
- Banned for "several Code of Conduct violations over the last few years"
- Community was divided — some supported the decision, others saw it as losing their most prolific contributor
- Antheas published their own account of events and indicated willingness to block trademark changes
- Several Bazzite repositories were archived/sunset as part of the transition

---

## What InputPlumber + OGC Stack Replaces

The intended replacement is a **modular stack** of separate projects:

| Old (HHD) | New (OGC Stack) | Status |
|-----------|-----------------|--------|
| Controller emulation | **InputPlumber** (ShadowBlip) | Mature, widely deployed |
| TDP management | **PowerStation** (ShadowBlip) | In development, AMD 8000 series support added |
| Fan curves | **PowerStation** / platform drivers | Limited — device support growing |
| Per-game profiles | PowerStation + Steam integration | In development |
| Steam QAM overlay | **Steam UI integration** (OGC goal) | Planned — RGB and fan controls moving into Steam UI |
| RGB control | Steam UI integration + separate tooling | In development |
| Overlay UI | **OpenGamepadUI** (ShadowBlip) | Alternative to Decky-based UIs |

### The problem: Three projects at varying maturity replacing one integrated project.

---

## The Gaps — What's Missing

### 1. TDP Management (Significant Gap)
- HHD's `adjustor` plugin provided seamless TDP control in the Steam QAM
- PowerStation has DBus-based TDP control but **less device coverage** and **less mature UI integration**
- AMD 8000 series support was added to PowerStation (via ChimeraOS 46)
- Strix Halo / Ryzen AI Max+ support status: **unknown**

### 2. Fan Curve Control (Significant Gap)
- HHD handled fan curves directly for OneXPlayer, AYANEO, GPD devices
- PowerStation has limited fan support
- ShadowBlip's `ayn-platform` driver provides fan control for AYN devices only
- **No equivalent OneXPlayer fan support** in the new stack yet
- CoolerControl (`ujust install-coolercontrol`) is available as a workaround but lacks Game Mode integration

### 3. Per-Game Profiles (Gap)
- HHD supported per-game TDP + fan + GPU clock profiles
- No equivalent in the new stack yet

### 4. RGB Control (Partial Gap)
- OGC plans to integrate RGB into Steam UI
- Timeline unclear

### 5. Device Support Breadth (Temporary Gap)
- HHD had profiles for many handheld devices
- InputPlumber's device coverage is growing but hasn't caught up for all devices

---

## Can Users Just Install HHD Themselves?

**Short answer: It's complicated, but possible with caveats.**

### Why it's hard on Bazzite

1. **Immutable OS:** Bazzite is an image-based (atomic) OS. The input daemon is baked into the system image. You can't just `dnf install hhd` — the root filesystem is read-only.

2. **Conflict with InputPlumber:** HHD and InputPlumber both manage the same low-level input devices (grab HID devices exclusively, create virtual gamepads). Running both simultaneously causes conflicts — double inputs, devices not being released, etc. You must disable one to use the other.

3. **System integration:** HHD has deep hooks into Bazzite's boot process, udev rules, and systemd services. The `hhd-bazzite` plugin specifically handles Bazzite integration. If Bazzite removes this from their image, users would need to layer it back manually.

### How users CAN still use HHD

1. **Bazzite Director's Cut (bazzite-dc):** Antheas maintains a fork/overlay called [Bazzite: Director's Cut](https://github.com/hhd-dev/bazzite-dc) that is "a Bazzite edition based on a stable build of bazzite-deck, with an up-to-date Handheld Daemon + other niceties." This is the easiest path for users who want to keep HHD.

2. **rpm-ostree overlay:** On Bazzite, you can layer packages on top of the base image using `rpm-ostree install`. If HHD is available as an RPM (or via pip in a container), it could theoretically be layered — but you'd need to disable InputPlumber first (`systemctl disable --now inputplumber`).

3. **Distrobox/container:** Run HHD in a container with access to the host's input devices. This is hacky and may not work well for low-level HID access.

4. **Other distros:** Users can switch to a mutable distro (Nobara, CachyOS, plain Fedora) and install HHD normally. Antheas continues to maintain HHD as an independent project.

### The real issue

Even if users install HHD, **Bazzite will stop testing against it**. Kernel updates, gamescope changes, and Steam client updates may break HHD integration without anyone upstream noticing or caring. Over time, the experience will degrade.

---

## Bazzite Director's Cut

[Bazzite: Director's Cut (bazzite-dc)](https://github.com/hhd-dev/bazzite-dc) is Antheas's answer to being removed from Bazzite:

- Based on a stable bazzite-deck build
- Ships with up-to-date Handheld Daemon
- Includes device quirks and kernel patches that Antheas maintains
- Licensed Apache-2.0

**Caveat:** As of the last available information, bazzite-dc was based on an older Bazzite image (40-20240427 with kernel 6.8.7). It's unclear how actively maintained this is or whether it tracks recent Bazzite releases. If Antheas is the sole maintainer and he's in conflict with the Bazzite team, long-term sustainability is questionable.

---

## Impact on OneXFly Apex Fan Control

### Good news: Fan control is independent of the input layer

The fan control pathway on OneXPlayer devices is:

```
User (Decky Plugin / CLI)
  → hwmon sysfs (/sys/class/hwmon/hwmonX/)
    → oxpec kernel driver
      → EC (Embedded Controller) registers
        → Fan hardware
```

This has **nothing to do with HHD or InputPlumber**. The `oxpec` driver is a kernel module that exposes fan speed/PWM controls through the standard Linux hwmon interface. Any userspace tool can read temperatures and set fan speeds through sysfs.

### What matters for us

1. **The `oxpec` driver needs Apex support.** Antheas submitted an upstream patch on Feb 23, 2026 adding OneXFly Apex DMI strings. This needs to land in Bazzite's kernel (or we apply it manually).

2. **Our Decky plugin talks to hwmon sysfs directly.** It doesn't depend on HHD or InputPlumber at all.

3. **TDP control via `ryzenadj` or PowerStation** is also independent — it talks to the AMD SMU (System Management Unit) via PCI/MSR, not through HHD.

4. **The only thing we lose** from HHD deprecation is the integrated overlay UI that combined controller settings + TDP + fan curves in one place. We're building our own Decky plugin for fan curves, which is the right approach regardless.

### What the OGC transition means for our project

| Concern | Impact on Our Work |
|---------|-------------------|
| InputPlumber replacing HHD for controller input | **None** — our fan plugin doesn't touch input |
| PowerStation replacing HHD adjustor for TDP | **None** — we can use ryzenadj or PowerStation's DBus API |
| Fan control moving to Steam UI (OGC goal) | **Future benefit** — if this happens, our Decky plugin could become unnecessary |
| oxpec driver needing Apex support | **Direct dependency** — we need this kernel patch |
| Bazzite kernel changing to OGC kernel | **Low risk** — hwmon interface is standard Linux; driver just needs to be included |

---

## Recommendations

### For our OneXFly Apex fan control project

1. **Continue with the Decky plugin approach.** It's the right architecture regardless of the HHD/InputPlumber transition. Our plugin talks to hwmon sysfs, which is stable kernel ABI.

2. **Track the oxpec Apex patch.** Monitor when Antheas's Feb 2026 patch lands in the OGC kernel or Bazzite's kernel. Until then, use manual EC register access via `ec_sys` as documented in our fan control plan.

3. **Don't depend on HHD for anything.** Even though HHD currently works on Bazzite, it's being phased out. Build our tools to be HHD-independent.

4. **Consider PowerStation for TDP integration.** If we add TDP controls to our Decky plugin, use PowerStation's DBus API or ryzenadj directly rather than HHD's adjustor.

5. **Watch for Steam UI fan control integration.** The OGC's stated goal is to move fan/RGB controls into Steam UI. If this materializes, we may want to contribute our device-specific fan curves upstream rather than maintaining a separate plugin long-term.

### For users who want the "full HHD experience" today

1. **Bazzite Director's Cut** is the path of least resistance, but sustainability is unclear
2. **Stay on current Bazzite** and use HHD while it still works — the transition is gradual
3. **For fan control specifically**, our Decky plugin will fill the gap once built
4. **For TDP**, SimpleDeckyTDP or PowerControl Decky plugins work independently of HHD

---

## Deep Dive: InputPlumber & The ShadowBlip/OGC Ecosystem

### What Is InputPlumber?

[InputPlumber](https://github.com/ShadowBlip/InputPlumber) is an **open source input routing and control daemon** for Linux, written in Rust. It is **purely an input management solution** — it does NOT handle fan control, TDP, thermal management, or any system-level hardware control.

**Scope:**
- Combine any number of input devices (gamepads, mice, keyboards)
- Translate input to virtual device formats (Xbox, DualSense, etc.)
- Intercept and route input through DBus for overlay control
- Create input mapping profiles
- Network-based input routing (in progress)

**OneXPlayer support:** InputPlumber has composite device configurations for "OneXPlayer Intel" matching product names "ONEXPLAYER" and "ONE XPLAYER" from sys_vendor "ONE-NETBOOK". The X1 series was added in v0.70.0.

**Key point:** InputPlumber replaces ONLY the controller emulation / input remapping portion of HHD. It does not replace TDP or fan control.

### What Is PowerStation?

[PowerStation](https://github.com/ShadowBlip/PowerStation) is an **open source TDP control and performance daemon** with a DBus interface. It manages:
- CPU settings (per-core control via `org.shadowblip.CPU`)
- GPU settings (clock speeds, power profiles, TDP limits via `org.shadowblip.GPU`)
- ThermalThrottleLimitC property (thermal throttle ceiling, NOT active fan control)

**PowerStation does NOT handle fan control.** Its scope is TDP and CPU/GPU performance tuning. There are no fan-related issues in its GitHub repo. The DBus object hierarchy lives under `/org/shadowblip/Performance/` with sub-paths for CPU (individual cores) and GPU (cards and connectors).

Recent development includes using AsusWMI platform to set TDP instead of FirmwareAttributes.

### What Is hhfc?

[hhfc (Hwmon Handheld Fan Controller)](https://github.com/Samsagax/hhfc) is a **userspace fan control daemon** written in Python by **Samsagax (Joaquín Ignacio Aramendía)** — the same person who wrote the `oxp-sensors` kernel driver that's in mainline Linux.

**How it works:**
- Reads temperatures from hwmon sysfs sensors
- Applies configurable temperature-to-fan-speed curves
- Writes PWM values back to hwmon fan control interfaces
- Runs as a systemd daemon
- Uses YAML configuration with SENSORS and FANS sections

**Configuration example:**
```yaml
SENSORS:
  - name: "oxp_sensors"
    input: "temp1_input"
    divisor: 1000
    offset: 0
FANS:
  - name: "oxp_sensors"
    pwm: "pwm1"
    min_value: 0
    max_value: 255
    curve:
      40: 0
      50: 30
      60: 50
      70: 80
      80: 100
```

**Key detail:** hhfc is already used by **ChimeraOS** (since v44, Sept 2023). ChimeraOS release notes state: *"ayn-platform is now compatible with HHFC for multi-temperature based fan curves."* By ChimeraOS 46, fan control was enabled for GPD Win Mini, AYANEO 2S/AIR 1S/GEEK 1S/KUN, OneXPlayer 2/OneXFly, and OrangePi NEO.

hhfc is available as `hhfc-git` in the AUR.

### The ShadowBlip Ecosystem Map

The ShadowBlip organization maintains a modular ecosystem for handheld gaming on Linux:

| Project | Purpose | Replaces from HHD |
|---------|---------|-------------------|
| [InputPlumber](https://github.com/ShadowBlip/InputPlumber) | Input routing/remapping (Rust) | Controller emulation, gyro, button remapping |
| [PowerStation](https://github.com/ShadowBlip/PowerStation) | TDP/CPU/GPU control via DBus (Rust) | `adjustor` TDP plugin |
| [OpenGamepadUI](https://github.com/ShadowBlip/OpenGamepadUI) | Game launcher + overlay (Godot 4) | HHD overlay UI |
| [ayn-platform](https://github.com/ShadowBlip/ayn-platform) | Kernel driver: fan/temp/RGB for AYN devices | N/A (kernel level) |
| [HandyGCCS](https://github.com/ShadowBlip/HandyGCCS) | Legacy controller support (Python) | Predecessor to InputPlumber |
| [HandyPT](https://github.com/ShadowBlip/HandyPT) | Legacy TDP plugin (Crankshaft) | Defunct — Crankshaft no longer works |

**Plus the community/ChimeraOS tools:**
| Project | Purpose | Maintainer |
|---------|---------|------------|
| [hhfc](https://github.com/Samsagax/hhfc) | Hwmon fan curve daemon (Python) | Samsagax (ChimeraOS contributor) |
| [oxp-sensors](https://github.com/Samsagax/oxp-sensors) | Kernel driver for OXP fan/temp (in mainline since 6.2) | Samsagax |

### The Fan Control Gap

Here is the critical finding: **there is no single OGC-blessed fan control solution yet.**

The current state:

1. **Kernel drivers** (`oxp-sensors`, `ayn-platform`, `ayaneo-platform`, etc.) expose fan PWM and temperature sensors via hwmon sysfs. These are in mainline Linux and work on Bazzite's kernel 6.17.

2. **hhfc** is the closest thing to a standard fan control daemon. It's used by ChimeraOS and works with any hwmon-exposed fan. But it's not explicitly part of the OGC stack and there's no indication Bazzite has adopted it.

3. **PowerStation** does NOT do fan control — it only handles TDP/CPU/GPU performance settings.

4. **The OGC stated goal** is to integrate fan and RGB controls into the **Steam UI** itself. This is aspirational with no clear timeline.

5. **HHD** handled fan curves as part of its integrated daemon. With HHD deprecated, this functionality has no direct replacement in the Bazzite stack.

6. **CoolerControl** (`ujust install-coolercontrol`) is available on Bazzite as a workaround but lacks Game Mode / Steam UI integration.

### The oxp-sensors Driver on Kernel 6.17

The `oxp-sensors` driver (CONFIG_SENSORS_OXP) has been in mainline Linux since kernel 6.2. On Bazzite's kernel 6.17:

**Confirmed supported OneXPlayer models:**
- OneXPlayer Mini (A07/AMD)
- OneXPlayer Mini Pro
- OneXPlayer 2 series
- OneXFly
- OneXPlayer X1 Mini

**Hwmon interface exposed:**
- `fan1_input` — current fan RPM (read-only)
- `pwm1` — duty cycle 0-255 (read-write when manual mode enabled)
- `pwm1_enable` — write "1" for manual, "0" for EC-controlled
- `tt_toggle` — turbo/silent button behavior toggle

**OneXFly Apex status:** The Apex (Ryzen AI Max+ / Strix Halo) needs DMI string additions to the driver. Antheas submitted a patch on Feb 23, 2026. This is a simple DMI match addition — the underlying EC protocol is the same as other OneXPlayer devices.

### What This Means For Our Project

The architecture is clear:

```
┌─────────────────────────────────────────────────┐
│                   Steam Game Mode                │
│  ┌─────────────┐  ┌──────────┐  ┌────────────┐  │
│  │ InputPlumber│  │PowerStn. │  │ Fan Control │  │
│  │ (input only)│  │(TDP only)│  │   (gap!)    │  │
│  └──────┬──────┘  └────┬─────┘  └──────┬──────┘  │
│         │              │               │          │
│  ┌──────▼──────┐  ┌────▼─────┐  ┌──────▼──────┐  │
│  │  /dev/input │  │ SMU/PCI  │  │ hwmon sysfs │  │
│  │  HID devices│  │ ryzenadj │  │ /sys/class/ │  │
│  └─────────────┘  └──────────┘  └──────┬──────┘  │
│                                        │          │
│                                 ┌──────▼──────┐  │
│                                 │  oxp-sensors │  │
│                                 │ kernel driver│  │
│                                 └──────┬──────┘  │
│                                        │          │
│                                 ┌──────▼──────┐  │
│                                 │   EC (ACPI)  │  │
│                                 │  Fan Hardware│  │
│                                 └─────────────┘  │
└─────────────────────────────────────────────────┘
```

**Our Decky plugin fills the "Fan Control (gap!)" box.** It sits at the same level as hhfc — a userspace daemon reading hwmon temperatures and writing PWM values. This is completely independent of InputPlumber and PowerStation.

The fan control stack is:
1. **Kernel driver** (`oxp-sensors`) — provides the hwmon interface ✅ (needs Apex DMI patch)
2. **Userspace fan daemon** — reads temps, applies curve, sets PWM — **this is what we're building**
3. **UI integration** — Decky plugin in Steam Game Mode — **this is what we're building**

---

## Key Sources

- [Open Gaming Collective Announcement (GamingOnLinux)](https://www.gamingonlinux.com/2026/01/open-gaming-collective-ogc-formed-to-push-linux-gaming-even-further/)
- [Open Gaming Collective Official Site](https://opengamingcollective.org/)
- [A Brighter Future for Bazzite (Universal Blue Discourse)](https://universal-blue.discourse.group/t/a-brighter-future-for-bazzite/11575)
- [ShadowBlip PowerStation](https://github.com/ShadowBlip/PowerStation)
- [ShadowBlip InputPlumber](https://github.com/ShadowBlip/InputPlumber)
- [ShadowBlip HandyPT (Handheld Power Tools)](https://github.com/ShadowBlip/HandyPT)
- [HHD (Handheld Daemon)](https://github.com/hhd-dev/hhd)
- [Bazzite Director's Cut (bazzite-dc)](https://github.com/hhd-dev/bazzite-dc)
- [Bazzite OneXPlayer Documentation](https://docs.bazzite.gg/Handheld_and_HTPC_edition/Handheld_Wiki/OneXPlayer_Handhelds/)
- [Bazzite Jan 2025 Update (Fan Curves, GPD, More Devices)](https://universal-blue.discourse.group/t/bazzite-update-happy-new-year-sleep-fixes-smoother-updates-bootc-fan-curves-gpd-more-devices/6200)
- [CoolerControl on Bazzite (Universal Blue Discourse)](https://universal-blue.discourse.group/t/fan-control-in-bazzite-coolercontrol-install/10038)
- [CachyOS Declines OGC (Technobezz)](https://www.technobezz.com/news/cachyos-founder-declines-to-join-new-linux-gaming-collective-2026-02-03-6mb4/)
- [GPD Statement on Bazzite Confusion (GamingOnLinux)](https://www.gamingonlinux.com/2026/01/gpd-release-their-own-statement-on-the-confusion-with-bazzite-linux-support/)
- [Bazzite Fall 2025 Update](https://gardinerbryant.com/bazzites-fall-2025-update-is-here/)
- [Lunduke Journal on Antheas Ban (X/Twitter)](https://x.com/LundukeJournal/status/2015079613291286581)
- [Bazzite + OGC (VideoCardz)](https://videocardz.com/newz/bazzite-and-asus-linux-shadowblip-pikaos-fyra-labs-launch-open-gaming-collective)
- [XDA: Bazzite Reveals OGC](https://www.xda-developers.com/bazzite-reveals-the-open-gaming-collective-to-make-gaming-on-linux-even-better/)
- [hhfc (Hwmon Handheld Fan Controller)](https://github.com/Samsagax/hhfc)
- [oxp-sensors Kernel Driver (Samsagax)](https://github.com/Samsagax/oxp-sensors)
- [oxp-sensors Kernel Documentation](https://www.kernel.org/doc/html/latest/hwmon/oxp-sensors.html)
- [ChimeraOS Release Notes (hhfc integration)](https://github.com/ChimeraOS/chimeraos/wiki/Release-Notes)
- [ShadowBlip OpenGamepadUI](https://github.com/ShadowBlip/OpenGamepadUI)
- [ShadowBlip ayn-platform driver](https://github.com/ShadowBlip/ayn-platform)
- [ShadowBlip HandyGCCS](https://github.com/ShadowBlip/HandyGCCS)
- [OGC Launch (OSTechNix)](https://ostechnix.com/bazzite-joins-open-gaming-collective-ogc/)
- [OGC Launch (KitGuru)](https://www.kitguru.net/gaming/joao-silva/open-gaming-collective-ogc-formed-to-unify-linux-gaming/)
- [Bazzite OneXPlayer X1 AMD Issue](https://github.com/ublue-os/bazzite/issues/1649)
- [Bazzite Latest Release (43.20260303, Kernel 6.17)](https://github.com/ublue-os/bazzite/releases)
