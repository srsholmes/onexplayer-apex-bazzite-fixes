# Back Paddle (L4/R4) Support — Final Implementation

## Summary

The OXP Apex back paddles (L4/R4) are supported via **full intercept mode** on the `1a86:fe00` vendor HID device. When intercept is enabled, the Xbox gamepad goes silent and ALL controller input (face buttons, sticks, triggers, dpad, back paddles) routes through the vendor HID channel. HHD then reconstructs a virtual gamepad from these packets.

## What Changed from Initial Approach

| Aspect | Initial (uinput daemon) | Final (HHD full intercept) |
|--------|------------------------|---------------------------|
| Back paddle handling | Separate `back_paddle.py` daemon creating virtual uinput device | Integrated into HHD's `OxpHidrawV2` class |
| Face buttons/sticks | Handled by Xbox gamepad as normal | ALL input parsed from vendor HID packets |
| Complexity | Two input paths (Xbox + vendor HID) | Single input path (vendor HID only) |
| L4/R4 mapping | `BTN_TRIGGER_HAPPY1/2` on virtual device | `extra_l1`/`extra_r1` in HHD's Multiplexer |

## Key Discovery

**Initial assumption (wrong)**: L4/R4 are hardwired as B/Y duplicates.

**Reality**: Sending `gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])` to the vendor hidraw device enables full intercept mode where L4/R4 have their own unique codes (0x22/0x23). The tradeoff is that the Xbox gamepad goes completely silent — you must parse everything from vendor HID.

Partial intercept (`[0x03, 0x02, 0x02]`) was tested but behaves identically to full intercept — the Xbox gamepad always goes silent.

## Implementation

Three HHD files are patched (bundled in `hhd_patches/patched/`):

1. **`const.py`** — Registers Apex device with `"apex": True`, `"protocol": "hid_v2"`
2. **`base.py`** — Routes Apex to `OxpHidrawV2(apex_v1=True)` using X1_MINI hidraw constants
3. **`hid_v2.py`** — `_produce_apex()` method parses all vendor HID packets into HHD events

See [hid-reverse-engineering.md](./hid-reverse-engineering.md) for full protocol details.

## Physical Button Swap

HID codes are physically swapped:
- Code `0x22` = **right** paddle (mapped to `extra_r1`)
- Code `0x23` = **left** paddle (mapped to `extra_l1`)

## Testing

1. Apply button fix via plugin UI (patches all 3 HHD files + restarts HHD)
2. All face buttons, sticks, triggers, dpad should work immediately
3. L4/R4 back paddles appear as remappable buttons in Steam Input
4. Revert via plugin UI restores vanilla HHD behavior (L4/R4 revert to B/Y)
