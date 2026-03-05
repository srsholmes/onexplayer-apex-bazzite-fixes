"""Speaker DSP enhancement for OneXPlayer Apex on Bazzite.

Writes a PipeWire filter-chain config that applies parametric EQ to the
internal speakers only. Uses PipeWire's builtin biquad filters — zero
external dependencies.

Config is written to ~/.config/pipewire/pipewire.conf.d/ so it survives
Bazzite updates and auto-loads on PipeWire startup.
"""

import json
import logging
import os
import pwd
import re
import subprocess

logger = logging.getLogger("OXP-SpeakerDSP")

# Pluggable log callbacks — set by main.py to route logs to the plugin log file.
_log_info_cb = None
_log_error_cb = None
_log_warning_cb = None


def set_log_callbacks(info_fn, error_fn, warning_fn):
    """Set external log callbacks (called by main.py to wire into plugin logging)."""
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


# --- Constants ---

CONFIG_FILENAME = "99-oxp-apex-speaker-dsp.conf"
SPEAKER_NODE = "alsa_output.pci-0000_65_00.6.HiFi__Speaker__sink"

# EQ profiles — serial biquad filter chains (same format as GPD Win Mini in Bazzite).
# Format: (label, freq_hz, Q, gain_db_or_None)
# Labels: bq_lowshelf, bq_highshelf, bq_peaking, bq_lowpass, bq_highpass
# Based on community-tested GPD Win Mini EQ by @BrotherChenwk.
PROFILES = {
    "balanced": {
        "description": "GPD-style EQ — sub-bass cut, upper bass boost, treble lift (recommended)",
        "bands": [
            ("bq_lowshelf", 32.0, 1.41, -10.0),    # Kill sub-bass (can't reproduce)
            ("bq_peaking", 64.0, 1.41, -6.0),       # Cut low bass
            ("bq_peaking", 125.0, 1.41, 3.0),        # Boost upper bass for warmth
            ("bq_peaking", 250.0, 1.41, -2.0),       # Reduce muddiness
            ("bq_peaking", 500.0, 1.41, 0.0),        # Neutral
            ("bq_peaking", 1000.0, 1.41, -1.0),      # Slight mid cut
            ("bq_peaking", 2000.0, 1.41, -1.0),      # Slight upper-mid cut
            ("bq_peaking", 4000.0, 1.41, 0.0),       # Neutral
            ("bq_peaking", 8000.0, 1.41, 1.0),       # Presence boost
            ("bq_peaking", 16000.0, 2.0, 3.0),       # Air boost
            ("bq_highshelf", 0.0, 1.0, 6.0),         # Treble lift (compensate rolloff)
        ],
    },
    "bass_boost": {
        "description": "Heavy bass — maximum low-end warmth and punch",
        "bands": [
            ("bq_lowshelf", 32.0, 1.41, -6.0),       # Cut sub-bass
            ("bq_peaking", 64.0, 1.41, 2.0),          # Low bass boost
            ("bq_peaking", 125.0, 1.41, 6.0),         # Upper bass boost
            ("bq_peaking", 250.0, 1.41, 3.0),         # Bass body
            ("bq_peaking", 500.0, 1.41, 0.0),         # Neutral
            ("bq_peaking", 1000.0, 1.41, -2.0),       # Scoop mids for bass contrast
            ("bq_peaking", 2000.0, 1.41, -2.0),       # Scoop upper mids
            ("bq_peaking", 4000.0, 1.41, -1.0),       # Slight cut
            ("bq_peaking", 8000.0, 1.41, 1.0),        # Presence
            ("bq_peaking", 16000.0, 2.0, 3.0),        # Air
            ("bq_highshelf", 0.0, 1.0, 6.0),          # Treble lift
        ],
    },
    "treble": {
        "description": "Bright — emphasis on clarity and detail",
        "bands": [
            ("bq_lowshelf", 32.0, 1.41, -10.0),     # Kill sub-bass
            ("bq_peaking", 64.0, 1.41, -6.0),        # Cut low bass
            ("bq_peaking", 125.0, 1.41, 2.0),         # Mild upper bass
            ("bq_peaking", 250.0, 1.41, -2.0),        # Cut muddiness
            ("bq_peaking", 500.0, 1.41, 0.0),         # Neutral
            ("bq_peaking", 1000.0, 1.41, 0.0),        # Neutral
            ("bq_peaking", 2000.0, 1.41, 1.0),        # Presence boost
            ("bq_peaking", 4000.0, 1.41, 2.0),        # Clarity boost
            ("bq_peaking", 8000.0, 1.41, 2.0),        # Presence
            ("bq_peaking", 16000.0, 2.0, 4.0),        # Air
            ("bq_highshelf", 0.0, 1.0, 8.0),          # Treble lift
        ],
    },
}

