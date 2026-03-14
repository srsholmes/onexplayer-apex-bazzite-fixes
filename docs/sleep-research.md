# AMD Strix Halo S0i3/s2idle Sleep Research

Research conducted 2026-03-02 for OneXPlayer Apex running Bazzite (kernel 6.17.7).
Updated 2026-03-14: Added GPD Win 5 deep sleep claim investigation, deep sleep enablement approaches, and DSDT/kernel backport guides.

## Current Status

**Light sleep (s2idle) is WORKING** as of 2026-03-11. The fix was enabling **"ACPI Auto configuration"** in the BIOS. With this BIOS setting and `mem_sleep_default=s2idle`, the device enters s2idle and wakes successfully.

**S0i3 deep sleep does NOT work on Strix Halo with kernel 6.17.** The kernel is missing ACPI C4 support required for VDD OFF / S0i3. This is expected to land in kernel 6.18+. Light sleep (s2idle) is a partial solution — lower power than awake but higher than S0i3.

**The plugin now applies light sleep kargs** (`mem_sleep_default=s2idle`) and automatically removes known-problematic legacy kargs.

### Working configuration (2026-03-11)
- **BIOS**: "ACPI Auto configuration" = Enabled
- **Kargs**: `mem_sleep_default=s2idle amd_iommu=off`
- **Kernel**: 6.17.7-ba25.fc43.x86_64
- **Sleep mode**: s2idle (light sleep)

## GPD Win 5 Deep Sleep Claim Investigation

### The claim
A user reported that deep sleep works on the GPD Win 5 (also AMD Strix Halo / Ryzen AI Max) running Bazzite. This prompted an investigation into whether the GPD Win 5 has achieved something the OneXPlayer Apex has not, and if so, how.

### Investigation findings

