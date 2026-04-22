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

# ─── Step 0: Unlock ostree deployment ────────────────────────────────
#
# On ostree systems /usr is read-only unless the current deployment is unlocked.
# Steps 4/4b write to /usr/bin, /usr/share/inputplumber, /usr/share/dbus-1, and
# /usr/lib64 (libiio), so the deployment must be hotfix-unlocked first.
#
# `ostree admin unlock --hotfix` is idempotent-ish: if the deployment is already
# unlocked it's a no-op; if it isn't, it mounts a writable overlayfs on /usr and
# creates a non-hotfixed rollback deployment. Safe to run every time.

echo "── Step 0: Ensure /usr is writable (ostree hotfix unlock) ──"

if [ ! -r /run/ostree-booted ]; then
    echo "Not an ostree system — skipping unlock"
elif touch /usr/.ip-write-check 2>/dev/null; then
    rm -f /usr/.ip-write-check
    echo "/usr is already writable — skipping unlock"
else
    echo "/usr is read-only. Running: ostree admin unlock --hotfix"
    ostree admin unlock --hotfix
    if ! touch /usr/.ip-write-check 2>/dev/null; then
        echo "ERROR: unlock ran but /usr is still read-only"
        exit 1
    fi
    rm -f /usr/.ip-write-check
    echo "/usr is now writable"
fi
echo

# ─── Step 1: Build hid-oxp.ko ────────────────────────────────────────

echo "── Step 1: Build hid-oxp kernel module ──"

KERNEL_KO="$REPO_DIR/kernel-patches/hid-oxp/$KERNEL/hid-oxp.ko"

# Always build+install the pre-built .ko to /var/lib/hid-oxp/ so the
# hid-oxp-load.service picks up the latest on next boot — even when the current
# boot has an older hid_oxp already loaded (we deliberately DON'T try to rmmod,
# since known-buggy older versions oops on unload; reboot to pick up fixes).
if [ ! -f "$KERNEL_KO" ]; then
    echo "Building hid-oxp.ko for $KERNEL..."
    # Run build as the repo owner, not root
    REPO_OWNER="$(stat -c '%U' "$REPO_DIR")"
    su - "$REPO_OWNER" -c "cd '$REPO_DIR' && bash scripts/build-hid-oxp.sh"

    if [ ! -f "$KERNEL_KO" ]; then
        echo "ERROR: Build failed — no module produced"
        exit 1
    fi
else
    echo "Pre-built module found for $KERNEL"
fi

echo "Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp "$KERNEL_KO" "$INSTALL_KO"
chcon -t modules_object_t "$INSTALL_KO" 2>/dev/null || true
echo "Installed: $INSTALL_KO"

if lsmod | grep -q "^hid_oxp "; then
    echo "hid-oxp already loaded — leaving running module alone (reboot to pick up any source updates)"
else
    echo "Loading hid-oxp..."
    if modprobe hid_oxp 2>/dev/null; then
        echo "Loaded via modprobe"
    elif insmod "$INSTALL_KO" 2>/dev/null; then
        echo "Loaded via insmod"
    else
        echo "insmod returned error (may already be loaded)"
    fi
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
# /var/lib/hid-oxp/hid-oxp.ko lives on /var, so we need local-fs mounted first.
# Without this ordering the service runs before /var is up and insmod fails with
# "No such file or directory" even though the file is present.
After=local-fs.target
Requires=local-fs.target
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

# Stop the service before overwriting the binary — a running ELF gives "Text file busy" on cp.
# Safe no-op if it isn't running. We re-enable + start it explicitly at the end of Step 4.
if systemctl is-active --quiet inputplumber.service; then
    echo "Stopping inputplumber.service so the binary can be replaced..."
    systemctl stop inputplumber.service
fi

# Install binary
echo "Installing InputPlumber binary..."
cp "$IP_BINARY" /usr/bin/inputplumber
chmod 755 /usr/bin/inputplumber