# 7 user-adjustable EQ bands (exposed in custom profile UI)
CUSTOM_EQ_BANDS = [
    {"label": "Bass",       "freq": 64,    "q": 1.41},
    {"label": "Upper Bass", "freq": 125,   "q": 1.41},
    {"label": "Low Mids",   "freq": 250,   "q": 1.41},
    {"label": "Mids",       "freq": 500,   "q": 1.41},
    {"label": "Upper Mids", "freq": 2000,  "q": 1.41},
    {"label": "Treble",     "freq": 8000,  "q": 1.41},
    {"label": "Air",        "freq": 16000, "q": 2.0},
]

# 4 fixed bands not exposed to the user (same across all profiles)
_FIXED_BANDS = [
    ("bq_lowshelf", 32.0, 1.41, -10.0),   # Sub-bass cut
    ("bq_peaking", 1000.0, 1.41, -1.0),    # Slight mid cut
    ("bq_peaking", 4000.0, 1.41, 0.0),     # Neutral
    ("bq_highshelf", 0.0, 1.0, 6.0),       # Treble lift (kept low to avoid clipping)
]

# Map from adjustable band freq → index in preset PROFILES bands list
# Preset band layout: 32(fixed), 64, 125, 250, 500, 1000(fixed), 2000, 4000(fixed), 8000, 16000, highshelf(fixed)
_PRESET_BAND_INDICES = {64: 1, 125: 2, 250: 3, 500: 4, 2000: 6, 8000: 8, 16000: 9}

CUSTOM_PROFILES_FILENAME = "oxp-custom-eq.json"

# Test sound subprocess — managed by play/stop functions
_test_sound_proc = None


def _get_user_info():
    """Get the real (non-root) user's info: (username, home_dir, uid).

    Decky runs as root, so we need to find the actual user.
    """
    # Check SUDO_USER first
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            pw = pwd.getpwnam(sudo_user)
            return (pw.pw_name, pw.pw_dir, pw.pw_uid)
        except KeyError:
            pass

    # Infer from Decky plugin dir path
    try:
        import decky
        plugin_dir = decky.DECKY_PLUGIN_DIR
        if plugin_dir.startswith("/home/"):
            parts = plugin_dir.split("/")
            if len(parts) >= 3:
                username = parts[2]
                try:
                    pw = pwd.getpwnam(username)
                    return (pw.pw_name, pw.pw_dir, pw.pw_uid)
                except KeyError:
                    pass
    except ImportError:
        pass

    # Fallback: first non-root user in /home
    try:
        for name in sorted(os.listdir("/home")):
            path = f"/home/{name}"
            if os.path.isdir(path) and name != "root":
                try:
                    pw = pwd.getpwnam(name)
                    return (pw.pw_name, pw.pw_dir, pw.pw_uid)
                except KeyError:
                    pass
    except OSError:
        pass

    return ("root", "/root", 0)


def _get_config_path():
    """Get the path for the PipeWire config file."""
    _, home_dir, _ = _get_user_info()
    config_dir = os.path.join(home_dir, ".config", "pipewire", "pipewire.conf.d")
    return os.path.join(config_dir, CONFIG_FILENAME)


