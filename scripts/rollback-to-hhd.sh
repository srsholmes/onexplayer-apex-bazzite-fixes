#!/bin/bash
# Rollback from InputPlumber to HHD on OneXPlayer Apex (Bazzite).
#
# Reverses everything done by migrate-to-inputplumber.sh:
#   1. Stops and disables InputPlumber
#   2. Unloads hid-oxp and removes its boot service
#   3. Unmasks and re-enables HHD services
#
# Usage:
#   sudo ./scripts/rollback-to-hhd.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root (sudo $0)"
    exit 1
fi

SERVICE_NAME="hid-oxp-load.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
INSTALL_DIR="/var/lib/hid-oxp"

echo "============================================"
echo "  InputPlumber → HHD Rollback"
echo "============================================"
echo

# ─── Step 1: Stop InputPlumber ────────────────────────────────────────

echo "── Step 1: Stop and remove InputPlumber ──"
if systemctl is-active inputplumber.service &>/dev/null; then
    systemctl stop inputplumber.service
    echo "Stopped InputPlumber"
else
    echo "InputPlumber not running"
fi
systemctl disable inputplumber.service 2>/dev/null || true

# Remove from-source install artifacts
if [ -f /etc/systemd/system/inputplumber.service ]; then
    rm -f /etc/systemd/system/inputplumber.service
    echo "Removed inputplumber.service"
fi
if [ -f /usr/bin/inputplumber ]; then
    rm -f /usr/bin/inputplumber
    echo "Removed /usr/bin/inputplumber"
fi
if [ -d /usr/share/inputplumber ]; then
    rm -rf /usr/share/inputplumber
    echo "Removed /usr/share/inputplumber"
fi
if [ -f /usr/share/dbus-1/system.d/org.shadowblip.InputPlumber.conf ]; then
    rm -f /usr/share/dbus-1/system.d/org.shadowblip.InputPlumber.conf
    echo "Removed InputPlumber dbus policy"
fi
systemctl daemon-reload
echo

# ─── Step 2: Unload hid-oxp ──────────────────────────────────────────

echo "── Step 2: Remove hid-oxp ──"

# Stop and remove service
if [ -f "$SERVICE_PATH" ]; then
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_PATH"
    systemctl daemon-reload
    echo "Removed $SERVICE_NAME"
fi

# Unload module
if lsmod | grep -q "^hid_oxp "; then
    rmmod hid_oxp
    echo "Unloaded hid-oxp module"
else
    echo "hid-oxp not loaded"
fi

# Clean up installed .ko
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "Removed $INSTALL_DIR"
fi
echo

# ─── Step 3: Unmask and enable HHD ───────────────────────────────────

echo "── Step 3: Re-enable HHD ──"

# Unmask common HHD units
for unit in hhd.service; do
    if systemctl list-unit-files "$unit" &>/dev/null; then
        systemctl unmask "$unit" 2>/dev/null || true
        systemctl enable "$unit" 2>/dev/null || true
        echo "Unmasked and enabled $unit"
    fi
done

# Find and unmask per-user HHD services
REAL_USER="$(logname 2>/dev/null || echo '')"
if [ -n "$REAL_USER" ]; then
    USER_UNIT="hhd@${REAL_USER}.service"
    systemctl unmask "$USER_UNIT" 2>/dev/null || true
    systemctl enable "$USER_UNIT" 2>/dev/null || true
    echo "Unmasked and enabled $USER_UNIT"
fi

# Start HHD
systemctl start hhd.service 2>/dev/null || true
if [ -n "$REAL_USER" ]; then
    systemctl start "hhd@${REAL_USER}.service" 2>/dev/null || true
fi
echo "HHD services started"
echo

# ─── Step 4: Verify ──────────────────────────────────────────────────

echo "── Step 4: Verification ──"

echo -n "HHD: "
if systemctl is-active hhd.service &>/dev/null; then
    echo "RUNNING"
else
    echo "NOT RUNNING (warning)"
fi

echo -n "InputPlumber: "
if systemctl is-active inputplumber.service &>/dev/null; then
    echo "STILL RUNNING (warning — should be stopped)"
else
    echo "STOPPED"
fi

echo -n "hid-oxp module: "
if lsmod | grep -q "^hid_oxp "; then
    echo "STILL LOADED (warning — should be unloaded)"
else
    echo "UNLOADED"
fi

echo
echo "============================================"
echo "  Rollback complete! HHD is back."
echo "  You may need to re-apply button fix in the"
echo "  Decky plugin if it was previously applied."
echo "============================================"
