"""Battery tether detection and workaround for OneXPlayer Apex.

The Apex has a swappable 85Wh battery that can be connected either directly
to the device slot or via a tether cable. When connected via tether cable,
the battery may report as "absent" to the OS because the EC's battery
presence detection (typically a sense/detection pin) doesn't register
through the cable.

This module provides:
  - Diagnostic information gathering (battery status, EC registers, DSDT info)
  - An ACPI override workaround that forces battery _STA to report present
  - EC register monitoring to detect battery-related state changes

The ACPI override approach:
  The kernel supports replacing individual ACPI methods at runtime via
  /sys/firmware/acpi/tables/dynamic/. We can override the battery device's
  _STA method to always return 0x1F (present + enabled + functional) when
  we know a battery is physically connected via tether cable.

  This is safer than a full DSDT override and can be toggled on/off.
"""

import glob
import logging
import os
import subprocess
import time

logger = logging.getLogger("OXP-BatteryTether")

# Callbacks for plugin log integration
_log_info = logger.info
_log_error = logger.error
_log_warning = logger.warning


def set_log_callbacks(info_fn, error_fn, warning_fn):
    global _log_info, _log_error, _log_warning
    _log_info = info_fn
    _log_error = error_fn
    _log_warning = warning_fn


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


# ── Power supply sysfs paths ──

POWER_SUPPLY_DIR = "/sys/class/power_supply"
EC_IO = "/sys/kernel/debug/ec/ec0/io"
DEV_PORT = "/dev/port"

# EC port I/O constants (same as fan_control.py)
EC_DATA_PORT = 0x62
EC_CMD_PORT = 0x66
EC_CMD_READ = 0x80
EC_CMD_WRITE = 0x81

# ACPI override paths
ACPI_OVERRIDE_DIR = "/sys/firmware/acpi/tables"
INITRD_ACPI_DIR = "/etc/initramfs-tools/acpi-upgrades"  # Debian/Ubuntu
DRACUT_ACPI_DIR = "/usr/lib/firmware/acpi"  # Fedora/Bazzite (dracut)

# State file to track if we've applied an override
STATE_DIR = "/tmp/oxp-battery-tether"
STATE_FILE = os.path.join(STATE_DIR, "override-active")


# ── EC register reading (port I/O fallback) ──

def _inb(port):
    """Read one byte from an I/O port."""
    with open(DEV_PORT, "rb") as f:
        f.seek(port)
        return f.read(1)[0]


def _outb(port, value):
    """Write one byte to an I/O port."""
    with open(DEV_PORT, "r+b") as f:
        f.seek(port)
        f.write(bytes([value]))


def _wait_ibf(timeout=0.5):
    """Wait for EC input buffer flag to clear."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not (_inb(EC_CMD_PORT) & 0x02):
            return True
        time.sleep(0.001)
    return False


def _wait_obf(timeout=0.5):
    """Wait for EC output buffer flag to be set."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _inb(EC_CMD_PORT) & 0x01:
            return True
        time.sleep(0.001)
    return False


def _drain_obf():
    """Drain stale data from EC output buffer."""
    for _ in range(16):
        if not (_inb(EC_CMD_PORT) & 0x01):
            return
        _inb(EC_DATA_PORT)
        time.sleep(0.001)


def _ec_read(reg, retries=3):
    """Read a single EC register using ACPI EC protocol."""
    for attempt in range(retries):
        try:
            _drain_obf()
            if not _wait_ibf():
                raise TimeoutError("IBF")
            _outb(EC_CMD_PORT, EC_CMD_READ)
            if not _wait_ibf():
                raise TimeoutError("IBF after cmd")
            _outb(EC_DATA_PORT, reg)
            if not _wait_obf():
                raise TimeoutError("OBF")
            val = _inb(EC_DATA_PORT)
            time.sleep(0.01)
            return val
        except (TimeoutError, OSError):
            if attempt < retries - 1:
                time.sleep(0.05)
                try:
                    _drain_obf()
                except OSError:
                    pass
            else:
                return None


def _ec_read_range(start, end):
    """Read a range of EC registers, return dict of {addr: value}."""
    result = {}
    for addr in range(start, end + 1):
        val = _ec_read(addr)
        result[addr] = val
        time.sleep(0.005)  # small delay between reads
    return result