def _find_speaker_node():
    """Try to auto-detect the speaker ALSA node, fall back to hardcoded.

    Runs pw-cli as the real user to list PipeWire nodes and find
    the ALC245 speaker sink.
    """
    username, _, uid = _get_user_info()
    try:
        env = _clean_env()
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
        r = subprocess.run(
            ["runuser", "-u", username, "--", "pw-cli", "list-objects", "Node"],
            capture_output=True, text=True, timeout=10,
            env=env,
        )
        if r.returncode == 0 and "Speaker" in r.stdout and "HiFi" in r.stdout:
            # Parse output to find speaker sink node name
            for line in r.stdout.splitlines():
                stripped = line.strip()
                if "node.name" in stripped and "Speaker" in stripped and "sink" in stripped:
                    # Format: node.name = "alsa_output..."
                    parts = stripped.split("=", 1)
                    if len(parts) == 2:
                        node = parts[1].strip().strip('"').strip()
                        if node:
                            _log_info(f"Auto-detected speaker node: {node}")
                            return node
    except Exception as e:
        _log_warning(f"Speaker node auto-detection failed: {e}")

    _log_info(f"Using hardcoded speaker node: {SPEAKER_NODE}")
    return SPEAKER_NODE


def _get_custom_profiles_path():
    """Get the path for the custom EQ profiles JSON file."""
    _, home_dir, _ = _get_user_info()
    return os.path.join(home_dir, ".config", "pipewire", CUSTOM_PROFILES_FILENAME)


def _load_custom_profiles():
    """Load custom EQ profiles from JSON file."""
    path = _get_custom_profiles_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("profiles", {})
    except Exception as e:
        _log_warning(f"Failed to load custom profiles: {e}")
        return {}


def _save_custom_profiles(profiles):
    """Save custom EQ profiles to JSON file."""
    path = _get_custom_profiles_path()
    config_dir = os.path.dirname(path)
    try:
        os.makedirs(config_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"profiles": profiles}, f, indent=2)
        # chown to real user
        username, _, uid = _get_user_info()
        gid = pwd.getpwnam(username).pw_gid
        os.chown(path, uid, gid)
    except Exception as e:
        _log_error(f"Failed to save custom profiles: {e}")
        raise


def _build_custom_bands(gains):
    """Build an 11-band chain from user gains dict + fixed bands.

    gains: {"64": -10, "125": 6, "250": -3, "500": 0, "2000": -1, "8000": 2, "16000": 6}
    Returns: list of (label, freq, q, gain) tuples in the same order as preset bands.
    """
    bands = []
    # Band 1: fixed sub-bass lowshelf
    bands.append(_FIXED_BANDS[0])
    # Bands 2-5: 64, 125, 250, 500
    for band_def in CUSTOM_EQ_BANDS[:4]:
        freq = band_def["freq"]
        gain = float(gains.get(str(freq), 0.0))
        bands.append(("bq_peaking", float(freq), band_def["q"], gain))
    # Band 6: fixed 1kHz
    bands.append(_FIXED_BANDS[1])
    # Band 7: 2kHz
    band_2k = CUSTOM_EQ_BANDS[4]
    bands.append(("bq_peaking", float(band_2k["freq"]), band_2k["q"],
                   float(gains.get(str(band_2k["freq"]), 0.0))))
    # Band 8: fixed 4kHz
    bands.append(_FIXED_BANDS[2])
    # Bands 9-10: 8kHz, 16kHz
    for band_def in CUSTOM_EQ_BANDS[5:7]:
        freq = band_def["freq"]
        gain = float(gains.get(str(freq), 0.0))
        bands.append(("bq_peaking", float(freq), band_def["q"], gain))
    # Band 11: fixed highshelf
    bands.append(_FIXED_BANDS[3])
    return bands


