"""Regression tests for mouse_control.py's tap/hold/drag state machine,
cursor mapping, and hand-loss handling. Run with: python -m pytest

These exist because most of the real bugs found in this file so far were
state-machine edge cases (noise right at a threshold, a single dropped
detection frame mid-drag, etc.) that are easy to reintroduce while tuning
constants and easy to miss by eye -- but cheap to pin down with a test.
"""
from unittest.mock import patch

import pytest

import mouse_control as mctrl


def _pt(x, y):
    return (x, y, 0.0)


def _hand(thumb=None, index=None, middle=None):
    """A 21-landmark hand with only the fingers pinch-detection cares about
    set explicitly; everything else is a dummy point far from the others."""
    h = [(0.0, 0.0, 0.0)] * 21
    h[4] = thumb if thumb is not None else _pt(1, 1)
    h[8] = index if index is not None else _pt(1, 1)
    h[12] = middle if middle is not None else _pt(1, 1)
    return h


NEAR = _pt(0.05, 0.05)
FAR = _pt(1, 1)


@pytest.fixture
def pyauto():
    """Patches every pyautogui call MouseController makes, so tests never
    touch the real mouse/screen, and hands back the mocks for assertions."""
    with patch.object(mctrl.pyautogui, "mouseDown") as md, \
         patch.object(mctrl.pyautogui, "mouseUp") as mu, \
         patch.object(mctrl.pyautogui, "click") as ck, \
         patch.object(mctrl.pyautogui, "moveTo") as mv, \
         patch.object(mctrl.pyautogui, "size", return_value=(1920, 1080)):
        yield {"mouseDown": md, "mouseUp": mu, "click": ck, "moveTo": mv}


def _settle_pinch(mc, thumb, index, middle, frames=8):
    """Feeds the same pinch shape for several frames so the internal distance
    smoothing has time to converge (mirrors how a real physical pinch, held
    for a few tick-loop frames, actually looks to the detector). Returns the
    LAST steady-state result -- use _feed_pinch if an earlier transition
    event (e.g. the single frame a click/release fires on) matters."""
    last = "tracking"
    for _ in range(frames):
        last = mc._update_pinch(_hand(thumb, index, middle))
    return last


def _feed_pinch(mc, thumb, index, middle, frames=8):
    """Like _settle_pinch, but returns every frame's result so a transient
    transition event (click, hold start, release) occurring on an early
    frame -- before later frames settle back to steady-state "tracking" --
    isn't lost."""
    return [mc._update_pinch(_hand(thumb, index, middle)) for _ in range(frames)]


class TestTapClick:
    def test_single_tap_fires_exactly_one_click_no_mousedown(self, pyauto):
        mc = mctrl.MouseController()
        _settle_pinch(mc, FAR, FAR, FAR)  # open hand first
        _settle_pinch(mc, _pt(0, 0), NEAR, FAR)  # engage pinch
        results = _feed_pinch(mc, _pt(0, 0), FAR, FAR, frames=3)  # release
        assert any("click" in r for r in results)
        assert pyauto["click"].call_count == 1
        assert pyauto["click"].call_args.kwargs.get("button") == "left"
        assert pyauto["mouseDown"].call_count == 0, (
            "a plain tap must never press the button down -- that's what "
            "used to let hand drift mid-tap turn a click into a drag"
        )

    def test_right_tap_uses_right_button(self, pyauto):
        mc = mctrl.MouseController()
        _settle_pinch(mc, FAR, FAR, FAR)
        _settle_pinch(mc, _pt(0, 0), FAR, NEAR)
        _settle_pinch(mc, _pt(0, 0), FAR, FAR, frames=3)
        assert pyauto["click"].call_args.kwargs.get("button") == "right"


