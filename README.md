# MotionVision

[![CI](https://github.com/darkspaz-v1/MotionVision/actions/workflows/ci.yml/badge.svg)](https://github.com/darkspaz-v1/MotionVision/actions/workflows/ci.yml)

Real-time webcam hand tracking that drives the mouse and fires user-recorded gestures.

Point to move the cursor, pinch to click, hold to drag — or record a hand pose and bind it to a
keystroke, a media key, or launching an app.

## How it works

- **MediaPipe HandLandmarker** returns 21 hand landmarks per frame from the webcam.
- `mouse_control.py` maps landmark positions to screen coordinates and runs the click/drag state
  machine.
- `gestures.py` records named poses into `gestures.json`; `actions.py` binds each to an action in
  `actions.json` — keystroke, media key, app launch, or toggling mouse mode.
- `app.py` is a Tkinter UI with a live camera preview and the gesture/action editor.

## Pipeline

```mermaid
flowchart LR
    CAM[Webcam<br/>camera.py] --> HT[Hand tracking<br/>hand_tracker.py<br/>MediaPipe: 21 landmarks]
    HT --> NORM[normalize_landmarks<br/>wrist-centered, scale-invariant]
    NORM --> G[Gesture match<br/>gestures.py + gestures.json]
    HT --> MC[Mouse control<br/>mouse_control.py<br/>One Euro filter, pinch state machine]
    G --> A[Actions<br/>actions.py + actions.json<br/>keystroke, media key, launch app]
    MC --> CUR[Cursor move, click, drag]
    A --> OS[Keyboard / app launch]
    UI[app.py Tkinter UI<br/>preview and gesture editor] -.drives.-> CAM
    UI -.records.-> G
```

## Demo

Demo GIF: coming (a hand-to-cursor capture needs a webcam session, which is not part of this repo yet).

## Install

Requires Windows 10/11 and Python 3.11 or newer, plus a webcam.

```
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Model file

Hand tracking needs MediaPipe's `hand_landmarker.task` model (about 7.8 MB). It is not committed to the repo. Download it into `models/`:

```
mkdir models
curl -L -o models\hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

Without it the app cannot start tracking, and the two tests that use the real HandLandmarker are skipped.

## Problems that were actually hard

- **Timestamps must strictly increase.** MediaPipe's `detect_for_video()` raises if two frames carry
  the same millisecond, and naive `int(time.time() * 1000)` truncation hands back a duplicate at a
  fast tick rate. There is a regression test for exactly this.
- **Jitter.** Raw landmark positions shake far too much to point with, so cursor position is smoothed
  before it is used.
- **DPI and multiple monitors.** Screen coordinates are not what you first assume on a scaled display.
  Multi-monitor support is behind an explicit `MULTI_MONITOR` flag rather than on by default, because
  the naive version is worse than single-monitor for most setups.

## Running it

```
run.bat
```

## Tests

```
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m pytest
```

**29 tests** across `test_mouse_control.py` (20), `test_gestures.py` (7) and `test_hand_tracker.py`
(2, exercising the real HandLandmarker rather than a mock). The two HandLandmarker tests skip
themselves, with the reason shown by `pytest -rs`, when the model file is missing or MediaPipe cannot be
loaded. Set `MOTIONVISION_SKIP_TRACKER_TESTS=1` to skip them explicitly (CI does this).

### Known environment issue

On a machine with Windows Application Control (WDAC / Smart App Control) active, loading MediaPipe's
native library fails:

```
OSError: [WinError 4551] An Application Control policy has blocked this file
```

That makes `test_hand_tracker.py` skip itself (27 pass, 2 skipped) **and breaks hand tracking at runtime**, since it is the
same blocked library. If tracking does nothing, check App & browser control before suspecting the
camera or the model file.

## Troubleshooting

**Log file location:** `logs/motionvision.log` inside the project folder (rotating, 3 backups of 500 KB;
`logs/` is gitignored). Set the `MOTIONVISION_LOG_DIR` environment variable to put it elsewhere.
Failed actions, per-frame errors and cursor-release failures are recorded there; warnings and errors are
also printed to the console when you start the app with `run.bat`.

## License

MIT — see [LICENSE](LICENSE).
