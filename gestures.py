"""Load/save/match named hand-gesture templates."""
import json
import os

STORE_PATH = os.path.join(os.path.dirname(__file__), "gestures.json")


def load_gestures():
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_gestures(gestures):
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(gestures, f, indent=2)


def delete_gesture(name):
    gestures = load_gestures()
    gestures.pop(name, None)
    save_gestures(gestures)


def add_gesture(name, normalized_landmarks):
    gestures = load_gestures()
    gestures[name] = normalized_landmarks
    save_gestures(gestures)


def average_landmarks(samples):
    """samples: list of normalized-landmark lists (each 21 x,y,z tuples)."""
    n = len(samples)
    count = len(samples[0])
    avg = []
    for i in range(count):
        sx = sum(s[i][0] for s in samples) / n
        sy = sum(s[i][1] for s in samples) / n
        sz = sum(s[i][2] for s in samples) / n
        avg.append((sx, sy, sz))
    return avg


def distance(a, b):
    """Mean per-point Euclidean distance between two normalized landmark sets."""
    total = 0.0
    for (ax, ay, az), (bx, by, bz) in zip(a, b):
        total += ((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2) ** 0.5
    return total / len(a)


def best_match(current, gestures, threshold=0.35):
    """Returns (name, distance) of the closest saved gesture within threshold, else (None, None)."""
    best_name, best_dist = None, None
    for name, saved in gestures.items():
        d = distance(current, saved)
        if best_dist is None or d < best_dist:
            best_name, best_dist = name, d
    if best_dist is not None and best_dist <= threshold:
        return best_name, best_dist
    return None, best_dist
