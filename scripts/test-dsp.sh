#!/bin/bash
# Quick DSP profile switcher for desktop testing
DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR="$HOME/.config/pipewire/pipewire.conf.d"
ACTIVE="$CONF_DIR/99-oxp-apex-speaker-dsp.conf"

case "$1" in
    balanced|bass_boost|treble)
        python3 -c "
import sys; sys.path.insert(0, '$DIR/decky-plugin/py_modules')
import speaker_dsp
config = speaker_dsp._generate_config('$1', 'alsa_output.pci-0000_65_00.6.HiFi__Speaker__sink')
with open('$ACTIVE', 'w') as f:
    f.write(config)
"
        systemctl --user restart pipewire.service
        echo "Switched to: $1"
        ;;
    off)
        rm -f "$ACTIVE"
        systemctl --user restart pipewire.service
        echo "DSP disabled"
        ;;
    *)
        echo "Usage: $0 {balanced|bass_boost|treble|off}"
        ;;
esac
