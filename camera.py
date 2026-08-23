"""Threaded, fault-tolerant webcam capture.

Runs the blocking cv2.VideoCapture read loop on its own thread so the GUI
never freezes, and keeps retrying / re-opening the device if a frame read
fails or the camera is temporarily unavailable (unplugged, busy, etc.).
"""
import threading
import time

import cv2


def list_available_cameras(max_index=5):
    """Probe camera indices 0..max_index-1 and return the ones that open."""
    found = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                found.append(index)
        cap.release()
    return found


class Camera:
    """Background-threaded camera reader with auto-reconnect."""

    def __init__(self, index=0, width=1280, height=720):
        self.index = index
        self.width = width
        self.height = height

        self._cap = None
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._last_error = None
        self.connected = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._release()

    def get_frame(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def status(self):
        return {"connected": self.connected, "error": self._last_error}

    def _open(self):
        self._release()
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _release(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _loop(self):
        retry_delay = 0.5
        while self._running:
            if self._cap is None:
                self._cap = self._open()
                if self._cap is None:
                    self.connected = False
                    self._last_error = f"Could not open camera index {self.index}"
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, 5.0)
                    continue
                retry_delay = 0.5

            ok, frame = self._cap.read()
            if not ok or frame is None:
                self.connected = False
                self._last_error = "Frame read failed, reconnecting"
                self._release()
                time.sleep(retry_delay)
                continue

            self.connected = True
            self._last_error = None
            with self._lock:
                self._frame = frame

        self._release()
