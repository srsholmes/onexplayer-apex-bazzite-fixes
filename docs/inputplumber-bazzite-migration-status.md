# InputPlumber on Bazzite: Migration Status & Plan

**Last updated:** March 2, 2026

## Current Status Summary

| Item | Status |
|------|--------|
| Bazzite default input daemon | **HHD** (still shipping) |
| InputPlumber in Bazzite stable images | **Not yet** |
| HHD receiving updates | **No** (repos archived) |
| InputPlumber latest release | **v0.74.0** (Feb 16, 2026) |
| Migration announced | **Jan 28, 2026** (OGC announcement) |
| Specific migration date | **Not announced** |

## Background: The Open Gaming Collective (OGC)

On January 28–29, 2026, Bazzite announced it was joining the **Open Gaming Collective (OGC)** — a coalition of Linux gaming distros and projects working to standardize the gaming stack across distributions.

### Founding Members
- **Universal Blue / Bazzite**
- **ASUS Linux**
- **ShadowBlip** (InputPlumber developers)
- **PikaOS**
- **Fyra Labs**

### Strategic Partners & Core Contributors
- ChimeraOS
- Nobara
- Playtron

## What's Changing

### HHD Is Being Phased Out
- HHD's repositories have been **archived/sunset**
- HHD will receive **no further updates**
- Features like RGB and fan control will be integrated into the **Steam UI**
- Features not supported by the Steam UI will get a **clean overlay** (replacing HHD's overlay)

### InputPlumber Is the Replacement
Bazzite is switching to **InputPlumber**, the same input framework used by:
- **SteamOS**
- **ChimeraOS**
- **Nobara**
- **Playtron GameOS**
- **Manjaro Handheld Edition**
- **CachyOS Handheld Edition**

InputPlumber standardizes the behavior of controllers, gyroscopes, touchpads, and other controls across devices.

## What Ships Today (March 2026)

### Bazzite Stable (43.20260217)
- **Still ships HHD** as the default input daemon
- InputPlumber is **not yet included** in stable images
- Kernel: 6.17.7-ba25, Mesa 26.0.0-1, GNOME 49.4-1 / KDE 6.5.5-1

### InputPlumber v0.74.0 (Feb 16, 2026)
- Latest release from ShadowBlip
- Includes: wildcard matching for Claw8, new PIDs for Steam Deck target, scroll wheel support for Legion Go, mouse wheel support, tap-to-click for GPD Win Mini, and more
- Already packaged in CachyOS (v0.74.2-1)

## Can You Use InputPlumber on Bazzite Now?

### Manual Installation (Not Recommended)
You *could* install InputPlumber manually, but:
1. Running it alongside HHD **causes conflicts** — both try to manage the same input devices
2. You'd need to disable HHD first: `systemctl disable --now hhd`
3. This would **break your current button/input setup** until InputPlumber is properly configured
4. No official Bazzite support for this configuration yet

### DeckyPlumber (Decky Plugin for InputPlumber)
- [DeckyPlumber](https://github.com/aarron-lee/DeckyPlumber) by aarron-lee provides a UI for InputPlumber within Game Mode
- Designed for distros that **already run InputPlumber** (SteamOS, CachyOS, Nobara)
- Not useful on Bazzite until InputPlumber ships as the default

### Rollback Safety
Bazzite's rollback and pin system allows users to stay on current builds if their specific hardware needs time to be supported under InputPlumber.

## What This Means for OneXPlayer Apex Support

### Why InputPlumber Profiles Are the Right Move Now
1. When Bazzite ships InputPlumber, the Apex **will need a device profile ready**
2. InputPlumber profiles work on **SteamOS today**
3. They work on **CachyOS, Nobara, and other OGC distros today**
4. Once Bazzite switches, the entire HHD-based button fix approach (`button_fix.py` + HHD patches) becomes **obsolete**

### Practical Approach
- **Build the InputPlumber YAML profile now** — it's useful immediately on SteamOS and other distros
- **Keep the HHD code path working** — Bazzite users still need it until the switch happens
- **Add daemon-detection logic** — so the plugin works with whichever input daemon is active

### Device Profile Essentials
An InputPlumber device profile for the OneXPlayer Apex needs:
- USB vendor/product ID matching
- Button mapping (including back paddles)
- Gyroscope configuration
- Touchpad support
- Any device-specific quirks

## Timeline Expectations

No specific date has been given by the Bazzite team. Key signals to watch:
1. **Bazzite testing images** — InputPlumber may appear in testing builds before stable
2. **OGC kernel adoption** — Bazzite is also switching to the shared OGC kernel
3. **ShadowBlip device support** — InputPlumber needs profiles for all devices Bazzite supports

## Sources

- [A Brighter Future for Bazzite - Universal Blue Discourse](https://universal-blue.discourse.group/t/a-brighter-future-for-bazzite/11575)
- [Bazzite Reveals the Open Gaming Collective - XDA Developers](https://www.xda-developers.com/bazzite-reveals-the-open-gaming-collective-to-make-gaming-on-linux-even-better/)
- [InputPlumber 0.74 Released - Phoronix](https://www.phoronix.com/news/InputPlumber-0.74)
- [InputPlumber GitHub](https://github.com/ShadowBlip/InputPlumber)
- [DeckyPlumber GitHub](https://github.com/aarron-lee/DeckyPlumber)
- [Linux Gaming Consolidates Around Shared Infrastructure - The Meridiem](https://www.themeridiem.com/innovation-future-trends/2026/01/29/linux-gaming-consolidates-around-shared-infrastructure-as-ogc-forms)
- [Open Gaming Collective - GamingOnLinux](https://www.gamingonlinux.com/2026/01/open-gaming-collective-ogc-formed-to-push-linux-gaming-even-further/)
- [Bazzite GitHub](https://github.com/ublue-os/bazzite/)
