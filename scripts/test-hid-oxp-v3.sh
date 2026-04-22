#!/bin/bash
# Test script for hid-oxp v3 kernel driver sysfs attributes
# Source: https://github.com/pastaq/linux/tree/pastaq/7.1/hid/hid-oxp
#
# Usage: sudo ./test-hid-oxp-v3.sh [test]
#   all        - run all tests (default)
#   load       - load/reload module
#   led        - LED/RGB tests
#   buttons    - button remapping tests
#   gamepad    - gamepad mode switching
#   rumble     - rumble intensity tests
#   unload     - test module unload (known oops bug)
#   info       - show udevadm attribute walk
#   interactive - interactive mode to manually poke attributes

set -uo pipefail

MODULE_PATH="/var/home/srsholmes/Work/onexplayer-apex-bazzite-fixes/kernel-patches/hid-oxp/build/hid-oxp.ko"
LED="/sys/class/leds/oxp:rgb:joystick_rings"
DRIVER_PATH="/sys/bus/hid/drivers/hid-oxp"

PASS=0
FAIL=0
WARN=0

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

header() { echo -e "\n${BOLD}${BLUE}=== $1 ===${NC}\n"; }
pass()   { echo -e "  ${GREEN}PASS${NC}: $1"; ((PASS++)); }
fail()   { echo -e "  ${RED}FAIL${NC}: $1"; ((FAIL++)); }
warn()   { echo -e "  ${YELLOW}WARN${NC}: $1"; ((WARN++)); }
info()   { echo -e "  ${BLUE}INFO${NC}: $1"; }

# Find the Gen2 config device (has button_a attribute)
find_cfg_device() {
    for dev in "$DRIVER_PATH"/0003:1A86:FE00.*; do
        if [ -f "$dev/button_a" ]; then
            echo "$dev"
            return 0
        fi
    done
    return 1
}

# Write an attribute and verify readback (3s timeout to avoid hangs)
test_write_read() {
    local path="$1"
    local value="$2"
    local label="$3"

    if ! timeout 3 bash -c "echo '$value' | tee '$path' > /dev/null 2>&1"; then
        fail "$label: WRITE TIMED OUT (sysfs blocked — workqueue may be wedged)"
        return
    fi
    local readback
    readback=$(timeout 2 cat "$path" 2>&1)
    if [ "$readback" = "$value" ]; then
        pass "$label: wrote '$value', read back '$readback'"
    else
        fail "$label: wrote '$value', read back '$readback'"
    fi
}

check_module_loaded() {
    local mods
    mods=$(lsmod)
    echo "$mods" | grep -q hid_oxp 2>/dev/null || return 1
    return 0
}

check_prerequisites() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}ERROR: Must run as root (sudo)${NC}"
        exit 1
    fi

    if ! check_module_loaded; then
        echo -e "${YELLOW}hid-oxp not loaded. Loading...${NC}"
        if [ -f "$MODULE_PATH" ]; then
            insmod "$MODULE_PATH" 2>&1 && sleep 1 && info "Module loaded" || { echo -e "${RED}Failed to load module${NC}"; exit 1; }
        else
            echo -e "${RED}Module not found at $MODULE_PATH${NC}"
            exit 1
        fi
    fi
}

# ─── Test: Load Module ───

test_load() {
    header "Module Load"

    if check_module_loaded; then
        info "hid-oxp already loaded, unloading first..."
        rmmod hid_oxp 2>/dev/null || true
        sleep 1
    fi

    if [ ! -f "$MODULE_PATH" ]; then
        fail "Module not found at $MODULE_PATH"
        return
    fi

    if insmod "$MODULE_PATH" 2>&1; then
        pass "insmod succeeded"
    else
        fail "insmod failed"
        return
    fi

    sleep 1

    if check_module_loaded; then
        pass "Module appears in lsmod"
    else
        fail "Module not in lsmod after insmod"
        return
    fi

    # Check device bindings
    local gen1_count gen2_count
    gen1_count=$(ls -d "$DRIVER_PATH"/0003:1A2C:B001.* 2>/dev/null | wc -l)
    gen2_count=$(ls -d "$DRIVER_PATH"/0003:1A86:FE00.* 2>/dev/null | wc -l)

    if [ "$gen1_count" -ge 2 ]; then
        pass "Gen1 devices bound: $gen1_count"
    else
        fail "Expected >=2 Gen1 devices, got $gen1_count"
    fi

    if [ "$gen2_count" -ge 3 ]; then
        pass "Gen2 devices bound: $gen2_count"
    else
        fail "Expected >=3 Gen2 devices, got $gen2_count"
    fi

    # Check for errors in dmesg
    local errors
    errors=$(dmesg | tail -20 | grep -i "oxp.*error\|oxp.*fail\|oxp.*oops" || true)
    if [ -z "$errors" ]; then
        pass "No errors in dmesg"
    else
        fail "Errors in dmesg: $errors"
    fi
}

