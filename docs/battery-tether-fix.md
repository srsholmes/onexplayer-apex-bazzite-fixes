# Battery Tether Cable Detection Fix

## Problem

When the OneXPlayer Apex's 85Wh swappable battery is connected via the **tether cable** (instead of directly into the device slot), the battery is reported as **"absent"** by the OS. This occurs on both Bazzite and CachyOS. The battery works normally when directly connected.

## Root Cause

The Apex's Embedded Controller (EC) uses a presence detection mechanism (likely a sense pin or detection line) to determine if a battery is physically installed. The tether cable either:

1. **Does not carry the detection/sense pin** — the cable may only route power lines (V+, GND) and data lines (I2C/SMBus for charge info), but not the physical presence detect pin
2. **Introduces enough resistance** on the sense line that the EC reads it as "no battery"
3. **The EC firmware** explicitly checks for direct-connect vs. cable-connect and disables battery reporting via cable

The ACPI DSDT contains a `_STA` (Status) method for the battery device (typically `BAT0`) that queries the EC to determine if the battery is present. When the EC reports "not present," the `_STA` method returns 0x00, and the kernel's ACPI battery driver marks the battery as absent.

## Diagnostic Script

Run the diagnostic script to gather detailed information about battery detection:

```bash
sudo ./scripts/battery-tether-diag.sh ~/Downloads/battery-diag.txt
```

Run it **twice** — once with the battery directly connected, once via tether cable — and compare the outputs. Key things to look for:

- **Power supply sysfs**: Does `/sys/class/power_supply/BAT0/` exist? Is `present` = 1?
- **EC registers**: Which registers differ between direct and tether connection? The differing register likely controls battery presence detection.
- **DSDT _STA method**: How does the battery's `_STA` method check for presence? Does it read an EC register?

## Software Workaround

### ACPI _STA Override

The workaround overrides the battery device's ACPI `_STA` method to always return `0x1F` (present + enabled + functional), bypassing the EC's presence check.

**Via the Decky plugin:**
The plugin provides battery tether diagnostics and fix buttons in the UI.

**Via command line (runtime, non-persistent):**

Requires `iasl` (ACPI compiler) and `CONFIG_ACPI_CUSTOM_METHOD=y` in the kernel:

```bash
# Install iasl
sudo dnf install acpica-tools  # Bazzite
sudo pacman -S acpica           # CachyOS

# Create the override
cat > /tmp/battery-fix.asl << 'EOF'
DefinitionBlock ("", "SSDT", 2, "OXP", "BATTFIX", 0x00000001)
{
    External (\_SB.BAT0, DeviceObj)
    Scope (\_SB.BAT0)
    {
        Method (_STA, 0, NotSerialized)
        {
            Return (0x1F)
        }
    }
}
EOF

# Compile
iasl /tmp/battery-fix.asl

# Apply (requires CONFIG_ACPI_CUSTOM_METHOD)
sudo cp /tmp/battery-fix.aml /sys/firmware/acpi/custom_method
```

**Persistent override (survives reboot):**

On Bazzite (dracut-based):
```bash
sudo mkdir -p /usr/lib/firmware/acpi
sudo cp /tmp/battery-fix.aml /usr/lib/firmware/acpi/
sudo rpm-ostree initramfs --enable
# Reboot to apply
```

On CachyOS (mkinitcpio-based):
```bash
sudo mkdir -p /etc/initcpio/acpi
sudo cp /tmp/battery-fix.aml /etc/initcpio/acpi/
# Add 'acpi_override' to HOOKS in /etc/mkinitcpio.conf
sudo mkinitcpio -P
# Reboot to apply
```

## Important Caveats

1. **The override forces battery "present"** — if you disconnect the battery entirely while the override is active, the OS will still think a battery is connected. This is cosmetic and won't cause damage, but charge info will show stale/zero values.

2. **Charge data depends on SMBus** — even if the `_STA` override makes the battery visible, charge level/voltage/current data depends on the SMBus (I2C) communication between the EC and battery's fuel gauge IC. If the tether cable carries SMBus lines, charge info will work. If not, the battery will show as "present" but with unknown charge.

3. **The battery device name may differ** — most systems use `BAT0`, but check your DSDT output. Some systems use `BAT1` or `BATC`.

4. **Test on Windows first** — if the battery also shows as absent on Windows via tether cable, this confirms the issue is hardware-level (missing detection pin in the cable). The ACPI override will still work on Linux, but it confirms that OneXPlayer's cable design is the root cause.

## EC Register Investigation

If you want to identify the exact EC register that controls battery presence, compare the EC register dumps from the diagnostic script:

```bash
# Direct connection (battery works)
sudo ./scripts/battery-tether-diag.sh ~/Downloads/diag-direct.txt

# Tether cable (battery absent)
sudo ./scripts/battery-tether-diag.sh ~/Downloads/diag-tether.txt

# Compare
diff ~/Downloads/diag-direct.txt ~/Downloads/diag-tether.txt
```

The register(s) that differ between the two connections likely control battery presence detection. If a single EC register bit controls presence, we could potentially write to that register to force detection — though this is riskier than the ACPI override approach.
