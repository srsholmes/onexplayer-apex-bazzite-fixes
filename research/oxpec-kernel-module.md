# Oxpec Kernel Module — Build, Load & Update Process

**Device:** OneXPlayer OneXFly Apex (AMD Ryzen AI Max+ 395 "Strix Halo")
**Driver:** `oxpec` — EC platform driver for OneXPlayer/AOKZOE hwmon + fan control
**Source:** `decky-plugin/py_modules/oxpec/build/oxpec.c` (GPL-2.0+, upstream by Antheas Kapenekakis)

---

## Overview

The oxpec driver exposes the Embedded Controller (EC) as a hwmon device, giving HHD
native fan curve control. Bazzite kernels ship their own `oxpec.ko` in the module tree,
but these builds lack the `ONEXPLAYER APEX` DMI entry, so `modprobe oxpec` returns
"No such device" on Apex hardware. The plugin bundles a patched `.ko` per kernel version
and handles loading automatically.

## Bundled .ko Layout

```
decky-plugin/py_modules/oxpec/
├── build/                         # Source + Makefile
│   ├── oxpec.c                    # Driver with APEX DMI entry (line 155)
│   └── Makefile
├── 6.17.7-ba25.fc43.x86_64/      # Compiled for ba25 kernel
│   └── oxpec.ko
└── 6.17.7-ba28.fc43.x86_64/      # Compiled for ba28 kernel
    └── oxpec.ko
```

Each kernel version gets its own directory. The plugin matches `uname -r` to pick
the right `.ko` at runtime.

---

## Building oxpec.ko for a New Kernel

When Bazzite pushes a kernel update (e.g. ba25 → ba28), the bundled `.ko` becomes
invalid ("Invalid module format"). To rebuild:

```bash
# 1. Enter the build directory
cd decky-plugin/py_modules/oxpec/build/

# 2. Build against the running kernel
make clean
make

# 3. Copy into a version-specific directory
KVER=$(uname -r)
mkdir -p "../${KVER}"
cp oxpec.ko "../${KVER}/oxpec.ko"

# 4. Verify the DMI match exists
strings "../${KVER}/oxpec.ko" | grep "ONEXPLAYER APEX"
```

Prerequisites: `kernel-devel` headers for the target kernel must be installed.
On Bazzite (rpm-ostree), these are usually at `/lib/modules/$(uname -r)/build`.

---

## Loading Strategy (Runtime)

The plugin loads oxpec via `oxpec_loader.py` with a three-tier fallback:

```
1. modprobe oxpec
   └─ Works if upstream kernel ever adds the Apex DMI entry
   └─ Currently fails: "No such device"

2. insmod <plugin_dir>/py_modules/oxpec/<kernel>/oxpec.ko
   └─ Direct load from bundled .ko
   └─ Fails on Bazzite: SELinux blocks insmod from ~/homebrew/plugins/

3. Copy to /var/lib/oxpec/oxpec.ko + chcon modules_object_t + insmod
   └─ SELinux-safe path with correct context label
   └─ This is what actually works on Bazzite
```

### Startup (ensure_loaded)

On every plugin startup (`_main()` → `ensure_loaded()`):
- If module already in `/proc/modules` → no-op
- Otherwise runs the 3-tier fallback above
- On success, restarts HHD so fan curves activate

This means after a kernel update, the user just needs to deploy a plugin build
with the new `.ko` — next boot handles everything automatically.

### Install Button (apply)

When the user clicks "Install oxpec" in the UI:
- Runs the same 3-tier load
- Writes a systemd service (`oxpec-load.service`) for boot persistence:
  ```
  ExecStart=/bin/sh -c 'modprobe oxpec 2>/dev/null || insmod /var/lib/oxpec/oxpec.ko'
  ```
- Enables the service via `systemctl enable --now`

The service is a belt-and-suspenders layer on top of `ensure_loaded()` — the plugin
auto-loads on startup anyway, but the service ensures the module is available before
HHD starts even if the plugin loads late.

---

## SELinux Considerations

Bazzite enforces SELinux. Loading kernel modules requires the `.ko` file to have
the `modules_object_t` type label. Files under `~/homebrew/plugins/` have `user_home_t`
which is denied by policy.

The fix:
```bash
cp oxpec.ko /var/lib/oxpec/oxpec.ko
chcon -t modules_object_t /var/lib/oxpec/oxpec.ko
insmod /var/lib/oxpec/oxpec.ko
```

This is handled automatically by `_install_bundled_ko()` in the loader.

---

## Kernel Update Workflow

When a new Bazzite kernel drops:

1. Boot into the new kernel (the old `.ko` will fail with "Invalid module format")
2. Verify kernel headers are available: `ls /lib/modules/$(uname -r)/build`
3. Build: `cd decky-plugin/py_modules/oxpec/build && make clean && make`
4. Copy: `mkdir -p ../$(uname -r) && cp oxpec.ko ../$(uname -r)/`
5. Build plugin: `cd decky-plugin && bun run deploy` (or `bun run install-plugin`)
6. Commit the new `.ko` directory and updated zip
7. Push to the `fix/update-for-kernel-<version>` branch

The plugin's `ensure_loaded()` handles the rest on next boot.

---

## ba28 Kernel Incident (2026-03-13)

### Problem

Kernel updated from `6.17.7-ba25` to `6.17.7-ba28`. Two bugs surfaced:

1. **`apply()` false positive from `modprobe --dry-run`** — ba28 ships its own
   `oxpec.ko` in the module tree, so `--dry-run` returned success. But actual
   `modprobe oxpec` fails with "No such device" because the kernel's oxpec lacks
   the Apex DMI entry. The old `apply()` code trusted `--dry-run` and wrote a
   modprobe-only service that couldn't start.

2. **`ensure_loaded()` SELinux block** — insmod from the plugin directory
   (`~/homebrew/plugins/...`) was denied by SELinux. The fallback to
   `/var/lib/oxpec/oxpec.ko` found the stale ba25 `.ko` there, which failed
   with "Invalid module format".

### Fix

- `apply()` now tries actual `modprobe` (not `--dry-run`). On failure, falls
  back to bundled `.ko`: copies to `/var/lib/oxpec/`, sets `modules_object_t`
  SELinux context, loads via insmod.
- `ensure_loaded()` detects "Permission denied" on bundled insmod, auto-copies
  to `/var/lib/oxpec/` with SELinux label, retries.
- Extracted `_install_bundled_ko()` helper for the copy + chcon pattern.
- Service template uses the installed `.ko` path as insmod fallback.

### Resolution

After deploying the fixed plugin, gaming mode boot triggered `ensure_loaded()`
which ran the full fallback chain → copied the ba28 `.ko` to `/var/lib/oxpec/`
→ loaded successfully → HHD restarted → fan control working.

---

## Verification

```bash
# Module loaded?
lsmod | grep oxpec

# hwmon device present?
cat /sys/class/hwmon/hwmon*/name | grep oxpec

# Fan control visible in HHD?
# Check HHD overlay → fan curve should be available

# Service status
systemctl status oxpec-load.service
```
