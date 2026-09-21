"""Regression test for the MediaPipe detect_for_video() timestamp bug:
timestamps must be strictly increasing between calls, but naive
int(time.time() * 1000) truncation can hand back the same millisecond twice
in a row at a fast tick rate -- which raises inside detect_for_video().
"""
import os
from unittest.mock import patch

import numpy as np
import pytest

if os.environ.get("MOTIONVISION_SKIP_TRACKER_TESTS"):
    # Set on CI: there is no webcam, no model file, and MediaPipe's native library is heavy.
    pytest.skip("MOTIONVISION_SKIP_TRACKER_TESTS is set", allow_module_level=True)

try:
    import hand_tracker
except (ImportError, OSError) as exc:
    # On machines with Windows Application Control (WDAC / Smart App Control) the import of
    # MediaPipe's native library can fail with OSError [WinError 4551].
    pytest.skip(f"MediaPipe could not be imported ({exc})", allow_module_level=True)


def _make_tracker():
    """Create a real HandTracker, or skip when this machine cannot load MediaPipe/the model."""
    try:
        return hand_tracker.HandTracker(num_hands=1)
    except FileNotFoundError as exc:
        # models/hand_landmarker.task is not committed; see "Model file" in the README.
        pytest.skip(f"hand_landmarker.task model is missing ({exc})")
    except OSError as exc:
        # WinError 4551: an Application Control policy blocked MediaPipe's DLL. Runtime hand
        # tracking is broken on such a machine too, so the tests cannot say anything useful.
        pytest.skip(f"HandLandmarker could not be loaded on this machine ({exc})")
    except RuntimeError as exc:
        pytest.skip(f"HandLandmarker could not be created ({exc})")


def _blank_frame():
    return np.zeros((480, 640, 3), dtype=np.uint8)


def test_timestamps_strictly_increase_even_when_wall_clock_does_not():
    tracker = _make_tracker()
    try:
        seen_timestamps = []
        real_detect = tracker._landmarker.detect_for_video

        def spy_detect(mp_image, timestamp_ms):
            seen_timestamps.append(timestamp_ms)
            return real_detect(mp_image, timestamp_ms)

        with patch.object(tracker._landmarker, "detect_for_video", side_effect=spy_detect), \
             patch.object(hand_tracker.time, "time", return_value=100.0):
            # wall clock frozen at the exact same instant across three calls
            for _ in range(3):
                tracker.process(_blank_frame())

        assert len(seen_timestamps) == 3
        assert seen_timestamps == sorted(set(seen_timestamps)), (
            "timestamps must be strictly increasing even when time.time() "
            "does not advance between frames"
        )
        assert len(set(seen_timestamps)) == 3, "no two frames may share a timestamp"
    finally:
        tracker.close()


def test_timestamps_survive_a_backwards_clock_jump():
    """A system clock adjustment (NTP sync, sleep/resume) must not be able
    to produce a timestamp <= the previous one."""
    tracker = _make_tracker()
    try:
        seen_timestamps = []
        real_detect = tracker._landmarker.detect_for_video

        def spy_detect(mp_image, timestamp_ms):
            seen_timestamps.append(timestamp_ms)
            return real_detect(mp_image, timestamp_ms)

        with patch.object(tracker._landmarker, "detect_for_video", side_effect=spy_detect):
            with patch.object(hand_tracker.time, "time", return_value=200.0):
                tracker.process(_blank_frame())
            with patch.object(hand_tracker.time, "time", return_value=50.0):  # clock jumped back
                tracker.process(_blank_frame())

        assert seen_timestamps[1] > seen_timestamps[0]
    finally:
        tracker.close()
