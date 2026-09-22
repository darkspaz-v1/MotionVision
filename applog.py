"""Rotating file logging for MotionVision.

Logs go to ``logs/motionvision.log`` next to the code (override the folder with the
``MOTIONVISION_LOG_DIR`` environment variable). Warnings and errors are also echoed to stderr.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.environ.get("MOTIONVISION_LOG_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "motionvision.log")

_configured = False


def setup_logging(level=logging.INFO):
    """Attach the rotating file handler to the 'motionvision' logger (idempotent)."""
    global _configured
    root = logging.getLogger("motionvision")
    if _configured:
        return root
    _configured = True
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=3, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError:
        # Read-only install folder: fall back to stderr only rather than refusing to start.
        pass
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)
    root.addHandler(console)
    return root


def get_logger(name):
    return logging.getLogger(f"motionvision.{name}")
