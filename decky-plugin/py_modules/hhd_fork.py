"""HHD fork manager for OneXPlayer Apex on Bazzite.

Instead of patching system HHD files in-place (which requires ostree unlock
and breaks on every Bazzite update), this module clones an HHD fork from
GitHub, installs it into a local venv, and runs it as a replacement for
the system HHD service.

The fork (default: srsholmes/hhd, apex branch) contains all Apex-specific
changes directly in the source tree.
"""

import logging
import os
import subprocess
import time

logger = logging.getLogger("OXP-HHDFork")

# Pluggable log callbacks — set by main.py
_log_info_cb = None
_log_error_cb = None
_log_warning_cb = None

DEFAULT_REPO = "https://github.com/srsholmes/hhd"
DEFAULT_BRANCH = "apex"


def set_log_callbacks(info_fn, error_fn, warning_fn):
    global _log_info_cb, _log_error_cb, _log_warning_cb
    _log_info_cb = info_fn
    _log_error_cb = error_fn
    _log_warning_cb = warning_fn


def _log_info(msg):
    if _log_info_cb:
        _log_info_cb(msg)
    else:
        logger.info(msg)


def _log_error(msg):
    if _log_error_cb:
        _log_error_cb(msg)
    else:
        logger.error(msg)


def _log_warning(msg):
    if _log_warning_cb:
        _log_warning_cb(msg)
    else:
        logger.warning(msg)


def _clean_env():
    """Return a subprocess environment without PyInstaller's LD_LIBRARY_PATH."""
    env = os.environ.copy()
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(var, None)
    return env


def _run(cmd, timeout=120, check=False):
    """Run a subprocess command with clean env, returning CompletedProcess."""
    _log_info(f"Running: {' '.join(cmd)}")
    r = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=_clean_env()
    )
    if r.stdout.strip():
        _log_info(f"  stdout: {r.stdout.strip()[:500]}")
    if r.stderr.strip():
        level = _log_error if r.returncode != 0 else _log_info
        level(f"  stderr: {r.stderr.strip()[:500]}")
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, cmd, r.stdout, r.stderr)
    return r


def _get_user():
    """Get the non-root username. Decky runs as root."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return sudo_user
    # Infer from /home
    try:
        for name in sorted(os.listdir("/home")):
            path = f"/home/{name}"
            if os.path.isdir(path) and name != "root":
                return name
    except OSError:
        pass
    return None


def _get_base_dir():
    """Base directory for the HHD fork install."""
    user = _get_user()
    if user:
        return f"/home/{user}/.local/share/hhd-apex"
    return "/opt/hhd-apex"


def _get_repo_dir():
    return os.path.join(_get_base_dir(), "repo")


def _get_venv_dir():
    return os.path.join(_get_base_dir(), "venv")


def _get_hhd_bin():
    return os.path.join(_get_venv_dir(), "bin", "hhd")


SERVICE_NAME = "hhd-apex"
SERVICE_TEMPLATE = "hhd-apex@"


def _get_service_path():
    return f"/etc/systemd/system/{SERVICE_TEMPLATE}.service"


def _service_unit_content(venv_dir):
    return f"""[Unit]
Description=Handheld Daemon (Apex Fork)
After=multi-user.target

[Service]
ExecStart={venv_dir}/bin/hhd --user %i
Nice=-12
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


# --- System HHD service management ---

def _find_system_hhd_units():
    """Find all active system HHD service units."""
    try:
        r = _run(
            ["systemctl", "list-units", "--plain", "--no-legend", "--type=service", "hhd*"],
            timeout=10
        )
        units = []
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if parts:
                unit = parts[0]
                # Don't include our own service
                if not unit.startswith("hhd-apex"):
                    units.append(unit)
        return units
    except Exception:
        return []


def _find_system_hhd_enabled_units():
    """Find all enabled system HHD service units (for restore on uninstall)."""
    try:
        r = _run(
            ["systemctl", "list-unit-files", "--plain", "--no-legend", "--type=service", "hhd*"],
            timeout=10
        )
        units = []
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ("enabled", "enabled-runtime"):
                unit = parts[0]
                if not unit.startswith("hhd-apex"):
                    units.append(unit)
        return units
    except Exception:
        return []


def _stop_system_hhd(steps):
    """Stop and disable system HHD services."""
    units = _find_system_hhd_units()
    if not units:
        _log_info("No system HHD units running")
        return True

    for unit in units:
        try:
            _run(["systemctl", "stop", unit], timeout=30)
            _run(["systemctl", "disable", unit], timeout=10)
            steps.append(f"Stopped system {unit}")
            _log_info(f"Stopped and disabled system {unit}")
        except Exception as e:
            _log_warning(f"Failed to stop {unit}: {e}")
    return True


