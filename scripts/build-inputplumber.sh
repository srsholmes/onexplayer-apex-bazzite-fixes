#!/bin/bash
# Clone and build InputPlumber from PR #567 (OXP HID driver).
#
# Usage:
#   ./scripts/build-inputplumber.sh
#
# Output: vendor/InputPlumber/target/release/inputplumber
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IP_DIR="$REPO_DIR/vendor/InputPlumber"
PR_BRANCH="pr-567"

echo "=== Building InputPlumber from PR #567 ==="

# Check for cargo
if ! command -v cargo &>/dev/null; then
    echo "ERROR: cargo not found. Install Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

# Bazzite ships libclang via ROCm — point bindgen at it
if [ -z "${LIBCLANG_PATH:-}" ] && [ -d /usr/lib64/rocm/llvm/lib ]; then
    export LIBCLANG_PATH=/usr/lib64/rocm/llvm/lib
    export LD_LIBRARY_PATH="${LIBCLANG_PATH}:${LD_LIBRARY_PATH:-}"
    echo "Using ROCm libclang at $LIBCLANG_PATH"
fi

# Check for build deps (on Bazzite: sudo ostree admin unlock --hotfix && sudo rpm -ivh ...)
MISSING_DEPS=()
[ ! -f /usr/lib64/libiio.so ] && MISSING_DEPS+=("libiio-devel")
[ ! -f /usr/lib64/libudev.so ] && MISSING_DEPS+=("systemd-devel")
if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
    echo "ERROR: Missing build deps: ${MISSING_DEPS[*]}"
    echo "On Bazzite:"
    echo "  sudo ostree admin unlock --hotfix"
    echo "  sudo rpm -ivh --nodeps \$(dnf download --url ${MISSING_DEPS[*]} 2>/dev/null | grep x86_64)"
    exit 1
fi

# Clone or update
if [ -d "$IP_DIR/.git" ]; then
    echo "InputPlumber repo exists, updating..."
    cd "$IP_DIR"
    git fetch origin
    git fetch origin pull/567/head:"$PR_BRANCH" 2>/dev/null || true
    git checkout "$PR_BRANCH"
    git pull origin pull/567/head 2>/dev/null || echo "Already up to date or PR fetch skipped"
else
    echo "Cloning InputPlumber..."
    mkdir -p "$(dirname "$IP_DIR")"
    git clone https://github.com/ShadowBlip/InputPlumber.git "$IP_DIR"
    cd "$IP_DIR"
    git fetch origin pull/567/head:"$PR_BRANCH"
    git checkout "$PR_BRANCH"
fi

echo "Building (release)..."
cargo build --release

BINARY="$IP_DIR/target/release/inputplumber"
if [ ! -f "$BINARY" ]; then
    echo "ERROR: Build failed — no binary produced"
    exit 1
fi

echo "=== Build complete ==="
echo "  Binary: $BINARY"
echo "  Size:   $(du -h "$BINARY" | cut -f1)"
echo
echo "To install system-wide:"
echo "  sudo cp $BINARY /usr/bin/inputplumber"
echo "  sudo cp $IP_DIR/rootfs/usr/lib/systemd/system/inputplumber.service /etc/systemd/system/"
echo "  sudo cp -r $IP_DIR/rootfs/usr/share/inputplumber/ /usr/share/inputplumber/"
