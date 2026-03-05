# AMD Strix Halo S0i3/s2idle Sleep Research

Research conducted 2026-03-02 for OneXPlayer Apex running Bazzite (kernel 6.17.7).

## Current Status

**S0i3 deep sleep does NOT work on Strix Halo with kernel 6.17.** The kernel is missing ACPI C4 support required for VDD OFF / S0i3. This is expected to land in kernel 6.18+. No kernel parameter fix is available — the issue is in the kernel's ACPI/PM code, not configuration.

**All previous sleep fix kargs have been removed.** The plugin now provides a cleanup tool to remove any previously applied kargs. No new kargs are applied.

## Problem

After sleep, the Apex:
- Screen turns off but fans keep running (never enters deep sleep)
- S0i3 residency = 0 (never reached hardware deep sleep)
- SMU Hint Count = 0 (kernel never asked SMU to enter S0i3)
- Black screen on wake (amdgpu resume failure) — requires hard power off

## Test Results Summary

### Test 1: `amd_iommu=off` (main branch)
- **Result**: Prevented IOMMU initialization entirely, blocked S0i3 path
- IOMMU is required for S0i3 — disabling it was counterproductive

### Test 2: `amd_iommu=on` + `iommu=pt` (early test/sleep branch)
- `amd_iommu=on` is **invalid** for AMD — silently ignored (`AMD-Vi: Unknown option - 'on'`)
- Only `iommu=pt` was active
- **Result**: Screen turned off, fans kept running, SMU Hint Count=0, black screen on wake, hard power off required

### Test 3: `iommu=pt` + `acpi.ec_no_wakeup=1` (final test/sleep branch)
- **Result**: `PM: suspend entry (s2idle)` logged, but S0i3 NOT reached
- SMU Hint Count=0 — kernel never sent S0i3 hint to SMU
- Device didn't wake — hard power off required
- `fw-fanctrl-suspend` errors kept fans running during sleep

### Conclusion
The issue is not a configuration problem. The kernel's ACPI PM code for Strix Halo doesn't support the C4 state transition needed for S0i3. This requires a kernel update (6.18+), not kargs.

## ACPI C4 Requirement (Kernel 6.18+)

Strix Halo (gfx1151) requires ACPI C4 support for VDD OFF, which is the prerequisite for S0i3. The ACPI tables on the Apex define C4 as the deep idle state, but kernel 6.17's `acpi_idle` driver doesn't process the C4 entry correctly for this platform. Patches are expected in the 6.18 cycle.

Without C4 → VDD OFF → S0i3, the system can only reach s2idle (CPU idle, but no hardware deep sleep). This results in:
- Higher power drain during sleep
- Fans may stay running (especially with `fw-fanctrl-suspend` failing)
- Possible resume failures from partial suspend states

## fw-fanctrl-suspend Issue

`/usr/lib/systemd/system-sleep/fw-fanctrl-suspend` is a Framework Laptop tool shipped with Bazzite's `fw-fanctrl` package. It fails on non-Framework hardware:

```
systemd-sleep: [Error] > An error occurred: [Errno 2] No such file or directory
(sd-exec-strv): /usr/lib/systemd/system-sleep/fw-fanctrl-suspend failed with exit status 1.
```

This causes fans to stay running during sleep on the Apex.

### How to neutralize it

On Bazzite (ostree), `/usr/lib` is read-only. To neutralize:

```bash
# Unlock the filesystem (survives reboots, lost on OS updates)
sudo ostree admin unlock --hotfix

# Option A: Make the script a no-op
sudo bash -c 'echo "#!/bin/bash" > /usr/lib/systemd/system-sleep/fw-fanctrl-suspend'
sudo chmod +x /usr/lib/systemd/system-sleep/fw-fanctrl-suspend

# Option B: Remove it entirely
sudo rm /usr/lib/systemd/system-sleep/fw-fanctrl-suspend

# Verify
ls -la /usr/lib/systemd/system-sleep/fw-fanctrl-suspend
```

Note: this won't fix S0i3 — it only prevents the error message and allows fans to properly respond to sleep events once deep sleep is supported.

## Key Findings

### 1. `amd_iommu=on` is INVALID for AMD

`amd_iommu=on` is not a valid value. dmesg shows: `AMD-Vi: Unknown option - 'on'`.

This is an Intel parameter (`intel_iommu=on`). Valid AMD values:
- `fullflush` (deprecated, equivalent to `iommu.strict=1`)
- `off` (disable IOMMU)
- `force_isolation`, `force_enable`
- `pgtbl_v1` / `pgtbl_v2`

The parameter was silently ignored. Only `iommu=pt` was doing anything.

