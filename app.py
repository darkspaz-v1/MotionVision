"""Gesture Studio: camera preview + hand-gesture editor.

Shows a live webcam feed with hand-landmark overlay, lets you record named
hand-pose gestures (averaged over a short capture window for stability),
and shows live "best match" feedback against your saved gestures so you can
confirm the camera + tracking are working reliably before wiring gestures
up to any actions.
"""
import sys

if sys.platform == "win32":
    # Must run before ANY module in this process imports pyautogui (actions.py
    # does, below) -- otherwise Windows may hand back DPI-virtualized (scaled)
    # coordinates from GetSystemMetrics/SetCursorPos instead of real physical
    # pixels, silently mis-mapping every absolute cursor move whenever display
    # scaling isn't 100%.
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import cv2
from PIL import Image, ImageTk

import actions as astore
import gestures as gstore
import mouse_control as mctrl
from camera import Camera, list_available_cameras
from hand_tracker import HandTracker, draw_landmarks, normalize_landmarks

CAPTURE_SECONDS = 1.2
COUNTDOWN_SECONDS = 2.0
MATCH_THRESHOLD = 0.35

HOLD_SECONDS = 0.45     # how long a gesture must be held before it fires
COOLDOWN_SECONDS = 1.0  # minimum time between fires, even after release

# ---------- Visual identity: hand-tracking HUD / targeting-scope theme ----------
MONO_FAMILY = "Consolas"

BG_VOID = "#090c10"      # window background
BG_PANEL = "#10161d"     # listbox / panel fill
LINE_STEEL = "#223038"   # borders, separators, unselected chrome
TEXT_PRIMARY = "#d7e4ea"
TEXT_DIM = "#5c7280"
SIGNAL_CYAN = "#49e0e8"  # tracking / idle / lock-on accent
ALERT_AMBER = "#ffb454"  # live / armed / recording accent

BRACKET_MARGIN = 6   # px gap between the video image and its viewfinder frame
BRACKET_LEN = 22      # px, corner-bracket arm length


class GestureApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gesture Studio")
        self.root.geometry("1040x680")
        self.root.minsize(860, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.camera = None
        self.tracker = HandTracker(num_hands=1)
        self.gestures = gstore.load_gestures()
        self.actions = astore.load_actions()

        self.mouse_mode = False
        self.mouse_controller = mctrl.MouseController()

        self.record_state = None  # None | "countdown" | "capturing"
        self.record_name = None
        self.record_deadline = 0.0
        self.record_samples = []

        self.live_mode = tk.BooleanVar(value=False)
        self.active_gesture = None
        self.active_since = 0.0
        self.fired_for = None
        self.last_fire_time = 0.0
        self.flash_msg = ""
        self.flash_until = 0.0

        self._build_style()
        self._build_ui()
        self._start_camera()
        self._tick()

    # ---------- Style ----------
    def _build_style(self):
        self.root.configure(background=BG_VOID)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG_VOID, foreground=TEXT_PRIMARY, font=(MONO_FAMILY, 10))
        style.configure("TFrame", background=BG_VOID)
        style.configure("TLabel", background=BG_VOID, foreground=TEXT_PRIMARY, font=(MONO_FAMILY, 10))
        style.configure("Header.TLabel", background=BG_VOID, foreground=SIGNAL_CYAN,
                         font=(MONO_FAMILY, 12, "bold"))
        style.configure("Dim.TLabel", background=BG_VOID, foreground=TEXT_DIM, font=(MONO_FAMILY, 9))
        style.configure("Match.TLabel", background=BG_VOID, foreground=SIGNAL_CYAN,
                         font=(MONO_FAMILY, 11, "bold"))

        style.configure("TButton", background=BG_PANEL, foreground=TEXT_PRIMARY,
                         font=(MONO_FAMILY, 9, "bold"), borderwidth=1, relief="flat",
                         padding=(10, 8), bordercolor=LINE_STEEL,
                         lightcolor=BG_PANEL, darkcolor=BG_PANEL)
        style.map(
            "TButton",
            background=[("pressed", SIGNAL_CYAN), ("active", LINE_STEEL)],
            foreground=[("pressed", BG_VOID)],
            bordercolor=[("active", SIGNAL_CYAN)],
        )

        style.configure("Accent.TButton", background=SIGNAL_CYAN, foreground=BG_VOID,
                         font=(MONO_FAMILY, 9, "bold"), borderwidth=1, relief="flat",
                         padding=(10, 8), bordercolor=SIGNAL_CYAN,
                         lightcolor=SIGNAL_CYAN, darkcolor=SIGNAL_CYAN)
        style.map(
            "Accent.TButton",
            background=[("pressed", TEXT_PRIMARY), ("active", "#7cecf2")],
            foreground=[("pressed", BG_VOID)],
        )

        style.configure("TCheckbutton", background=BG_VOID, foreground=TEXT_PRIMARY, font=(MONO_FAMILY, 9))
        style.map("TCheckbutton", background=[("active", BG_VOID)])
        style.configure("TRadiobutton", background=BG_VOID, foreground=TEXT_PRIMARY, font=(MONO_FAMILY, 9))
        style.map("TRadiobutton", background=[("active", BG_VOID)])

        style.configure("TSeparator", background=LINE_STEEL)

        style.configure("TEntry", fieldbackground=BG_PANEL, foreground=TEXT_PRIMARY,
                         insertcolor=SIGNAL_CYAN, bordercolor=LINE_STEEL,
                         lightcolor=BG_PANEL, darkcolor=BG_PANEL)
        style.configure("TCombobox", fieldbackground=BG_PANEL, foreground=TEXT_PRIMARY,
                         background=BG_PANEL, bordercolor=LINE_STEEL, arrowcolor=SIGNAL_CYAN,
                         lightcolor=BG_PANEL, darkcolor=BG_PANEL)
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_PANEL)],
            foreground=[("readonly", TEXT_PRIMARY)],
        )
        self.root.option_add("*TCombobox*Listbox.background", BG_PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT_PRIMARY)
        self.root.option_add("*TCombobox*Listbox.selectBackground", SIGNAL_CYAN)
        self.root.option_add("*TCombobox*Listbox.selectForeground", BG_VOID)
        self.root.option_add("*TCombobox*Listbox.font", (MONO_FAMILY, 9))

    @staticmethod
    def _spaced(text):
        """Uppercase with letter-spacing (Tk has no tracking option) for HUD-style headers."""
        return " ".join(text.upper())

    # ---------- UI ----------
    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        # Video panel -- framed like a targeting-scope viewfinder
        video_frame = ttk.Frame(outer)
        video_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        self.video_canvas = tk.Canvas(video_frame, background=BG_VOID, highlightthickness=0, bd=0)
        self.video_canvas.pack(fill="both", expand=True)
        self.video_label = tk.Label(self.video_canvas, background="black", bd=0)
        self.video_label.place(
            x=BRACKET_MARGIN, y=BRACKET_MARGIN, relwidth=1, relheight=1,
            width=-2 * BRACKET_MARGIN, height=-2 * BRACKET_MARGIN,
        )
        self.video_canvas.bind("<Configure>", self._draw_corner_brackets)

        self.status_var = tk.StringVar(value="Starting camera...")
        ttk.Label(video_frame, textvariable=self.status_var, style="Dim.TLabel").pack(
            anchor="w", pady=(8, 0)
        )
        self.match_var = tk.StringVar(value="")
        ttk.Label(video_frame, textvariable=self.match_var, style="Match.TLabel").pack(anchor="w")

        live_row = ttk.Frame(video_frame)
        live_row.pack(anchor="w", pady=(8, 0), fill="x")
        self.live_check = ttk.Checkbutton(
            live_row,
            text="LIVE MODE (gestures will trigger their commands)",
            variable=self.live_mode,
            command=self._on_live_toggle,
        )
        self.live_check.pack(side="left")
        self.live_status_var = tk.StringVar(value="Preview only — nothing will run")
        self.live_status_label = ttk.Label(
            live_row, textvariable=self.live_status_var, foreground=TEXT_DIM,
            font=(MONO_FAMILY, 9, "bold"),
        )
        self.live_status_label.pack(side="left", padx=(10, 0))

        # Side panel
        side = ttk.Frame(outer)
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)

        ttk.Label(side, text=self._spaced("Saved gestures"), style="Header.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.listbox = tk.Listbox(
            side, height=14, exportselection=False,
            background=BG_PANEL, foreground=TEXT_PRIMARY,
            selectbackground=SIGNAL_CYAN, selectforeground=BG_VOID,
            activestyle="none", bd=0, highlightthickness=1,
            highlightbackground=LINE_STEEL, highlightcolor=SIGNAL_CYAN,
            font=(MONO_FAMILY, 10),
        )
        self.listbox.grid(row=1, column=0, sticky="nsew", pady=(6, 10))
        side.rowconfigure(1, weight=1)
        self._refresh_list()

        btns = ttk.Frame(side)
        btns.grid(row=2, column=0, sticky="ew")
        btns.columnconfigure((0, 1), weight=1)

        self.record_btn = ttk.Button(
            btns, text="Record new gesture", command=self.start_record_flow,
            style="Accent.TButton",
        )
        self.record_btn.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        ttk.Button(btns, text="Rename", command=self.rename_selected).grid(
            row=1, column=0, sticky="ew", padx=(0, 3)
        )
        ttk.Button(btns, text="Delete", command=self.delete_selected).grid(
            row=1, column=1, sticky="ew", padx=(3, 0)
        )
        ttk.Button(btns, text="Assign action...", command=self.assign_action).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )

        ttk.Separator(side).grid(row=3, column=0, sticky="ew", pady=12)

        ttk.Label(side, text=self._spaced("Camera"), style="Header.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        ttk.Button(side, text="Switch camera", command=self.switch_camera).grid(
            row=5, column=0, sticky="ew", pady=(6, 0)
        )

    def _draw_corner_brackets(self, event=None):
        """Redraws the viewfinder corner brackets around the video panel to
        match its current size -- called on every resize."""
        c = self.video_canvas
        c.delete("bracket")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 40 or h < 40:
            return
        m, arm = BRACKET_MARGIN, BRACKET_LEN
        for x, y, dx, dy in (
            (m, m, 1, 1),
            (w - m, m, -1, 1),
            (m, h - m, 1, -1),
            (w - m, h - m, -1, -1),
        ):
            c.create_line(x, y, x + dx * arm, y, fill=SIGNAL_CYAN, width=2, tags="bracket")
            c.create_line(x, y, x, y + dy * arm, fill=SIGNAL_CYAN, width=2, tags="bracket")

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for name in sorted(self.gestures.keys()):
            action = self.actions.get(name)
            summary = astore.describe(action)
            self.listbox.insert(tk.END, f"{name}  →  {summary}")

    def _on_live_toggle(self):
        if self.live_mode.get():
            self.live_status_label.configure(foreground=ALERT_AMBER)
            self.live_status_var.set("LIVE — matched gestures will run their commands")
        else:
            self.live_status_label.configure(foreground=TEXT_DIM)
            self.live_status_var.set("Preview only — nothing will run")
            if self.mouse_mode:
                self.mouse_mode = False
                self.mouse_controller.release_all()
        # reset trigger state so switching modes never fires stale gestures
        self.active_gesture = None
        self.fired_for = None

    # ---------- Action assignment ----------
    def assign_action(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Assign action", "Select a saved gesture from the list first.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.configure(background=BG_VOID)
        dialog.title(f'Assign action: "{name}"')
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        pad = {"padx": 10, "pady": 6}
        action_type = tk.StringVar(value=(self.actions.get(name) or {}).get("type", "keystroke"))

        ttk.Label(dialog, text="Action type:").grid(row=0, column=0, sticky="w", **pad)
        type_frame = ttk.Frame(dialog)
        type_frame.grid(row=0, column=1, sticky="w", **pad)
        ttk.Radiobutton(type_frame, text="Keystroke", variable=action_type, value="keystroke").pack(side="left")
        ttk.Radiobutton(type_frame, text="Media key", variable=action_type, value="media").pack(side="left")
        ttk.Radiobutton(type_frame, text="Launch app", variable=action_type, value="launch").pack(side="left")
        ttk.Radiobutton(type_frame, text="Mouse control toggle", variable=action_type, value="mouse_toggle").pack(side="left")

        # Keystroke field
        ttk.Label(dialog, text="Keys (e.g. ctrl+shift+m):").grid(row=1, column=0, sticky="w", **pad)
        key_entry = ttk.Entry(dialog, width=32)
        key_entry.grid(row=1, column=1, sticky="ew", **pad)

        # Media key field
        ttk.Label(dialog, text="Media key:").grid(row=2, column=0, sticky="w", **pad)
        media_combo = ttk.Combobox(
            dialog, values=list(astore.MEDIA_KEYS.keys()), state="readonly", width=29
        )
        media_combo.grid(row=2, column=1, sticky="ew", **pad)

        # Launch field
        ttk.Label(dialog, text="App / file / URL:").grid(row=3, column=0, sticky="w", **pad)
        launch_entry = ttk.Entry(dialog, width=32)
        launch_entry.grid(row=3, column=1, sticky="ew", **pad)

        existing = self.actions.get(name)
        if existing:
            t = existing.get("type")
            v = existing.get("value", "")
            if t == "keystroke":
                key_entry.insert(0, v)
            elif t == "media":
                label = next((k for k, val in astore.MEDIA_KEYS.items() if val == v), "")
                media_combo.set(label)
            elif t == "launch":
                launch_entry.insert(0, v)

        def save():
            t = action_type.get()
            if t == "keystroke":
                value = key_entry.get().strip()
                if not value:
                    messagebox.showwarning("Missing value", "Enter one or more keys, e.g. ctrl+shift+m")
                    return
            elif t == "media":
                label = media_combo.get()
                if label not in astore.MEDIA_KEYS:
                    messagebox.showwarning("Missing value", "Choose a media key.")
                    return
                value = astore.MEDIA_KEYS[label]
            elif t == "launch":
                value = launch_entry.get().strip()
                if not value:
                    messagebox.showwarning("Missing value", "Enter an app name, file path, or URL.")
                    return
            else:  # mouse_toggle
                other = next(
                    (g for g, a in self.actions.items() if g != name and a.get("type") == "mouse_toggle"),
                    None,
                )
                if other and not messagebox.askyesno(
                    "Replace existing toggle?",
                    f'"{other}" is currently the mouse-control toggle gesture. '
                    f'Reassigning it to "{name}" will make "{other}" a plain gesture with no action. Continue?',
                ):
                    return
                astore.set_action(name, {"type": t})
                self.actions = astore.load_actions()
                self._refresh_list()
                dialog.destroy()
                return

            astore.set_action(name, {"type": t, "value": value})
            self.actions = astore.load_actions()
            self._refresh_list()
            dialog.destroy()

        def clear():
            astore.remove_action(name)
            self.actions = astore.load_actions()
            self._refresh_list()
            dialog.destroy()

        btn_row = ttk.Frame(dialog)
        btn_row.grid(row=4, column=0, columnspan=2, pady=(10, 10))
        ttk.Button(btn_row, text="Save", command=save).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Remove action", command=clear).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Cancel", command=dialog.destroy).pack(side="left", padx=5)

    # ---------- Camera lifecycle ----------
    def _start_camera(self, index=0):
        if self.camera is not None:
            self.camera.stop()
        self.camera = Camera(index=index)
        self.camera.start()

    def switch_camera(self):
        current = self.camera.index if self.camera else 0
        found = list_available_cameras()
        if not found:
            messagebox.showwarning("No cameras found", "No other cameras were detected.")
            return
        options = [i for i in found if i != current] or found
        next_index = options[0]
        self._start_camera(index=next_index)

    # ---------- Recording flow ----------
    def start_record_flow(self):
        if self.record_state is not None:
            return
        name = simpledialog.askstring("Gesture name", "Name this gesture:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in self.gestures:
            if not messagebox.askyesno(
                "Overwrite?", f'"{name}" already exists. Overwrite it?'
            ):
                return
        self.record_name = name
        self.record_state = "countdown"
        self.record_deadline = time.time() + COUNTDOWN_SECONDS
        self.record_samples = []
        self.record_btn.state(["disabled"])

    def _abort_record(self, reason):
        self.record_state = None
        self.record_btn.state(["!disabled"])
        messagebox.showwarning("Recording cancelled", reason)

    def _finish_record(self):
        if not self.record_samples:
            self._abort_record("No hand was detected during capture. Try again with your hand clearly in frame.")
            return
        averaged = gstore.average_landmarks(self.record_samples)
        gstore.add_gesture(self.record_name, averaged)
        self.gestures = gstore.load_gestures()
        self._refresh_list()
        self.record_state = None
        self.record_btn.state(["!disabled"])
        self.status_var.set(f'Saved gesture "{self.record_name}" ({len(self.record_samples)} samples).')

    # ---------- List actions ----------
    def _selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        # listbox rows are rendered as "name  →  action summary"
        return self.listbox.get(sel[0]).split("  →  ")[0]

    def rename_selected(self):
        name = self._selected_name()
        if not name:
            return
        new_name = simpledialog.askstring("Rename gesture", "New name:", initialvalue=name, parent=self.root)
        if not new_name or new_name.strip() == "" or new_name == name:
            return
        new_name = new_name.strip()
        self.gestures[new_name] = self.gestures.pop(name)
        gstore.save_gestures(self.gestures)
        existing_action = self.actions.pop(name, None)
        if existing_action:
            astore.set_action(new_name, existing_action)
            astore.remove_action(name)
            self.actions = astore.load_actions()
        self._refresh_list()

    def delete_selected(self):
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno("Delete gesture", f'Delete "{name}"?'):
            gstore.delete_gesture(name)
            astore.remove_action(name)
            self.gestures = gstore.load_gestures()
            self.actions = astore.load_actions()
            self._refresh_list()

    # ---------- Main loop ----------
    def _tick(self):
        frame = self.camera.get_frame() if self.camera else None
        status = self.camera.status() if self.camera else {"connected": False, "error": "no camera"}

        if not status["connected"] or frame is None:
            self.status_var.set(f"Camera: reconnecting... ({status['error'] or 'waiting'})")
            self.match_var.set("")
            if self.mouse_mode:
                self.mouse_controller.release_all()
            self.root.after(200, self._tick)
            return

        self.status_var.set(f"Camera: connected (index {self.camera.index})")

        try:
            frame = cv2.flip(frame, 1)
            hands, _labels = self.tracker.process(frame)

            raw_hand = None
            current_norm = None
            if hands:
                draw_landmarks(frame, hands)
                raw_hand = hands[0]
                current_norm = normalize_landmarks(raw_hand)

            self._handle_recording_state(frame, current_norm)
            if self.mouse_mode:
                self._update_mouse_mode(raw_hand, current_norm)
            else:
                self._update_match_label(current_norm)
            self._render(frame)
        except Exception:
            # Never let a single bad frame silently kill the tick loop --
            # that would freeze the camera feed, tracking, AND the cursor
            # (if a drag was in progress, the button could stay stuck down).
            traceback.print_exc()
            if self.mouse_mode:
                self.mouse_controller.release_all()
        finally:
            self.root.after(20, self._tick)

    def _handle_recording_state(self, frame, current_norm):
        if self.record_state == "countdown":
            remaining = self.record_deadline - time.time()
            if remaining <= 0:
                self.record_state = "capturing"
                self.record_deadline = time.time() + CAPTURE_SECONDS
            else:
                self._overlay_text(frame, f"Get ready: {remaining:0.1f}s")
        elif self.record_state == "capturing":
            remaining = self.record_deadline - time.time()
            if current_norm is not None:
                self.record_samples.append(current_norm)
            self._overlay_text(frame, f"Hold still... {max(remaining, 0):0.1f}s")
            if remaining <= 0:
                self._finish_record()

    def _update_match_label(self, current_norm):
        if self.record_state is not None:
            return
        if current_norm is None:
            self.match_var.set("No hand detected")
            self._reset_trigger_state()
            return
        if not self.gestures:
            self.match_var.set("Hand detected (no saved gestures yet)")
            self._reset_trigger_state()
            return

        name, dist = gstore.best_match(current_norm, self.gestures, threshold=MATCH_THRESHOLD)
        status = self._advance_trigger_state(name)

        now = time.time()
        if now < self.flash_until:
            self.match_var.set(self.flash_msg)
        elif name:
            self.match_var.set(f"Match: {name}  (distance {dist:0.3f})  [{status}]")
        else:
            shown = f"{dist:0.3f}" if dist is not None else "n/a"
            self.match_var.set(f"No match (closest distance {shown})")

    def _update_mouse_mode(self, raw_hand, current_norm):
        if self.record_state is not None:
            return

        status = self.mouse_controller.update(raw_hand, current_norm)

        toggle_name = self._toggle_gesture_name()
        matched = None
        if current_norm is not None and toggle_name and toggle_name in self.gestures:
            dist = gstore.distance(current_norm, self.gestures[toggle_name])
            if dist <= MATCH_THRESHOLD:
                matched = toggle_name
        trigger_status = self._advance_trigger_state(matched)

        now = time.time()
        if now < self.flash_until:
            self.match_var.set(self.flash_msg)
        else:
            self.match_var.set(
                f"Mouse mode ON [{status}] — hold toggle gesture to exit [{trigger_status}]"
            )

    def _toggle_mouse_mode(self):
        self.mouse_mode = not self.mouse_mode
        if not self.mouse_mode:
            self.mouse_controller.release_all()

    def _toggle_gesture_name(self):
        for gname, action in self.actions.items():
            if action.get("type") == "mouse_toggle":
                return gname
        return None

    def _reset_trigger_state(self):
        self.active_gesture = None
        self.fired_for = None

    def _advance_trigger_state(self, matched_name):
        """Hold-to-confirm + cooldown state machine. Returns a short status string."""
        now = time.time()

        if matched_name != self.active_gesture:
            self.active_gesture = matched_name
            self.active_since = now
            self.fired_for = None

        if matched_name is None:
            return "idle"

        held_for = now - self.active_since

        if self.fired_for == matched_name:
            return "held"

        if held_for < HOLD_SECONDS:
            return f"hold {held_for:0.1f}/{HOLD_SECONDS:0.1f}s"

        if now - self.last_fire_time < COOLDOWN_SECONDS:
            return "cooldown"

        # Ready to fire
        self.fired_for = matched_name
        self.last_fire_time = now
        self._fire(matched_name)
        return "fired"

    def _fire(self, gesture_name):
        action = self.actions.get(gesture_name)
        if not action:
            self.flash_msg = f'"{gesture_name}" has no action assigned'
            self.flash_until = time.time() + 1.5
            return
        if not self.live_mode.get():
            self.flash_msg = f'Would run: {gesture_name} → {astore.describe(action)} (Live mode is off)'
            self.flash_until = time.time() + 1.5
            return
        try:
            if action.get("type") == "mouse_toggle":
                self._toggle_mouse_mode()
                self.flash_msg = f'Mouse control: {"ON" if self.mouse_mode else "OFF"}'
            else:
                astore.run_action(action)
                self.flash_msg = f'Ran: {gesture_name} → {astore.describe(action)}'
        except Exception as e:
            self.flash_msg = f'Action failed for "{gesture_name}": {e}'
        self.flash_until = time.time() + 1.5

    @staticmethod
    def _overlay_text(frame, text):
        cv2.putText(frame, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (84, 180, 255), 2, cv2.LINE_AA)

    def _render(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)

        target_w = self.video_label.winfo_width() or img.width
        target_h = self.video_label.winfo_height() or img.height
        if target_w > 10 and target_h > 10:
            img.thumbnail((target_w, target_h), Image.LANCZOS)

        photo = ImageTk.PhotoImage(image=img)
        self.video_label.configure(image=photo)
        self.video_label.image = photo  # keep a reference

    def on_close(self):
        try:
            self.mouse_controller.release_all()
            if self.camera:
                self.camera.stop()
            self.tracker.close()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    GestureApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