class TestDoubleTapHold:
    def test_second_tap_within_window_starts_a_real_hold(self, pyauto):
        mc = mctrl.MouseController()
        _settle_pinch(mc, FAR, FAR, FAR)
        _settle_pinch(mc, _pt(0, 0), NEAR, FAR)
        _settle_pinch(mc, _pt(0, 0), FAR, FAR, frames=3)  # tap 1 -> click
        assert pyauto["click"].call_count == 1

        mc._last_release_time -= 0.2  # simulate a real elapsed gap before tap 2
        mc._await_deadline += 0.2
        result = None
        for _ in range(8):
            result = mc._update_pinch(_hand(_pt(0, 0), NEAR, FAR))
            if "hold" in result:
                break
        assert result == "left hold start"
        assert pyauto["mouseDown"].call_args.kwargs.get("button") == "left"

        release_results = _feed_pinch(mc, _pt(0, 0), FAR, FAR, frames=3)
        assert "left up" in release_results
        assert pyauto["mouseUp"].call_count == 1
        # a drag's release must never also fire a click
        assert pyauto["click"].call_count == 1

    def test_second_tap_after_window_is_a_fresh_click_not_a_hold(self, pyauto):
        mc = mctrl.MouseController()
        _settle_pinch(mc, FAR, FAR, FAR)
        _settle_pinch(mc, _pt(0, 0), NEAR, FAR)
        _settle_pinch(mc, _pt(0, 0), FAR, FAR, frames=3)
        mc._await_deadline = 0  # force the double-tap window to have expired
        result = _settle_pinch(mc, _pt(0, 0), NEAR, FAR)
        assert "hold" not in result
        assert pyauto["mouseDown"].call_count == 0

    def test_instant_reengage_right_after_release_is_not_a_second_tap(self, pyauto):
        """A noise-driven bounce back under the enter threshold the instant
        after release is physically impossible as a deliberate second tap --
        regression test for the bug that made single taps randomly hold."""
        mc = mctrl.MouseController()
        _settle_pinch(mc, FAR, FAR, FAR)
        _settle_pinch(mc, _pt(0, 0), NEAR, FAR)
        _settle_pinch(mc, _pt(0, 0), FAR, FAR, frames=3)
        assert pyauto["click"].call_count == 1
        # re-engage on the very next frame, zero real time elapsed
        result = mc._update_pinch(_hand(_pt(0, 0), NEAR, FAR))
        assert "hold" not in result
        assert pyauto["mouseDown"].call_count == 0


class TestPinchExclusivity:
    def test_left_and_right_never_fire_together(self, pyauto):
        mc = mctrl.MouseController()
        _settle_pinch(mc, FAR, FAR, FAR)
        # both fingers pinched close to the thumb simultaneously; index is closer
        result = _settle_pinch(mc, _pt(0, 0), _pt(0.05, 0.05), _pt(0.07, 0.07))
        assert result.startswith("left")
        assert pyauto["click"].call_count == 0  # not released yet
        # a right pinch cannot also start while left is active
        second = mc._update_pinch(_hand(_pt(0, 0), _pt(0.05, 0.05), _pt(0.05, 0.05)))
        assert not second.startswith("right")


class TestHandLossGrace:
    def test_brief_dropout_mid_drag_does_not_release(self, pyauto):
        mc = mctrl.MouseController()
        mc._pinched_side = "left"
        mc._holding = True
        result = mc.update(None, None)
        assert result == "tracking flicker"
        assert pyauto["mouseUp"].call_count == 0
        assert mc._holding is True, "a single missed frame must not kill an in-progress drag"

    def test_sustained_loss_does_release(self, pyauto):
        import time as _t
        mc = mctrl.MouseController()
        mc._pinched_side = "left"
        mc._holding = True
        mc._hand_lost_since = _t.time() - 1.0  # well past HAND_LOST_GRACE
        result = mc.update(None, None)
        assert result == "no hand"
        assert pyauto["mouseUp"].call_count == 1
        assert mc._holding is False

    def test_detection_resuming_mid_grace_continues_the_drag(self, pyauto):
        mc = mctrl.MouseController()
        mc._pinched_side = "left"
        mc._holding = True
        mc.update(None, None)  # one missed frame
        result = mc.update(_hand(_pt(0, 0), NEAR, FAR), _hand(_pt(0, 0), NEAR, FAR))
        assert "holding" in result or "down" in result
        assert pyauto["mouseUp"].call_count == 0