Reference: https://www.kernel.org/doc/html/latest/admin-guide/kernel-parameters.html?highlight=amd_iommu

### 2. Hint Count = 0 Means S0i3 Was Never Attempted

The SMU was never asked to enter S0i3. Something blocks the s2idle flow before the hardware sleep notification phase.

Common blockers on AMD platforms:
| Blocker | Description | Diagnostic |
|---------|-------------|------------|
| PCIe ASPM L1.2 not enabled | Any PCIe device without L1.2 blocks deepest idle | `lspci -vv` |
| Missing StorageD3Enable | BIOS bug — NVMe doesn't enter D3 | `dmesg \| grep "simple suspend"` |
| HPD interrupts on eDP | Display hotplug detection blocks IPS entry | Fixed in kernel 6.14+ |
| UCSI/USB-C errors | Type-C connector failures interrupt suspend | `dmesg \| grep ucsi` |
| EC wakeup events | Embedded Controller wakes device immediately | `acpi.ec_no_wakeup=1` |
| Fingerprint sensor | USB wakeup from FocalTech 2808:c652 | udev rule to disable |
| Missing ACPI C4 support | Kernel doesn't process C4 for Strix Halo | **Kernel 6.18+** |

Reference: https://docs.kernel.org/arch/x86/amd-debugging.html

### 3. GPU Wake Failure (Black Screen)