def _generate_config(profile_name, speaker_node, custom_bands=None):
    """Generate PipeWire SPA-JSON filter-chain config string.

    Uses serial biquad chain — same format as GPD Win Mini in Bazzite
    (/usr/share/pipewire/hardware-profiles/gpd-g1617-01/).

    If custom_bands is provided, uses those instead of looking up PROFILES.
    """
    if custom_bands is not None:
        bands = custom_bands
    else:
        profile = PROFILES.get(profile_name)
        if not profile:
            raise ValueError(f"Unknown profile: {profile_name}")
        bands = profile["bands"]

    # Build node entries
    nodes = []
    for i, (label, freq, q, gain) in enumerate(bands, 1):
        control = f'"Freq" = {freq} "Q" = {q}'
        if gain is not None:
            control += f' "Gain" = {gain}'
        nodes.append(
            f"                    {{\n"
            f"                        type  = builtin\n"
            f"                        name  = eq_band_{i}\n"
            f"                        label = {label}\n"
            f"                        control = {{ {control} }}\n"
            f"                    }}"
        )
    nodes_str = "\n".join(nodes)

    # Build serial links (band_1 -> band_2 -> ... -> band_N)
    links = []
    for i in range(1, len(bands)):
        links.append(
            f'                    {{ output = "eq_band_{i}:Out" input = "eq_band_{i+1}:In" }}'
        )
    links_str = "\n".join(links)

    return f"""\
# OXP Apex Speaker DSP — {profile_name} profile
# Auto-generated by OneXPlayer Apex Tools
# Based on GPD Win Mini community EQ by @BrotherChenwk
context.modules = [
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "OXP Apex Speaker EQ"
            media.name       = "OXP Apex Speaker EQ"
            filter.graph = {{
                nodes = [
{nodes_str}
                ]
                links = [
{links_str}
                ]
            }}

            audio.channels = 2
            audio.position = [ FL FR ]
            capture.props = {{
                node.name   = "OXP Apex Speaker EQ"
                media.class = Audio/Sink
                node.virtual = false
                priority.driver = 1009
                priority.session = 1009
            }}
            playback.props = {{
                node.name   = "OXP Apex Speaker EQ Output"
                node.passive = true
                node.target = "{speaker_node}"
            }}
        }}
    }}
]
"""


def _restart_pipewire():
    """Restart PipeWire user service so the config takes effect."""
    username, _, uid = _get_user_info()
    env = _clean_env()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

    _log_info(f"Restarting PipeWire for user {username} (uid={uid})...")
    try:
        r = subprocess.run(
            ["runuser", "-u", username, "--",
             "systemctl", "--user", "restart", "pipewire.service"],
            capture_output=True, text=True, timeout=15,
            env=env,
        )
        if r.returncode != 0:
            _log_warning(f"PipeWire restart returned {r.returncode}: {r.stderr.strip()}")
            # Also try pipewire-pulse in case it needs a kick
            subprocess.run(
                ["runuser", "-u", username, "--",
                 "systemctl", "--user", "restart", "pipewire-pulse.service"],
                capture_output=True, text=True, timeout=15,
                env=env,
            )
        else:
            _log_info("PipeWire restarted successfully")
    except subprocess.TimeoutExpired:
        _log_error("PipeWire restart timed out")
        raise
    except Exception as e:
        _log_error(f"PipeWire restart failed: {e}")
        raise


def get_status():
    """Check if speaker DSP is currently enabled and which profile is active.

    Returns: {"enabled": bool, "profile": str|None, "speaker_node": str|None}
    """
    config_path = _get_config_path()

    if not os.path.exists(config_path):
        return {"enabled": False, "profile": None, "speaker_node": None}

    # Parse profile from the config file comment header
    profile = None
    try:
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("# OXP Apex Speaker DSP"):
                    # Format: "# OXP Apex Speaker DSP — balanced profile"
                    if "—" in line and "profile" in line:
                        part = line.split("—", 1)[1].strip()
                        profile = part.replace("profile", "").strip()
                        # Accept preset names and custom profile names
                        if profile not in PROFILES:
                            custom = _load_custom_profiles()
                            if profile not in custom:
                                profile = None
                    break
    except Exception:
        pass

    # Try to read speaker node from config
    speaker_node = None
    try:
        with open(config_path) as f:
            content = f.read()
        for line in content.splitlines():
            if "node.target" in line and "=" in line:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    speaker_node = parts[1].strip().strip('"').strip()
                    break
    except Exception:
        pass

    return {
        "enabled": True,
        "profile": profile,
        "speaker_node": speaker_node,
    }


