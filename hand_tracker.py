"""Wraps MediaPipe's HandLandmarker (Tasks API) for per-frame hand detection."""
import os
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    HandLandmarker,
    HandLandmarkerOptions,
    RunningMode,
)

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "hand_landmarker.task")

# Landmark connections for drawing the hand skeleton (MediaPipe's 21-point layout).
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # index
    (5, 9), (9, 10), (10, 11), (11, 12),   # middle
    (9, 13), (13, 14), (14, 15), (15, 16), # ring
    (13, 17), (17, 18), (18, 19), (19, 20),# pinky
    (0, 17),
]


class HandTracker:
    def __init__(self, num_hands=1, min_detection_confidence=0.6):
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=RunningMode.VIDEO,
            num_hands=num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_detection_confidence,
        )
        self._landmarker = HandLandmarker.create_from_options(options)
        self._start_time = time.time()
        self._last_timestamp_ms = -1

    def process(self, frame_bgr):
        """Returns (list_of_hands, handedness_labels).

        Each hand is a list of 21 (x, y, z) tuples, normalized 0..1.
        """
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int((time.time() - self._start_time) * 1000)
        # detect_for_video() requires strictly increasing timestamps; two
        # calls landing in the same millisecond (easy at a 20ms tick rate
        # once you factor in float/int truncation) would otherwise raise.
        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)

        hands = []
        labels = []
        if result.hand_landmarks:
            for hand_landmarks in result.hand_landmarks:
                hands.append([(lm.x, lm.y, lm.z) for lm in hand_landmarks])
        if result.handedness:
            for h in result.handedness:
                labels.append(h[0].category_name if h else "Unknown")
        return hands, labels

    def close(self):
        self._landmarker.close()


# BGR (not RGB) -- matches app.py's SIGNAL_CYAN (#49e0e8) / ALERT_AMBER (#ffb454)
SKELETON_LINE_COLOR = (232, 224, 73)
SKELETON_JOINT_COLOR = (84, 180, 255)


def draw_landmarks(frame_bgr, hands):
    """Draws hand skeleton(s) directly onto the frame (in place)."""
    h, w = frame_bgr.shape[:2]
    for hand in hands:
        pts = [(int(x * w), int(y * h)) for x, y, z in hand]
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame_bgr, pts[a], pts[b], SKELETON_LINE_COLOR, 2)
        for x, y in pts:
            cv2.circle(frame_bgr, (x, y), 4, SKELETON_JOINT_COLOR, -1)
    return frame_bgr


def normalize_landmarks(hand):
    """Make a landmark set translation- and scale-invariant.

    Centers on the wrist (landmark 0) and scales by the distance from the
    wrist to the middle-finger MCP (landmark 9), so the same gesture reads
    the same regardless of hand position/distance from the camera.
    """
    wrist = hand[0]
    ref = hand[9]
    scale = ((ref[0] - wrist[0]) ** 2 + (ref[1] - wrist[1]) ** 2 + (ref[2] - wrist[2]) ** 2) ** 0.5
    scale = scale or 1e-6
    return [
        ((x - wrist[0]) / scale, (y - wrist[1]) / scale, (z - wrist[2]) / scale)
        for x, y, z in hand
    ]
