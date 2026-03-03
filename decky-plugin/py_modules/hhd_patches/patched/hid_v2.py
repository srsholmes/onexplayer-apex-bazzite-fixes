import logging
import struct
import time
from collections import deque
from typing import Literal

from hhd.controller import can_read
from hhd.controller.physical.hidraw import GenericGamepadHidraw

logger = logging.getLogger(__name__)

# Patch version marker — if you see this in logs, the correct file is loaded
_PATCH_VERSION = "apex-stick-v8-no-filter"
logger.info(f"OXP hid_v2 loaded: {_PATCH_VERSION}")


def _decode_axis(raw: int, negate: bool = False) -> float:
    """Decode a signed 16-bit stick axis value to [-1.0, 1.0].

    Handles normalization, optional Y-axis inversion, and clamping.
    Overflow correction (negation at s16 boundaries) is applied in
    _produce_apex before this value is emitted.
    """
    val = raw / 32768.0
    if negate:
        val = -val
    return max(-1.0, min(1.0, val))


def gen_cmd(cid: int, cmd: bytes | list[int] | str, size: int = 64):
    # Command: [idx, cid, 0x3f, *cmd, 0x3f, cid], idx is optional
    if isinstance(cmd, str):
        c = bytes.fromhex(cmd)
    else:
        c = bytes(cmd)
    base = bytes([cid, 0xFF, *c])
    return base + bytes([0] * (size - len(base)))


def gen_cmd_v1(cid: int, cmd: list[int], idx: int = 0x01, size: int = 64):
    """Generate an HID v1 command packet (0x3F framing).

    Used by Apex and X1 Mini devices. Format:
    [cid, 0x3F, idx, ...cmd..., padding, 0x3F, cid]
    """
    base = bytes([cid, 0x3F, idx] + cmd)
    padding = bytes([0] * (size - len(base) - 2))
    return base + padding + bytes([0x3F, cid])


def gen_rgb_mode(mode: str):
    mc = 0
    match mode:
        case "monster_woke":
            mc = 0x0D
        case "flowing":
            mc = 0x03
        case "sunset":
            mc = 0x0B
        case "neon":
            mc = 0x05
        case "dreamy":
            mc = 0x07
        case "cyberpunk":
            mc = 0x09
        case "colorful":
            mc = 0x0C
        case "aurora":
            mc = 0x01
        case "sun":
            mc = 0x08
        case "aok":
            mc = 0x0E
    return gen_cmd(0x07, [mc])


gen_intercept = lambda enable: gen_cmd(0xB2, [0x03 if enable else 0x00, 0x01, 0x02])


def gen_brightness(
    enabled: bool,
    brightness: Literal["low", "medium", "high"],
):
    match brightness:
        case "low":
            bc = 0x01
        case "medium":
            bc = 0x03
        case _:  # "high":
            bc = 0x04

    return gen_cmd(0x07, [0xFD, enabled, 0x05, bc])


def gen_rgb_solid(r, g, b):
    return gen_cmd(0x07, [0xFE] + 20 * [r, g, b] + [0x00])


KBD_NAME = "keyboard"
HOME_NAME = "guide"
KBD_NAME_NON_TURBO = "share"
KBD_HOLD = 0.2
OXP_BUTTONS = {
    0x24: KBD_NAME,
    0x21: HOME_NAME,
    0x22: "extra_l1",
    0x23: "extra_r1",
}

# Full button map for Apex v1 intercept mode.
# When intercept is active, ALL input comes through vendor HID.
# D-pad is handled separately as hat axes (see _produce_apex).
APEX_V1_BUTTONS = {
    0x01: "a",
    0x02: "b",
    0x03: "x",
    0x04: "y",
    0x05: "lb",
    0x06: "rb",
    # 0x07/0x08: LT/RT digital click — ignored, use analog from state packets
    0x09: "start",
    0x0A: "select",
    0x0B: "ls",
    0x0C: "rs",
    0x21: HOME_NAME,
    0x22: "extra_r1",  # HID 0x22 = physical RIGHT paddle
    0x23: "extra_l1",  # HID 0x23 = physical LEFT paddle
    0x24: KBD_NAME,
}

