# Oxpec loader fixes for ba28 kernel

## What was wrong
The oxpec driver failed to load on kernel 6.17.7-ba28 due to two bugs:

1. **`apply()` false positive from `modprobe --dry-run`** — ba28 kernel ships its own oxpec.ko in the module tree, so `--dry-run` returned success. But actual `modprobe oxpec` fails with "No such device" (kernel's oxpec lacks Apex DMI entry). The systemd service ended up with `insmod /dev/null` as fallback.

2. **`ensure_loaded()` SELinux block** — insmod from the plugin directory (`~/homebrew/plugins/...`) got "Permission denied" because SELinux blocks loading kernel modules from non-standard paths.

## What was fixed
- `apply()` now tries actual `modprobe` (not `--dry-run`). On failure, falls back to bundled .ko: copies to `/var/lib/oxpec/`, sets `modules_object_t` SELinux context, loads via insmod.
- `ensure_loaded()` detects "Permission denied" on bundled insmod, auto-copies to `/var/lib/oxpec/` with SELinux label, retries.
- Extracted `_install_bundled_ko()` helper for copy + chcon.
- Service template uses `_INSTALL_KO` path instead of `/dev/null` as insmod fallback.

## Testing
- Deploy: `cd decky-plugin && bun run install-plugin`
- Click "Install oxpec" in plugin UI — should copy bundled .ko to `/var/lib/oxpec/` and load
- Verify: fan control in HHD, UI shows "Loaded (bundled)"

## Rollback if needed
```bash
sudo rpm-ostree rollback
systemctl reboot
```
