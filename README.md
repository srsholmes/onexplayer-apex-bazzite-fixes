# OneXPlayer Apex — Bazzite Fixes

Temporary workaround fixes for the **OneXPlayer Apex** (AMD Ryzen AI Max+ 395 "Strix Halo") running **Bazzite**.

These fixes address hardware support gaps that exist because the Apex is too new for the current Bazzite release. They are intended as a stopgap until Bazzite ships with updated InputPlumber/HHD profiles and kernel drivers that support this device natively.

> **This is not a general-purpose tool.** It is specifically for the OneXPlayer Apex on Bazzite. It patches device-specific HID mappings, EC registers, and kernel parameters that are unique to this hardware.

## What It Fixes

| Problem | Cause | Fix |
|---------|-------|-----|
| **Face buttons don't work** | HHD doesn't recognize the Apex — no device profile exists, so it grabs input but doesn't forward events | Patches HHD's `const.py` and `base.py` to add the Apex as a known device with correct button mappings (KEY_G for Home instead of KEY_D) and keyboard VID:PID (1a86:fe00) |
| **Sleep/suspend crashes or freezes** | Multiple Strix Halo amdgpu firmware bugs (MES CWSR hang, VPE idle timeout, VRAM eviction OOM) | Applies kernel parameters via `rpm-ostree kargs` (`amdgpu.cwsr_enable=0`, `iommu=pt`, `amdgpu.gttsize=126976`, `ttm.pages_limit=32505856`) and disables spurious wake sources via udev |
| **Home/Orange button does nothing** | Button sends a non-standard HID modifier combo (LCtrl+LAlt+LGUI) that nothing listens for | Monitors the hidraw device and launches HHD UI on button press |
| **No fan control** | The `oxpec` kernel driver patch for the Apex isn't in Bazzite's kernel yet | Provides fan control via three fallback backends: hwmon sysfs, EC debugfs (`ec_sys`), or raw port I/O (`/dev/port`) |

## Structure

```
.
├── decky-plugin/              # Decky Loader plugin (runs in SteamOS Game Mode)
│   ├── main.py                # Plugin backend — exposes RPC methods to frontend
│   ├── plugin.json            # Decky plugin metadata (runs as root)
│   ├── src/index.tsx           # React frontend for the QAM sidebar
│   ├── py_modules/
│   │   ├── button_fix.py      # HHD patching with backup/restore
│   │   ├── sleep_fix.py       # Kernel params + udev rules
│   │   ├── fan_control.py     # Fan control (hwmon / EC / port I/O)
│   │   └── home_button.py     # Async hidraw monitor for Home button
│   ├── package.json
│   ├── rollup.config.js
│   └── tsconfig.json
├── scripts/                   # Standalone CLI tools (run manually via sudo)
│   ├── fix-buttons.sh         # Shell script version of the button fix
│   ├── fix-sleep.sh           # Shell script version of the sleep fix
│   ├── oxp-fan-ctl            # Python CLI for fan control
│   ├── home-button-hhd.py     # Standalone Home button monitor
│   └── setup-home-button.sh   # Installs Home button monitor as systemd service
├── docs/
│   ├── onexplayer-apex-bazzite-guide.md    # Complete troubleshooting guide
│   └── onexplayer-apex-fan-control-plan.md # Fan control implementation plan
└── research/
    └── linux-gaming-os-onexfly-apex.md     # Linux gaming OS comparison for this device
```

## Decky Plugin vs Standalone Scripts

There are two ways to use these fixes:

### Decky Plugin (Recommended)

The Decky Loader plugin provides a UI in the SteamOS Quick Access Menu (QAM) sidebar. Toggle fixes on/off, control fan speed with a slider, and select fan profiles — all without leaving Game Mode.

- **Button Fix**: Toggle on to patch HHD, toggle off to restore original files from backup
- **Sleep Fix**: Toggle on to apply kernel params and udev rules (may require reboot)
- **Home Button**: Toggle on/off to start/stop the hidraw monitor
- **Fan Control**: Switch between auto and manual mode, pick profiles (Silent/Balanced/Performance), or set a custom speed

### Standalone Scripts

For users who prefer the terminal or aren't using Decky:

```bash
# Fix face buttons
sudo bash scripts/fix-buttons.sh

# Fix sleep/suspend
sudo bash scripts/fix-sleep.sh

# Fan control CLI
sudo scripts/oxp-fan-ctl status
sudo scripts/oxp-fan-ctl set 60
sudo scripts/oxp-fan-ctl auto
sudo scripts/oxp-fan-ctl curve

# Install Home button as a systemd service
sudo bash scripts/setup-home-button.sh
```