def _start_system_hhd(steps):
    """Re-enable and start system HHD services."""
    user = _get_user()
    if not user:
        _log_error("Cannot determine user for system HHD restart")
        return False

    # Try to enable and start the per-user service
    unit = f"hhd@{user}.service"
    try:
        _run(["systemctl", "enable", unit], timeout=10)
        r = _run(["systemctl", "start", unit], timeout=30)
        if r.returncode == 0:
            steps.append(f"Started system {unit}")
            _log_info(f"Re-enabled and started system {unit}")
            return True
        else:
            _log_warning(f"Failed to start {unit}, trying hhd.service")
    except Exception as e:
        _log_warning(f"Failed to start {unit}: {e}")

    # Fallback to hhd.service
    try:
        _run(["systemctl", "enable", "hhd.service"], timeout=10)
        r = _run(["systemctl", "start", "hhd.service"], timeout=30)
        if r.returncode == 0:
            steps.append("Started system hhd.service")
            return True
    except Exception:
        pass

    _log_error("Failed to restart any system HHD service")
    return False


# --- Fork HHD service management ---

def _install_service(steps):
    """Create the systemd service file for the fork."""
    venv_dir = _get_venv_dir()
    service_path = _get_service_path()
    content = _service_unit_content(venv_dir)

    try:
        os.makedirs(os.path.dirname(service_path), exist_ok=True)
        with open(service_path, "w") as f:
            f.write(content)
        _run(["systemctl", "daemon-reload"], timeout=10)
        steps.append("Created systemd service")
        _log_info(f"Installed service at {service_path}")
        return True
    except Exception as e:
        _log_error(f"Failed to install service: {e}")
        return False


def _remove_service(steps):
    """Remove the systemd service file."""
    service_path = _get_service_path()
    try:
        if os.path.exists(service_path):
            os.remove(service_path)
            _run(["systemctl", "daemon-reload"], timeout=10)
            steps.append("Removed systemd service")
            _log_info(f"Removed service at {service_path}")
    except Exception as e:
        _log_warning(f"Failed to remove service: {e}")


def _start_fork_hhd(steps):
    """Enable and start the fork HHD service."""
    user = _get_user()
    if not user:
        _log_error("Cannot determine user for fork HHD")
        return False

    unit = f"{SERVICE_TEMPLATE}{user}.service"
    try:
        _run(["systemctl", "enable", unit], timeout=10)
        r = _run(["systemctl", "start", unit], timeout=30)
        if r.returncode == 0:
            steps.append(f"Started fork HHD ({unit})")
            _log_info(f"Fork HHD started: {unit}")
            return True
        else:
            _log_error(f"Failed to start fork HHD: {r.stderr.strip()}")
            return False
    except Exception as e:
        _log_error(f"Failed to start fork HHD: {e}")
        return False


def _stop_fork_hhd(steps):
    """Stop and disable the fork HHD service."""
    user = _get_user()
    if not user:
        return True

    unit = f"{SERVICE_TEMPLATE}{user}.service"
    try:
        _run(["systemctl", "stop", unit], timeout=30)
        _run(["systemctl", "disable", unit], timeout=10)
        steps.append(f"Stopped fork HHD ({unit})")
        _log_info(f"Fork HHD stopped: {unit}")
    except Exception as e:
        _log_warning(f"Failed to stop fork HHD: {e}")
    return True