Known amdgpu resume issue on Strix Halo:
- amdgpu driver can fail during suspend/resume if insufficient RAM to back up VRAM
- Strix Halo (gfx1151) has known stability issues — kernels older than 6.18.4 may have problems
- OneXPlayer AMD devices have documented black-screen-after-suspend issues (Bazzite #2081)

References:
- https://nyanpasu64.gitlab.io/blog/amdgpu-sleep-wake-hang/
- https://github.com/ublue-os/bazzite/issues/2081
- https://github.com/ROCm/ROCm/issues/5590

### 4. ACPI facts

- `ACPI: PM: (supports S0 S4 S5)` — no S3, only S0ix path
- `Low-power S0 idle used by default for system suspend`
- `mem_sleep` = `[s2idle]` only
- NVMe has `platform quirk: setting simple suspend` (good)

### 5. External USB devices may block S0i3

During testing, external devices were connected via USB-C dock:
- Magic Keyboard, Gaming Mouse, AX88179A Ethernet, NS1081, USB hub
- These are prime suspects for blocking S0i3 even if the kernel supported it
- Future testing should be done with all external USB devices disconnected

## PM Debug Messages

For future debugging when kernel 6.18+ is available, enable persistent PM debug messages:

```bash
# Enable debug messages (immediate)
echo 1 | sudo tee /sys/power/pm_debug_messages
echo 1 | sudo tee /sys/power/pm_print_times

# Make persistent via tmpfiles (survives reboots)
echo 'w /sys/power/pm_debug_messages - - - - 1' | sudo tee /etc/tmpfiles.d/pm-debug.conf
echo 'w /sys/power/pm_print_times - - - - 1' | sudo tee -a /etc/tmpfiles.d/pm-debug.conf
```

## Kargs Applied and Removed

### All kargs (now removed)
- `iommu=pt` — IOMMU passthrough mode
- `acpi.ec_no_wakeup=1` — suppress EC wakeup events
- `amd_iommu=on` — invalid AMD parameter, silently ignored
- `amd_iommu=off` — prevented IOMMU initialization, blocked S0i3 entirely
- `amdgpu.cwsr_enable=0` — compute-specific, not needed for sleep
- `amdgpu.gttsize=126976` — not sleep-related
- `ttm.pages_limit=32505856` — not sleep-related

### Potential additions when kernel 6.18+ is available
- `amd_iommu=fullflush` — reported to fix suspend on Ryzen AI 300 (Framework)
- `iommu=pt` — may still be needed for S0i3

## Diagnostic Tools

### AMD's official diagnostic tool
```bash
pip install amd-s2idle
sudo amd-s2idle test    # Runs timed suspend cycle
sudo amd-s2idle report  # Generates diagnostic report
```
Source: https://github.com/superm1/amd-debug-tools

Note: requires `python-devel` to build on Python 3.14 (cysystemd dependency).

### Manual diagnostics
```bash
# Enable PM debug messages
echo 1 | sudo tee /sys/power/pm_debug_messages
echo 1 | sudo tee /sys/power/pm_print_times

# After resume, check:
sudo cat /sys/kernel/debug/amd_pmc/s0ix_stats
sudo cat /sys/kernel/debug/amd_pmc/smu_fw_info
cat /sys/power/suspend_stats/last_hw_sleep
cat /sys/power/pm_wakeup_irq
sudo dmesg | grep -iE "constraint|s0i3|s0ix|lps0|suspend|resume"
```

### Check what's blocking S0i3
```bash
# PCIe ASPM status
lspci -vv | grep -E "LnkCtl:|ASPM"

# Active PCI runtime devices
for d in /sys/devices/pci*/*; do
  if [ -f "$d/power/runtime_status" ]; then
    status=$(cat "$d/power/runtime_status" 2>/dev/null)
    [ "$status" = "active" ] && echo "$(basename $d) = $status"
  fi
done

# USB wakeup sources
for d in /sys/bus/usb/devices/*/power/wakeup; do
  echo "$(dirname $d | xargs basename): $(cat $d)"
done

# ACPI wakeup sources
cat /proc/acpi/wakeup
```

## System Info
- Device: OneXPlayer Apex (AMD Strix Halo)
- OS: Bazzite (Fedora-based, ostree)
- Kernel: 6.17.7-ba25.fc43.x86_64
- Sleep mode: s2idle only (no S3)
- SMU idlemask: 0x5eb4390c

## Hibernate (S4) — Viable Alternative

Since S0i3 doesn't work and S3 is not available (ACPI supports S0, S4, S5 only), **hibernate (S4 — suspend to disk) is the only viable deep power-saving option** until kernel 6.18+.

### Why HHD Hibernate Fails on Bazzite

HHD's hibernate wraps `systemctl hibernate` — the mechanism is fine, but Bazzite's default config is missing resume infrastructure. The system hibernates (writes to swap, powers off), but on boot it does a **fresh boot instead of resuming** because:

1. **Missing dracut `resume` module** — initramfs never checks for a hibernate image on the swap device
2. **zram used by default** — RAM-backed compressed swap can't persist to disk
3. **No `resume=` / `resume_offset=` kernel params** — kernel doesn't know where to look for the saved image

### S4 vs s2idle Resume Path

| Factor | s2idle (broken) | S4 Hibernate |
|--------|----------------|--------------|
| GPU state | Kept in VRAM (fails to restore) | Written to swap, full cold-boot re-init |
| Resume path | Driver resumes from idle | Full driver initialization from power-off |
| Power state | Partial (fans run, battery drains) | Full power off (zero drain) |
| Reliability | Black screen, MES hang | Expected more reliable (avoids idle resume bugs) |

S4 uses a **full cold-boot re-init** for the GPU, which should avoid the s2idle resume black screen issues since the driver initializes from scratch rather than trying to restore from an idle state.

### Setup Requirements on Bazzite

The plugin's `hibernate_setup.py` module automates all of this:

1. Create BTRFS `/swap` subvolume (excluded from snapshots)
2. Create swap file (>= RAM size, ~66GB for 64GB RAM)
3. Disable zram (`/etc/systemd/zram-generator.conf`)
4. Add fstab entry for swap
5. Add `resume=UUID=<UUID>` and `resume_offset=<OFFSET>` kernel params
6. Add dracut resume module config (`/etc/dracut.conf.d/resume.conf`)
7. Enable initramfs regeneration (`rpm-ostree initramfs --enable`)
8. Reboot

### Known Risks

- **Disk space**: 64GB RAM requires ~66GB swap file
- **amdgpu S4 resume**: Untested on Strix Halo, but expected to be more reliable than s2idle since it uses cold-boot initialization
- **rpm-ostree kargs**: Creates new deployment, losing any `ostree admin unlock --hotfix` overlays (button fix must be re-applied)
- **Bazzite updates**: Swap file and fstab entry persist, but dracut config and kargs survive updates

## References
- [Linux Kernel AMD Debugging Docs](https://docs.kernel.org/arch/x86/amd-debugging.html)
- [Fedora IOMMU Sleep Investigation](https://discussion.fedoraproject.org/t/investigating-the-role-of-iommu-in-fixing-linux-sleep-issues-with-modern-standby/142021)
- [Framework 13 Ryzen AI 300 Suspend Failures](https://community.frame.work/t/framework-13-ryzen-ai-300-fails-to-properly-suspend-and-resume-from-suspend/74660)
- [Framework Ryzen AI 9 HX Suspend Capability](https://community.frame.work/t/amd-ryzen-ai-9-hx-suspend-capability/70305)
- [Bazzite #2553 — OXP Fingerprint Wake](https://github.com/ublue-os/bazzite/issues/2553)
- [Bazzite #2081 — OXP AMD Black Screen After Suspend](https://github.com/ublue-os/bazzite/issues/2081)
- [amdgpu Sleep-Wake Hang](https://nyanpasu64.gitlab.io/blog/amdgpu-sleep-wake-hang/)
- [amd-debug-tools (amd-s2idle)](https://github.com/superm1/amd-debug-tools)
