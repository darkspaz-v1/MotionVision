"""Gesture-driven mouse cursor: absolute tablet-style position mapping plus
tap-to-click / double-tap-to-hold (thumb+index = left, thumb+middle = right).

Position source is the palm center -- the centroid of the raw wrist (0) and
four knuckle (5, 9, 13, 17) landmarks -- rather than a fingertip, since the
palm barely moves when your fingers curl to pinch. Pinch distances are measured on
hand_tracker.normalize_landmarks() output (wrist->middle-MCP scale = 1.0),
consistent with how gestures.distance() already works, so thresholds stay
hand-size/distance-from-camera invariant. Pinch distance intentionally
ignores z (the noisiest axis from a single camera).

A single pinch (engage then release) always resolves to one atomic
pyautogui.click() on release -- no button is held during the physical pinch,
so hand drift mid-tap can no longer turn a click into an accidental drag.
Holding/dragging requires a *second* pinch that starts within
DOUBLE_TAP_WINDOW of the first one's release; that second pinch presses the
button down for real and holds it until released.
"""
import logging
import math
import sys
import time

log = logging.getLogger("motionvision.mouse_control")

if sys.platform == "win32":
    # Defensive copy of the same call app.py makes at process startup (before
    # any pyautogui import) -- kept here too so importing this module on its
    # own (tests, a REPL) still gets correct absolute coordinates on a
    # display with non-100% Windows scaling. Safe to call twice.
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError) as exc:
        log.debug("SetProcessDpiAwareness failed: %s", exc)  # older Windows or already set
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError) as exc2:
            log.debug("SetProcessDPIAware failed: %s", exc2)  # best effort; coordinates may be scaled

import pyautogui

pyautogui.PAUSE = 0          # default 0.1s/call would visibly stall the 20ms Tk tick loop
pyautogui.FAILSAFE = False   # active box is designed to let mapped position reach real corners

ACTIVE_BOX_MARGIN_X = 0.28   # fraction of frame trimmed off each edge before mapping to
ACTIVE_BOX_MARGIN_Y = 0.28   # pyautogui.size() -- bigger margin = smaller box = more screen
                              # travel per inch of hand movement (more sensitive)

# One Euro Filter (https://cristal.univ-lille.fr/~casiez/1euro/): adapts its
# own smoothing strength to hand speed, unlike a fixed-alpha EMA -- heavy
# smoothing while the hand is nearly still (kills jitter for precision
# clicking), light smoothing during fast moves (stays responsive). It also
# correctly accounts for the tick loop's real, variable frame interval
# (MediaPipe inference time varies frame to frame) instead of assuming a
# clean fixed 20ms step the way the old EMA implicitly did.
ONE_EURO_MIN_CUTOFF = 0.8    # lower = steadier at rest
ONE_EURO_BETA = 0.6          # higher = less lag during fast movement
ONE_EURO_D_CUTOFF = 1.0      # cutoff for the derivative (speed) estimate itself

MIN_MOVE_PX = 3              # ignore filtered moves smaller than this -- kills residual jitter at rest

MULTI_MONITOR = False        # set True to let the cursor reach every monitor (spans the full
                              # virtual desktop) instead of just the primary one (pyautogui.size()
                              # always reports ONLY the primary monitor -- on a multi-monitor setup
                              # the cursor can currently never be placed on any other monitor at
                              # all). Off by default because it roughly halves effective sensitivity
                              # per monitor on a 2-monitor setup, since the same active-box hand
                              # movement now has to sweep both screens' combined width.

LEFT_PINCH_ENTER, LEFT_PINCH_EXIT = 0.32, 0.62     # thumb(4)-index(8), wide hysteresis vs chatter
RIGHT_PINCH_ENTER, RIGHT_PINCH_EXIT = 0.32, 0.62    # thumb(4)-middle(12)

DIST_SMOOTHING_ALPHA = 0.5   # EMA on the pinch distances themselves -- raw per-frame landmark
                              # noise right at the enter/exit threshold was flipping engage/release
                              # state within a single physical pinch, which read as "glitchy" and
                              # made single taps randomly turn into holds

DOUBLE_TAP_WINDOW = 0.6      # seconds -- a second pinch starting within this long after the
                              # first one's release becomes a hold/drag instead of another click