# ─── Test: LED Control ───

test_led() {
    header "LED / RGB Control"

    if [ ! -d "$LED" ]; then
        fail "LED sysfs path not found: $LED"
        return
    fi

    # Check all expected attributes exist
    for attr in brightness effect effect_index enabled enabled_index max_brightness multi_index multi_intensity speed speed_range; do
        if [ -f "$LED/$attr" ]; then
            local val
            val=$(cat "$LED/$attr")
            pass "$attr exists (value: $val)"
        else
            fail "$attr missing"
        fi
    done

    # Save original state
    local orig_enabled orig_effect orig_brightness orig_rgb orig_speed
    orig_enabled=$(cat "$LED/enabled")
    orig_effect=$(cat "$LED/effect")
    orig_brightness=$(cat "$LED/brightness")
    orig_rgb=$(cat "$LED/multi_intensity")
    orig_speed=$(cat "$LED/speed")

    info "Saved original state: enabled=$orig_enabled effect=$orig_effect brightness=$orig_brightness rgb=$orig_rgb speed=$orig_speed"

    # Test enable/disable
    test_write_read "$LED/enabled" "true" "LED enable"
    test_write_read "$LED/enabled" "false" "LED disable"
    test_write_read "$LED/enabled" "true" "LED re-enable"

    # Test effects
    local effects
    effects=$(cat "$LED/effect_index")
    info "Available effects: $effects"

    for effect in monocolor neon chroma_breathing aurora; do
        if echo "$effects" | grep -qw "$effect"; then
            test_write_read "$LED/effect" "$effect" "Effect $effect"
            sleep 0.5
        fi
    done

    # Test RGB (monocolor mode)
    echo monocolor | tee "$LED/effect" > /dev/null
    test_write_read "$LED/multi_intensity" "255 0 0" "RGB red"
    sleep 0.3
    test_write_read "$LED/multi_intensity" "0 255 0" "RGB green"
    sleep 0.3
    test_write_read "$LED/multi_intensity" "0 0 255" "RGB blue"
    sleep 0.3
    test_write_read "$LED/multi_intensity" "255 255 255" "RGB white"
    sleep 0.3

    # Test brightness
    test_write_read "$LED/brightness" "50" "Brightness 50"
    test_write_read "$LED/brightness" "100" "Brightness 100"
    test_write_read "$LED/brightness" "0" "Brightness 0"

    # Test speed
    test_write_read "$LED/speed" "0" "Speed 0"
    test_write_read "$LED/speed" "5" "Speed 5"
    test_write_read "$LED/speed" "9" "Speed 9"

    # Restore original state
    echo "$orig_speed" | tee "$LED/speed" > /dev/null
    echo "$orig_brightness" | tee "$LED/brightness" > /dev/null
    echo "$orig_rgb" | tee "$LED/multi_intensity" > /dev/null
    echo "$orig_enabled" | tee "$LED/enabled" > /dev/null
    [ "$orig_effect" != "unknown" ] && echo "$orig_effect" | tee "$LED/effect" > /dev/null 2>&1 || true
    info "Restored original LED state"
}

# ─── Test: Button Remapping ───