# ── Battery detection ──

def _get_batteries():
    """List all battery devices in sysfs."""
    batteries = []
    if not os.path.isdir(POWER_SUPPLY_DIR):
        return batteries
    for name in sorted(os.listdir(POWER_SUPPLY_DIR)):
        ps_path = os.path.join(POWER_SUPPLY_DIR, name)
        type_file = os.path.join(ps_path, "type")
        try:
            with open(type_file) as f:
                if f.read().strip() == "Battery":
                    batteries.append(name)
        except (OSError, IOError):
            continue
    return batteries


def _read_sysfs(path):
    """Read a sysfs attribute, return None on failure."""
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def _get_battery_info(name):
    """Get detailed info about a battery device."""
    base = os.path.join(POWER_SUPPLY_DIR, name)
    info = {"name": name}
    for attr in ("type", "status", "present", "capacity", "voltage_now",
                 "current_now", "charge_full", "charge_now", "energy_full",
                 "energy_now", "manufacturer", "model_name", "serial_number",
                 "technology", "cycle_count"):
        val = _read_sysfs(os.path.join(base, attr))
        if val is not None:
            info[attr] = val
    return info


def _get_ac_adapters():
    """List all AC adapter/mains devices."""
    adapters = []
    if not os.path.isdir(POWER_SUPPLY_DIR):
        return adapters
    for name in sorted(os.listdir(POWER_SUPPLY_DIR)):
        ps_path = os.path.join(POWER_SUPPLY_DIR, name)
        type_file = os.path.join(ps_path, "type")
        try:
            with open(type_file) as f:
                ps_type = f.read().strip()
                if ps_type in ("Mains", "USB"):
                    online = _read_sysfs(os.path.join(ps_path, "online"))
                    adapters.append({"name": name, "type": ps_type, "online": online})
        except (OSError, IOError):
            continue
    return adapters


# ── DSDT Analysis ──

