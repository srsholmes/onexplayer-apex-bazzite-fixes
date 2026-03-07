#!/usr/bin/env bash
# battery-tether-diag.sh — Diagnose battery detection issues on OneXPlayer Apex
#
# Run this script with and without the tether cable to compare results.
# Usage: sudo ./battery-tether-diag.sh [output_file]
#
# The script collects:
#   1. Power supply sysfs status (all batteries and AC adapters)
#   2. ACPI battery info from dmesg
#   3. DSDT battery device _STA method (disassembled)
#   4. EC register dump (battery-related region 0x60-0x90)
#   5. upower battery enumeration
#   6. ACPI table list

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

OUTPUT="${1:-/dev/stdout}"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

log() { echo -e "${CYAN}[DIAG]${NC} $*" | tee -a "$OUTPUT"; }
section() { echo -e "\n${GREEN}=== $* ===${NC}" | tee -a "$OUTPUT"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "$OUTPUT"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" | tee -a "$OUTPUT"; }

if [[ $EUID -ne 0 ]]; then
    fail "This script must be run as root (sudo)."
    exit 1
fi

echo "OneXPlayer Apex Battery Tether Diagnostic" | tee "$OUTPUT"
echo "Date: $(date -Iseconds)" | tee -a "$OUTPUT"
echo "Kernel: $(uname -r)" | tee -a "$OUTPUT"
echo "" | tee -a "$OUTPUT"

# ── 1. Power supply sysfs ──
section "Power Supply Devices (/sys/class/power_supply/)"

if [[ -d /sys/class/power_supply ]]; then
    for ps in /sys/class/power_supply/*/; do
        name=$(basename "$ps")
        log "--- $name ---"
        for attr in type status present capacity voltage_now current_now \
                    charge_full charge_now energy_full energy_now \
                    manufacturer model_name serial_number technology \
                    cycle_count; do
            f="$ps/$attr"
            if [[ -r "$f" ]]; then
                val=$(cat "$f" 2>/dev/null || echo "(read error)")
                echo "  $attr = $val" | tee -a "$OUTPUT"
            fi
        done
    done
else
    fail "No /sys/class/power_supply directory found"
fi

# ── 2. ACPI battery info from dmesg ──
section "Kernel Messages (battery/ACPI related)"

dmesg 2>/dev/null | grep -iE 'battery|BAT[0-9]|power.supply|acpi.*bat|_STA.*bat' \
    | tail -50 | tee -a "$OUTPUT" || warn "No battery-related dmesg messages found"

# ── 3. ACPI tables ──
section "ACPI Tables"

if [[ -d /sys/firmware/acpi/tables ]]; then
    ls -la /sys/firmware/acpi/tables/ 2>/dev/null | tee -a "$OUTPUT"
else
    warn "ACPI tables directory not accessible"
fi

# ── 4. DSDT battery device analysis ──
section "DSDT Battery Device Analysis"

DSDT_BIN="$TMPDIR/dsdt.dat"
DSDT_DSL="$TMPDIR/dsdt.dsl"

if [[ -r /sys/firmware/acpi/tables/DSDT ]]; then
    cp /sys/firmware/acpi/tables/DSDT "$DSDT_BIN"
    log "DSDT table size: $(wc -c < "$DSDT_BIN") bytes"

    if command -v iasl &>/dev/null; then
        iasl -d "$DSDT_BIN" 2>/dev/null
        if [[ -f "$DSDT_DSL" ]]; then
            log "DSDT disassembled successfully"

            # Find battery device definitions
            log ""
            log "Battery device definitions found:"
            grep -n -A 2 'Device.*BAT\|Name.*_HID.*PNP0C0A' "$DSDT_DSL" 2>/dev/null \
                | tee -a "$OUTPUT" || warn "No battery devices in DSDT"

            # Extract _STA method for battery devices
            log ""
            log "Battery _STA methods (presence detection):"
            # Use awk to extract _STA methods within battery device scope
            awk '
                /Device\s*\(BAT/ { in_bat=1; bat_name=$0; depth=0 }
                in_bat && /{/ { depth++ }
                in_bat && /}/ { depth--; if(depth<=0) in_bat=0 }
                in_bat && /Method\s*\(_STA/ { in_sta=1; sta_depth=0; print ""; print "// From " bat_name }
                in_sta { print; if (/{/) sta_depth++; if (/}/) { sta_depth--; if(sta_depth<=0) in_sta=0 } }
            ' "$DSDT_DSL" 2>/dev/null | tee -a "$OUTPUT" || warn "Could not extract _STA"

            # Extract _BIF/_BIX (battery info) methods
            log ""
            log "Battery info methods (_BIF/_BIX):"
            grep -n '_BIF\|_BIX\|_BST\|_BTP' "$DSDT_DSL" 2>/dev/null \
                | tee -a "$OUTPUT" || warn "No battery info methods found"

            # Look for EC references in battery context
            log ""
            log "EC OperationRegion definitions:"
            grep -n -A 3 'OperationRegion.*EC\|EmbeddedControl' "$DSDT_DSL" 2>/dev/null \
                | head -40 | tee -a "$OUTPUT" || warn "No EC regions found"

            # Look for battery presence field in EC
            log ""
            log "EC fields potentially related to battery presence:"
            grep -n -B 2 -A 2 'BPRS\|BATS\|BTST\|BDET\|BTIN\|BAT.*Present\|Bat.*Status' \
                "$DSDT_DSL" 2>/dev/null \
                | tee -a "$OUTPUT" || warn "No obvious battery presence EC fields found"

            # Save full DSDT for manual analysis
            cp "$DSDT_DSL" "$TMPDIR/dsdt-full.dsl"
            log ""
            log "Full DSDT saved to: $TMPDIR/dsdt-full.dsl"
            log "To manually inspect: grep -n 'BAT\|battery' $TMPDIR/dsdt-full.dsl"
        else
            warn "iasl disassembly failed"
        fi
    else
        warn "iasl (acpica-tools) not installed — cannot disassemble DSDT"
        warn "Install with: sudo dnf install acpica-tools (Bazzite) or sudo pacman -S acpica (CachyOS)"
    fi
else
    fail "Cannot read DSDT table — check if running as root"
fi

# ── 5. EC register dump (battery region) ──
section "EC Register Dump (battery-related regions)"

EC_IO="/sys/kernel/debug/ec/ec0/io"
DEV_PORT="/dev/port"

dump_ec_region() {
    local start=$1 end=$2 label=$3
    log "$label (0x$(printf '%02X' $start)-0x$(printf '%02X' $end)):"
    local line=""
    for ((addr=start; addr<=end; addr++)); do
        if [[ -r "$EC_IO" ]]; then
            val=$(dd if="$EC_IO" bs=1 skip=$addr count=1 2>/dev/null | xxd -p)
        elif [[ -r "$DEV_PORT" ]]; then
            # Use ACPI EC protocol via port I/O
            # This is simplified — the full protocol is in fan_control.py
            val="??"
        else
            val="??"
        fi
        line+="$val "
        if (( (addr - start + 1) % 16 == 0 )); then
            printf "  0x%02X: %s\n" $((addr - (addr - start) % 16)) "$line" | tee -a "$OUTPUT"
            line=""
        fi
    done
    if [[ -n "$line" ]]; then
        printf "  0x%02X: %s\n" $((end - ${#line}/3 + 1)) "$line" | tee -a "$OUTPUT"
    fi
}

if [[ -r "$EC_IO" ]]; then
    log "Using EC debugfs interface"
    # Common battery EC regions on AMD platforms
    dump_ec_region 0x00 0x0F "EC status/config region"
    dump_ec_region 0x60 0x9F "Battery data region (typical)"
    dump_ec_region 0xA0 0xBF "Extended battery/charger region"
elif [[ -r "$DEV_PORT" ]]; then
    log "EC debugfs not available, using port I/O for targeted reads"
    # Read specific known registers using the EC command protocol
    python3 -c "
import sys, time
DEV_PORT = '/dev/port'
EC_DATA, EC_CMD = 0x62, 0x66

def inb(port):
    with open(DEV_PORT, 'rb') as f:
        f.seek(port)
        return f.read(1)[0]

def outb(port, val):
    with open(DEV_PORT, 'r+b') as f:
        f.seek(port)
        f.write(bytes([val]))

def wait_ibf(timeout=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not (inb(EC_CMD) & 0x02): return True
        time.sleep(0.001)
    return False

def wait_obf(timeout=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if inb(EC_CMD) & 0x01: return True
        time.sleep(0.001)
    return False

def drain():
    for _ in range(16):
        if not (inb(EC_CMD) & 0x01): return
        inb(EC_DATA)
        time.sleep(0.001)

def ec_read(reg):
    drain()
    if not wait_ibf(): return None
    outb(EC_CMD, 0x80)
    if not wait_ibf(): return None
    outb(EC_DATA, reg)
    if not wait_obf(): return None
    val = inb(EC_DATA)
    time.sleep(0.01)
    return val

print('EC register dump via port I/O:')
for start in [0x00, 0x60, 0xA0]:
    end = start + 0x1F
    print(f'  Region 0x{start:02X}-0x{end:02X}:')
    line = ''
    for addr in range(start, end + 1):
        val = ec_read(addr)
        if val is not None:
            line += f'{val:02X} '
        else:
            line += '?? '
        if (addr - start + 1) % 16 == 0:
            print(f'    0x{addr - 15:02X}: {line}')
            line = ''
    if line:
        print(f'    0x{start + len(line)//3:02X}: {line}')
    time.sleep(0.05)
" 2>&1 | tee -a "$OUTPUT" || warn "EC port I/O read failed"
else
    fail "Neither EC debugfs nor /dev/port accessible"
fi

# ── 6. upower enumeration ──
section "UPower Battery Enumeration"

if command -v upower &>/dev/null; then
    upower -e 2>/dev/null | tee -a "$OUTPUT"
    echo "" | tee -a "$OUTPUT"
    # Show details for each battery device
    for dev in $(upower -e 2>/dev/null | grep battery); do
        log "Details for $dev:"
        upower -i "$dev" 2>/dev/null | tee -a "$OUTPUT"
    done
else
    warn "upower not installed"
fi

# ── 7. ACPI battery procfs (legacy) ──
section "ACPI Battery Procfs (legacy)"

if [[ -d /proc/acpi/battery ]]; then
    for bat in /proc/acpi/battery/*/; do
        name=$(basename "$bat")
        log "--- $name ---"
        for f in state info alarm; do
            if [[ -r "$bat/$f" ]]; then
                echo "  [$f]:" | tee -a "$OUTPUT"
                cat "$bat/$f" 2>/dev/null | sed 's/^/    /' | tee -a "$OUTPUT"
            fi
        done
    done
else
    log "/proc/acpi/battery not present (normal on modern kernels)"
fi

# ── 8. Loaded ACPI/battery kernel modules ──
section "Loaded Kernel Modules (battery/ACPI)"

lsmod 2>/dev/null | grep -iE 'battery|acpi|power|supply|ec' | tee -a "$OUTPUT" \
    || warn "No matching modules found"

# ── 9. Summary and recommendations ──
section "Summary"

bat_count=$(ls -d /sys/class/power_supply/BAT* 2>/dev/null | wc -l)
if [[ $bat_count -eq 0 ]]; then
    fail "NO BATTERY DETECTED by the kernel"
    echo "" | tee -a "$OUTPUT"
    log "Possible causes for tether cable 'absent' status:"
    log "  1. The tether cable may not carry the battery detection/sense pin"
    log "  2. The EC firmware may not recognize the battery via cable"
    log "  3. Signal integrity issues on the detection line through the cable"
    echo "" | tee -a "$OUTPUT"
    log "Next steps:"
    log "  1. Run this script with battery DIRECTLY connected (for comparison)"
    log "  2. Save both outputs and compare EC register dumps"
    log "  3. Share the DSDT _STA method output — it shows how presence is checked"
    log "  4. If DSDT shows an EC register check, we can try an ACPI override"
else
    log "Found $bat_count battery device(s)"
    for bat in /sys/class/power_supply/BAT*/; do
        name=$(basename "$bat")
        present=$(cat "$bat/present" 2>/dev/null || echo "unknown")
        status=$(cat "$bat/status" 2>/dev/null || echo "unknown")
        log "  $name: present=$present status=$status"
    done
fi

echo "" | tee -a "$OUTPUT"
log "Diagnostic complete."
log "Run with battery directly connected AND via tether cable, then compare."
log "Save output: sudo ./battery-tether-diag.sh ~/Downloads/battery-diag-\$(date +%s).txt"
