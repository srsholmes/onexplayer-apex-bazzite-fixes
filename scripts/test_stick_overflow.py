#!/usr/bin/env python3
"""Unit tests for stick axis overflow handling.

Strategy:
- Right stick (rs_x, rs_y): unconditional overflow correction at exact
  s16 boundaries (32767/-32768). Physical range ~[-32513, 32766].
- Left stick (ls_x, ls_y): NO correction — legitimately reaches ±32767/±32768.
- Delta > 1.5 safety net for all axes (catches rare left-stick overflow).

Run: python3 test_stick_overflow.py
"""
import sys

sys.path.insert(0, "../decky-plugin/py_modules/hhd_patches/patched")
try:
    from hid_v2 import _decode_axis
except ImportError:
    def _decode_axis(raw: int, negate: bool = False) -> float:
        val = raw / 32768.0
        if negate:
            val = -val
        return max(-1.0, min(1.0, val))
    print("WARNING: Could not import _decode_axis, using local copy\n")


# ── Simulate _produce_apex axis processing ──────────────────────

class AxisProcessor:
    """Simulates the axis processing from _produce_apex."""

    def __init__(self):
        self.prev_axes = {}

    def process(self, code, raw, negate=False):
        """Process one axis frame. Returns emitted value or None."""
        value = _decode_axis(raw, negate=negate)
        # Right stick unconditional overflow correction
        if code in ("rs_x", "rs_y") and raw in (32767, -32768):
            value = -value
        prev_val = self.prev_axes.get(code)
        if prev_val is not None:
            delta = abs(value - prev_val)
            if delta > 1.5:
                value = 1.0 if prev_val > 0 else -1.0
            elif delta < 0.002:
                return None
        self.prev_axes[code] = value
        return value


# ── Test helpers ──────────────────────────────────────────────────

passed = 0
failed = 0


def check(condition, label):
    global passed, failed
    if condition:
        print(f"PASS: {label}")
        passed += 1
    else:
        print(f"FAIL: {label}")
        failed += 1


def near(a, b, tol=0.001):
    return abs(a - b) < tol


# ── _decode_axis basic tests ─────────────────────────────────────

def test_decode_basics():
    check(_decode_axis(0) == 0.0, "decode: 0 -> 0.0")
    check(near(_decode_axis(16384), 0.5), "decode: 16384 -> ~0.5")
    check(near(_decode_axis(-16384), -0.5), "decode: -16384 -> ~-0.5")
    check(near(_decode_axis(16384, negate=True), -0.5), "decode: 16384 negate -> ~-0.5")
    check(near(_decode_axis(-16384, negate=True), 0.5), "decode: -16384 negate -> ~0.5")
    check(_decode_axis(32767) > 0.999, "decode: 32767 -> ~+1.0")
    check(_decode_axis(-32768) == -1.0, "decode: -32768 -> -1.0")
    for raw in [-32768, -16384, 0, 16384, 32767]:
        for neg in [False, True]:
            r = _decode_axis(raw, negate=neg)
            check(-1.0 <= r <= 1.0, f"decode clamped: raw={raw} negate={neg}")


# ── Right stick overflow (unconditional correction) ───────────────

def test_rs_x_overflow_left():
    """RS_X push left, wraps to 32767 -> corrected to -1.0."""
    proc = AxisProcessor()
    proc.process("rs_x", 0)
    proc.process("rs_x", -15000)
    proc.process("rs_x", -31000)
    result = proc.process("rs_x", 32767)
    check(result is not None and result < -0.9,
          f"RS_X left overflow corrected (got {result})")


def test_rs_x_overflow_right():
    """RS_X push right, wraps to -32768 -> corrected to +1.0."""
    proc = AxisProcessor()
    proc.process("rs_x", 0)
    proc.process("rs_x", 15000)
    proc.process("rs_x", 31000)
    result = proc.process("rs_x", -32768)
    check(result is not None and result > 0.9,
          f"RS_X right overflow corrected (got {result})")


def test_rs_y_overflow():
    """RS_Y (negated) overflow corrected."""
    proc = AxisProcessor()
    proc.process("rs_y", 0, negate=True)
    proc.process("rs_y", -15000, negate=True)  # positive output
    proc.process("rs_y", -31000, negate=True)  # ~+0.946
    result = proc.process("rs_y", 32767, negate=True)  # overflow
    check(result is not None and result > 0.9,
          f"RS_Y overflow corrected (got {result})")


