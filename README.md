# OneXPlayer Apex — Bazzite Fixes

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for the **OneXPlayer Apex** (AMD Ryzen AI Max+ 395 "Strix Halo") running **Bazzite**. Provides hardware fixes, sleep enablement, and speaker EQ — all accessible from the SteamOS Quick Access Menu.

> **This is not a general-purpose tool.** It is specifically for the OneXPlayer Apex on Bazzite. It patches device-specific HID mappings, EC registers, and PipeWire configs that are unique to this hardware.

## Screenshots

| Fixes & Speaker DSP | EQ Sliders |
|:---:|:---:|
| ![Fixes and Speaker DSP](screenshots/fixes-and-speaker-dsp.png) | ![EQ Sliders](screenshots/eq-sliders.png) |

## Features

### EC Sensor Driver (oxpec)
Loads the `oxpec` kernel module which provides hwmon sensor access and enables [HHD](https://github.com/hhd-dev/hhd)'s native fan curves. With this driver loaded, fan control is handled natively by HHD and PowerControl — no custom fan curve code needed.

- Toggle on to install the module and a systemd service that loads it on boot
- Bundled `.ko` files for kernels `6.17.7-ba25` and `6.17.7-ba28` — auto-selects the right one at boot

### Button Fix
Patches [HHD](https://github.com/hhd-dev/hhd) to recognize the Apex as a known device. Without this, HHD grabs input but doesn't forward button events — face buttons, Home, and QAM buttons are all dead.

- Toggle on to apply the fix, toggle off to restore original HHD files
- Survives sleep/wake but must be re-applied after Bazzite updates or `rpm-ostree` operations

### Back Paddle Support
Enables the L4/R4 back paddles as separate buttons via HHD's full intercept mode. The Apex's vendor HID device (`1a86:fe00`) reports all gamepad input through a proprietary protocol — this mode parses it and exposes the back paddles to Steam Input.

- Back paddles appear as extra buttons you can remap in Steam Input settings (per-game or global)
- Toggle off if you experience stick drift or input issues (full intercept replaces the standard Xbox controller)

### Speaker DSP (EQ Enhancement)
Applies a parametric EQ to the internal speakers using PipeWire's built-in filter-chain. The Apex's speakers sound tinny and lack bass out of the box — this makes them significantly better.

- **3 built-in presets**: Balanced, Bass Boost, Treble — tuned and tested at 100% volume without clipping
- **Custom profiles**: copy a preset and tweak the 7-band EQ (64 Hz to 16 kHz, +/-15 dB per band)
- **Original Sound toggle**: instantly A/B compare your EQ against the raw speaker output
- **Test Sound**: plays a bundled music track ([NCS](https://ncs.io/) licensed) for quick EQ previewing
- Only affects internal speakers — headphones and external audio pass through unmodified

### Resume Recovery
Installs a background service that recovers the gamepad after sleep by rebinding the xHCI USB controller. Without this, the Xbox controller disappears after wake and requires a reboot.

- Listens for D-Bus resume events and rebinds PCI device `0000:65:00.4`
- Two-phase recovery: fast (1s) then fallback (2s)

### Light Sleep
Applies kernel parameters for s2idle light sleep. **Requires "ACPI Auto configuration" enabled in BIOS.**

- Applies `mem_sleep_default=s2idle` and `amd_iommu=off`
- Automatically removes known-problematic legacy kargs from previous fix attempts
- Requires reboot after applying (button fix must be re-applied after reboot)

> **Note:** S0i3 deep sleep is still broken on Strix Halo with kernel 6.17 (requires ACPI C4 in kernel 6.18+). This is *light sleep* (s2idle) which provides lower power draw than staying awake but not as deep as S0i3.

#### Required kargs

```
mem_sleep_default=s2idle
amd_iommu=off
```

`amd_iommu=off` is required for s2idle to work on this hardware — while it blocks the S0i3 path, s2idle will not enter correctly without it on Strix Halo.

#### Known problematic kargs (auto-removed)

| Karg | Issue |
|------|-------|
| `amd_iommu=on` | Invalid AMD parameter, silently ignored |
| `acpi.ec_no_wakeup=1` | Prevents EC-based wakeup |
| `amdgpu.cwsr_enable=0` | Compute-specific, not needed |
| `amdgpu.gttsize=126976` | Not sleep-related |
| `ttm.pages_limit=32505856` | Not sleep-related |

### Sleep Fix (Fan Noise)
Neutralizes the `fw-fanctrl-suspend` script — a Framework Laptop tool shipped with Bazzite that errors on non-Framework hardware, keeping fans running during sleep. Also installs a udev rule to prevent the fingerprint reader from immediately waking the device.

### Home Button
Monitors the Apex's hidraw device for the Home/Orange button press (sends a non-standard `LCtrl+LAlt+LGUI` combo) and launches HHD's overlay UI.

## Installation

Make sure [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) is installed on your Bazzite system.

### Option A: Install via Decky Developer Mode (Recommended)

1. Download `OneXPlayer_Apex_Tools.zip` from the [Releases](https://github.com/srsholmes/onexplayer-apex-bazzite-fixes/releases) page
2. Enable Developer mode in Decky Loader:
   - Open the QAM sidebar (press the `...` button)
   - Go to the Decky tab (plug icon)
   - Open the settings gear icon (top right)
   - Toggle **Developer mode** on
3. Install the plugin:
   - In the Decky settings page, a new **Developer** section will appear
   - Click **Install Plugin from ZIP File**
   - Navigate to `OneXPlayer_Apex_Tools.zip` and select it

### Option B: Install via Terminal

```bash
sudo unzip OneXPlayer_Apex_Tools.zip -d ~/homebrew/plugins/
sudo systemctl restart plugin_loader.service
```

### Option C: Build from Source

```bash
curl -fsSL https://bun.sh/install | bash  # install bun if needed
cd decky-plugin
bun install
bun run build
bun run package
# Then install the zip via Option A or B
```

## Bazzite Updates & Kernel Compatibility

Bazzite ships periodic kernel updates (e.g. `ba25` → `ba28`). When the kernel changes, the bundled `oxpec.ko` must match the running kernel or it will fail to load with "Invalid module format".

**What happens on a kernel update:**
- The oxpec driver will fail to load until a matching `.ko` is bundled in the plugin
- Button fix and sleep fix patches in `/usr/lib/` get overwritten — re-apply after updates
- The `ostree admin unlock --hotfix` overlay is lost on reboot/update

**Current mitigations:**
- The plugin auto-loads oxpec on every boot via `ensure_loaded()`, with a fallback chain that handles SELinux and kernel mismatches gracefully
- Multiple kernel `.ko` files are bundled so minor updates don't break things
- Working on making the update process smoother so new kernels can be supported quickly

**Once Bazzite's upstream kernel includes the Apex DMI entry in `oxpec`, none of this will be needed** — a simple `modprobe oxpec` will just work and the bundled `.ko` becomes unnecessary.

## Important Notes

- **Temporary fixes.** The button fix and sleep fix patch files in `/usr/lib/` which get overwritten on Bazzite updates. Re-apply after updates.
- **Immutable filesystem.** Uses `ostree admin unlock --hotfix` to make `/usr/lib` writable. This unlock is lost on reboot/update.
- **Requires root.** The Decky plugin runs with the `root` flag.
- **Fan control.** With the oxpec driver loaded, fan curves are handled natively by HHD / PowerControl. No custom fan control is included in this plugin.

## Acknowledgments

The `oxpec` kernel module, `xpad-fix3` resume recovery script, and `fw-fanctrl-suspend` sleep fix are based on fix packages shared by **스트로바쿠다스 (drama8448)** from the Korean OneXPlayer community on DCInside UMPC Gallery:

- [Apex Bazzite Fan Control Patch Update](https://gall.dcinside.com/mgallery/board/view/?id=umpc&no=141816) — Original post with oxpec.ko, hhd-autolink, xpad-fix3, and fw-fanctrl-suspend fixes

The HHD button fix patches are built against [HHD v4.1.5](https://github.com/hhd-dev/hhd) by [antheas](https://github.com/antheas).

## When Will This Be Unnecessary?

These fixes become obsolete when:

- **HHD** adds Apex to its device list upstream ([hhd-dev/hhd](https://github.com/hhd-dev/hhd))
- **Bazzite's kernel** includes the `oxpec` driver for Strix Halo EC sensor access
- **Kernel 6.18+** ships with ACPI C4 support for S0i3 deep sleep on Strix Halo

## Hardware Reference

| Component | Detail |
|-----------|--------|
| **APU** | AMD Ryzen AI Max+ 395 (16C/32T Zen 5, RDNA 3.5 40 CUs) |
| **RAM** | 48 GB unified (shared CPU+GPU) |
| **Display** | 8" 1920x1200, 120 Hz VRR |
| **Cooling** | Dual-fan, 5400 RPM max |
| **Keyboard HID** | USB VID `1a86`, PID `FE00` |
| **EC PWM range** | 0-184 native (scaled to 0-255 for sysfs) |

## Disclaimer

This software is provided "as is", without warranty of any kind. Use at your own risk. These fixes modify system files, kernel parameters, and interact directly with hardware registers.

## License

MIT
