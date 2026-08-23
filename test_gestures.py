"""Regression tests for gestures.py's landmark averaging and matching."""
import pytest

import gestures as gstore


def _flat_landmarks(x, y, z=0.0, n=21):
    return [(x, y, z)] * n


class TestAverageLandmarks:
    def test_averages_each_point_independently(self):
        samples = [_flat_landmarks(0.0, 0.0), _flat_landmarks(2.0, 4.0)]
        avg = gstore.average_landmarks(samples)
        assert avg[0] == pytest.approx((1.0, 2.0, 0.0))
        assert len(avg) == 21

    def test_single_sample_returns_itself(self):
        samples = [_flat_landmarks(1.0, 2.0, 3.0)]
        avg = gstore.average_landmarks(samples)
        assert avg[0] == pytest.approx((1.0, 2.0, 3.0))


class TestDistance:
    def test_identical_sets_have_zero_distance(self):
        a = _flat_landmarks(0.5, 0.5)
        assert gstore.distance(a, a) == pytest.approx(0.0)

    def test_distance_is_symmetric(self):
        a = _flat_landmarks(0.0, 0.0)
        b = _flat_landmarks(1.0, 1.0)
        assert gstore.distance(a, b) == pytest.approx(gstore.distance(b, a))


class TestBestMatch:
    def test_returns_closest_gesture_within_threshold(self):
        current = _flat_landmarks(0.0, 0.0)
        gestures = {
            "near": _flat_landmarks(0.01, 0.01),
            "far": _flat_landmarks(5.0, 5.0),
        }
        name, dist = gstore.best_match(current, gestures, threshold=0.35)
        assert name == "near"

    def test_returns_none_when_nothing_within_threshold(self):
        current = _flat_landmarks(0.0, 0.0)
        gestures = {"far": _flat_landmarks(5.0, 5.0)}
        name, dist = gstore.best_match(current, gestures, threshold=0.35)
        assert name is None
        assert dist > 0.35

    def test_empty_gesture_set_returns_none_none(self):
        current = _flat_landmarks(0.0, 0.0)
        name, dist = gstore.best_match(current, {}, threshold=0.35)
        assert name is None
        assert dist is None