def enable(profile="balanced"):
    """Enable speaker DSP with the specified profile (preset or custom name).

    Detects the speaker node, writes the PipeWire config, and restarts PipeWire.
    """
    _log_info(f"=== Speaker DSP Enable ({profile}) ===")

    custom_bands = None
    if profile not in PROFILES:
        # Check if it's a custom profile
        custom = _load_custom_profiles()
        if profile not in custom:
            return {"success": False, "error": f"Unknown profile: {profile}"}
        custom_bands = _build_custom_bands(custom[profile])

    # Detect speaker node
    speaker_node = _find_speaker_node()

    # Generate config
    try:
        config = _generate_config(profile, speaker_node, custom_bands=custom_bands)
    except Exception as e:
        _log_error(f"Failed to generate config: {e}")
        return {"success": False, "error": str(e)}

    # Ensure config directory exists
    config_path = _get_config_path()
    config_dir = os.path.dirname(config_path)
    try:
        os.makedirs(config_dir, exist_ok=True)
    except Exception as e:
        _log_error(f"Failed to create config directory: {e}")
        return {"success": False, "error": f"Cannot create config directory: {e}"}

    # Write config file
    try:
        with open(config_path, "w") as f:
            f.write(config)
        _log_info(f"Wrote config to {config_path}")
    except Exception as e:
        _log_error(f"Failed to write config: {e}")
        return {"success": False, "error": f"Cannot write config: {e}"}

    # chown to the real user (PipeWire runs as user, not root)
    username, _, uid = _get_user_info()
    try:
        gid = pwd.getpwnam(username).pw_gid
        # chown the config file and parent dirs we may have created
        os.chown(config_path, uid, gid)
        # Walk up and fix ownership for dirs we created
        d = config_dir
        while d and not d.endswith(".config"):
            try:
                os.chown(d, uid, gid)
            except Exception:
                break
            d = os.path.dirname(d)
    except Exception as e:
        _log_warning(f"chown failed (config may not load): {e}")

    # Restart PipeWire
    try:
        _restart_pipewire()
    except Exception as e:
        return {
            "success": True,
            "warning": f"Config written but PipeWire restart failed: {e}",
            "profile": profile,
            "speaker_node": speaker_node,
        }

    _log_info(f"Speaker DSP enabled with {profile} profile")
    return {
        "success": True,
        "message": f"Speaker DSP enabled — {profile} profile",
        "profile": profile,
        "speaker_node": speaker_node,
    }


def disable():
    """Disable speaker DSP by removing the config file and restarting PipeWire."""
    _log_info("=== Speaker DSP Disable ===")

    config_path = _get_config_path()

    if not os.path.exists(config_path):
        return {"success": True, "message": "Already disabled"}

    try:
        os.remove(config_path)
        _log_info(f"Removed config: {config_path}")
    except Exception as e:
        _log_error(f"Failed to remove config: {e}")
        return {"success": False, "error": f"Cannot remove config: {e}"}

    # Restart PipeWire
    try:
        _restart_pipewire()
    except Exception as e:
        return {
            "success": True,
            "warning": f"Config removed but PipeWire restart failed: {e}",
        }

    _log_info("Speaker DSP disabled")
    return {"success": True, "message": "Speaker DSP disabled"}


def set_profile(name):
    """Switch to a different EQ profile (preset or custom). Rewrites config and restarts PipeWire."""
    _log_info(f"=== Speaker DSP Set Profile: {name} ===")

    if name not in PROFILES:
        custom = _load_custom_profiles()
        if name not in custom:
            return {"success": False, "error": f"Unknown profile: {name}"}

    # Re-enable with new profile (overwrites config + restarts)
    return enable(name)


def list_profiles():
    """Return available EQ profiles with descriptions."""
    return {
        name: {"description": p["description"]}
        for name, p in PROFILES.items()
    }


def get_preset_bands(profile_name):
    """Return the 7 adjustable band values for a preset profile.

    Returns: {"bands": [{"label": "Bass", "freq": 64, "gain": -10}, ...]}
    """
    profile = PROFILES.get(profile_name)
    if not profile:
        return {"error": f"Unknown preset: {profile_name}"}

    result = []
    for band_def in CUSTOM_EQ_BANDS:
        freq = band_def["freq"]
        idx = _PRESET_BAND_INDICES.get(freq)
        if idx is not None:
            gain = profile["bands"][idx][3]  # 4th element is gain
        else:
            gain = 0.0
        result.append({"label": band_def["label"], "freq": freq, "gain": gain})
    return {"bands": result}


