# MotionVision

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
venv\Scripts\python.exe -m pytest
```

**29 tests** across `test_mouse_control.py` (20), `test_gestures.py` (7) and `test_hand_tracker.py`
(2, exercising the real HandLandmarker rather than a mock).

### Known environment issue

On a machine with Windows Application Control (WDAC / Smart App Control) active, loading MediaPipe's
native library fails:

```
OSError: [WinError 4551] An Application Control policy has blocked this file
```

That takes out `test_hand_tracker.py` (28/29 pass) **and hand tracking at runtime**, since it is the
same blocked library. If tracking does nothing, check App & browser control before suspecting the
camera or the model file.

## License

MIT — see [LICENSE](LICENSE).