## Installation

Make sure [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) is installed on your Bazzite system before proceeding.

### Option A: Install via Decky Developer Mode (Recommended)

1. Download `OneXPlayer_Apex_Tools.zip` from the [Releases](https://github.com/srsholmes/onexplayer-apex-bazzite-fixes/releases) page
2. Enable Developer mode in Decky Loader:
   - Open the QAM sidebar (press the `...` button)
   - Go to the Decky tab (plug icon)
   - Open the settings gear icon (top right)
   - Toggle **Developer mode** on
3. Install the plugin from the zip file:
   - In the Decky settings page, a new **Developer** section will appear
   - Click **Install Plugin from ZIP File**
   - Navigate to where you saved `OneXPlayer_Apex_Tools.zip` and select it
   - Decky will install the plugin and it will appear in the QAM sidebar automatically

### Option B: Install Manually via Terminal

1. Download `OneXPlayer_Apex_Tools.zip` from the [Releases](https://github.com/srsholmes/onexplayer-apex-bazzite-fixes/releases) page
2. Extract it to Decky's plugin directory and restart the loader:
   ```bash
   sudo unzip OneXPlayer_Apex_Tools.zip -d ~/homebrew/plugins/
   sudo systemctl restart plugin_loader.service
   ```
3. Open the QAM sidebar in Game Mode — "OXP Apex Tools" should appear

### Option C: Build from Source

1. Install [bun](https://bun.sh):
   ```bash
   curl -fsSL https://bun.sh/install | bash
   ```
2. Build and package the plugin:
   ```bash
   cd decky-plugin
   bun install
   bun run build
   bun run package
   ```
3. Install using either Option A or Option B above (the zip will be in `decky-plugin/`)

## Important Notes

- **Sleep fix is not working yet.** The sleep/suspend fix scripts are still a work in progress and do not currently resolve the issue. It is best to avoid using them for now.

- **Temporary fixes.** The button fix patches files in `/usr/lib/` which get overwritten on every Bazzite update. You'll need to re-apply after updates. The sleep fix kernel params persist across updates.

- **Requires root.** The Decky plugin runs with the `root` flag. The standalone scripts require `sudo`.

- **Immutable filesystem.** Bazzite uses an immutable root filesystem. The button fix uses `ostree admin unlock --hotfix` to make `/usr/lib` temporarily writable. This unlock is lost on reboot/update.

- **Backup & restore.** The button fix saves a backup of the original HHD files before patching. Toggle the fix off in the Decky plugin to restore the originals.

- **Fan control backends.** The plugin tries three backends in order:
  1. **hwmon** (`oxpec` driver) — best option, proper kernel driver
  2. **ec_sys** — direct EC register access via debugfs
  3. **/dev/port** — raw I/O port access (lowest level fallback)

  If none are available, fan control will show as unavailable in the plugin.

- **EC register map (Apex-specific):**

  | Register | Address | Description |
  |----------|---------|-------------|
  | `PWM_ENABLE` | `0x4A` | `0x00` = auto, `0x01` = manual |
  | `PWM_VALUE` | `0x4B` | Duty cycle 0–184 |
  | `FAN_RPM` | `0x76` | Fan speed (2 bytes, little-endian) |

## When Will This Be Unnecessary?

These fixes become obsolete when:

- **InputPlumber** ships with a built-in OneXPlayer Apex device profile (tracks [bazzite#1635](https://github.com/ublue-os/bazzite/issues/1635))
- **HHD** adds Apex to its device list upstream ([hhd-dev/hhd](https://github.com/hhd-dev/hhd))
- **Bazzite's kernel** includes the `oxpec` driver patch for Strix Halo fan control
- **amdgpu** suspend/resume bugs are fixed upstream (kernel 6.15+)

## Hardware Reference

- **APU:** AMD Ryzen AI Max+ 395 (16C/32T Zen 5, RDNA 3.5 40 CUs)
- **Display:** 8" 1920x1200 native landscape, 120Hz VRR
- **Cooling:** Dual-fan, 5400 RPM max
- **Keyboard HID:** USB VID `1a86`, PID `FE00`
- **EC PWM range:** 0–184 native (scaled to 0–255 for sysfs)

## Disclaimer

This software is provided "as is", without warranty of any kind, express or implied. Use it at your own risk. The author accepts no responsibility or liability for any damage, data loss, or other issues caused by using this software. These fixes modify system files, kernel parameters, and interact directly with hardware registers — make sure you understand what they do before applying them.

## License

MIT