class TestReleaseAll:
    def test_releases_held_button_and_resets_state(self, pyauto):
        mc = mctrl.MouseController()
        mc._pinched_side = "right"
        mc._holding = True
        mc.release_all()
        assert pyauto["mouseUp"].call_args.kwargs.get("button") == "right"
        assert mc._pinched_side is None
        assert mc._holding is False
        assert mc._await_side is None

    def test_safe_to_call_when_nothing_is_held(self, pyauto):
        mc = mctrl.MouseController()
        mc.release_all()  # must not raise
        assert pyauto["mouseUp"].call_count == 0

    def test_idempotent(self, pyauto):
        mc = mctrl.MouseController()
        mc._pinched_side = "left"
        mc._holding = True
        mc.release_all()
        mc.release_all()
        assert pyauto["mouseUp"].call_count == 1


class TestCursorMapping:
    def test_palm_center_is_centroid_of_wrist_and_knuckles(self):
        hand = [(0.0, 0.0, 0.0)] * 21
        hand[0] = (0.0, 0.0, 0.0)
        hand[5] = (0.4, 0.0, 0.0)
        hand[9] = (0.4, 0.4, 0.0)
        hand[13] = (0.0, 0.4, 0.0)
        hand[17] = (0.2, 0.2, 0.0)
        cx, cy, _ = mctrl._palm_center(hand)
        assert cx == pytest.approx((0.0 + 0.4 + 0.4 + 0.0 + 0.2) / 5)
        assert cy == pytest.approx((0.0 + 0.0 + 0.4 + 0.4 + 0.2) / 5)

    def test_active_box_clamps_outside_the_margin(self, pyauto):
        mc = mctrl.MouseController()
        hand = [(0.0, 0.0, 0.0)] * 21
        for i in mctrl.PALM_LANDMARKS:
            hand[i] = (-5.0, -5.0, 0.0)  # far outside the active box
        mc._move_cursor((0.0, 0.0, 0.0))
        called_xy = pyauto["moveTo"].call_args[0]
        assert called_xy[0] == pytest.approx(0.0, abs=1.0)
        assert called_xy[1] == pytest.approx(0.0, abs=1.0)

    def test_single_monitor_bounds_match_pyautogui_size(self, pyauto):
        assert mctrl.MULTI_MONITOR is False, (
            "multi-monitor span must stay opt-in -- flipping the default "
            "silently changes cursor sensitivity for every existing user"
        )
        left, top, w, h = mctrl._screen_bounds()
        assert (left, top, w, h) == (0, 0, 1920, 1080)

    def test_multi_monitor_bounds_use_win32_virtual_screen(self, pyauto):
        with patch.object(mctrl, "MULTI_MONITOR", True), \
             patch.object(mctrl.sys, "platform", "win32"), \
             patch.object(mctrl, "ctypes") as fake_ctypes:
            fake_ctypes.windll.user32.GetSystemMetrics.side_effect = lambda i: {
                76: -1920, 77: 0, 78: 3968, 79: 1280,
            }[i]
            left, top, w, h = mctrl._screen_bounds()
            assert (left, top, w, h) == (-1920, 0, 3968, 1280)


class TestOneEuroFilter:
    def test_first_sample_passes_through_unfiltered(self):
        f = mctrl.OneEuroFilter()
        assert f.filter(42.0, 0.0) == 42.0

    def test_converges_toward_a_held_steady_value(self):
        f = mctrl.OneEuroFilter()
        f.filter(0.0, 0.0)
        t = 0.0
        for _ in range(60):
            t += 1 / 60
            out = f.filter(100.0, t)
        assert out == pytest.approx(100.0, abs=1.0)

    def test_zero_or_negative_dt_does_not_raise(self):
        f = mctrl.OneEuroFilter()
        f.filter(1.0, 5.0)
        f.filter(2.0, 5.0)  # same timestamp twice -- must not divide by zero
        f.filter(3.0, 4.0)  # even a clock going backwards must not raise

    def test_reset_drops_history(self):
        f = mctrl.OneEuroFilter()
        f.filter(1.0, 0.0)
        f.filter(2.0, 0.1)
        f.reset()
        assert f.filter(99.0, 10.0) == 99.0
