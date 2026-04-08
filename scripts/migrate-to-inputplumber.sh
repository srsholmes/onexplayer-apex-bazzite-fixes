#!/bin/bash
# Migrate from HHD to InputPlumber on OneXPlayer Apex (Bazzite).
#
# This script:
#   1. Builds and installs the hid-oxp kernel module
#   2. Stops and masks HHD services
#   3. Enables and starts InputPlumber
#
# Prerequisites:
#   - InputPlumber must already be installed (Bazzite ships it)
#   - Kernel headers for the running kernel
#
# Usage:
#   sudo ./scripts/migrate-to-inputplumber.sh
#
# Rollback:
#   sudo ./scripts/rollback-to-hhd.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Must run as root (sudo $0)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$REPO_DIR/kernel-patches/hid-oxp/build"
KERNEL="$(uname -r)"
INSTALL_DIR="/var/lib/hid-oxp"
INSTALL_KO="$INSTALL_DIR/hid-oxp.ko"
SERVICE_NAME="hid-oxp-load.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

echo "============================================"
echo "  HHD → InputPlumber Migration"
echo "  Kernel: $KERNEL"
echo "============================================"
echo

# ─── Step 1: Build hid-oxp.ko ────────────────────────────────────────

echo "── Step 1: Build hid-oxp kernel module ──"

# Check if already loaded
if lsmod | grep -q "^hid_oxp "; then
    echo "hid-oxp already loaded, skipping build"
else
    KERNEL_KO="$REPO_DIR/kernel-patches/hid-oxp/$KERNEL/hid-oxp.ko"

    if [ -f "$KERNEL_KO" ]; then
        echo "Pre-built module found for $KERNEL"
    else
        echo "Building hid-oxp.ko for $KERNEL..."
        # Run build as the repo owner, not root
        REPO_OWNER="$(stat -c '%U' "$REPO_DIR")"
        su - "$REPO_OWNER" -c "cd '$REPO_DIR' && bash scripts/build-hid-oxp.sh"

        if [ ! -f "$KERNEL_KO" ]; then
            echo "ERROR: Build failed — no module produced"
            exit 1
        fi
    fi

    # Install to /var/lib/hid-oxp with SELinux context
    echo "Installing to $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
    cp "$KERNEL_KO" "$INSTALL_KO"
    chcon -t modules_object_t "$INSTALL_KO" 2>/dev/null || true
    echo "Installed: $INSTALL_KO"

    # Load the module
    echo "Loading hid-oxp..."
    if modprobe hid_oxp 2>/dev/null; then
        echo "Loaded via modprobe"
    elif insmod "$INSTALL_KO" 2>/dev/null; then
        echo "Loaded via insmod"
    else
        echo "insmod returned error (may already be loaded)"
    fi

    # Wait for module to settle
    sleep 2
fi

# Verify module loaded
if ! lsmod | grep -q "^hid_oxp "; then
    # Also check dmesg — module may bind to devices without appearing in lsmod
    # if it's built into the HID subsystem differently
    if dmesg | tail -20 | grep -q "hid-oxp"; then
        echo "hid-oxp driver active (found in dmesg)"
    else
        echo "ERROR: hid-oxp module did not load"
        exit 1
    fi
else
    echo "hid-oxp module loaded OK"
fi
echo

# ─── Step 2: Create boot service ─────────────────────────────────────

echo "── Step 2: Create hid-oxp boot service ──"

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Load hid-oxp HID driver for OneXPlayer
DefaultDependencies=no
After=systemd-modules-load.service
Before=inputplumber.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'grep -q "^hid_oxp " /proc/modules && exit 0; modprobe hid_oxp 2>/dev/null || insmod $INSTALL_KO'
ExecStop=/sbin/rmmod hid_oxp

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
echo "Created and enabled $SERVICE_NAME"
echo

# ─── Step 3: Stop and mask HHD ───────────────────────────────────────

echo "── Step 3: Disable HHD ──"

# Find all HHD service units
HHD_UNITS=()
while IFS= read -r unit; do
    [ -n "$unit" ] && HHD_UNITS+=("$unit")
done < <(systemctl list-units --plain --no-legend --type=service 'hhd*' 2>/dev/null | awk '{print $1}')

# Also check for common unit names that might not be running
for u in hhd.service "hhd@$(logname 2>/dev/null || echo '').service"; do
    if systemctl list-unit-files "$u" &>/dev/null; then
        # Only add if not already in list
        if [[ ! " ${HHD_UNITS[*]:-} " =~ " $u " ]]; then
            HHD_UNITS+=("$u")
        fi
    fi
done

