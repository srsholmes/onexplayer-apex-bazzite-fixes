#!/usr/bin/env bash
# Fix empty/stale systemd override files that block hibernate on BTRFS.
# Run with: sudo bash scripts/fix-hibernate-manual.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Error: must run as root (sudo)" >&2
  exit 1
fi

echo "=== Fixing systemd hibernate overrides ==="

mkdir -p /etc/systemd/system/systemd-logind.service.d
cat > /etc/systemd/system/systemd-logind.service.d/oxp-hibernate.conf << 'EOF'
# OXP Apex Tools — bypass BTRFS swap device check for hibernate
[Service]
Environment=SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK=1
EOF
echo "[OK] logind override"

mkdir -p /etc/systemd/system/systemd-hibernate.service.d
cat > /etc/systemd/system/systemd-hibernate.service.d/oxp-hibernate.conf << 'EOF'
# OXP Apex Tools — bypass BTRFS swap device check for hibernate
[Service]
Environment=SYSTEMD_BYPASS_HIBERNATION_MEMORY_CHECK=1
EOF
echo "[OK] hibernate override"

echo "=== Fixing sleep.conf ==="

mkdir -p /etc/systemd/sleep.conf.d
cat > /etc/systemd/sleep.conf.d/oxp-hibernate.conf << 'EOF'
# OXP Apex Tools — enable hibernate
[Sleep]
AllowHibernation=yes
HibernateMode=shutdown
EOF
echo "[OK] sleep.conf"

echo "=== Reloading systemd ==="
systemctl daemon-reload
systemctl restart systemd-logind
echo "[OK] daemon-reload + logind restart"

echo "=== Verifying CanHibernate ==="
RESULT=$(busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager CanHibernate)
echo "CanHibernate: $RESULT"

if [[ "$RESULT" == *'"yes"'* ]]; then
  echo "[OK] Hibernate is available"
else
  echo "[FAIL] CanHibernate did not return 'yes' — check resume kargs"
fi

echo "=== Checking resume_offset ==="
if command -v btrfs &>/dev/null && [[ -f /var/swap/swapfile ]]; then
  ACTUAL=$(btrfs inspect-internal map-swapfile -r /var/swap/swapfile)
  CURRENT=$(grep -oP 'resume_offset=\K[0-9]+' /proc/cmdline || echo "MISSING")
  echo "Swapfile offset: $ACTUAL"
  echo "Kernel cmdline:  $CURRENT"
  if [[ "$ACTUAL" == "$CURRENT" ]]; then
    echo "[OK] resume_offset matches"
  else
    echo "[WARN] Mismatch — may need: rpm-ostree kargs --delete=resume_offset=$CURRENT --append=resume_offset=$ACTUAL"
  fi
else
  echo "[SKIP] No swapfile at /var/swap/swapfile"
fi

echo ""
echo "Done. If CanHibernate=yes, test with: sudo systemctl hibernate"
