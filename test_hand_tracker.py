"""Regression test for the MediaPipe detect_for_video() timestamp bug:
timestamps must be strictly increasing between calls, but naive
int(time.time() * 1000) truncation can hand back the same millisecond twice
in a row at a fast tick rate -- which raises inside detect_for_video().
"""
from unittest.mock import patch

import numpy as np

import hand_tracker


def _blank_frame():
    return np.zeros((480, 640, 3), dtype=np.uint8)


def test_timestamps_strictly_increase_even_when_wall_clock_does_not():
    tracker = hand_tracker.HandTracker(num_hands=1)
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
    tracker = hand_tracker.HandTracker(num_hands=1)
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