if [ ${#HHD_UNITS[@]} -eq 0 ]; then
    echo "No HHD services found"
else
    for unit in "${HHD_UNITS[@]}"; do
        echo "Stopping and masking $unit..."
        systemctl stop "$unit" 2>/dev/null || true
        systemctl disable "$unit" 2>/dev/null || true
        systemctl mask "$unit" 2>/dev/null || true
    done
    echo "HHD services masked"
fi
echo

# ─── Step 4: Build/install InputPlumber and start ─────────────────────

echo "── Step 4: InputPlumber (from PR #567) ──"

IP_DIR="$REPO_DIR/vendor/InputPlumber"
IP_BINARY="$IP_DIR/target/release/inputplumber"
IP_ROOTFS="$IP_DIR/rootfs"

# Build if not already built
if [ ! -f "$IP_BINARY" ]; then
    echo "InputPlumber not built yet — building from PR #567..."
    REPO_OWNER="$(stat -c '%U' "$REPO_DIR")"
    su - "$REPO_OWNER" -c "cd '$REPO_DIR' && LIBCLANG_PATH=/usr/lib64/rocm/llvm/lib LD_LIBRARY_PATH=/usr/lib64/rocm/llvm/lib:\${LD_LIBRARY_PATH:-} bash scripts/build-inputplumber.sh"
    if [ ! -f "$IP_BINARY" ]; then
        echo "ERROR: InputPlumber build failed"
        exit 1
    fi
fi

# Install binary
echo "Installing InputPlumber binary..."
cp "$IP_BINARY" /usr/bin/inputplumber
chmod 755 /usr/bin/inputplumber

# Install config/profiles
if [ -d "$IP_ROOTFS/usr/share/inputplumber" ]; then
    echo "Installing InputPlumber config and device profiles..."
    cp -r "$IP_ROOTFS/usr/share/inputplumber/" /usr/share/inputplumber/
fi

# Install dbus policy if present
if [ -f "$IP_ROOTFS/usr/share/dbus-1/system.d/org.shadowblip.InputPlumber.conf" ]; then
    mkdir -p /usr/share/dbus-1/system.d/
    cp "$IP_ROOTFS/usr/share/dbus-1/system.d/org.shadowblip.InputPlumber.conf" \
       /usr/share/dbus-1/system.d/
fi

# Install systemd service (prefer their shipped one, fall back to ours)
if [ -f "$IP_ROOTFS/usr/lib/systemd/system/inputplumber.service" ]; then
    cp "$IP_ROOTFS/usr/lib/systemd/system/inputplumber.service" \
       /etc/systemd/system/inputplumber.service
else
    cat > /etc/systemd/system/inputplumber.service <<IPEOF
[Unit]
Description=InputPlumber — Open source input manager
After=dbus.service
Wants=dbus.service

[Service]
Type=dbus
BusName=org.shadowblip.InputPlumber
ExecStart=/usr/bin/inputplumber
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
IPEOF
fi

systemctl daemon-reload
systemctl enable --now inputplumber.service
echo "InputPlumber installed, enabled, and started"
echo

# ─── Step 5: Verify ──────────────────────────────────────────────────

echo "── Step 5: Verification ──"

echo -n "hid-oxp module: "
if lsmod | grep -q "^hid_oxp "; then
    echo "LOADED"
else
    echo "NOT LOADED (warning)"
fi

echo -n "hid-oxp service: "
if systemctl is-enabled "$SERVICE_NAME" &>/dev/null; then
    echo "ENABLED"
else
    echo "NOT ENABLED (warning)"
fi

echo -n "HHD: "
if systemctl is-active hhd.service &>/dev/null; then
    echo "STILL RUNNING (warning — should be stopped)"
else
    echo "STOPPED"
fi

echo -n "InputPlumber: "
if systemctl is-active inputplumber.service &>/dev/null; then
    echo "RUNNING"
else
    echo "NOT RUNNING (warning)"
fi

# Check USB devices
echo -n "Vendor HID (1a86:fe00): "
if lsusb -d 1a86:fe00 &>/dev/null; then echo "PRESENT"; else echo "NOT FOUND"; fi

echo -n "Xbox gamepad (045e:028e): "
if lsusb -d 045e:028e &>/dev/null; then echo "PRESENT"; else echo "NOT FOUND"; fi

# Check sysfs
echo -n "RGB LED sysfs: "
if ls /sys/class/leds/oxp:rgb:joystick_rings/ &>/dev/null; then
    echo "PRESENT"
else
    echo "NOT FOUND (may appear after device rebind)"
fi

echo
echo "============================================"
echo "  Migration complete!"
echo "  Rollback: sudo ./scripts/rollback-to-hhd.sh"
echo "============================================"
