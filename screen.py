"""Seeing the screen: a desktop-portal screenshot, then Gemini vision to describe it or find things on it.
Screenshots live only in memory and a private tmpfs folder; the file is deleted as soon as it is read."""
import io
import json
import logging
import re
import subprocess
from pathlib import Path

from google.genai import types
from PIL import Image

import config
from llm import hedged

log = logging.getLogger("jarvis.screen")
HELPER = config.ROOT / "helpers" / "screenshot_portal.py"
LOOK_WIDTH, POINT_WIDTH = 1920, 1280          # text needs more pixels to be readable; pointing doesn't

CONTEXT = ("This is a screenshot of {user}'s computer screen (Fedora Linux, GNOME). Jarvis, the voice assistant, "
           "may show its own small window with a glowing orb and a transcript: ignore it unless asked about it.")


def capture() -> Image.Image:
    """Blocking: full-resolution screenshot via the xdg-desktop-portal (works on GNOME Wayland)."""
    r = subprocess.run(["/usr/bin/python3", str(HELPER), "20"], capture_output=True, text=True, timeout=40)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "screenshot failed")
    src = Path(r.stdout.strip())
    try:
        img = Image.open(src)
        img.load()
        return img.convert("RGB")
    finally:
        src.unlink(missing_ok=True)               # the portal drops it in ~/Pictures; never leave it there


def _jpeg(img: Image.Image, width: int) -> bytes:
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


async def describe(client, question: str, img: Image.Image) -> str:
    """Answer a question about the screen in plain spoken sentences."""
    prompt = (CONTEXT.format(user=config.USER_NAME) + "\n\n" +
              (f"{config.USER_NAME} asks: {question}" if question.strip() else
               f"{config.USER_NAME} asks: what is on my screen?") +
              "\n\nAnswer for a voice assistant to read aloud: plain sentences, at most 4, no markdown, no lists, "
              "no coordinates. Name the apps/pages you see and what they show; quote text exactly when asked to "
              "read it. If something is blurry or cut off, say so rather than guessing.")
    contents = [types.Part.from_bytes(data=_jpeg(img, LOOK_WIDTH), mime_type="image/jpeg"), prompt]
    model, text = await hedged(client, list(config.VISION_MODELS), contents, hedge_s=6, timeout=45)
    log.info("screen described by %s", model)
    return text.strip()


async def locate(client, target: str, img: Image.Image) -> dict | None:
    """Find `target` on screen. Returns {label, x, y, box: (x0, y0, x1, y1)} in screen pixels, or None."""
    prompt = (CONTEXT.format(user=config.USER_NAME) + f"\n\nFind this on the screen: {target}.\n"
              'Answer ONLY with JSON: {"found": true or false, "label": "2-4 word name of what you found", '
              '"point": [y, x], "box_2d": [ymin, xmin, ymax, xmax]} with every coordinate normalised to 0-1000 '
              "(top-left corner is 0,0). `point` is the centre of the element to click or look at. If it is not "
              "visible, or you are unsure which one is meant, found=false. Never invent a location.")
    contents = [types.Part.from_bytes(data=_jpeg(img, POINT_WIDTH), mime_type="image/jpeg"), prompt]
    cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)
    model, text = await hedged(client, list(config.VISION_MODELS), contents, cfg, hedge_s=5, timeout=30)
    try:
        d = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip())
        if isinstance(d, list):
            d = d[0] if d else {}
        if not d.get("found", True) or not d.get("point"):
            return None
        y, x = (float(v) for v in d["point"][:2])
        W, H = img.size
        out = {"label": str(d.get("label") or target)[:40], "x": min(max(x / 1000 * W, 0), W - 1),
               "y": min(max(y / 1000 * H, 0), H - 1), "box": None, "model": model}
        b = d.get("box_2d")
        if b and len(b) == 4:
            y0, x0, y1, x1 = (float(v) for v in b)
            box = (x0 / 1000 * W, y0 / 1000 * H, x1 / 1000 * W, y1 / 1000 * H)
            # a box that doesn't contain the point, or is huge, is the model guessing: use the point alone
            if box[0] - 20 <= out["x"] <= box[2] + 20 and box[1] - 20 <= out["y"] <= box[3] + 20 \
                    and (box[2] - box[0]) < W * 0.6 and (box[3] - box[1]) < H * 0.6:
                out["box"] = box
        return out
    except (ValueError, KeyError, TypeError, IndexError) as e:
        log.warning("could not parse a location from %r: %s", text[:120], e)
        return None