def test_rs_overflow_recovery():
    """After RS overflow correction, normal values resume."""
    proc = AxisProcessor()
    proc.process("rs_x", 0)
    proc.process("rs_x", 25000)
    proc.process("rs_x", 31000)
    proc.process("rs_x", -32768)  # overflow, corrected to +1.0
    result = proc.process("rs_x", 30000)  # back to normal
    check(result is not None and result > 0.9,
          f"RS recovery after overflow (got {result})")


def test_rs_sustained_overflow():
    """Multiple RS overflow frames handled correctly."""
    proc = AxisProcessor()
    proc.process("rs_x", 0)
    proc.process("rs_x", -25000)
    proc.process("rs_x", -31000)
    r1 = proc.process("rs_x", 32767)  # corrected to -0.999
    check(r1 is not None and r1 < -0.9, f"RS first overflow (got {r1})")
    r2 = proc.process("rs_x", 32767)  # same, jitter filtered
    check(r2 is None, f"RS sustained overflow filtered (got {r2})")


def test_rs_multiple_cycles():
    """Multiple RS overflow cycles don't corrupt state."""
    proc = AxisProcessor()
    proc.process("rs_x", 0)
    proc.process("rs_x", 20000)
    proc.process("rs_x", 31000)
    r1 = proc.process("rs_x", -32768)
    check(r1 is not None and r1 > 0.9, f"RS cycle 1 (got {r1})")
    proc.process("rs_x", 31000)
    proc.process("rs_x", 15000)
    proc.process("rs_x", 0)
    proc.process("rs_x", -20000)
    proc.process("rs_x", -31000)
    r2 = proc.process("rs_x", 32767)
    check(r2 is not None and r2 < -0.9, f"RS cycle 2 (got {r2})")


# ── Left stick: legitimate boundary values (must NOT correct) ─────

def test_ls_x_legit_full_right():
    """LS_X legitimately reaches 32767 (full right). No correction."""
    proc = AxisProcessor()
    proc.process("ls_x", 0)
    proc.process("ls_x", 15000)
    proc.process("ls_x", 31000)
    result = proc.process("ls_x", 32767)
    check(result is not None and result > 0.9,
          f"LS_X legit 32767 stays positive (got {result})")


def test_ls_x_legit_full_left():
    """LS_X legitimately reaches -32768 (full left). No correction."""
    proc = AxisProcessor()
    proc.process("ls_x", 0)
    proc.process("ls_x", -15000)
    proc.process("ls_x", -31000)
    result = proc.process("ls_x", -32768)
    check(result is not None and result < -0.9,
          f"LS_X legit -32768 stays negative (got {result})")


def test_ls_y_legit_boundary():
    """LS_Y (negated) legitimately at boundary. No correction."""
    proc = AxisProcessor()
    proc.process("ls_y", 0, negate=True)
    proc.process("ls_y", 15000, negate=True)
    proc.process("ls_y", 31000, negate=True)
    result = proc.process("ls_y", 32767, negate=True)
    check(result is not None and result < -0.9,
          f"LS_Y legit boundary stays correct (got {result})")


def test_ls_x_sustained_boundary():
    """LS_X held at 32767 for multiple frames stays positive."""
    proc = AxisProcessor()
    proc.process("ls_x", 0)
    proc.process("ls_x", 20000)
    r1 = proc.process("ls_x", 32767)
    check(r1 is not None and r1 > 0.9, f"LS_X first 32767 (got {r1})")
    r2 = proc.process("ls_x", 32767)
    check(r2 is None, f"LS_X sustained 32767 jitter-filtered (got {r2})")


# ── Delta > 1.5 safety net (catches rare LS overflow) ────────────

def test_ls_overflow_caught_by_delta():
    """If LS somehow overflows, delta > 1.5 safety net catches it."""
    proc = AxisProcessor()
    proc.process("ls_x", 0)
    proc.process("ls_x", -25000)
    proc.process("ls_x", -31000)
    # Simulate overflow: raw=32767 but on ls_x (no unconditional fix)
    result = proc.process("ls_x", 32767)
    # value = +0.999, prev = -0.946, delta = 1.945 > 1.5
    # -> clamped to -1.0 (prev was negative)
    check(result is not None and result < -0.9,
          f"LS overflow caught by delta safety net (got {result})")


# ── Real hardware data sequences ──────────────────────────────────