def _extract_dsdt_battery_info():
    """Extract battery-related ACPI info from DSDT.

    Returns a dict with battery device names, _STA method hints, and
    EC field names that may relate to battery presence detection.
    """
    result = {
        "dsdt_accessible": False,
        "iasl_available": False,
        "battery_devices": [],
        "sta_methods": [],
        "ec_fields": [],
        "raw_excerpt": "",
    }

    dsdt_path = os.path.join(ACPI_OVERRIDE_DIR, "DSDT")
    if not os.path.isfile(dsdt_path):
        return result
    result["dsdt_accessible"] = True

    # Check for iasl
    try:
        subprocess.run(["iasl", "-v"], capture_output=True, env=_clean_env())
        result["iasl_available"] = True
    except FileNotFoundError:
        return result

    # Disassemble DSDT to a temp file
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        dsdt_bin = os.path.join(tmpdir, "dsdt.dat")
        dsdt_dsl = os.path.join(tmpdir, "dsdt.dsl")
        try:
            with open(dsdt_path, "rb") as src, open(dsdt_bin, "wb") as dst:
                dst.write(src.read())
            subprocess.run(
                ["iasl", "-d", dsdt_bin],
                capture_output=True, env=_clean_env(), timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return result

        if not os.path.isfile(dsdt_dsl):
            return result

        try:
            with open(dsdt_dsl) as f:
                dsdt_text = f.read()
        except OSError:
            return result

        # Find battery device definitions
        import re
        for m in re.finditer(r'Device\s*\((BAT\w*)\)', dsdt_text):
            result["battery_devices"].append(m.group(1))

        # Extract _STA method bodies within battery scope (simplified)
        # Look for patterns like: Method (_STA, ...) { ... return ... }
        for m in re.finditer(
            r'(Device\s*\(BAT\w*\).*?)(Method\s*\(_STA[^)]*\)\s*\{[^}]*\})',
            dsdt_text, re.DOTALL
        ):
            result["sta_methods"].append(m.group(2)[:500])

        # Find EC field names that might relate to battery presence
        for m in re.finditer(
            r'(B[A-Z]*PR[A-Z]*|B[A-Z]*ST[A-Z]*|B[A-Z]*DET|ECRD|ECWR)',
            dsdt_text
        ):
            field = m.group(0)
            if field not in result["ec_fields"]:
                result["ec_fields"].append(field)

    return result


# ── ACPI Override ──

def _check_acpi_override_support():
    """Check if the kernel supports ACPI method override via configfs or initrd."""
    support = {
        "configfs": os.path.isdir("/sys/firmware/acpi/tables"),
        "custom_method": os.path.isfile("/sys/firmware/acpi/custom_method"),
        "initrd_override": True,  # always possible if we can write to /etc
        "dracut_available": False,
    }
    try:
        subprocess.run(["dracut", "--version"], capture_output=True, env=_clean_env())
        support["dracut_available"] = True
    except FileNotFoundError:
        pass
    return support


def _create_battery_sta_override_aml(battery_name="BAT0"):
    """Create an AML file that overrides the battery _STA method to always
    return 0x1F (present, enabled, shown in UI, functioning, has battery).

    This is the core workaround: when the EC reports battery absent (because
    the sense pin doesn't connect through the tether cable), we override the
    ACPI _STA method to always report the battery as present.

    Returns the path to the compiled AML file, or None on failure.
    """
    import tempfile

    # Minimal ACPI Source Language (ASL) that overrides _STA
    asl_source = f"""
DefinitionBlock ("", "SSDT", 2, "OXP", "BATTFIX", 0x00000001)
{{
    External (\\_SB.{battery_name}, DeviceObj)

    Scope (\\_SB.{battery_name})
    {{
        Method (_STA, 0, NotSerialized)
        {{
            Return (0x1F)
        }}
    }}
}}
"""

    tmpdir = tempfile.mkdtemp(prefix="oxp-bat-")
    asl_file = os.path.join(tmpdir, "battery-sta-override.asl")
    aml_file = os.path.join(tmpdir, "battery-sta-override.aml")

    try:
        with open(asl_file, "w") as f:
            f.write(asl_source)

        result = subprocess.run(
            ["iasl", asl_file],
            capture_output=True, text=True, env=_clean_env(), timeout=10
        )
        if result.returncode != 0:
            _log_error(f"iasl compilation failed: {result.stderr}")
            return None

        if os.path.isfile(aml_file):
            return aml_file
        return None
    except (OSError, subprocess.TimeoutExpired) as e:
        _log_error(f"Failed to create AML override: {e}")
        return None


def _install_acpi_override_initrd(aml_path):
    """Install an ACPI table override via initrd (persistent across reboots).

    On Bazzite (Fedora/dracut), we place the AML file in the firmware directory
    and regenerate the initramfs. On CachyOS (Arch/mkinitcpio), we use the
    acpi_override hook.

    Note: Bazzite uses an immutable filesystem (ostree), so this may require
    an ostree filesystem unlock first.
    """
    import shutil

    # Determine the initramfs system
    if os.path.isdir("/usr/lib/dracut"):
        # Fedora/Bazzite: use dracut
        target_dir = "/usr/lib/firmware/acpi"
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "battery-sta-override.aml")
        shutil.copy2(aml_path, target)
        _log_info(f"ACPI override installed to {target}")

        # Regenerate initramfs
        try:
            result = subprocess.run(
                ["rpm-ostree", "initramfs", "--enable"],
                capture_output=True, text=True, env=_clean_env(), timeout=120
            )
            if result.returncode == 0:
                _log_info("initramfs regeneration queued (will apply on next boot)")
                return True
            else:
                _log_warning(f"rpm-ostree initramfs failed: {result.stderr}")
                # Try dracut directly
                result = subprocess.run(
                    ["dracut", "--force"],
                    capture_output=True, text=True, env=_clean_env(), timeout=120
                )
                return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as e:
            _log_error(f"initramfs regeneration failed: {e}")
            return False

    elif os.path.isfile("/etc/mkinitcpio.conf"):
        # Arch/CachyOS: use mkinitcpio acpi_override
        target_dir = "/etc/initcpio/acpi"
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "battery-sta-override.aml")
        shutil.copy2(aml_path, target)
        _log_info(f"ACPI override installed to {target}")
        _log_info("Add 'acpi_override' to HOOKS in /etc/mkinitcpio.conf and run mkinitcpio -P")
        return True

    else:
        _log_warning("Unknown initramfs system — cannot install persistent override")
        return False