1. **Same motherboard**: Both GPD Win 5 and OneXPlayer Apex use the **Sixunited AXB35 motherboard**. Their ACPI tables, EC firmware, and power management firmware are identical or near-identical. All AXB35 firmwares are reported to be cross-compatible across OEMs ([Strix Halo Wiki](https://strixhalo.wiki/Hardware/Boards/Sixunited_AXB35/Firmware)).

2. **No public evidence of deep sleep on GPD Win 5**: Searched GitHub (ublue-os/bazzite issues and PRs), community forums, and Reddit. No confirmed reports of S0i3 or S3 deep sleep working on GPD Win 5 with Bazzite were found.

3. **No GPD Win 5 sleep PRs in Bazzite**: The only GPD Win 5 PRs in ublue-os/bazzite are for audio (microphone DSP profiles — PRs #3942, #3980). No sleep-related PRs exist.

4. **GPD handhelds have sleep problems**: Bazzite issue [#3577](https://github.com/ublue-os/bazzite/issues/3577) reports GPD devices with lids immediately going back to sleep after being woken — a worse sleep/wake experience than the Apex.

5. **No official Bazzite support for GPD Win 5**: GPD claimed "official" Bazzite adaptation in January 2026, but the Bazzite team disputed this, stating they had not received hardware from GPD and had no recent contact.

6. **Same kernel limitation applies**: Both devices run the same Bazzite kernel (6.17.x). The ACPI C4 limitation that blocks S0i3 on the Apex applies equally to the GPD Win 5.

### Most likely explanation

The user observed **s2idle working** (screen off, low power, successful wake) and interpreted it as "deep sleep." To a non-technical user, working s2idle is indistinguishable from S0i3 in casual use — the key difference is power draw during sleep (~3-8W for s2idle vs ~0.5-1W for S0i3), not observable behavior.

### Conclusion

**No evidence supports that the GPD Win 5 achieves deeper sleep than the OneXPlayer Apex on Bazzite.** Both devices share the same motherboard, the same firmware architecture, and face the same kernel limitation (missing ACPI C4 support). The GPD Win 5 likely has s2idle working with the same BIOS and karg configuration documented above.

## Problem

After sleep, the Apex:
- Screen turns off but fans keep running (never enters deep sleep)
- S0i3 residency = 0 (never reached hardware deep sleep)
- SMU Hint Count = 0 (kernel never asked SMU to enter S0i3)
- Black screen on wake (amdgpu resume failure) — requires hard power off

## Test Results Summary

### Test 1: `amd_iommu=off` (main branch)
- **Result**: Prevented IOMMU initialization entirely, blocked S0i3 path
- IOMMU is required for S0i3 — disabling it blocks deep sleep
- However, `amd_iommu=off` was later found to be **required for s2idle** on Strix Halo — s2idle fails without it

### Test 2: `amd_iommu=on` + `iommu=pt` (early test/sleep branch)
- `amd_iommu=on` is **invalid** for AMD — silently ignored (`AMD-Vi: Unknown option - 'on'`)
- Only `iommu=pt` was active
- **Result**: Screen turned off, fans kept running, SMU Hint Count=0, black screen on wake, hard power off required

### Test 3: `iommu=pt` + `acpi.ec_no_wakeup=1` (final test/sleep branch)
- **Result**: `PM: suspend entry (s2idle)` logged, but S0i3 NOT reached
- SMU Hint Count=0 — kernel never sent S0i3 hint to SMU
- Device didn't wake — hard power off required
- `fw-fanctrl-suspend` errors kept fans running during sleep

### Test 4: BIOS "ACPI Auto configuration" (2026-03-11)
- **BIOS change**: Enabled "ACPI Auto configuration"
- **Kargs**: `mem_sleep_default=s2idle`
- **Result**: **Light sleep (s2idle) WORKING** — device sleeps and wakes successfully
- This is not S0i3 deep sleep (no hardware deep sleep / VDD OFF) but s2idle works
- The BIOS setting was the key missing piece — it configures ACPI tables to allow proper s2idle entry/exit on Strix Halo

### Conclusion
S0i3 deep sleep still requires kernel 6.18+ for ACPI C4 support. However, **light sleep (s2idle) works** when "ACPI Auto configuration" is enabled in the BIOS. This is the recommended configuration until kernel 6.18+ is available.

## Understanding Sleep Levels on Strix Halo

| Level | Name | What Happens | Power Draw | Status |
|-------|------|-------------|------------|--------|
| s2idle | Suspend-to-idle (light sleep) | CPU enters idle, clocks gated, peripherals may stay powered | ~3-8W | **WORKING** |
| S0i3 | Deep idle within S0 (deep sleep) | VDD OFF, all power rails cut except memory self-refresh | ~0.5-1W | **BLOCKED** (kernel 6.17 — needs C4) |
| S3 | Suspend-to-RAM (legacy deep sleep) | Full hardware suspend, context saved to RAM only | ~0.3-1W | **NOT SUPPORTED** by firmware |

**Critical distinction**: Strix Halo firmware advertises `S0 S4 S5` only — **no S3**. The intended deep sleep path on modern AMD is **S0i3 reached via the s2idle entry point**, not traditional S3. When the kernel enters s2idle, it *can* reach S0i3 depth if all prerequisites are met (ACPI C4 support, IOMMU configured, ASPM L1.2 on all PCIe devices, no wake source blockers). Without those prerequisites, s2idle stays at the shallow "light sleep" level.

## Approaches to Enable Deep Sleep

### Approach 1: Wait for Kernel 6.18+ (ACPI C4 Support) — RECOMMENDED

- **Description**: Kernel 6.18+ adds proper ACPI C4 handling for Strix Halo. C4 is the deep idle state that triggers VDD OFF → S0i3.
- **Risk**: None
- **Pros**: Upstream, supported, permanent fix, no device risk
- **Cons**: Passive — depends on Bazzite's kernel update schedule
- **When kernel 6.18+ arrives**:
  1. Remove `amd_iommu=off` karg (S0i3 requires IOMMU enabled)
  2. Add `iommu=pt` and potentially `amd_iommu=fullflush` (reported to fix suspend on Ryzen AI 300 Framework)
  3. Run `sudo amd-s2idle test` to verify S0i3 residency
  4. Check `/sys/kernel/debug/amd_pmc/s0ix_stats` for non-zero entry counts
  5. Check `/sys/power/suspend_stats/last_hw_sleep` for non-zero hardware sleep time

### Approach 2: Backport Kernel C4 Patches — VIABLE (Advanced)

- **Description**: Backport the ACPI C4 support patches from kernel 6.18 to the current 6.17 kernel. See [Kernel C4 Backport Guide](#kernel-c4-backport-guide) below.
- **Risk**: Low-moderate (upstream patches applied to older kernel could have conflicts)
- **Pros**: Achieves deep sleep before Bazzite ships 6.18; uses proper upstream patches
- **Cons**: Requires building custom kernel; complex on immutable Bazzite

### Approach 3: DSDT/ACPI Table Override — EXPERIMENTAL (High Risk)

- **Description**: Extract DSDT, patch to improve S0ix support or force S3 advertisement, load via initrd override. See [DSDT Patching Guide](#dsdt-patching-guide-experimental) below.
- **Risk**: **HIGH** — firmware explicitly does not advertise S3. Forcing it can cause hangs, data loss, or filesystem corruption.
- **Pros**: No kernel changes needed; can test quickly
- **Cons**: Even if S3 is patched into DSDT, the SoC's power management state machine may not implement the S3 power-down sequence. At best it might be equivalent to s2idle with extra risk. DSDT patches are fragile and device-specific.

### Approach 4: Smokeless UMAF / Hidden BIOS Settings — NOT RECOMMENDED

- **Description**: Use [Smokeless UMAF](https://github.com/DavidS95/Smokeless_UMAF) to access hidden AMD CBS/PBS settings that may allow switching from Modern Standby to S3.
- **Risk**: **VERY HIGH** — wrong BIOS settings can brick the device
- **Pros**: Could enable proper S3 at firmware level if the option exists
- **Cons**: Hidden settings are hidden for a reason; may not exist on AXB35 firmware; could render device unbootable
- **Status**: Documented for completeness only. Not recommended.

## Kernel C4 Backport Guide

To backport ACPI C4 support from kernel 6.18 to the current Bazzite kernel:

### 1. Identify the patches

Search the kernel git log for ACPI C4 / acpi_idle changes targeting Strix Halo in the 6.18 development cycle:

```bash
# Clone the kernel source
git clone --depth=1 https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
cd linux

# Search for C4-related changes in acpi_idle
git log --oneline --all -- drivers/acpi/processor_idle.c | head -20
git log --oneline --all -- arch/x86/kernel/acpi/cstate.c | head -20

# Search for Strix Halo / gfx1151 specific power management changes
git log --all --grep="C4" --grep="acpi_idle" --all-match --oneline | head -20
git log --all --grep="strix" --grep="idle" --all-match --oneline | head -20
```

Key files to check:
- `drivers/acpi/processor_idle.c` — ACPI processor idle state handling
- `arch/x86/kernel/acpi/cstate.c` — x86 C-state support
- `drivers/platform/x86/amd/pmc/` — AMD PMC (Power Management Controller)

### 2. Build custom kernel on Bazzite

Bazzite uses `bazzite-org/kernel-bazzite` (Fedora kernel config + Bazzite patches):

```bash
# Clone the Bazzite kernel repo
git clone https://github.com/bazzite-org/kernel-bazzite.git
cd kernel-bazzite

# Apply your C4 backport patches
# (add .patch files to the patches directory or apply directly)

# Build the kernel RPMs (follow Fedora kernel build process)
# This produces kernel-*.rpm files

# On the Apex, replace the kernel using rpm-ostree
sudo rpm-ostree override replace ./kernel-*.rpm

# Reboot to the new kernel
sudo systemctl reboot
```

### 3. Test S0i3 with the new kernel

```bash
# Verify kernel version
uname -r

# Check for C4 processing in dmesg
dmesg | grep -iE "c4|acpi_idle|deep|processor.*idle"

# Update kargs for S0i3 (remove amd_iommu=off, add iommu=pt)
sudo rpm-ostree kargs --delete-if-present=amd_iommu=off --append=iommu=pt

# After reboot, test S0i3
sudo amd-s2idle test

# Check S0i3 residency
sudo cat /sys/kernel/debug/amd_pmc/s0ix_stats
sudo cat /sys/kernel/debug/amd_pmc/smu_fw_info
cat /sys/power/suspend_stats/last_hw_sleep
```

### 4. Submit upstream

If the backport works, submit a PR to `bazzite-org/kernel-bazzite` to include the C4 patches for all Strix Halo users on Bazzite.

## DSDT Patching Guide (Experimental)

> **WARNING**: This approach is experimental. The Strix Halo firmware does NOT advertise S3 support. Forcing S3 via DSDT patching can cause hangs, data loss, filesystem corruption, or require hard power-off. Proceed at your own risk.

### Prerequisites

Bazzite's kernel has `CONFIG_ACPI_TABLE_UPGRADE=y` enabled (even with Secure Boot), which allows loading modified ACPI tables from the initrd.

Install ACPI tools:
```bash
sudo dnf install acpica-tools
```

### 1. Extract and decompile DSDT

```bash
# Extract all ACPI tables
sudo acpidump > acpi_tables.dat
acpixtract -a acpi_tables.dat

# Decompile DSDT to human-readable ASL
iasl -d dsdt.dat    # Produces dsdt.dsl
```

### 2. Analyze current sleep states

```bash
# Check what sleep states are defined
grep -E "_S[0-5]_|_LPI|Cx.*deep|Package.*sleep" dsdt.dsl
```

Expect to find `_S0_`, `_S4_`, `_S5_` defined but **no `_S3_`**.

### 3. Patching options

**Option A — Improve LPI/C4 definitions (less risky)**:
Look for `_LPI` (Low Power Idle) objects in the DSDT and ensure C4/deep idle states are properly defined. This works within the S0ix paradigm and doesn't try to enable unsupported S3.

**Option B — Force S3 advertisement (risky)**:
Add the `_S3_` sleep state definition to the DSDT:
```asl
Name (_S3_, Package (0x04) { 0x05, 0x05, 0x00, 0x00 })
```
This tells the kernel S3 is available. However, the SoC firmware may not implement the S3 power-down sequence — the CPU may not actually enter S3.

### 4. Recompile and create CPIO archive

```bash
# Increment the OEM revision number in dsdt.dsl (find OEM Revision line, bump by 1)
# This ensures the patched table takes priority

# Recompile
iasl -tc dsdt.dsl   # Produces dsdt.aml

# Create the CPIO override archive
mkdir -p kernel/firmware/acpi
cp dsdt.aml kernel/firmware/acpi/
find kernel | cpio -o -H newc > /boot/acpi_override.cpio
```

### 5. Load via GRUB on Bazzite

On Bazzite (GRUB2 + ostree), configure early initrd loading:

```bash
# Edit GRUB defaults
sudo nano /etc/default/grub

# Add the ACPI override CPIO as an early initrd:
# GRUB_EARLY_INITRD_LINUX_STOCK="acpi_override.cpio"

# If forcing S3, also add kernel parameter:
# Append to GRUB_CMDLINE_LINUX: mem_sleep_default=deep

# Regenerate GRUB config
sudo grub2-mkconfig -o /boot/grub2/grub.cfg
```

### 6. Verify

After reboot:
```bash
# Check if patched DSDT was loaded
dmesg | grep -i "ACPI.*upgrade\|ACPI.*override"

# Check available sleep states
cat /sys/power/mem_sleep
# If S3 patching worked, should show: s2idle [deep]
# Without S3: [s2idle]

# Test sleep (be prepared for a hard power-off if it hangs)
sudo systemctl suspend
```

## Recommended Path Forward

1. **Now**: Continue using the current s2idle configuration. It works, it's stable, and provides meaningful power savings over staying fully awake.

2. **When kernel 6.18+ lands in Bazzite**: Test S0i3 immediately. Remove `amd_iommu=off`, add `iommu=pt`, run `amd-s2idle test`. This is the highest-probability path to real deep sleep with lowest risk.

3. **If impatient (advanced users only)**: Attempt the kernel C4 backport (Approach 2). This is the safest "do it now" option because it uses upstream patches that are intended to fix this exact problem.

4. **Not recommended**: DSDT patching (Approach 3) and Smokeless UMAF (Approach 4) carry significant risk with low probability of success, given that the firmware does not implement S3.

5. **GPD Win 5 claim**: Investigated and debunked. No evidence supports that the GPD Win 5 achieves deeper sleep than the Apex on Bazzite. Both devices share the same motherboard and face the same kernel limitation. See [investigation details](#gpd-win-5-deep-sleep-claim-investigation) above.

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

## Kargs

### Active (light sleep — working)
- `mem_sleep_default=s2idle` — explicitly sets s2idle as default sleep mode
- `amd_iommu=off` — required for s2idle on Strix Halo (blocks S0i3 but s2idle won't work without it)

### Problematic (auto-removed by plugin)
- `amd_iommu=on` — invalid AMD parameter, silently ignored
- `acpi.ec_no_wakeup=1` — suppress EC wakeup events
- `amdgpu.cwsr_enable=0` — compute-specific, not needed for sleep
- `amdgpu.gttsize=126976` — not sleep-related
- `ttm.pages_limit=32505856` — not sleep-related

### Removed (previously tried, not needed)
- `iommu=pt` — IOMMU passthrough, not needed for s2idle

### Potential changes when kernel 6.18+ is available
- Remove `amd_iommu=off` — S0i3 requires IOMMU enabled; may need to swap for `amd_iommu=fullflush` (reported to fix suspend on Ryzen AI 300 Framework)
- `iommu=pt` — may be needed for S0i3

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

## References
- [Linux Kernel AMD Debugging Docs](https://docs.kernel.org/arch/x86/amd-debugging.html)
- [Fedora IOMMU Sleep Investigation](https://discussion.fedoraproject.org/t/investigating-the-role-of-iommu-in-fixing-linux-sleep-issues-with-modern-standby/142021)
- [Framework 13 Ryzen AI 300 Suspend Failures](https://community.frame.work/t/framework-13-ryzen-ai-300-fails-to-properly-suspend-and-resume-from-suspend/74660)
- [Framework Ryzen AI 9 HX Suspend Capability](https://community.frame.work/t/amd-ryzen-ai-9-hx-suspend-capability/70305)
- [Bazzite #2553 — OXP Fingerprint Wake](https://github.com/ublue-os/bazzite/issues/2553)
- [Bazzite #2081 — OXP AMD Black Screen After Suspend](https://github.com/ublue-os/bazzite/issues/2081)
- [Bazzite #3577 — GPD Device Immediately Goes Back to Sleep After Waking](https://github.com/ublue-os/bazzite/issues/3577)
- [amdgpu Sleep-Wake Hang](https://nyanpasu64.gitlab.io/blog/amdgpu-sleep-wake-hang/)
- [amd-debug-tools (amd-s2idle)](https://github.com/superm1/amd-debug-tools)
- [Bazzite Kernel Repository](https://github.com/bazzite-org/kernel-bazzite)
- [Linux Kernel ACPI Table Override Documentation](https://docs.kernel.org/admin-guide/acpi/initrd_table_override.html)
- [Enabling S3 Sleep on Lenovo Yoga 7 AMD Gen 7 (DSDT Patching)](https://saveriomiroddi.github.io/Enabling-the-S3-sleep-suspend-on-the-Lenovo-Yoga-7-AMD-Gen-7-and-possibly-others/)
- [Acer SF314-43 ACPI Fix (S3 on AMD)](https://github.com/lbschenkel/acer-sf314_43-acpi-fix)
- [Smokeless UMAF (Hidden BIOS Settings)](https://github.com/DavidS95/Smokeless_UMAF)
- [Strix Halo Wiki — AXB35 Firmware Compatibility](https://strixhalo.wiki/Hardware/Boards/Sixunited_AXB35/Firmware)
- [Fedora DSDT Override Discussion](https://discussion.fedoraproject.org/t/how-to-ovveride-dsdt-differentiated-system-description-table-in-fedora-to-enable-s3-sleep/77096)
