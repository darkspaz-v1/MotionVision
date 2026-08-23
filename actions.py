"""Load/save gesture -> action mappings, and execute an action."""
import json
import os
import subprocess

import pyautogui

STORE_PATH = os.path.join(os.path.dirname(__file__), "actions.json")

MEDIA_KEYS = {
    "Volume Up": "volumeup",
    "Volume Down": "volumedown",
    "Mute": "volumemute",
    "Play / Pause": "playpause",
    "Next Track": "nexttrack",
    "Previous Track": "prevtrack",
}


def load_actions():
    if not os.path.exists(STORE_PATH):
        return {}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_actions(actions):
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(actions, f, indent=2)


def set_action(gesture_name, action):
    actions = load_actions()
    actions[gesture_name] = action
    save_actions(actions)


def remove_action(gesture_name):
    actions = load_actions()
    actions.pop(gesture_name, None)
    save_actions(actions)


def describe(action):
    if not action:
        return "(none)"
    t = action.get("type")
    if t == "keystroke":
        return f"Key: {action.get('value')}"
    if t == "media":
        return f"Media: {action.get('value')}"
    if t == "launch":
        return f"Launch: {action.get('value')}"
    if t == "mouse_toggle":
        return "Toggle mouse control"
    return "(unknown)"


def run_action(action):
    """Executes an action. Raises on failure so the caller can report it.

    Note: "mouse_toggle" is intentionally not handled here. It's pure UI
    state (GestureApp.mouse_mode), not an external command -- GestureApp._fire
    intercepts it before this is ever called. If it reaches here that's a
    bug, and the `else` branch below will surface it.
    """
    t = action.get("type")
    value = action.get("value", "")

    if t == "keystroke":
        keys = [k.strip() for k in value.split("+") if k.strip()]
        if not keys:
            return
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)

    elif t == "media":
        pyautogui.press(value)

    elif t == "launch":
        subprocess.Popen(value, shell=True)

    else:
        raise ValueError(f"Unknown action type: {t}")
