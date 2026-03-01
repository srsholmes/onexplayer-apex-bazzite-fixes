# Testing Plan

## 1. Sleep Fix v2

The old sleep fix (4 kargs + udev rule) is being replaced with a single `amd_iommu=off` karg, reported to fix wake-from-sleep.

```bash
# SSH into the Apex, then:
sudo bash scripts/fix-sleep-v2.sh
# Reboot when prompted
systemctl reboot

# After reboot, verify:
cat /proc/cmdline
# Should contain: amd_iommu=off
# Should NOT contain: amdgpu.cwsr_enable=0, iommu=pt, amdgpu.gttsize=126976, ttm.pages_limit=32505856

# Test suspend:
sudo systemctl suspend
# Wake the device — it should come back

# Check logs after wake:
journalctl -b | grep -i 'suspend\|resume\|iommu\|error\|fail' | tail -30
```

## 2. Controller-after-sleep bug

After confirming wake works, test whether the controller still functions after resume. If it doesn't, grab logs:

```bash
journalctl -b | grep -i 'usb\|hid\|input\|controller' | tail -50
```

## 3. Button Fix — back paddle remapping (hid_v2.py)

Download the build artifact and sideload:

```bash
# Extract to plugin dir
cd ~/homebrew/plugins
unzip OneXPlayer_Apex_Tools.zip
sudo systemctl restart plugin_loader.service
```

- **Fresh install**: Toggle Button Fix on — all 3 files should be patched (const, base, hid_v2)
- **Upgrade from old patch**: UI should show "Update available — toggle off then on for back paddle support". Toggle off, then on.
- Verify back paddles are remapped correctly

## 4. Fan speed slider

- Switch to Manual Fan Control
- Select "Custom (slider)" profile
- Drag the slider on the touchscreen — should be smooth, no jank
- Let go — fan speed should commit after release, not during drag
- Verify RPM updates to match the set speed