# D-pad codes — emitted as hat_x/hat_y axis events for the Multiplexer
APEX_V1_DPAD = {0x0D: "up", 0x0E: "down", 0x0F: "left", 0x10: "right"}


INITIALIZE = [
    # gen_cmd(
    #     0xF5,
    #     "010238020101010101000000020102000000030103000000040104000000050105000000060106000000070107000000080108000000090109000000",
    # ),
    # gen_cmd(
    #     0xF5,
    #     "0102380202010a010a0000000b010b0000000c010c0000000d010d0000000e010e0000000f010f000000100110000000220200000000230200000000",
    # ),
    # gen_intercept(False),
]

INIT_DELAY = 4
WRITE_DELAY = 0.05
SCAN_DELAY = 1


class OxpHidrawV2(GenericGamepadHidraw):
    def __init__(self, *args, turbo: bool = True, apex_v1: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.prev = {}
        self.queue_kbd = None
        self.queue_home = None
        self.queue_cmd = deque(maxlen=10)
        self.next_send = 0
        self.queue_led = None
        self.turbo = turbo
        self.apex_v1 = apex_v1
        self.prev_axes = {}
        self.dpad = {"up": False, "down": False, "left": False, "right": False}

        self.prev_brightness = None
        self.prev_stick = None
        self.prev_stick_enabled = None
        # self.prev_center = None
        # self.prev_center_enabled = None

    def open(self):
        a = super().open()
        self.queue_kbd = None
        self.queue_home = None
        self.prev = {}
        self.prev_axes = {}
        self.dpad = {"up": False, "down": False, "left": False, "right": False}
        self.next_send = time.perf_counter() + INIT_DELAY

        if self.apex_v1:
            # Send intercept enable — takes over Xbox gamepad, routes all
            # input through vendor HID (buttons as type 0x01, analog as 0x02)
            self.queue_cmd.extend([gen_cmd_v1(0xB2, [0x03, 0x01, 0x02])])
        else:
            self.queue_cmd.extend(INITIALIZE)
        return a

    def close(self, exit: bool) -> bool:
        if self.apex_v1 and self.dev:
            try:
                # Disable intercept — Xbox gamepad resumes normal operation
                self.dev.write(gen_cmd_v1(0xB2, [0x00, 0x01, 0x02]))
                time.sleep(0.05)
            except Exception:
                pass
        return super().close(exit)

    def consume(self, events):
        if not self.dev:
            return

        # Apex v1 mode has no RGB — only flush queued commands (intercept enable)
        if self.apex_v1:
            curr = time.perf_counter()
            if self.queue_cmd and curr - self.next_send > 0:
                cmd = self.queue_cmd.popleft()
                logger.info(f"OXP C: {cmd.hex()}")
                self.dev.write(cmd)
                self.next_send = curr + WRITE_DELAY
            return

        # Capture led events
        for ev in events:
            if ev["type"] == "led":
                # if self.queue_led:
                #     logger.warning("OXP HID LED event queue overflow.")
                self.queue_led = ev

        # Send queued event if applicable
        curr = time.perf_counter()
        if self.queue_cmd and curr - self.next_send > 0:
            cmd = self.queue_cmd.popleft()
            logger.info(f"OXP C: {cmd.hex()}")
            self.dev.write(cmd)
            self.next_send = curr + WRITE_DELAY

        # Queue needs to flush before switching to next event
        # Also, there needs to be a led event to queue
        if self.queue_cmd or not self.queue_led:
            return
        ev = self.queue_led
        self.queue_led = None

        brightness = "high"
        stick = None
        stick_enabled = True

        match ev["mode"]:
            case "solid":
                stick = ev["red"], ev["green"], ev["blue"]
            case "oxp" | "aok":
                brightness = ev["brightnessd"]
                stick = ev["oxp"]
                if stick == "classic":
                    # Classic mode is a cherry red
                    stick = 0xB7, 0x30, 0x00
            case _:  # "disabled":
                stick_enabled = False

        # Force RGB to not initialize to workaround RGB breaking
        # rumble when being set
        if self.prev_stick_enabled is None:
            self.prev_stick_enabled = stick_enabled
        if self.prev_brightness is None:
            self.prev_brightness = brightness
        if self.prev_stick is None:
            self.prev_stick = stick

        if (
            stick_enabled != self.prev_stick_enabled
            or brightness != self.prev_brightness
        ):
            self.queue_cmd.append(gen_brightness(stick_enabled, brightness))
            self.prev_brightness = brightness
            self.prev_stick_enabled = stick_enabled

        if stick_enabled and stick != self.prev_stick:
            if isinstance(stick, str):
                self.queue_cmd.append(gen_rgb_mode(stick))
            else:
                self.queue_cmd.append(gen_rgb_solid(*stick))
            self.prev_stick = stick
            self.prev_brightness = brightness
            self.prev_stick_enabled = stick_enabled

    def _produce_apex(self, fds):
        """Produce events in Apex v1 intercept mode.

        Full intercept takes over the Xbox gamepad — all input comes through
        vendor HID as two packet types:
          - type 0x01: discrete button press/release events
          - type 0x02: continuous gamepad state (sticks + triggers analog)
        """
        evs = []

        if self.fd not in fds:
            return evs

        while can_read(self.fd):
            cmd = self.dev.read()

            if len(cmd) < 4 or cmd[0] != 0xB2:
                continue

            pkt_type = cmd[3]

            if pkt_type == 0x01 and len(cmd) >= 13:
                # Button event
                btn_code = cmd[6]
                pressed = cmd[12] == 0x01

                if btn_code in APEX_V1_DPAD:
                    # D-pad: track state and emit hat_x/hat_y axes
                    direction = APEX_V1_DPAD[btn_code]
                    if self.dpad[direction] != pressed:
                        self.dpad[direction] = pressed
                        hat_x = float(self.dpad["right"]) - float(self.dpad["left"])
                        hat_y = float(self.dpad["down"]) - float(self.dpad["up"])
                        prev_hx = self.prev_axes.get("hat_x")
                        prev_hy = self.prev_axes.get("hat_y")
                        if prev_hx is None or hat_x != prev_hx:
                            evs.append({"type": "axis", "code": "hat_x", "value": hat_x})
                            self.prev_axes["hat_x"] = hat_x
                        if prev_hy is None or hat_y != prev_hy:
                            evs.append({"type": "axis", "code": "hat_y", "value": hat_y})
                            self.prev_axes["hat_y"] = hat_y

                elif btn_code in APEX_V1_BUTTONS:
                    btn_name = APEX_V1_BUTTONS[btn_code]

                    # Debounce — skip if same state as previous
                    if btn_name in self.prev and self.prev[btn_name] == pressed:
                        continue
                    self.prev[btn_name] = pressed

                    evs.append(
                        {
                            "type": "button",
                            "code": btn_name,
                            "value": pressed,
                        }
                    )

            elif pkt_type == 0x02 and len(cmd) >= 25:
                # Gamepad state — analog sticks and triggers
                # byte[15]: RT (0-255), byte[16]: LT (0-255)
                # bytes[17:19]: LX (s16 LE), bytes[19:21]: LY (s16 LE, inverted)
                # bytes[21:23]: RX (s16 LE, wraps at full deflection)
                # bytes[23:25]: RY (s16 LE, inverted, wraps at full deflection)
                lt = cmd[16] / 255.0
                rt = cmd[15] / 255.0
                lx = max(-1.0, min(1.0, struct.unpack_from("<h", cmd, 17)[0] / 32768.0))
                ly = max(-1.0, min(1.0, -(struct.unpack_from("<h", cmd, 19)[0] / 32768.0)))

                # RX: the stick's physical range (~±31700) exceeds signed
                # 16-bit at full deflection. At full left the raw value
                # wraps from ~-31617 to exactly +32767; at full right it
                # wraps to exactly -32768. Only these exact boundary values
                # indicate overflow — all other values are real positions.
                rx_raw = struct.unpack_from("<h", cmd, 21)[0]
                if rx_raw == 32767:
                    rx = -1.0   # overflowed from left extreme
                elif rx_raw == -32768:
                    rx = 1.0    # overflowed from right extreme
                else:
                    rx = max(-1.0, min(1.0, rx_raw / 32768.0))

                # RY: same overflow as RX. At full up (raw negative) it
                # wraps to +32767; at full down (raw positive) it wraps
                # to -32768. The Y axis is inverted (negated).
                ry_raw = struct.unpack_from("<h", cmd, 23)[0]
                if ry_raw == 32767:
                    ry = 1.0    # overflowed from up (negative raw -> positive output)
                elif ry_raw == -32768:
                    ry = -1.0   # overflowed from down (positive raw -> negative output)
                else:
                    ry = max(-1.0, min(1.0, -(ry_raw / 32768.0)))

                axes = {
                    "lt": lt,
                    "rt": rt,
                    "ls_x": lx,
                    "ls_y": ly,
                    "rs_x": rx,
                    "rs_y": ry,
                }

                # Emit every axis value unconditionally — no delta
                # filtering. The direct uinput relay proved that
                # unfiltered values give native-feeling sticks.
                # Boundary overflow corrections above (rx_raw/ry_raw
                # == ±32768/32767) are kept.
                for code, value in axes.items():
                    evs.append(
                        {
                            "type": "axis",
                            "code": code,
                            "value": value,
                        }
                    )

            # type 0x03 = ACK responses, silently ignore

        return evs

    def produce(self, fds):
        if not self.dev:
            return []

        # Apex v1 intercept mode — completely different parsing path
        if self.apex_v1:
            return self._produce_apex(fds)

        evs = []
        # A bit unclean with 2 buttons but it works
        if self.queue_kbd:
            curr = time.perf_counter()
            if curr - KBD_HOLD > self.queue_kbd:
                evs = [
                    {
                        "type": "button",
                        "code": KBD_NAME if self.turbo else KBD_NAME_NON_TURBO,
                        "value": False,
                    }
                ]
                self.queue_kbd = None
        if self.queue_home:
            curr = time.perf_counter()
            if curr - KBD_HOLD > self.queue_home:
                evs = [
                    {
                        "type": "button",
                        "code": HOME_NAME,
                        "value": False,
                    }
                ]
                self.queue_home = None

        if self.fd not in fds:
            return evs

        while can_read(self.fd):
            cmd = self.dev.read()
            # logger.info(f"OXP R: {cmd.hex()}")

            cid = cmd[0]
            valid = cmd[1] == 0x3F and cmd[-2] == 0x3F

            if not valid:
                logger.warning(f"OXP HID invalid command: {cmd.hex()}")
                continue

            if cid in (0xF5, 0xB8):
                # Initialization (0xf5) and rgb (0xb8) command responses, skip
                continue

            if cid != 0xB2:
                logger.warning(f"OXP HID unknown command: {cmd.hex()}")
                continue

            btn = cmd[6]

            if btn not in OXP_BUTTONS:
                logger.warning(
                    f"OXP HID unknown button: {btn:x} from cmd:\n{cmd.hex()}"
                )
                continue

            btn = OXP_BUTTONS[btn]
            pressed = cmd[12] == 1

            if btn == KBD_NAME:
                if pressed and (btn not in self.prev or self.prev[btn] != pressed):
                    evs.append(
                        {
                            "type": "button",
                            "code": KBD_NAME if self.turbo else KBD_NAME_NON_TURBO,
                            "value": True,
                        }
                    )
                    self.queue_kbd = time.perf_counter()
                self.prev[btn] = pressed
                continue

            if btn == HOME_NAME:
                if pressed and (btn not in self.prev or self.prev[btn] != pressed):
                    evs.append(
                        {
                            "type": "button",
                            "code": HOME_NAME,
                            "value": True,
                        }
                    )
                    self.queue_home = time.perf_counter()
                self.prev[btn] = pressed
                continue

            if btn in self.prev and self.prev[btn] == pressed:
                # Debounce
                continue

            self.prev[btn] = pressed
            evs.append(
                {
                    "type": "button",
                    "code": btn,
                    "value": pressed,
                }
            )

        return evs