def get_custom_profiles():
    """Return all saved custom profiles.

    Returns: {"profiles": {"My Profile": {"64": -10, "125": 6, ...}, ...}}
    """
    return {"profiles": _load_custom_profiles()}


def save_custom_profile(name, gains):
    """Save or update a named custom profile.

    name: profile name string
    gains: dict of {"64": -10, "125": 6, "250": -3, "500": 0, "2000": -1, "8000": 2, "16000": 6}
    """
    _log_info(f"Saving custom EQ profile: {name}")
    if not name or not name.strip():
        return {"success": False, "error": "Profile name cannot be empty"}
    name = name.strip()
    if name in PROFILES:
        return {"success": False, "error": f"Cannot overwrite preset profile: {name}"}

    # Validate gains — must have all 7 bands, values in [-15, 15]
    validated = {}
    for band_def in CUSTOM_EQ_BANDS:
        freq_str = str(band_def["freq"])
        val = gains.get(freq_str, 0)
        try:
            val = float(val)
        except (TypeError, ValueError):
            val = 0.0
        val = max(-15.0, min(15.0, val))
        validated[freq_str] = val

    try:
        profiles = _load_custom_profiles()
        profiles[name] = validated
        _save_custom_profiles(profiles)
    except Exception as e:
        return {"success": False, "error": str(e)}

    # If this profile is currently active, re-apply it
    status = get_status()
    if status["enabled"] and status["profile"] == name:
        return enable(name)

    return {"success": True, "message": f"Saved profile: {name}"}


def delete_custom_profile(name):
    """Remove a named custom profile."""
    _log_info(f"Deleting custom EQ profile: {name}")
    profiles = _load_custom_profiles()
    if name not in profiles:
        return {"success": False, "error": f"Profile not found: {name}"}

    del profiles[name]
    try:
        _save_custom_profiles(profiles)
    except Exception as e:
        return {"success": False, "error": str(e)}

    # If this was the active profile, switch to balanced
    status = get_status()
    if status["enabled"] and status["profile"] == name:
        enable("balanced")

    return {"success": True, "message": f"Deleted profile: {name}"}


def _run_wpctl(args, uid, username):
    """Run a wpctl command as the real user and return stdout."""
    env = _clean_env()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    r = subprocess.run(
        ["runuser", "-u", username, "--", "wpctl"] + args,
        capture_output=True, text=True, timeout=10,
        env=env,
    )
    return r


def _find_node_id(node_name, uid, username):
    """Find the PipeWire node ID for a given sink name via wpctl status.

    Parses wpctl status output to find the node ID matching the given name.
    Returns the node ID as a string, or None if not found.
    """
    r = _run_wpctl(["status"], uid, username)
    if r.returncode != 0:
        _log_warning(f"wpctl status failed: {r.stderr.strip()}")
        return None

    for line in r.stdout.splitlines():
        if node_name in line:
            # Lines look like: " │  *   40. OXP Apex Speaker EQ  [Audio/Sink]"
            # or:               " │      66. Ryzen HD Audio Controller Speaker  [vol: 1.00]"
            m = re.search(r"(\d+)\.", line)
            if m:
                return m.group(1)
    return None


def bypass():
    """Bypass the EQ by switching default sink to the physical speaker.

    Instant — no config rewrite, no PipeWire restart.
    Finds the speaker sink by description in wpctl status (not ALSA node name).
    """
    username, _, uid = _get_user_info()
    r = _run_wpctl(["status"], uid, username)
    if r.returncode != 0:
        return {"success": False, "error": f"wpctl status failed: {r.stderr.strip()}"}

    # Find the physical speaker in the Sinks section by description
    in_sinks = False
    node_id = None
    for line in r.stdout.splitlines():
        if "Sinks:" in line:
            in_sinks = True
            continue
        if in_sinks:
            if line.strip() and not line.startswith(" "):
                break
            if "Speaker" in line and "EQ" not in line:
                m = re.search(r"(\d+)\.", line)
                if m:
                    node_id = m.group(1)
                    break

    if not node_id:
        return {"success": False, "error": "Cannot find physical speaker sink in wpctl"}

    r = _run_wpctl(["set-default", node_id], uid, username)
    if r.returncode != 0:
        return {"success": False, "error": f"wpctl set-default failed: {r.stderr.strip()}"}

    _log_info(f"EQ bypassed — default sink set to physical speaker (node {node_id})")
    return {"success": True, "bypassed": True}