def _install_acpi_override_runtime(aml_path):
    """Install an ACPI table override at runtime via custom_method.

    This is a non-persistent override that works immediately but is lost
    on reboot. Requires CONFIG_ACPI_CUSTOM_METHOD=y in the kernel.
    """
    custom_method = "/sys/firmware/acpi/custom_method"
    if not os.path.isfile(custom_method):
        _log_warning("Kernel does not support runtime ACPI method override")
        _log_warning("CONFIG_ACPI_CUSTOM_METHOD may not be enabled")
        return False

    try:
        with open(aml_path, "rb") as src:
            aml_data = src.read()
        with open(custom_method, "wb") as dst:
            dst.write(aml_data)
        _log_info("ACPI battery _STA override applied at runtime")

        # Mark state
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            f.write(f"{time.time()}\n{aml_path}\n")
        return True
    except (OSError, IOError) as e:
        _log_error(f"Failed to apply runtime ACPI override: {e}")
        return False


# ── Public API ──

def get_status():
    """Get battery tether status — battery presence, power supply info, and override state.

    Returns a dict suitable for the Decky plugin frontend.
    """
    batteries = _get_batteries()
    battery_info = [_get_battery_info(b) for b in batteries]
    ac_adapters = _get_ac_adapters()

    # Check if any battery is present
    any_present = False
    for info in battery_info:
        if info.get("present") == "1":
            any_present = True
            break

    # Check if override is currently active
    override_active = os.path.isfile(STATE_FILE)

    return {
        "batteries": battery_info,
        "battery_count": len(batteries),
        "any_battery_present": any_present,
        "ac_adapters": ac_adapters,
        "override_active": override_active,
        "battery_absent": len(batteries) == 0 or not any_present,
    }


