"""Jarvis' on-screen cursor: a glowing pointer that glides to things and highlights them.

GNOME on Wayland doesn't let apps draw over other windows, but XWayland windows can: this uses transparent,
click-through, override-redirect X11 windows (the user's own mouse and clicks pass straight through).
It never moves or clicks the real mouse."""
import logging
import math
import queue
import subprocess
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("jarvis.pointer")
FPS = 50
CYAN = (34, 211, 238)
INK = (8, 12, 28)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for q in ("Cantarell:bold", "Inter:bold", "Noto Sans:bold", "DejaVu Sans:bold"):
        try:
            path = subprocess.run(["fc-match", "-f", "%{file}", q], capture_output=True, text=True, timeout=3).stdout
            if path:
                return ImageFont.truetype(path, size)
        except (OSError, subprocess.SubprocessError):
            pass
    return ImageFont.load_default(size)


def _bgra(img: Image.Image, alpha: float = 1.0) -> bytes:
    """RGBA PIL image -> premultiplied BGRA bytes (what a 32-bit X11 ARGB window expects)."""
    a = np.asarray(img, dtype=np.float32)
    a[..., 3] *= alpha
    a[..., :3] *= a[..., 3:4] / 255.0
    return a[..., [2, 1, 0, 3]].astype(np.uint8).tobytes()