def test_real_rx_left_wrap():
    """Real RX data: slow push left, wraps at 32767."""
    raw = [0, -2049, -5761, -8321, -14721, -20865, -25345, -30977, -31617,
           32767, 32767, 32767,
           -31617, -22401, -9345, -2049, 0]
    proc = AxisProcessor()
    for r in raw:
        proc.process("rs_x", r)
    final = proc.prev_axes.get("rs_x")
    check(final is not None and abs(final) < 0.01,
          f"Real RX wrap ends near zero (got {final})")


def test_real_lx_right_boundary():
    """Real LX data: push right to 32767 (legitimate)."""
    raw = [0, 3328, 7680, 32128, 32767, 32767, 32767, 28000, 15000, 0]
    proc = AxisProcessor()
    vals = []
    for r in raw:
        v = proc.process("ls_x", r)
        if v is not None:
            vals.append(v)
    # All positive values should stay positive
    pos_vals = [v for v in vals if v > 0.5]
    check(len(pos_vals) > 0 and all(v > 0 for v in pos_vals),
          f"Real LX right boundary stays positive")


def test_real_lx_left_boundary():
    """Real LX data: push left to -32768 (legitimate)."""
    raw = [0, -15000, -32512, -32768, -32768, -32768, -20000, 0]
    proc = AxisProcessor()
    vals = []
    for r in raw:
        v = proc.process("ls_x", r)
        if v is not None:
            vals.append(v)
    neg_vals = [v for v in vals if v < -0.9]
    check(len(neg_vals) > 0 and all(v < 0 for v in neg_vals),
          f"Real LX left boundary stays negative")


# ── Fast direction change (passes through for non-boundary) ──────

def test_fast_direction_change():
    """Fast full-range flip: delta > 1.5 safety net clamps to prev direction.
    This matches the original code behavior (acceptable tradeoff)."""
    proc = AxisProcessor()
    proc.process("ls_x", 0)
    proc.process("ls_x", 25000)
    result = proc.process("ls_x", -25000)  # delta ~1.53 > 1.5
    check(result is not None and result > 0.9,
          f"Fast direction change clamped by safety net (got {result})")


def test_moderate_direction_change():
    """Moderate direction change: delta < 1.5, passes through."""
    proc = AxisProcessor()
    proc.process("ls_x", 0)
    proc.process("ls_x", 20000)
    result = proc.process("ls_x", -20000)  # delta ~1.22 < 1.5
    check(result is not None and result < -0.5,
          f"Moderate direction change accepted (got {result})")


# ── Normal movement & jitter ──────────────────────────────────────

def test_normal_movement():
    """Normal gradual movement passes through."""
    proc = AxisProcessor()
    proc.process("ls_x", 0)
    r = proc.process("ls_x", 10000)
    check(r is not None and near(r, 10000/32768.0, tol=0.01),
          f"Normal movement (got {r})")


def test_jitter_filtered():
    """Small jitter below 0.002 is filtered."""
    proc = AxisProcessor()
    proc.process("ls_x", 16384)
    result = proc.process("ls_x", 16385)
    check(result is None, f"Jitter filtered (got {result})")


def test_near_boundary_not_overflow():
    """Raw 32766 on RS: no unconditional correction, but delta safety
    net may still trigger if the jump is > 1.5."""
    proc = AxisProcessor()
    proc.process("rs_x", 0)
    proc.process("rs_x", 25000)
    r1 = proc.process("rs_x", 32766)  # near boundary, small delta from 25000
    check(r1 is not None and r1 > 0.9,
          f"RS 32766 from nearby value passes through (got {r1})")


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Stick Overflow Tests (RS-only correction + delta safety)")
    print("=" * 60)

    test_decode_basics()
    test_rs_x_overflow_left()
    test_rs_x_overflow_right()
    test_rs_y_overflow()
    test_rs_overflow_recovery()
    test_rs_sustained_overflow()
    test_rs_multiple_cycles()
    test_ls_x_legit_full_right()
    test_ls_x_legit_full_left()
    test_ls_y_legit_boundary()
    test_ls_x_sustained_boundary()
    test_ls_overflow_caught_by_delta()
    test_real_rx_left_wrap()
    test_real_lx_right_boundary()
    test_real_lx_left_boundary()
    test_fast_direction_change()
    test_moderate_direction_change()
    test_normal_movement()
    test_jitter_filtered()
    test_near_boundary_not_overflow()

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{passed + failed} passed")
    if failed:
        print(f"  {failed} FAILED")
    print(f"{'=' * 60}")

    sys.exit(0 if failed == 0 else 1)