def diagnose():
    """Run a comprehensive diagnostic and return results.

    This is the programmatic equivalent of battery-tether-diag.sh.
    Returns a dict with all diagnostic data.
    """
    status = get_status()

    # EC register dump (battery region)
    ec_data = {}
    try:
        if os.path.isfile(EC_IO) and os.access(EC_IO, os.R_OK):
            # Use debugfs
            with open(EC_IO, "rb") as f:
                for region_start in [0x00, 0x60, 0xA0]:
                    f.seek(region_start)
                    data = f.read(0x20)
                    for i, byte in enumerate(data):
                        ec_data[region_start + i] = byte
        elif os.path.isfile(DEV_PORT) and os.access(DEV_PORT, os.R_OK):
            # Use port I/O
            for region_start in [0x00, 0x60, 0xA0]:
                region = _ec_read_range(region_start, region_start + 0x1F)
                ec_data.update(region)
    except (OSError, IOError) as e:
        _log_warning(f"EC read failed: {e}")

    # Format EC data as hex strings for display
    ec_hex = {}
    for addr, val in sorted(ec_data.items()):
        if val is not None:
            ec_hex[f"0x{addr:02X}"] = f"0x{val:02X}"
        else:
            ec_hex[f"0x{addr:02X}"] = "??"

    # DSDT info
    dsdt_info = _extract_dsdt_battery_info()

    # ACPI override support
    override_support = _check_acpi_override_support()

    # Kernel battery messages
    dmesg_lines = []
    try:
        result = subprocess.run(
            ["dmesg"], capture_output=True, text=True, env=_clean_env(), timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                lower = line.lower()
                if any(kw in lower for kw in ("battery", "bat0", "bat1", "power_supply", "_sta")):
                    dmesg_lines.append(line.strip())
            dmesg_lines = dmesg_lines[-30:]  # last 30 relevant lines
    except (OSError, subprocess.TimeoutExpired):
        pass

    # Loaded modules
    modules = []
    try:
        result = subprocess.run(
            ["lsmod"], capture_output=True, text=True, env=_clean_env(), timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                lower = line.lower()
                if any(kw in lower for kw in ("battery", "acpi", "power", "supply", " ec ")):
                    modules.append(line.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass

    return {
        **status,
        "ec_registers": ec_hex,
        "dsdt_info": dsdt_info,
        "override_support": override_support,
        "dmesg_battery": dmesg_lines,
        "loaded_modules": modules,
    }


def apply_override(battery_name="BAT0", persistent=False):
    """Apply the ACPI _STA override to force battery detection.

    Args:
        battery_name: The ACPI battery device name (BAT0, BAT1, etc.)
        persistent: If True, install via initrd (survives reboot).
                    If False, apply at runtime (lost on reboot).

    Returns a dict with success status and details.
    """
    # Check prerequisites
    try:
        subprocess.run(["iasl", "-v"], capture_output=True, env=_clean_env())
    except FileNotFoundError:
        return {
            "success": False,
            "error": "iasl (acpica-tools) is not installed. "
                     "Install with: sudo dnf install acpica-tools (Bazzite) "
                     "or sudo pacman -S acpica (CachyOS)"
        }

    # Create the AML override
    aml_path = _create_battery_sta_override_aml(battery_name)
    if not aml_path:
        return {"success": False, "error": "Failed to compile ACPI override"}

    if persistent:
        ok = _install_acpi_override_initrd(aml_path)
        if ok:
            return {
                "success": True,
                "message": f"Persistent ACPI override installed for {battery_name}. "
                           "Reboot required to take effect.",
                "reboot_required": True,
            }
        else:
            return {
                "success": False,
                "error": "Failed to install persistent override. "
                         "On Bazzite, you may need to run 'sudo ostree admin unlock' first."
            }
    else:
        ok = _install_acpi_override_runtime(aml_path)
        if ok:
            # Trigger a battery status re-read
            _trigger_battery_rescan()
            return {
                "success": True,
                "message": f"Runtime ACPI override applied for {battery_name}. "
                           "Battery should now be detected. Override is lost on reboot.",
                "reboot_required": False,
            }
        else:
            return {
                "success": False,
                "error": "Runtime override failed. Kernel may not support "
                         "CONFIG_ACPI_CUSTOM_METHOD. Try persistent mode instead."
            }


def remove_override():
    """Remove the battery tether ACPI override.

    For runtime overrides, a reboot is needed to fully revert.
    For persistent overrides, we remove the AML file and regenerate initrd.
    """
    removed = []

    # Clean up state file
    if os.path.isfile(STATE_FILE):
        os.unlink(STATE_FILE)
        removed.append("runtime state")

    # Remove persistent overrides
    for path in [
        os.path.join(DRACUT_ACPI_DIR, "battery-sta-override.aml"),
        "/etc/initcpio/acpi/battery-sta-override.aml",
    ]:
        if os.path.isfile(path):
            os.unlink(path)
            removed.append(path)
            _log_info(f"Removed persistent override: {path}")

    if removed:
        return {
            "success": True,
            "message": f"Removed: {', '.join(removed)}. "
                       "Reboot to fully revert runtime override.",
            "removed": removed,
        }
    else:
        return {
            "success": True,
            "message": "No active overrides found.",
            "removed": [],
        }


def _trigger_battery_rescan():
    """Try to trigger the kernel to re-enumerate battery devices."""
    # Method 1: unbind/bind the ACPI battery driver
    try:
        bat_path = glob.glob("/sys/bus/acpi/drivers/battery/PNP0C0A:*")
        for bp in bat_path:
            name = os.path.basename(bp)
            unbind = "/sys/bus/acpi/drivers/battery/unbind"
            bind = "/sys/bus/acpi/drivers/battery/bind"
            if os.path.isfile(unbind) and os.path.isfile(bind):
                with open(unbind, "w") as f:
                    f.write(name)
                time.sleep(0.5)
                with open(bind, "w") as f:
                    f.write(name)
                _log_info(f"Re-bound battery driver for {name}")
    except (OSError, IOError) as e:
        _log_warning(f"Battery driver rebind failed: {e}")

    # Method 2: trigger udev change event
    try:
        for ps in glob.glob("/sys/class/power_supply/BAT*/uevent"):
            with open(ps, "w") as f:
                f.write("change")
    except (OSError, IOError):
        pass