class Overlay:
    """One transparent click-through ARGB window."""

    def __init__(self, display, screen, visual, cmap, shape):
        self.d, self.screen, self.visual, self.cmap = display, screen, visual, cmap
        self.win = None
        self.gc = None
        self.geom = (0, 0, 0, 0)
        self.shape = shape

    def show(self, x: int, y: int, w: int, h: int, img: Image.Image, alpha: float = 1.0):
        from Xlib import X
        w, h = max(1, w), max(1, h)
        if self.win is None:
            self.win = self.screen.root.create_window(
                x, y, w, h, 0, 32, X.InputOutput, self.visual.visual_id, background_pixel=0, border_pixel=0,
                colormap=self.cmap, override_redirect=1)
            if self.shape:      # empty input region: every click and hover passes through to what's below
                from Xlib.ext import shape
                self.win.shape_rectangles(shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
            self.gc = self.win.create_gc()
            self.win.map()
            self.geom = (x, y, w, h)
        elif self.geom != (x, y, w, h):
            self.win.configure(x=x, y=y, width=w, height=h)
            self.geom = (x, y, w, h)
        data = _bgra(img, alpha)
        stride = w * 4
        rows_per = max(1, 200_000 // stride)                 # stay under the X request size limit
        for r in range(0, h, rows_per):
            n = min(rows_per, h - r)
            self.win.put_image(self.gc, 0, r, w, n, X.ZPixmap, 32, 0, data[r * stride:(r + n) * stride])
        self.d.flush()

    def hide(self):
        if self.win is not None:
            self.win.unmap()
            self.d.flush()
            self.geom = (0, 0, 0, 0)
            self.win.destroy()
            self.win = self.gc = None

    def raise_(self):
        if self.win is not None:
            self.win.configure(stack_mode=0)       # X.Above
            self.d.flush()


class Pointer:
    """Thread-owned overlay: point(x, y, label, box) from any thread."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="pointer", daemon=True)
        self.ok = False
        self.error = ""
        self.screen_w, self.screen_h = 2560, 1440
        self.ready = threading.Event()
        self.thread.start()
        self.ready.wait(5)

    # ---- public API ------------------------------------------------------------------
    def point(self, x: float, y: float, label: str = "", box: tuple | None = None, hold: float = 6.0):
        self.q.put(("point", float(x), float(y), label, box, hold))

    def hide(self, now: bool = False):
        self.q.put(("hide_now",) if now else ("hide",))

    def stop(self):
        self.q.put(("quit",))

    # ---- thread ---------------------------------------------------------------------
    def _run(self):
        try:
            from Xlib import X, display
            d = display.Display()
            screen = d.screen()
            self.screen_w, self.screen_h = screen.width_in_pixels, screen.height_in_pixels
            visual = next((v for dp in screen.allowed_depths if dp.depth == 32 for v in dp.visuals
                           if v.visual_class == X.TrueColor), None)
            if visual is None:
                raise RuntimeError("no 32-bit ARGB visual (compositor without alpha support)")
            cmap = screen.root.create_colormap(visual.visual_id, X.AllocNone)
            has_shape = d.has_extension("SHAPE")
            self.cursor = Overlay(d, screen, visual, cmap, has_shape)
            self.box = Overlay(d, screen, visual, cmap, has_shape)
            self.font = _font(26 * 2)             # drawn on a 2x supersampled canvas
            self.sprite_cache: dict = {}
            self.ok = True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.warning("pointer unavailable: %s", self.error)
            self.ready.set()
            return
        self.ready.set()
        self._loop()

    def _loop(self):
        pos = None                      # current tip position (x, y)
        state = None                    # active animation
        while True:
            try:
                cmd = self.q.get(timeout=0.2 if state is None else 1 / FPS)
            except queue.Empty:
                cmd = None
            if cmd:
                if cmd[0] == "quit":
                    self.cursor.hide(); self.box.hide()
                    return
                if cmd[0] == "hide":
                    if state:
                        state["t_fade"] = time.monotonic()
                elif cmd[0] == "hide_now":
                    self.cursor.hide(); self.box.hide()
                    state, pos = None, None
                elif cmd[0] == "point":
                    _, x, y, label, box, hold = cmd
                    x = min(max(x, 4), self.screen_w - 4)
                    y = min(max(y, 4), self.screen_h - 4)
                    now = time.monotonic()
                    start = pos or (min(max(x - 320, 0), self.screen_w), min(max(y - 200, 0), self.screen_h))
                    state = {"from": start, "to": (x, y), "t0": now, "glide": 0.75 if pos else 0.6, "label": label,
                             "box": box, "hold": hold, "t_fade": None, "born": pos is None or state is None,
                             "boxed": False}
                    self.box.hide()
            if state is None:
                continue
            try:
                done = self._frame(state)
                pos = state.get("pos", pos)
            except Exception as e:
                log.warning("pointer frame failed: %s", e)
                done = True
            if done:
                self.cursor.hide(); self.box.hide()
                state, pos = None, None

    # ---- drawing ---------------------------------------------------------------------
    def _frame(self, s: dict) -> bool:
        now = time.monotonic()
        t = now - s["t0"]
        g = min(1.0, t / s["glide"])
        e = g * g * (3 - 2 * g)                                             # ease in-out
        (x0, y0), (x1, y1) = s["from"], s["to"]
        # a gentle arc so the cursor sweeps instead of sliding in a straight line
        arc = math.sin(math.pi * e) * min(60.0, math.hypot(x1 - x0, y1 - y0) * 0.12)
        x = x0 + (x1 - x0) * e + arc * -(y1 - y0) / (math.hypot(x1 - x0, y1 - y0) or 1)
        y = y0 + (y1 - y0) * e + arc * (x1 - x0) / (math.hypot(x1 - x0, y1 - y0) or 1)
        s["pos"] = (x1, y1) if g >= 1 else (x, y)
        alpha = min(1.0, t / 0.25) if s["born"] else 1.0
        if s["t_fade"] is not None or t > s["glide"] + s["hold"]:
            f0 = s["t_fade"] if s["t_fade"] is not None else s["t0"] + s["glide"] + s["hold"]
            fade = (now - f0) / 0.45
            if fade >= 1:
                return True
            alpha *= 1 - fade
        if g >= 1 and s["box"] and not s["boxed"]:
            self._draw_box(s["box"], alpha)
            s["boxed"] = True
        img, ox, oy, (w, h) = self._sprite(s["label"], x1 > self.screen_w - 420, y1 > self.screen_h - 140,
                                           max(0.0, t - s["glide"]))
        self.cursor.show(int(x - ox), int(y - oy), w, h, img, alpha)
        if s["boxed"]:
            self.box.raise_()
            self.cursor.raise_()
        return False

    def _sprite(self, label: str, flip_x: bool, flip_y: bool, since_arrival: float):
        """Cursor + ripple + label on one transparent canvas. Returns (image, tip_x, tip_y, (w, h))."""
        S = 2                                                               # supersample for smooth edges
        font = self.font
        text_w = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(label[:60], font=font) / S) if label else 0
        pill_w = min(520, text_w + 34) if label else 0
        W = max(190, pill_w + 90)
        H = 190
        tip = (W - 60, 60) if flip_x else (60, 60)
        ty = H - 60 if flip_y else 60
        tip = (tip[0], ty)
        img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd, d = ImageDraw.Draw(glow), ImageDraw.Draw(img)
        tx, ty = tip[0] * S, tip[1] * S
        # ripples that keep pulsing once the cursor has arrived
        for k in range(2):
            p = ((since_arrival * 0.9) + k * 0.5) % 1.0 if since_arrival > 0 else 0.0
            if since_arrival > 0:
                r = (14 + 46 * p) * S
                a = int(190 * (1 - p) ** 1.5)
                d.ellipse([tx - r, ty - r, tx + r, ty + r], outline=CYAN + (a,), width=3 * S)
        # arrow (classic cursor outline), tip at (tx, ty)
        pts = [(0, 0), (0, 29), (7.2, 22.5), (12.3, 34), (18, 31.2), (12.8, 20), (22, 20)]
        poly = [(tx + px * S * 1.15, ty + py * S * 1.15) for px, py in pts]
        gd.polygon(poly, fill=CYAN + (255,))
        d.polygon(poly, fill=CYAN + (255,), outline=(255, 255, 255, 255), width=2 * S)
        if label:
            x0 = (tip[0] - 24 - pill_w) if flip_x else (tip[0] + 22)
            y0 = (tip[1] - 62) if flip_y else (tip[1] + 34)
            box = [x0 * S, y0 * S, (x0 + pill_w) * S, (y0 + 38) * S]
            gd.rounded_rectangle(box, radius=19 * S, fill=CYAN + (170,))
            d.rounded_rectangle(box, radius=19 * S, fill=INK + (225,), outline=CYAN + (255,), width=2 * S)
            d.text(((x0 + 17) * S, (y0 + 19) * S), label[:60], font=font, fill=(235, 245, 255, 255), anchor="lm")
        glow = glow.filter(ImageFilter.GaussianBlur(9 * S))
        out = Image.alpha_composite(glow, img).resize((W, H), Image.LANCZOS)
        return out, tip[0], tip[1], (W, H)

    def _draw_box(self, box: tuple, alpha: float):
        """Highlight rectangle (x0, y0, x1, y1) in screen pixels."""
        x0, y0, x1, y1 = (int(v) for v in box)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.screen_w, x1), min(self.screen_h, y1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        pad = 14
        w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        r = min(14, (x1 - x0) // 4, (y1 - y0) // 4)
        ImageDraw.Draw(glow).rounded_rectangle([pad, pad, w - pad, h - pad], radius=r, outline=CYAN + (255,), width=6)
        glow = glow.filter(ImageFilter.GaussianBlur(7))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([pad, pad, w - pad, h - pad], radius=r, fill=CYAN + (28,), outline=CYAN + (255,), width=3)
        self.box.show(x0 - pad, y0 - pad, w, h, Image.alpha_composite(glow, img), alpha)