MIN_SECOND_TAP_GAP = 0.12    # seconds -- a re-engage sooner than this after a release can't be a
                              # deliberate second tap (fingers can't physically separate and
                              # re-touch that fast) -- it's noise, so it's always a fresh tap

HAND_LOST_GRACE = 0.15       # seconds -- a pinched/closed hand is genuinely harder for MediaPipe
                              # to detect than an open one, so tracking can drop for a frame or two
                              # even mid-drag. Only treat the hand as truly gone (and release/reset)
                              # after this long of *consecutive* missed detections.


def _dist2(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


class OneEuroFilter:
    """1D adaptive low-pass filter. Call filter(x, t) once per sample with a
    monotonically increasing timestamp t (seconds); reset() to drop history."""

    def __init__(self, min_cutoff=ONE_EURO_MIN_CUTOFF, beta=ONE_EURO_BETA,
                 d_cutoff=ONE_EURO_D_CUTOFF):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self):
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        r = 2 * math.pi * cutoff * dt
        return r / (r + 1)

    def filter(self, x, t):
        if self._t_prev is None:
            self._x_prev, self._dx_prev, self._t_prev = x, 0.0, t
            return x
        dt = max(t - self._t_prev, 1e-6)  # guard div-by-zero if two samples share a timestamp

        dx = (x - self._x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self._dx_prev

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self._x_prev

        self._x_prev, self._dx_prev, self._t_prev = x_hat, dx_hat, t
        return x_hat


def _screen_bounds():
    """Returns (left, top, width, height) in real screen pixels. Spans every
    monitor -- correctly handling one placed at a negative origin (e.g. to
    the left of or above the primary) -- when MULTI_MONITOR is True;
    otherwise just the primary monitor, matching plain pyautogui.size()."""
    if MULTI_MONITOR and sys.platform == "win32":
        try:
            gm = ctypes.windll.user32.GetSystemMetrics
            SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
            SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
            left, top = gm(SM_XVIRTUALSCREEN), gm(SM_YVIRTUALSCREEN)
            width, height = gm(SM_CXVIRTUALSCREEN), gm(SM_CYVIRTUALSCREEN)
            if width > 0 and height > 0:
                return left, top, width, height
        except (AttributeError, OSError) as exc:
            log.debug("Virtual-screen query failed, using primary monitor: %s", exc)
    w, h = pyautogui.size()
    return 0, 0, w, h


PALM_LANDMARKS = (0, 5, 9, 13, 17)  # wrist + four MCP knuckles


def _palm_center(hand):
    xs = [hand[i][0] for i in PALM_LANDMARKS]
    ys = [hand[i][1] for i in PALM_LANDMARKS]
    return (sum(xs) / len(xs), sum(ys) / len(ys), 0.0)


class MouseController:
    def __init__(self):
        self._filter_x = OneEuroFilter()
        self._filter_y = OneEuroFilter()
        self._last_moved = None     # (screen_x, screen_y) or None -- last position actually sent
        self._pinched_side = None   # None | "left" | "right" -- physically pinched right now
        self._holding = False       # True once a second tap has engaged and the button is down
        self._await_side = None     # None | "left" | "right" -- side eligible for a second tap
        self._await_deadline = 0.0
        self._last_release_time = 0.0
        self._smooth_dist_left = None
        self._smooth_dist_right = None
        self._hand_lost_since = None  # None while tracked; timestamp once detection first drops

    def update(self, raw_hand, norm_hand):
        """Call once per tick while mouse_mode is on. Either arg may be None
        (no hand this frame). A single missed detection frame is common
        mid-pinch (a closed hand is harder to detect than an open one) and
        is tolerated for HAND_LOST_GRACE before actually releasing/resetting."""
        if raw_hand is None or norm_hand is None:
            now = time.time()
            if self._hand_lost_since is None:
                self._hand_lost_since = now
            if now - self._hand_lost_since < HAND_LOST_GRACE:
                return "tracking flicker"  # brief dropout -- hold/pinch state preserved as-is
            self.release_all()
            return "no hand"
        self._hand_lost_since = None
        status = self._update_pinch(norm_hand)
        # Palm-center tracking already resists most of the drift pinching
        # causes, but freeze position for the rest of the squeeze anyway
        # (tap or not-yet-hold), only tracking again once released --
        # except during an actual hold/drag, where the cursor is supposed
        # to follow your hand.
        frozen = self._pinched_side is not None and not self._holding
        if not frozen:
            self._move_cursor(_palm_center(raw_hand))
        return status

    def _move_cursor(self, palm_point):
        x, y, _z = palm_point
        bx0, bx1 = ACTIVE_BOX_MARGIN_X, 1.0 - ACTIVE_BOX_MARGIN_X
        by0, by1 = ACTIVE_BOX_MARGIN_Y, 1.0 - ACTIVE_BOX_MARGIN_Y
        nx = min(max((x - bx0) / (bx1 - bx0), 0.0), 1.0)
        ny = min(max((y - by0) / (by1 - by0), 0.0), 1.0)

        left, top, screen_w, screen_h = _screen_bounds()
        sx, sy = left + nx * (screen_w - 1), top + ny * (screen_h - 1)

        now = time.time()
        fx = self._filter_x.filter(sx, now)
        fy = self._filter_y.filter(sy, now)
        filtered = (fx, fy)

        if self._last_moved is not None and _dist2(filtered, self._last_moved) < MIN_MOVE_PX:
            return  # hand is basically still -- don't chase sub-pixel noise
        pyautogui.moveTo(*filtered)
        self._last_moved = filtered

    def _update_pinch(self, norm_hand):
        now = time.time()
        thumb, index, middle = norm_hand[4], norm_hand[8], norm_hand[12]
        raw_left, raw_right = _dist2(thumb, index), _dist2(thumb, middle)

        self._smooth_dist_left = raw_left if self._smooth_dist_left is None else (
            DIST_SMOOTHING_ALPHA * raw_left + (1 - DIST_SMOOTHING_ALPHA) * self._smooth_dist_left
        )
        self._smooth_dist_right = raw_right if self._smooth_dist_right is None else (
            DIST_SMOOTHING_ALPHA * raw_right + (1 - DIST_SMOOTHING_ALPHA) * self._smooth_dist_right
        )
        dist_left, dist_right = self._smooth_dist_left, self._smooth_dist_right

        if self._pinched_side is None:
            left_engage = dist_left <= LEFT_PINCH_ENTER
            right_engage = dist_right <= RIGHT_PINCH_ENTER
            side = None
            if left_engage and (not right_engage or dist_left <= dist_right):
                side = "left"
            elif right_engage:
                side = "right"

            if side is None:
                if self._await_side and now > self._await_deadline:
                    self._await_side = None
                return "tracking"

            self._pinched_side = side
            if (
                side == self._await_side
                and now <= self._await_deadline
                and now - self._last_release_time >= MIN_SECOND_TAP_GAP
            ):
                self._await_side = None
                self._holding = True
                pyautogui.mouseDown(button=side)
                return f"{side} hold start"
            self._await_side = None  # a mismatched new pinch cancels any stale pending window
            return f"{side} down (tap?)"

        # currently pinched
        side = self._pinched_side
        dist = dist_left if side == "left" else dist_right
        exit_thresh = LEFT_PINCH_EXIT if side == "left" else RIGHT_PINCH_EXIT
        if dist < exit_thresh:
            return f"{side} holding" if self._holding else f"{side} down (tap?)"

        # pinch just released
        self._pinched_side = None
        if self._holding:
            pyautogui.mouseUp(button=side)
            self._holding = False
            return f"{side} up"

        pyautogui.click(button=side)
        self._await_side = side
        self._await_deadline = now + DOUBLE_TAP_WINDOW
        self._last_release_time = now
        return f"{side} click"

    def release_all(self):
        """Force-release any held button. Safe to call anytime. Call on
        hand-loss, mouse-mode toggle-off, Live-mode-off, and app close."""
        if self._holding and self._pinched_side:
            try:
                pyautogui.mouseUp(button=self._pinched_side)
            except (pyautogui.PyAutoGUIException, OSError) as exc:
                # Best effort: this runs on hand-loss/shutdown, so it must not raise.
                log.warning("mouseUp(%s) failed while releasing: %s", self._pinched_side, exc)
        self._pinched_side = None
        self._holding = False
        self._await_side = None
        self._await_deadline = 0.0
        self._last_release_time = 0.0
        self._smooth_dist_left = None
        self._smooth_dist_right = None
        self._hand_lost_since = None
        self._filter_x.reset()  # re-entering mouse mode snaps instead of easing from a stale pos
        self._filter_y.reset()
        self._last_moved = None
