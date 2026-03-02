#!/usr/bin/env bash
# fix-sleep.sh — Apply sleep/suspend fix for OneXPlayer Apex (Strix Halo) on Bazzite
#
# Adds amd_iommu=off kernel parameter to fix wake-from-sleep.
# Also cleans up any old sleep fix kargs/udev rules if present.
# Requires a reboot to take effect.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (sudo)."
    exit 1
fi

# ---------- 1. Remove old sleep-fix kernel params ----------

OLD_KARGS=(
    "amdgpu.cwsr_enable=0"
    "iommu=pt"
    "amdgpu.gttsize=126976"
    "ttm.pages_limit=32505856"
)

CMDLINE=$(cat /proc/cmdline)
REBOOT_NEEDED=false

info "Removing old sleep-fix kernel parameters..."
for karg in "${OLD_KARGS[@]}"; do
    if echo "$CMDLINE" | grep -q "$karg"; then
        info "  Removing: $karg"
        rpm-ostree kargs --delete="$karg" 2>/dev/null || warn "  Could not remove $karg (may not be set as a separate karg)"
        REBOOT_NEEDED=true
    else
        info "  Not present: $karg (skipping)"
    fi
done

# ---------- 2. Remove udev rule ----------

UDEV_RULE="/etc/udev/rules.d/99-disable-spurious-wake.rules"

if [[ -f "$UDEV_RULE" ]]; then
    info "Removing udev rule: $UDEV_RULE"
    rm -f "$UDEV_RULE"
    udevadm control --reload-rules
    info "  Removed and reloaded udev rules"
else
    info "No old udev rule found (skipping)"
fi

# ---------- 3. Apply the simple fix ----------

NEW_KARG="amd_iommu=off"

info "Applying new sleep fix: $NEW_KARG"
if echo "$CMDLINE" | grep -q "$NEW_KARG"; then
    info "  Already set: $NEW_KARG"
else
    rpm-ostree kargs --append-if-missing="$NEW_KARG"
    REBOOT_NEEDED=true
    info "  Added: $NEW_KARG"
fi

# ---------- 4. Summary ----------

echo ""
info "=== Sleep Fix v2 Summary ==="
info "Removed old kargs: ${OLD_KARGS[*]}"
info "Removed udev rule: $UDEV_RULE"
info "Applied: $NEW_KARG"

if $REBOOT_NEEDED; then
    echo ""
    warn "Kernel parameters were changed. A reboot is required."
    warn "Run: systemctl reboot"
else
    echo ""
    info "No changes needed. Everything already set."
fi

echo ""
info "After reboot, verify with:"
info "  cat /proc/cmdline"
echo ""
info "To test suspend:"
info "  sudo systemctl suspend"
info "  # After wake, check for errors:"
info "  journalctl -b | grep -i 'suspend\|resume\|amdgpu\|iommu\|error\|fail' | tail -30"