def unbypass():
    """Restore the EQ by switching default sink back to the virtual EQ sink.

    Instant — no config rewrite, no PipeWire restart.
    """
    username, _, uid = _get_user_info()
    node_id = _find_node_id("OXP Apex Speaker EQ", uid, username)
    if not node_id:
        return {"success": False, "error": "Cannot find EQ sink — is speaker DSP enabled?"}

    r = _run_wpctl(["set-default", node_id], uid, username)
    if r.returncode != 0:
        return {"success": False, "error": f"wpctl set-default failed: {r.stderr.strip()}"}

    _log_info(f"EQ unbypass — default sink set to EQ (node {node_id})")
    return {"success": True, "bypassed": False}


def is_bypassed():
    """Check if the EQ is currently bypassed (physical speaker is default sink).

    Returns: {"bypassed": bool}
    """
    username, _, uid = _get_user_info()
    r = _run_wpctl(["status"], uid, username)
    if r.returncode != 0:
        return {"bypassed": False, "error": f"wpctl status failed: {r.stderr.strip()}"}

    # Look for the default sink (marked with * ) in the Audio Sinks section
    in_sinks = False
    for line in r.stdout.splitlines():
        if "Sinks:" in line:
            in_sinks = True
            continue
        if in_sinks:
            # End of sinks section
            if line.strip() and not line.startswith(" "):
                break
            if "*" in line and "OXP Apex Speaker EQ" in line:
                return {"bypassed": False}
            if "*" in line:
                # Default sink is something else (physical speaker or other)
                return {"bypassed": True}

    # If we couldn't determine, assume not bypassed
    return {"bypassed": False}


def _get_test_sound_path():
    """Get the path to the bundled test sound, falling back to system sound."""
    # Try bundled NCS track first
    try:
        import decky
        bundled = os.path.join(decky.DECKY_PLUGIN_DIR, "assets", "ncs-cyberblade.ogg")
        if os.path.exists(bundled):
            return bundled
    except ImportError:
        pass

    # Fallback: check relative to this file (dev/testing)
    here = os.path.dirname(os.path.abspath(__file__))
    bundled_dev = os.path.join(here, "..", "assets", "ncs-cyberblade.ogg")
    if os.path.exists(bundled_dev):
        return bundled_dev

    # Final fallback: system test tone
    system_sound = "/usr/share/sounds/freedesktop/stereo/audio-test-signal.oga"
    if os.path.exists(system_sound):
        return system_sound

    return None


def play_test_sound():
    """Start looping a test sound via pw-play for EQ preview."""
    global _test_sound_proc
    stop_test_sound()  # Kill any existing

    username, _, uid = _get_user_info()
    env = _clean_env()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

    sound_file = _get_test_sound_path()
    if not sound_file:
        return {"success": False, "error": "No test sound file found"}

    # Loop by running pw-play in a bash while loop
    try:
        _test_sound_proc = subprocess.Popen(
            ["runuser", "-u", username, "--", "bash", "-c",
             f'while true; do pw-play "{sound_file}"; done'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _log_info("Test sound started")
        return {"success": True, "playing": True}
    except Exception as e:
        _log_error(f"Failed to start test sound: {e}")
        return {"success": False, "error": str(e)}


def stop_test_sound():
    """Stop the looping test sound."""
    global _test_sound_proc
    if _test_sound_proc is not None:
        try:
            import signal
            # Kill the entire session (bash loop + pw-play child)
            os.killpg(_test_sound_proc.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            _test_sound_proc.wait(timeout=2)
        except Exception:
            try:
                _test_sound_proc.kill()
            except (ProcessLookupError, OSError):
                pass
        _test_sound_proc = None
        _log_info("Test sound stopped")
    return {"success": True, "playing": False}
