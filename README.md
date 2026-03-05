# OneXPlayer Apex — Bazzite Fixes

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for the **OneXPlayer Apex** (AMD Ryzen AI Max+ 395 "Strix Halo") running **Bazzite**. Provides hardware fixes, speaker EQ, and fan control — all accessible from the SteamOS Quick Access Menu.

> **This is not a general-purpose tool.** It is specifically for the OneXPlayer Apex on Bazzite. It patches device-specific HID mappings, EC registers, and PipeWire configs that are unique to this hardware.

## Screenshots

| Fixes & Speaker DSP | EQ Sliders | Fan Control & Test Sound |
|:---:|:---:|:---:|
| ![Fixes and Speaker DSP](screenshots/fixes-and-speaker-dsp.png) | ![EQ Sliders](screenshots/eq-sliders.png) | ![Fan Control and Test Sound](screenshots/fan-control-and-test-sound.png) |

## Features

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

### Fan Control
Direct fan control via the Apex's embedded controller. Three backends are tried in order: `oxpec` hwmon driver, EC debugfs (`ec_sys`), or raw port I/O (`/dev/port`).

- **Profiles**: Silent, Balanced, Performance — predefined fan curves based on CPU temperature
- **Custom slider**: set an exact fan speed percentage
- **Auto mode**: returns control to the EC's built-in fan management
- Live readout of temperature, RPM, and duty cycle

### Home Button
Monitors the Apex's hidraw device for the Home/Orange button press (sends a non-standard `LCtrl+LAlt+LGUI` combo) and launches HHD's overlay UI.

### Sleep Fix
S0i3 deep sleep is currently broken on Strix Halo with kernel 6.17 (requires ACPI C4 support in kernel 6.18+). The plugin provides a cleanup tool to remove any previously applied (broken) sleep kargs.

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

## Standalone Scripts

For users who prefer the terminal or aren't using Decky:

```bash
# Fix face buttons
sudo bash scripts/fix-buttons.sh

# Fan control CLI
sudo scripts/oxp-fan-ctl status
sudo scripts/oxp-fan-ctl set 60
sudo scripts/oxp-fan-ctl auto

# Install Home button as a systemd service
sudo bash scripts/setup-home-button.sh
```

## Important Notes

- **Temporary fixes.** The button fix patches files in `/usr/lib/` which get overwritten on Bazzite updates. Re-apply after updates.
- **Immutable filesystem.** Uses `ostree admin unlock --hotfix` to make `/usr/lib` writable. This unlock is lost on reboot/update.
- **Requires root.** The Decky plugin runs with the `root` flag. Standalone scripts require `sudo`.
- **fw-fanctrl-suspend (known Bazzite issue).** The `fw-fanctrl` package ships a sleep hook that fails on non-Framework hardware. See [sleep research](docs/sleep-research.md#fw-fanctrl-suspend-issue) for how to neutralize it.

## When Will This Be Unnecessary?

These fixes become obsolete when:

- **HHD** adds Apex to its device list upstream ([hhd-dev/hhd](https://github.com/hhd-dev/hhd))
- **Bazzite's kernel** includes the `oxpec` driver patch for Strix Halo fan control
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