test_buttons() {
    header "Button Remapping"

    local dev
    dev=$(find_cfg_device) || { fail "No Gen2 config device found"; return; }
    info "Config device: $dev"

    # Check all button attributes exist
    for btn in button_a button_b button_x button_y button_lb button_rb button_lt button_rt button_l3 button_r3 button_start button_select button_d_up button_d_down button_d_left button_d_right button_m1 button_m2; do
        if [ -f "$dev/$btn" ]; then
            local val
            val=$(cat "$dev/$btn")
            pass "$btn exists (value: $val)"
        else
            fail "$btn missing"
        fi
    done

    # Check mapping options
    if [ -f "$dev/button_mapping_options" ]; then
        local options
        options=$(cat "$dev/button_mapping_options")
        info "Mapping options: $options"

        # Check key categories are present
        echo "$options" | grep -q "BTN_A" && pass "Gamepad buttons in options" || fail "Gamepad buttons missing from options"
        echo "$options" | grep -q "DPAD_UP" && pass "D-pad in options" || fail "D-pad missing from options"
        echo "$options" | grep -q "JOY_L_UP" && pass "Joystick directions in options" || fail "Joystick directions missing from options"
        echo "$options" | grep -q "KEY_F1" && pass "F-keys in options" || fail "F-keys missing from options"
        echo "$options" | grep -q "KEY_F24" && pass "F24 in options" || fail "F24 missing from options"
        echo "$options" | grep -q "BTN_GUIDE" && pass "Guide button in options" || fail "Guide button missing from options"
    else
        fail "button_mapping_options missing"
    fi

    # Save original button mappings
    local orig_a orig_m1 orig_m2
    orig_a=$(cat "$dev/button_a")
    orig_m1=$(cat "$dev/button_m1")
    orig_m2=$(cat "$dev/button_m2")

    # Test remapping
    test_write_read "$dev/button_a" "BTN_B" "Remap A -> B"
    test_write_read "$dev/button_a" "BTN_X" "Remap A -> X"
    test_write_read "$dev/button_m1" "BTN_GUIDE" "Remap M1 -> Guide"
    test_write_read "$dev/button_m1" "KEY_F13" "Remap M1 -> F13"
    test_write_read "$dev/button_m2" "KEY_F24" "Remap M2 -> F24"

    # Test reset_buttons
    if [ -f "$dev/reset_buttons" ]; then
        echo 1 | tee "$dev/reset_buttons" > /dev/null 2>&1
        local after_a after_m1 after_m2
        after_a=$(cat "$dev/button_a")
        after_m1=$(cat "$dev/button_m1")
        after_m2=$(cat "$dev/button_m2")

        if [ "$after_a" = "BTN_A" ]; then
            pass "reset_buttons restored button_a to BTN_A"
        else
            fail "reset_buttons: button_a is '$after_a', expected 'BTN_A'"
        fi
        if [ "$after_m1" = "KEY_F16" ]; then
            pass "reset_buttons restored button_m1 to KEY_F16"
        else
            fail "reset_buttons: button_m1 is '$after_m1', expected 'KEY_F16'"
        fi
        if [ "$after_m2" = "KEY_F17" ]; then
            pass "reset_buttons restored button_m2 to KEY_F17"
        else
            fail "reset_buttons: button_m2 is '$after_m2', expected 'KEY_F17'"
        fi
    else
        fail "reset_buttons attribute missing"
    fi
}

# ─── Test: Gamepad Mode ───

test_gamepad() {
    header "Gamepad Mode Switching"

    local dev
    dev=$(find_cfg_device) || { fail "No Gen2 config device found"; return; }

    if [ -f "$dev/gamepad_mode" ]; then
        local orig_mode
        orig_mode=$(cat "$dev/gamepad_mode")
        pass "gamepad_mode exists (value: $orig_mode)"

        local mode_index
        mode_index=$(cat "$dev/gamepad_mode_index")
        info "Available modes: $mode_index"

        test_write_read "$dev/gamepad_mode" "debug" "Switch to debug"
        sleep 0.5
        test_write_read "$dev/gamepad_mode" "xinput" "Switch to xinput"
        sleep 0.5

        # Restore
        echo "$orig_mode" | tee "$dev/gamepad_mode" > /dev/null
        info "Restored gamepad_mode to $orig_mode"
    else
        fail "gamepad_mode attribute missing"
    fi
}

# ─── Test: Rumble Intensity ───

test_rumble() {
    header "Rumble Intensity"

    local dev
    dev=$(find_cfg_device) || { fail "No Gen2 config device found"; return; }

    if [ -f "$dev/rumble_intensity" ]; then
        local orig_rumble
        orig_rumble=$(cat "$dev/rumble_intensity")
        pass "rumble_intensity exists (value: $orig_rumble)"

        local range
        range=$(cat "$dev/rumble_intensity_range")
        info "Range: $range"

        test_write_read "$dev/rumble_intensity" "0" "Rumble 0 (off)"
        test_write_read "$dev/rumble_intensity" "3" "Rumble 3 (mid)"
        test_write_read "$dev/rumble_intensity" "5" "Rumble 5 (max)"

        # Restore
        echo "$orig_rumble" | tee "$dev/rumble_intensity" > /dev/null
        info "Restored rumble_intensity to $orig_rumble"
    else
        fail "rumble_intensity attribute missing"
    fi
}

# ─── Test: Module Unload ───

