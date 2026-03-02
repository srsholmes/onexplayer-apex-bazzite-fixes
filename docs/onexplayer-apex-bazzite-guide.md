# OneXFly Apex on Bazzite — Complete Fix Guide

**Device:** OneXPlayer OneXFly Apex (AMD Ryzen AI Max+ 395 "Strix Halo")
**OS:** Bazzite (Fedora Atomic, dual-boot with Windows)
**Last Updated:** 2026-03-02

This is the reference document for fixing the main issues on the Apex running Bazzite: broken face buttons/back paddles, no fan control, and unreliable sleep. A Decky Loader plugin handles all fixes through the Game Mode UI.

For the detailed HID protocol documentation, see [hid-reverse-engineering.md](./hid-reverse-engineering.md).

---

## Table of Contents

1. [First Boot Diagnostics](#1-first-boot-diagnostics)
2. [Fix Face Buttons & Back Paddles](#2-fix-face-buttons--back-paddles)
3. [Fix Sleep / Suspend](#3-fix-sleep--suspend)
4. [Fan Control](#4-fan-control)
5. [Diagnostic Scripts](#5-diagnostic-scripts)
6. [Quick Reference](#6-quick-reference)

---

## 1. First Boot Diagnostics

Run these on the Bazzite machine before attempting any fixes. The output tells you which fix paths to take.

```bash
# === System Info ===
uname -r                                    # Kernel version (need 6.15+ for Strix Halo)
rpm-ostree status                           # Bazzite version and deployments
cat /sys/class/dmi/id/product_name          # DMI product name (used by InputPlumber/HHD)
cat /sys/class/dmi/id/sys_vendor            # DMI vendor
cat /sys/class/dmi/id/board_name            # DMI board name

# === Input Stack ===
systemctl status inputplumber               # Is InputPlumber running?
systemctl status hhd                        # Is HHD running?
sudo evtest                                 # List all input devices — press buttons, see what appears

# === Fan / Hardware Monitoring ===
lsmod | grep -i oxp                         # Is oxpec/oxp-sensors loaded?
ls /sys/class/hwmon/hwmon*/name | xargs -I{} sh -c 'echo "$(cat {}) → {}"'

# === EC Access ===
sudo modprobe ec_sys write_support=1
sudo xxd /sys/kernel/debug/ec/ec0/io | head -20

# === USB / HID Devices ===
lsusb                                       # List USB devices
ls /dev/input/by-id/                        # Named input device symlinks

# === Kernel Boot Parameters (current) ===
cat /proc/cmdline
```

**Save the output.** You'll reference it throughout the fixes below.

---

## 2. Fix Face Buttons & Back Paddles

### 2a. Understanding the Problem

The OneXFly Apex gamepad has two input paths:

| Path | Devices | What It Handles |
|------|---------|-----------------|
| **Xbox gamepad** (`045e:028e`) | Standard ABXY, dpad, sticks, triggers via `xpad` driver |
| **Vendor HID** (`1a86:fe00`) | Special buttons (Home, KB), back paddles (L4/R4) via hidraw |

HHD (Handheld Daemon) doesn't recognize the Apex as a known device — it's too new. Without our patches, HHD can't manage the controller, and back paddles mirror B/Y instead of being separate buttons.

### 2a-1. The Full Intercept Approach

Our fix patches HHD to enable **full intercept mode** on the vendor HID device. This sends a command that takes over the entire controller — the Xbox gamepad goes silent and ALL input (face buttons, sticks, triggers, dpad, back paddles) routes through the vendor HID channel. HHD reconstructs a virtual gamepad from these packets.

This gives us L4/R4 as separate remappable buttons, plus full control over all input processing. See [hid-reverse-engineering.md](./hid-reverse-engineering.md) for protocol details.

### 2b. Diagnose

#### Step 1: Identify what Linux sees

```bash
sudo evtest
```

This lists all `/dev/input/eventX` devices. Look for:
- Something named like `Microsoft X-Box 360 pad` or `OneXPlayer Controller`
- Something named like `AT Translated Set 2 keyboard` (where special buttons appear)

Select the gamepad device and press every button. **Record what happens:**

| Result | Meaning | Go To |
|--------|---------|-------|
| ABXY produces `BTN_A`, `BTN_B`, etc. events | Gamepad is detected, kernel driver works. Problem is in userspace remapping. | Fix A or B |
| No gamepad device exists at all | xpad driver didn't bind to the device | Fix C |
| Gamepad device exists but no events on button press | InputPlumber grabbed the device but isn't forwarding | Fix A |
| Events appear on keyboard device as key codes | Buttons are routed through EC as keyboard events | Fix A or B |

#### Step 2: Check which input daemon is active

```bash
systemctl status inputplumber 2>/dev/null
systemctl status hhd 2>/dev/null
```

Bazzite is migrating from HHD to InputPlumber. Your version may have either or both.

#### Step 3: Check if your device is recognized

```bash
# For InputPlumber:
ls /usr/share/inputplumber/devices/*onex* 2>/dev/null
journalctl -u inputplumber | grep -i "match\|onex\|apex\|profile\|device" | tail -20

# For HHD:
journalctl -u hhd | grep -i "detect\|device\|onex\|apex" | tail -20
```

If no match appears for "APEX" or your DMI product name, the device isn't recognized.

### 2c. Fix A: Add Apex to InputPlumber (Most Likely Fix)

InputPlumber uses YAML device profiles to match hardware. If the Apex isn't matched, create one.

#### Get your DMI strings (from diagnostics):

```bash
cat /sys/class/dmi/id/product_name   # e.g. "ONEXFLY APEX" or "OneXFly"
cat /sys/class/dmi/id/sys_vendor     # e.g. "ONE-NETBOOK" or "ONE-NETBOOK TECHNOLOGY CO., LTD."
```

#### Find the existing OneXFly profile as a template:

```bash
cat /usr/share/inputplumber/devices/50-onexplayer_onexfly.yaml
```

#### Create an Apex-specific profile:

```bash
sudo mkdir -p /etc/inputplumber/devices/
sudo nano /etc/inputplumber/devices/60-onexfly-apex.yaml
```

**Template** (adjust DMI strings to match your actual output):

```yaml
kind: DeviceProfile
version: v1.0
name: OneXFly APEX
matches:
  - dmi:
      product_name: "ONEXFLY APEX"
      sys_vendor: "ONE-NETBOOK"
capability_map: onexfly
source_devices:
  - group: keyboard
    hidraw:
      vendor_id: 0x2563
      product_id: 0x058d
  - group: gamepad
    evdev:
      name: "Microsoft X-Box 360 pad*"
composite_device:
  name: OneXFly APEX Controller
  type: gamepad
```

**Notes:**
- `capability_map: onexfly` reuses the existing OneXFly mapping. If this doesn't exist, try `oxp` or check what maps are available in `/usr/share/inputplumber/capability_maps/`
- The `vendor_id` and `product_id` are for the OneXFly line — verify with `lsusb` output
- If the Apex uses a different USB product ID, update accordingly

#### Restart and test:

```bash
sudo systemctl restart inputplumber
sudo evtest   # check if a virtual composite gamepad now appears
```

### 2d. Fix B: Add Apex to HHD Device Quirks

If HHD is the active daemon (not InputPlumber):

```bash
# Find HHD's OXP device list
find /usr/lib/python3* -path "*/hhd/device/oxp*" -name "*.py" 2>/dev/null
```

The device detection file contains a list of DMI product names. You need to add your Apex's `product_name` to that list.

```bash
# Example: if the file is at /usr/lib/python3.12/site-packages/hhd/device/oxp/const.py
# Find the device list and add your DMI product name
grep -n "product_name\|ONEXFLY\|OneXFly" /usr/lib/python3.12/site-packages/hhd/device/oxp/const.py
```

**On immutable Bazzite, you can't edit files in /usr/lib directly.** Options:
1. `sudo ostree admin unlock --hotfix` then edit (persists until next update)
2. Use a distrobox to install a patched HHD version
3. File an issue at [hhd-dev/hhd](https://github.com/hhd-dev/hhd/issues) requesting Apex support (Antheas is still maintaining HHD independently)

### 2e. Fix C: Kernel xpad Driver Doesn't Bind

If `evtest` shows **no gamepad device at all** and `dmesg | grep xpad` shows nothing:

```bash
# Check if xpad module is loaded
lsmod | grep xpad

# If not loaded:
sudo modprobe xpad

# Check USB device IDs
lsusb | grep -i "2563\|one-netbook\|onex\|gamepad\|xbox"

# Manually bind the device to xpad
echo "2563 058d" | sudo tee /sys/bus/usb/drivers/xpad/new_id
```

If that makes the gamepad appear in `evtest`, make it permanent:

```bash
sudo tee /etc/udev/rules.d/99-oxp-apex-gamepad.rules << 'EOF'
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="2563", ATTR{idProduct}=="058d", RUN+="/bin/sh -c 'echo 2563 058d > /sys/bus/usb/drivers/xpad/new_id'"
EOF
sudo udevadm control --reload-rules
```

### 2f. Fix D: Temporary Workaround via Steam Input

If the gamepad is detected but buttons are mapped wrong, Steam can remap at the application level:

1. Steam > Settings > Controller > General Controller Settings
2. Enable "Generic Gamepad Configuration Support"
3. Your device should appear — select it and "Define Layout"
4. Manually map each button

This only works for games launched through Steam.

### 2g. Nuclear Option: Disable InputPlumber/HHD Temporarily

If the input daemon is grabbing the device but breaking it, disable the daemon to use the raw kernel device directly:

```bash
# Disable InputPlumber
sudo systemctl stop inputplumber
sudo systemctl disable inputplumber

# OR disable HHD
sudo systemctl stop hhd
sudo systemctl disable hhd
```

The raw Xbox 360 gamepad should then work directly via xpad. You lose special button remapping and gyro, but ABXY/dpad/shoulders/triggers should work. Re-enable the daemon after creating the proper device profile.

---

## 3. Fix Sleep / Suspend

### 3a. Known Strix Halo Suspend Bugs

The AMD Strix Halo platform has **multiple overlapping suspend/resume bugs** in the amdgpu kernel driver. These are being actively fixed upstream but may not all be in your kernel yet.

| Bug | Frequency | Symptom | Root Cause | Fix/Workaround |
|-----|-----------|---------|-----------|----------------|
| VPE idle timeout hang | ~8% of resumes | Screen black 1s after wake, system frozen | VPE idle handler fires too soon (1s timeout, needs 2s) | Kernel patch to `amdgpu_vpe.c` ([amd-gfx patch](https://www.mail-archive.com/amd-gfx@lists.freedesktop.org/msg127724.html)) |
| MES firmware hang | Variable | GPU hang with MES 0x80 error | Compute wave store/resume (CWSR) bug in MES firmware | `amdgpu.cwsr_enable=0` kernel param |
| VRAM eviction OOM | Under high VRAM use | System crashes during suspend | Can't swap VRAM to disk, OOMs instead | Kernel 6.14+ partial fix; reduce VRAM allocation |
| Screen artifacts on wake | Common | Glitchy display, wrong colors | Gamescope + amdgpu timing race | Avoid minimum brightness before sleep; Bazzite updates |
| Performance degradation after resume | Intermittent | Lower FPS after wake until reboot | Bazzite kernel regression | Try stock Fedora kernel (`rpm-ostree override remove kernel-bazzite`) |
| Fans/RGB stay on during sleep | Common on OXP | Device appears asleep but hardware still running | EC not notified of suspend state | Bazzite modern standby patches (Jan 2025+) |
| Spurious wake | Common | Device wakes immediately after suspend | Fingerprint sensor / touchscreen triggering wake | Disable wake sources (see below) |

### 3b. Apply Kernel Parameter

A single kernel parameter fixes wake-from-sleep on the Apex:

```bash
rpm-ostree kargs --append-if-missing="amd_iommu=off"
```

Reboot after applying. Verify with:

```bash
cat /proc/cmdline
# Should contain: amd_iommu=off
```

Or use the standalone script which also cleans up any old sleep fix kargs:

```bash
sudo bash scripts/fix-sleep.sh
```

**Note:** `rpm-ostree kargs` creates a new ostree deployment. Any `ostree admin unlock --hotfix` overlay (e.g. button fix patches) will be lost on reboot. Re-apply the button fix after rebooting.

### 3c. Test Suspend

```bash
# Suspend
sudo systemctl suspend

# After wake, check for errors:
journalctl -b | grep -i "suspend\|resume\|amdgpu\|vpe\|mes\|error\|fail" | tail -30

# Check if GPU is healthy:
cat /sys/class/drm/card*/device/power_state
```

### 3e. If Screen Doesn't Come Back

1. **Short press power button** — may trigger resume
2. **Ctrl+Alt+F2** then **Ctrl+Alt+F1** — TTY switch can reset display
3. **SSH from another device** — `ssh deck@<device-ip>` then check `journalctl -b`
4. **Long press power button (10s)** — hard shutdown (last resort)

### 3f. Advanced: VPE Timeout Patch

If you're hitting the ~8% resume freeze and your kernel doesn't have the fix, the change is small:

In `drivers/gpu/drm/amd/amdgpu/amdgpu_vpe.c`, change:
```c
#define VPE_IDLE_TIMEOUT    msecs_to_jiffies(1000)
```
to:
```c
#define VPE_IDLE_TIMEOUT    msecs_to_jiffies(2000)
```

Building this as a standalone kernel module on immutable Bazzite is non-trivial. Options:
- Wait for Bazzite/upstream to pick up the patch
- Build a custom Bazzite image via BlueBuild with the patched kernel
- Use `ostree admin unlock --hotfix` and rebuild the amdgpu module (fragile)

### 3g. OneXFly-Specific Sleep Tips

From community reports on earlier OneXFly models:
- **Don't sleep at minimum brightness** — can cause screen-off bug requiring sleep/wake cycle to fix
- **Use higher TDP settings before sleep** — low TDP + suspend has caused hangs on OneXFly F1 Pro
- **Calibrate battery in Windows first** — F1 Pro had incorrect battery reporting causing unexpected shutdowns at 20%; OneXPlayer issued EC firmware fix for Jan 2026 production units

---

## 4. Fan Control

Fan control is handled by the Decky plugin and a standalone CLI tool.

### EC Register Map

| Register | Address | R/W | Description |
|----------|---------|-----|-------------|
| `FAN_RPM` | `0x76` | R | Fan speed (2 bytes LE) |
| `PWM_ENABLE` | `0x4A` | R/W | `0x00`=auto, `0x01`=manual |
| `PWM_VALUE` | `0x4B` | R/W | Duty cycle 0–184 (sysfs scales to 0–255) |

### Plugin

The Decky plugin provides fan control through the Game Mode QAM:
- Presets: Silent, Performance, Max, Auto
- Custom slider for manual speed control
- Real-time RPM display
- Auto mode restored on boot

### CLI Tool

`scripts/oxp-fan-ctl` is a standalone Python CLI for SSH/terminal use:

```bash
sudo scripts/oxp-fan-ctl status    # Show current RPM and mode
sudo scripts/oxp-fan-ctl set 60    # Set fan to 60%
sudo scripts/oxp-fan-ctl auto      # Return to EC auto control
sudo scripts/oxp-fan-ctl max       # Full speed
sudo scripts/oxp-fan-ctl curve     # Run temperature-responsive fan curve
```

---

## 5. Diagnostic Scripts

All scripts are in the `scripts/` directory. These are tools used during development and useful for debugging similar devices.

| Script | Purpose |
|--------|---------|
| `monitor-hidraw.py` | Monitor all hidraw devices — see raw HID reports |
| `monitor-vendor-hid.py` | Full intercept monitor with byte-level diffs |
| `monitor-intercept.py` | All-in-one: vendor HID + Xbox evdev during intercept |
| `monitor-inputs.py` | Monitor multiple evdev devices simultaneously |
| `evtest.py` | Lightweight evdev event reader |
| `button-mapper.py` | Interactive guided button mapper |
| `stick-diagnostic.py` | Comprehensive analog axis diagnostic |
| `stick-jump-detector.py` | Detect anomalous stick value jumps |
| `test-no-intercept.py` | Verify behavior without intercept (negative test) |
| `fix-sleep.sh` | Standalone sleep fix + cleanup of old kargs |
| `oxp-fan-ctl` | CLI fan control tool |

See [hid-reverse-engineering.md](./hid-reverse-engineering.md) for the recommended workflow when using these scripts on a new device.

---

## 6. Quick Reference

### Essential Commands

| Task | Command |
|------|---------|
| Kernel version | `uname -r` |
| Bazzite version | `rpm-ostree status` |
| Rollback Bazzite update | `rpm-ostree rollback` |
| Check input daemon | `systemctl status inputplumber` / `systemctl status hhd` |
| Debug buttons | `sudo evtest` |
| Check fan driver | `lsmod \| grep oxp` |
| Read EC registers | `sudo xxd /sys/kernel/debug/ec/ec0/io` |
| Test suspend | `sudo systemctl suspend` |
| Suspend logs | `journalctl -b -1 \| grep -i "suspend\|amdgpu"` |
| Restart Decky | `sudo systemctl restart plugin_loader.service` |
| Restart InputPlumber | `sudo systemctl restart inputplumber` |
| Restart HHD | `sudo systemctl restart hhd` |
| Unlock filesystem (hotfix) | `sudo ostree admin unlock --hotfix` |

### Kernel Parameters (Strix Halo)

```
amd_iommu=off
```

### EC Register Map (OneXFly Apex)

| Register | Address | R/W | Description |
|----------|---------|-----|-------------|
| `FAN_RPM` | `0x76` | R | Fan speed (2 bytes LE) |
| `PWM_ENABLE` | `0x4A` | R/W | `0x00`=auto, `0x01`=manual |
| `PWM_VALUE` | `0x4B` | R/W | Duty cycle 0–184 |
| `TURBO_SWITCH` | `0xF1` | R/W | Write `0x40` to capture |

### Key Links

| Resource | URL |
|----------|-----|
| Bazzite OXP Docs | https://docs.bazzite.gg/Handheld_and_HTPC_edition/Handheld_Wiki/OneXPlayer_Handhelds/ |
| InputPlumber | https://github.com/ShadowBlip/InputPlumber |
| HHD | https://github.com/hhd-dev/hhd |
| oxp-sensors kernel docs | https://docs.kernel.org/hwmon/oxp-sensors.html |
| Decky Plugin Template | https://github.com/SteamDeckHomebrew/decky-plugin-template |
| SimpleDeckyTDP (reference) | https://github.com/aarron-lee/SimpleDeckyTDP |
| PowerControl (reference) | https://github.com/mengmeet/PowerControl |
| VPE Timeout Patch | https://www.mail-archive.com/amd-gfx@lists.freedesktop.org/msg127724.html |
| Bazzite Strix Halo Bug | https://github.com/ublue-os/bazzite/issues/3818 |
| Memory Bandwidth Bug | https://github.com/ublue-os/bazzite/issues/3317 |
| OXP Button Bug | https://github.com/ublue-os/bazzite/issues/1635 |
| Open Gaming Collective | https://www.pcgamer.com/software/linux/a-whole-bunch-of-different-linux-gaming-distros-are-teaming-up-to-improve-the-open-source-gaming-ecosystem/ |

### Verification Checklist

After implementing all fixes:

- [ ] `sudo evtest` → press every button → all produce correct events
- [ ] Launch a game → ABXY, dpad, shoulders, triggers, sticks all work
- [ ] `sudo oxp-fan-ctl status` → shows RPM and mode
- [ ] `sudo oxp-fan-ctl set 80` → fan audibly speeds up
- [ ] Decky QAM → fan plugin appears with working controls
- [ ] `cat /proc/cmdline` → contains `amd_iommu=off`
- [ ] `sudo systemctl suspend` → device sleeps and wakes cleanly
- [ ] After wake: display, audio, controller, fan control all still work
- [ ] `journalctl -b` → no amdgpu errors after resume
