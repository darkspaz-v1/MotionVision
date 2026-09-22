"""Regenerates the MotionVision pipeline diagram media (needs only Pillow).

    python docs/media/make_media.py

Writes docs/media/pipeline.gif (animated architecture diagram) and
docs/media/social-preview.png (1280x640). Both are DRAWN diagrams, labelled as such in the
image itself; neither is a screenshot or a recording of the app.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
FONTS = Path("C:/Windows/Fonts")
S = 2  # supersample factor for smooth edges

BG = "#12131C"
PANEL = "#1B1D2B"
LINE = "#3A3D57"
TEXT = "#EDEEF7"
MUTED = "#8688A6"
ACCENT = "#3DDC97"
BLUE = "#5B8DEF"


def font(size, bold=False, mono=False):
    name = ("consolab.ttf" if bold else "consola.ttf") if mono else ("segoeuib.ttf" if bold else "segoeui.ttf")
    try:
        return ImageFont.truetype(str(FONTS / name), size * S)
    except OSError:
        return ImageFont.load_default()


# name, sub-lines, x, y, w, h  (design space 1000 x 360)
NODES = {
    "cam": ("Webcam", ["camera.py"], 20, 145, 120, 70),
    "track": ("Hand tracking", ["hand_tracker.py", "MediaPipe: 21 landmarks"], 170, 145, 180, 70),
    "gest": ("Gesture match", ["gestures.py + gestures.json", "wrist-centered, scale-invariant"], 410, 76, 230, 76),
    "act": ("Actions", ["actions.py", "actions.json"], 670, 76, 140, 76),
    "mouse": ("Mouse control", ["mouse_control.py", "One Euro filter, pinch state machine"], 410, 208, 400, 76),
    "out1": ("Keyboard / apps", ["keystroke, media key", "app launch"], 840, 76, 150, 76),
    "out2": ("Cursor", ["move, click, drag"], 840, 208, 150, 76),
}

# polylines the "packet" travels along (design coords)
PATH_A = [(140, 180), (170, 180), (350, 180), (380, 180), (380, 114), (410, 114), (640, 114), (670, 114),
          (810, 114), (840, 114)]
PATH_B = [(140, 180), (170, 180), (350, 180), (380, 180), (380, 246), (410, 246), (810, 246), (840, 246)]
EDGES = [PATH_A, PATH_B]


def point_on(path, t):
    segs = list(zip(path, path[1:]))
    lens = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in segs]
    d = t * sum(lens)
    for (a, b), n in zip(segs, lens):
        if d <= n:
            f = d / n if n else 0
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        d -= n
    return path[-1]


def sc(v):
    flat = [c for item in v for c in (item if isinstance(item, tuple) else (item,))]
    return [int(x * S) for x in flat]


def draw_node(d, key, active):
    title, subs, x, y, w, h = NODES[key]
    box = sc((x, y, x + w, y + h))
    d.rounded_rectangle(box, radius=10 * S, fill=PANEL, outline=ACCENT if active else LINE, width=(3 if active else 2) * S)
    ty = y + 10
    d.text(sc((x + 12, ty)), title, font=font(13, bold=True), fill=TEXT)
    for i, s in enumerate(subs):
        d.text(sc((x + 12, ty + 22 + i * 15)), s, font=font(10, mono=True), fill=MUTED)


def draw_arrowheads(d):
    for path in EDGES:
        x1, y1 = path[-1]
        d.polygon(sc([(x1, y1), (x1 - 8, y1 - 5), (x1 - 8, y1 + 5)]), fill=LINE)
    d.polygon(sc([(170, 180), (162, 175), (162, 185)]), fill=LINE)


def frame(p, size=(1000, 360)):
    """p in [0,1): packet progress along both branches."""
    img = Image.new("RGB", (size[0] * S, size[1] * S), BG)
    d = ImageDraw.Draw(img)
    # header label so this can never pass for a screenshot
    d.text(sc((20, 14)), "ARCHITECTURE DIAGRAM", font=font(12, bold=True, mono=True), fill=ACCENT)
    d.text(sc((190, 14)), "illustration of the code layout, not a screenshot or a recording", font=font(12, mono=True), fill=MUTED)
    for path in EDGES:
        d.line(sc([q for pt in path for q in pt]), fill=LINE, width=2 * S, joint="curve")
    draw_arrowheads(d)
    dots = [point_on(PATH_A, p), point_on(PATH_B, p)]
    active = set()
    for key, (_, _, x, y, w, h) in NODES.items():
        for dx, dy in dots:
            if x - 4 <= dx <= x + w + 4 and y - 4 <= dy <= y + h + 4:
                active.add(key)
    for key in NODES:
        draw_node(d, key, key in active)
    for dx, dy in dots:
        r = 6
        d.ellipse(sc((dx - r, dy - r, dx + r, dy + r)), fill=BLUE, outline=TEXT, width=S)
    d.text(sc((20, 318)), "app.py (Tkinter): live camera preview and the gesture / action editor; it drives the camera and records gestures.",
           font=font(11), fill=MUTED)
    return img.resize(size, Image.LANCZOS)


def build_gif():
    n = 36
    frames = [frame(i / n) for i in range(n)]
    pal = frames[0].quantize(colors=48, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(OUT / "pipeline.gif", save_all=True, append_images=q[1:], duration=80, loop=0, optimize=True, disposal=1)


def build_social():
    W, H = 1280, 640
    img = Image.new("RGB", (W * S, H * S), BG)
    d = ImageDraw.Draw(img)
    d.rectangle(sc((0, 0, W, 6)), fill=ACCENT)
    d.text(sc((80, 34)), "MotionVision", font=font(62, bold=True), fill=TEXT)
    d.text(sc((82, 118)), "Move your hand to move the mouse, pinch to click, record a pose to trigger any action.",
           font=font(24), fill=TEXT)
    d.text(sc((82, 156)), "Webcam hand tracking for Windows, in Python", font=font(18), fill=MUTED)
    dw = 1120
    dh = int(360 * dw / 1000)
    diagram = frame(0.42).resize((dw * S, dh * S), Image.LANCZOS)
    img.paste(diagram, sc((80, 214)))
    img.resize((W, H), Image.LANCZOS).save(OUT / "social-preview.png", optimize=True)


if __name__ == "__main__":
    build_gif()
    build_social()
    print("wrote pipeline.gif and social-preview.png in", OUT)