test_unload() {
    header "Module Unload (KNOWN BUG: expects oops)"

    warn "rmmod currently causes kernel oops due to missing cancel_delayed_work_sync()"
    warn "The module will still unload and reload, but dmesg will show oops traces"

    read -rp "  Proceed with rmmod test? [y/N] " confirm
    if [[ "$confirm" != [yY] ]]; then
        info "Skipped"
        return
    fi

    # Remap a button first to test v3 reset-on-remove
    local dev
    dev=$(find_cfg_device) || { fail "No Gen2 config device found"; return; }
    echo "BTN_X" | tee "$dev/button_a" > /dev/null 2>&1
    info "Set button_a=BTN_X before unload"

    rmmod hid_oxp 2>&1 || true
    sleep 2

    if ! check_module_loaded; then
        pass "Module unloaded"
    else
        fail "Module still loaded after rmmod"
    fi

    # Check dmesg for oops
    local oops
    oops=$(dmesg | tail -40 | grep -c "Oops" || true)
    if [ "$oops" -gt 0 ]; then
        warn "Oops detected in dmesg ($oops occurrences) — known bug, needs cancel_delayed_work_sync fix"
    else
        pass "No oops on unload (bug may be fixed!)"
    fi

    # Reload and check button was reset
    info "Reloading module..."
    insmod "$MODULE_PATH" 2>&1
    sleep 1

    dev=$(find_cfg_device) || { fail "No Gen2 config device after reload"; return; }
    local btn_a
    btn_a=$(cat "$dev/button_a")
    if [ "$btn_a" = "BTN_A" ]; then
        pass "v3 reset-on-remove worked: button_a is BTN_A after reload"
    else
        warn "button_a is '$btn_a' after reload (expected BTN_A if v3 reset worked)"
    fi
}

# ─── Info: udevadm attribute walk ───

test_info() {
    header "udevadm Attribute Walk"

    if [ -d "$LED" ]; then
        info "LED device attributes:"
        echo ""
        cd "$LED" && udevadm info --attribute-walk "$PWD" 2>&1 | head -40
        cd /var/home/srsholmes/Work/onexplayer-apex-bazzite-fixes
        echo ""
    fi

    info "Gen2 config device attributes:"
    local dev
    dev=$(find_cfg_device) || { fail "No Gen2 config device found"; return; }
    echo ""
    ls "$dev/"
    echo ""

    info "All hid-oxp bound devices:"
    ls "$DRIVER_PATH/" | grep "^0003"
}

# ─── Interactive Mode ───

test_interactive() {
    header "Interactive Mode"

    local dev
    dev=$(find_cfg_device) || { fail "No Gen2 config device found"; return; }

    echo -e "LED path:    ${BOLD}$LED${NC}"
    echo -e "Config path: ${BOLD}$dev${NC}"
    echo ""
    echo "Examples:"
    echo "  cat $LED/effect"
    echo "  echo neon | sudo tee $LED/effect"
    echo "  echo true | sudo tee $LED/enabled"
    echo "  echo '255 0 0' | sudo tee $LED/multi_intensity"
    echo "  cat $dev/button_m1"
    echo "  echo BTN_GUIDE | sudo tee $dev/button_m1"
    echo "  echo 1 | sudo tee $dev/reset_buttons"
    echo ""
    echo "Pastaq's test command:"
    echo "  cd $LED && udevadm info --attribute-walk \$PWD"
    echo "  ls $dev/device/  (parent device attributes)"
    echo ""
    echo -e "${YELLOW}Dropping to shell — type 'exit' to return${NC}"
    bash
}

# ─── Summary ───

print_summary() {
    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$WARN warnings${NC}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# ─── Main ───

TEST="${1:-all}"

case "$TEST" in
    load)
        test_load
        ;;
    led)
        check_prerequisites
        test_led
        ;;
    buttons)
        check_prerequisites
        test_buttons
        ;;
    gamepad)
        check_prerequisites
        test_gamepad
        ;;
    rumble)
        check_prerequisites
        test_rumble
        ;;
    unload)
        check_prerequisites
        test_unload
        ;;
    info)
        check_prerequisites
        test_info
        ;;
    interactive)
        check_prerequisites
        test_interactive
        ;;
    all)
        check_prerequisites
        test_led
        test_buttons
        test_gamepad
        test_rumble
        test_info
        ;;
    *)
        echo "Usage: sudo $0 [load|led|buttons|gamepad|rumble|unload|info|interactive|all]"
        exit 1
        ;;
esac

print_summary