# Install runtime deps reported missing by ldd (libiio isn't in the base Bazzite image).
# Uses rpm -ivh --nodeps into the hotfix overlay — consistent with how the binary itself is layered.
MISSING_LIBS="$(ldd /usr/bin/inputplumber 2>/dev/null | awk '/not found/ {print $1}')"
if [ -n "$MISSING_LIBS" ]; then
    echo "Missing runtime libs: $MISSING_LIBS"

    # Map sonames to Fedora package names.
    PKGS=()
    for lib in $MISSING_LIBS; do
        case "$lib" in
            libiio.so.*) PKGS+=("libiio") ;;
            *)
                echo "ERROR: no package mapping for $lib — add one to migrate-to-inputplumber.sh"
                exit 1
                ;;
        esac
    done

    if command -v dnf5 &>/dev/null; then DNF=dnf5
    elif command -v dnf &>/dev/null; then DNF=dnf
    else echo "ERROR: dnf/dnf5 not available to fetch ${PKGS[*]}"; exit 1
    fi

    DL_DIR="$(mktemp -d)"
    trap 'rm -rf "$DL_DIR"' EXIT
    echo "Downloading ${PKGS[*]} via $DNF..."
    # Flags must come after the subcommand in dnf5.
    "$DNF" download --destdir="$DL_DIR" "${PKGS[@]}"
    echo "Installing into /usr overlay (rpm -ivh --nodeps)..."
    rpm -ivh --nodeps --replacepkgs "$DL_DIR"/*.rpm
    ldconfig

    # Verify nothing is still missing.
    STILL_MISSING="$(ldd /usr/bin/inputplumber 2>/dev/null | awk '/not found/ {print $1}')"
    if [ -n "$STILL_MISSING" ]; then
        echo "ERROR: still missing after install: $STILL_MISSING"
        exit 1
    fi
fi

# Install config/profiles.
# Clean the target first so reruns don't accumulate nested copies from `cp -r src/ dst/`
# when dst already exists — that behavior created /usr/share/inputplumber/inputplumber/.
if [ -d "$IP_ROOTFS/usr/share/inputplumber" ]; then
    echo "Installing InputPlumber config and device profiles..."
    rm -rf /usr/share/inputplumber
    cp -r "$IP_ROOTFS/usr/share/inputplumber" /usr/share/inputplumber
fi

# Install dbus policy if present, and reload dbus-broker so a freshly-installed
# policy file takes effect without a reboot. Without this, the service fails with
# "Request to own name refused by policy" on the first start after install.
DBUS_POLICY_INSTALLED=0
if [ -f "$IP_ROOTFS/usr/share/dbus-1/system.d/org.shadowblip.InputPlumber.conf" ]; then
    mkdir -p /usr/share/dbus-1/system.d/
    cp "$IP_ROOTFS/usr/share/dbus-1/system.d/org.shadowblip.InputPlumber.conf" \
       /usr/share/dbus-1/system.d/
    DBUS_POLICY_INSTALLED=1
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

# ─── Step 4b: Apex-specific overrides ────────────────────────────────
#
# Upstream ships:
#   - profile  /usr/share/inputplumber/profiles/default.yaml            (Guide+North on the KB button)
#   - device   /usr/share/inputplumber/devices/50-onexplayer_apex.yaml  (target_devices: xbox-series)
#
# For this plugin's overlay we need:
#   - KB button → KeyF16 (so the overlay catches it via evdev, no Steam leak)
#   - target_devices[0] → xbox-elite (so LeftPaddle1/RightPaddle1 from the oxp8 capability map
#     have a real target; xbox-series has no back paddles)
#
# InputPlumber only reads from /usr/share/inputplumber/ — user/etc paths are ignored by the
# system service — so overrides must land under /usr. These files are the "carry-the-config-
# across-ostree-rebase" layer; any time upstream changes materially, resync the vendored copies
# under $REPO_DIR/config/inputplumber/ by hand.
APEX_CFG_SRC="$REPO_DIR/config/inputplumber"

echo "── Step 4b: Apply Apex-specific InputPlumber overrides ──"

install_override() {
    local rel="$1"  # path relative to the inputplumber config root
    local src="$APEX_CFG_SRC/$rel"
    local dst="/usr/share/inputplumber/$rel"
    if [ ! -f "$src" ]; then
        echo "WARNING: missing vendored override $src — skipping $rel"
        return
    fi
    if [ ! -d "$(dirname "$dst")" ]; then
        echo "WARNING: $dst parent dir missing — Step 4 profile install may have failed; skipping $rel"
        return
    fi
    echo "Installing $rel (Apex override)"
    cp "$src" "$dst"
}

install_override "profiles/default.yaml"
install_override "devices/50-onexplayer_apex.yaml"
echo

# Reload dbus so a newly installed policy file is picked up before we start the
# service. Fedora uses dbus-broker; 'systemctl reload dbus.service' handles both.
if [ "$DBUS_POLICY_INSTALLED" = "1" ]; then
    echo "Reloading dbus to pick up InputPlumber policy..."
    systemctl reload dbus.service 2>/dev/null || systemctl reload dbus-broker.service 2>/dev/null || true
fi

# Restart rather than just enable --now so Apex overrides from Step 4b take effect
# even when the service was already running from a prior invocation.
systemctl enable inputplumber.service
systemctl restart inputplumber.service
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