def _is_fork_running():
    """Check if the fork HHD service is running."""
    user = _get_user()
    if not user:
        return False

    unit = f"{SERVICE_TEMPLATE}{user}.service"
    try:
        r = _run(["systemctl", "is-active", unit], timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


# --- Git operations ---

def _clone_repo(repo_url, branch, steps):
    """Clone the HHD fork."""
    repo_dir = _get_repo_dir()
    base_dir = _get_base_dir()

    if os.path.isdir(os.path.join(repo_dir, ".git")):
        _log_info("Repo already exists, updating instead")
        return _update_repo(steps)

    os.makedirs(base_dir, exist_ok=True)

    try:
        _run(
            ["git", "clone", "--branch", branch, "--depth", "1", repo_url, repo_dir],
            timeout=300, check=True
        )
        steps.append(f"Cloned {repo_url} ({branch})")
        return True
    except subprocess.CalledProcessError as e:
        _log_error(f"Git clone failed: {e.stderr}")
        return False
    except subprocess.TimeoutExpired:
        _log_error("Git clone timed out (300s)")
        return False


def _update_repo(steps):
    """Pull latest changes from the fork."""
    repo_dir = _get_repo_dir()

    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        _log_error("Repo not cloned yet")
        return False

    try:
        _run(["git", "-C", repo_dir, "fetch", "--depth", "1", "origin"], timeout=120, check=True)
        _run(["git", "-C", repo_dir, "reset", "--hard", "FETCH_HEAD"], timeout=30, check=True)
        steps.append("Updated repo (git pull)")

        # Get current commit for logging
        r = _run(["git", "-C", repo_dir, "log", "--oneline", "-1"], timeout=5)
        if r.returncode == 0:
            _log_info(f"Current commit: {r.stdout.strip()}")

        return True
    except subprocess.CalledProcessError as e:
        _log_error(f"Git update failed: {e.stderr}")
        return False
    except subprocess.TimeoutExpired:
        _log_error("Git update timed out")
        return False


# --- Venv and pip ---

def _setup_venv(steps):
    """Create venv and install HHD fork in editable mode."""
    venv_dir = _get_venv_dir()
    repo_dir = _get_repo_dir()

    # Create venv with system site packages (for evdev, PyGObject, dbus-python, etc.)
    if not os.path.isdir(venv_dir):
        try:
            _run(
                ["python3", "-m", "venv", "--system-site-packages", venv_dir],
                timeout=60, check=True
            )
            steps.append("Created Python venv")
        except subprocess.CalledProcessError as e:
            _log_error(f"Venv creation failed: {e.stderr}")
            return False

    # Install HHD fork in editable mode
    pip = os.path.join(venv_dir, "bin", "pip")
    try:
        _run(
            [pip, "install", "-e", repo_dir],
            timeout=300, check=True
        )
        steps.append("Installed HHD fork (pip install -e)")
        return True
    except subprocess.CalledProcessError as e:
        _log_error(f"pip install failed: {e.stderr}")
        return False
    except subprocess.TimeoutExpired:
        _log_error("pip install timed out (300s)")
        return False


# --- Public API ---

def install(repo_url=None, branch=None):
    """Clone the HHD fork, install it, and start the local service.

    This replaces the system HHD with the fork.
    """
    steps = []
    repo_url = repo_url or DEFAULT_REPO
    branch = branch or DEFAULT_BRANCH

    _log_info(f"=== HHD Fork Install Start ({repo_url}, branch: {branch}) ===")

    # 1. Clone/update repo
    if not _clone_repo(repo_url, branch, steps):
        return {"success": False, "error": "Failed to clone HHD fork", "steps": steps}

    # 2. Setup venv and install
    if not _setup_venv(steps):
        return {"success": False, "error": "Failed to install HHD fork into venv", "steps": steps}

    # 3. Verify hhd binary exists
    hhd_bin = _get_hhd_bin()
    if not os.path.isfile(hhd_bin):
        _log_error(f"HHD binary not found at {hhd_bin}")
        return {"success": False, "error": "HHD binary not found after install", "steps": steps}

    # 4. Install systemd service
    if not _install_service(steps):
        return {"success": False, "error": "Failed to create systemd service", "steps": steps}

    # 5. Stop system HHD
    _stop_system_hhd(steps)

    # 6. Start fork HHD
    if not _start_fork_hhd(steps):
        # Rollback: restart system HHD
        _log_warning("Fork HHD failed to start, rolling back to system HHD")
        _start_system_hhd(steps)
        return {"success": False, "error": "Fork HHD failed to start (rolled back to system)", "steps": steps}

    # Fix ownership — we run as root but the user owns the files
    user = _get_user()
    if user:
        base_dir = _get_base_dir()
        try:
            _run(["chown", "-R", f"{user}:{user}", base_dir], timeout=30)
        except Exception:
            pass

    _log_info("HHD fork installed and running")
    return {"success": True, "message": "HHD fork installed and running", "steps": steps}


def uninstall():
    """Stop the fork HHD and restore the system HHD service."""
    steps = []

    _log_info("=== HHD Fork Uninstall Start ===")

    # 1. Stop fork HHD
    _stop_fork_hhd(steps)

    # 2. Remove service
    _remove_service(steps)

    # 3. Restart system HHD
    if not _start_system_hhd(steps):
        _log_warning("System HHD restart may have failed")

    _log_info("HHD fork uninstalled, system HHD restored")
    return {"success": True, "message": "System HHD restored", "steps": steps}


def update():
    """Pull latest changes from the fork and restart."""
    steps = []

    _log_info("=== HHD Fork Update Start ===")

    if not is_installed().get("installed"):
        return {"success": False, "error": "HHD fork is not installed", "steps": steps}

    # 1. Update repo
    if not _update_repo(steps):
        return {"success": False, "error": "Failed to update repo", "steps": steps}

    # 2. Re-run pip install (in case dependencies changed)
    if not _setup_venv(steps):
        return {"success": False, "error": "Failed to reinstall after update", "steps": steps}

    # 3. Restart fork service
    user = _get_user()
    if user:
        unit = f"{SERVICE_TEMPLATE}{user}.service"
        try:
            r = _run(["systemctl", "restart", unit], timeout=30)
            if r.returncode == 0:
                steps.append("Restarted fork HHD")
            else:
                _log_warning(f"Restart failed: {r.stderr.strip()}")
                steps.append("Restart may have failed")
        except Exception as e:
            _log_warning(f"Restart failed: {e}")

    _log_info("HHD fork updated")
    return {"success": True, "message": "HHD fork updated and restarted", "steps": steps}


def is_installed():
    """Check if the HHD fork is installed and running."""
    repo_dir = _get_repo_dir()
    venv_dir = _get_venv_dir()
    hhd_bin = _get_hhd_bin()

    has_repo = os.path.isdir(os.path.join(repo_dir, ".git"))
    has_venv = os.path.isdir(venv_dir)
    has_bin = os.path.isfile(hhd_bin)
    running = _is_fork_running()
    service_exists = os.path.isfile(_get_service_path())

    return {
        "installed": has_repo and has_venv and has_bin and service_exists,
        "running": running,
        "has_repo": has_repo,
        "has_venv": has_venv,
        "has_bin": has_bin,
        "service_exists": service_exists,
    }


def get_status():
    """Get detailed status of the HHD fork installation."""
    status = is_installed()
    repo_dir = _get_repo_dir()

    # Get git info
    if status.get("has_repo"):
        try:
            r = _run(["git", "-C", repo_dir, "log", "--oneline", "-1"], timeout=5)
            if r.returncode == 0:
                status["commit"] = r.stdout.strip()
        except Exception:
            pass

        try:
            r = _run(["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
            if r.returncode == 0:
                status["branch"] = r.stdout.strip()
        except Exception:
            pass

    return status


def get_intercept_mode():
    """Check if full intercept mode is enabled in the fork's const.py."""
    repo_dir = _get_repo_dir()
    const_path = os.path.join(repo_dir, "src", "hhd", "device", "oxp", "const.py")

    if not os.path.exists(const_path):
        return {"enabled": True, "error": "Fork const.py not found"}

    try:
        with open(const_path) as f:
            content = f.read()
        if '"apex_intercept": False' in content:
            return {"enabled": False}
        return {"enabled": True}
    except Exception as e:
        return {"enabled": True, "error": str(e)}


def set_intercept_mode(enabled):
    """Toggle full intercept mode in the fork's const.py and restart HHD."""
    steps = []
    repo_dir = _get_repo_dir()
    const_path = os.path.join(repo_dir, "src", "hhd", "device", "oxp", "const.py")

    _log_info(f"=== Set Intercept Mode: {'Full' if enabled else 'Face buttons only'} ===")

    if not os.path.exists(const_path):
        return {"success": False, "error": "Fork const.py not found", "steps": steps}

    # Read current content
    try:
        with open(const_path) as f:
            content = f.read()
    except Exception as e:
        return {"success": False, "error": f"Failed to read const.py: {e}", "steps": steps}

    # Toggle the value
    if enabled:
        new_content = content.replace('"apex_intercept": False', '"apex_intercept": True')
    else:
        new_content = content.replace('"apex_intercept": True', '"apex_intercept": False')

    if new_content == content:
        mode_str = "Full intercept" if enabled else "Face buttons only"
        return {"success": True, "message": f"Already in {mode_str} mode", "steps": ["No change needed"]}

    # Write modified content (no ostree unlock needed — it's in /home/)
    try:
        with open(const_path, "w") as f:
            f.write(new_content)
        mode_str = "Full intercept" if enabled else "Face buttons only"
        steps.append(f"Set intercept mode: {mode_str}")
        _log_info(f"Set apex_intercept={enabled} in fork const.py")
    except Exception as e:
        return {"success": False, "error": f"Failed to write const.py: {e}", "steps": steps}

    # Restart fork HHD
    user = _get_user()
    if user:
        unit = f"{SERVICE_TEMPLATE}{user}.service"
        try:
            r = _run(["systemctl", "restart", unit], timeout=30)
            if r.returncode == 0:
                steps.append("Restarted fork HHD")
            else:
                steps.append("HHD restart may have failed")
        except Exception:
            steps.append("HHD restart may have failed")

    mode_str = "Full intercept" if enabled else "Face buttons only"
    _log_info(f"Intercept mode switched to: {mode_str}")
    return {"success": True, "message": f"Switched to {mode_str} mode", "steps": steps}
