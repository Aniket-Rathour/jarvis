"""Microphone capture and low-latency, interruptible speaker playback."""
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

IN_RATE, OUT_RATE = 16000, 24000
IN_BLOCK = 512           # 32 ms mic chunks
OUT_BLOCK = 480          # 20 ms speaker callback


def level(buf: bytes, gain: float) -> float:
    """0..1 loudness of int16 PCM."""
    x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768
    return float(min(1.0, np.sqrt(np.mean(x * x)) * gain)) if x.size else 0.0


class Speaker:
    """Callback-driven output. write() never blocks the network loop and clear() silences
    Jarvis instantly when the user interrupts."""

    def __init__(self, device=None):
        self._buf = bytearray()
        self._lock = threading.Lock()
        self.level = 0.0
        self.last_sound = 0.0
        self.held = False                    # paused mid-sentence while someone may be interrupting
        self._stream = sd.RawOutputStream(samplerate=OUT_RATE, channels=1, dtype="int16", device=device,
                                          blocksize=OUT_BLOCK, latency="low", callback=self._cb)

    def _cb(self, outdata, frames, t, status):
        n = frames * 2
        if self.held:
            outdata[:] = b"\0" * n
            return
        with self._lock:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
        if chunk:
            self.level = level(chunk, 6)
            self.last_sound = time.monotonic()
        else:
            self.level = 0.0
        outdata[:] = chunk.ljust(n, b"\0")

    def start(self):
        self._stream.start()

    def write(self, pcm: bytes):
        with self._lock:
            self._buf.extend(pcm)

    def clear(self):
        with self._lock:
            self._buf.clear()
        self.level = 0.0
        self.held = False

    def hold(self, on: bool):
        self.held = on

    @property
    def playing(self) -> bool:
        return bool(self._buf) or time.monotonic() - self.last_sound < 0.15

    def close(self):
        self._stream.stop()
        self._stream.close()


class HighPass:
    """One-pole 80 Hz high-pass. Removes DC offset and the slow rumble some analog mics produce
    for seconds after opening, which otherwise sounds like constant speech to Gemini's turn detection."""

    def __init__(self, cutoff=80.0, rate=IN_RATE):
        self.a = 1 / (1 + 2 * np.pi * cutoff / rate)
        self.px = self.py = 0.0

    def __call__(self, x: np.ndarray) -> np.ndarray:
        a, px, py = self.a, self.px, self.py
        out = np.empty_like(x)
        for i, v in enumerate(x.tolist()):
            py = a * (py + v - px)
            px = v
            out[i] = py
        self.px, self.py = px, py
        return out


class Mic:
    """16 kHz mono capture, high-pass filtered. on_chunk(pcm_bytes, rms) runs on the audio thread."""
    SETTLE_S = 1.0                      # many inputs "pop" for a moment when opened (Bluetooth: profile switch)

    def __init__(self, on_chunk, device=None):
        self.device = device            # explicit MIC_DEVICE from .env, else follow the system route
        self.source = ""
        self.rms = 0.0
        self._hp = HighPass()
        self._t0 = 0.0
        self._on_chunk = on_chunk
        self._stream = None
        self._open(pick_route().source if device is None else "")

    @staticmethod
    def _prepare_bluetooth(source: str):
        """A Bluetooth mic only exists in call mode. If the stream is opened while the headset is still switching
        from music (A2DP) to call mode it stays silent, and the default narrow 8 kHz CVSD codec is mis-heard by
        speech models. So switch first (mSBC = 16 kHz wideband, CVSD as fallback), let it settle, then open."""
        if not source.startswith("bluez_input."):
            return
        card = "bluez_card." + source.split(".", 1)[1].replace(":", "_")
        for profile in ("headset-head-unit", "headset-head-unit-cvsd"):
            try:
                if subprocess.run(["pactl", "set-card-profile", card, profile], capture_output=True,
                                  timeout=4).returncode == 0:
                    break
            except (OSError, subprocess.SubprocessError):
                return
        time.sleep(1.2)

    def _open(self, source: str):
        self.source = source
        self._prepare_bluetooth(source)
        saved = os.environ.get("PIPEWIRE_NODE")
        if source:
            os.environ["PIPEWIRE_NODE"] = source          # read by PipeWire's ALSA layer when the stream opens
        try:
            self._stream = sd.RawInputStream(samplerate=IN_RATE, channels=1, dtype="int16", device=self.device,
                                             blocksize=IN_BLOCK, latency="low", callback=self._cb)
        finally:                                          # scoped: must never redirect the speaker stream
            if saved is None:
                os.environ.pop("PIPEWIRE_NODE", None)
            else:
                os.environ["PIPEWIRE_NODE"] = saved

    def reopen(self, source: str):
        """Switch to another microphone (headphones connected/disconnected) without restarting Jarvis."""
        if self.device is not None or source == self.source:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._hp = HighPass()
        self._open(source)
        self.start()

    def _cb(self, indata, frames, t, status):
        x = self._hp(np.frombuffer(indata, dtype=np.int16).astype(np.float32))
        self.rms = float(np.sqrt(np.mean(x * x))) / 32768
        if time.monotonic() - self._t0 < self.SETTLE_S:
            return
        self._on_chunk(np.clip(x, -32768, 32767).astype(np.int16).tobytes(), self.rms)

    def start(self):
        self._t0 = time.monotonic()
        self._stream.start()

    def close(self):
        self._stream.stop()
        self._stream.close()


def _pactl_json(*args):
    try:
        return json.loads(subprocess.run(["pactl", "-f", "json", *args], capture_output=True, text=True,
                                         timeout=4).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def _default(kind: str) -> str:
    try:
        return subprocess.run(["pactl", f"get-default-{kind}"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


HEADSET_FORMS = {"headset", "headphone", "hands-free", "handset", "portable"}


@dataclass(frozen=True)
class Route:
    sink: str = ""               # speaker: the system default output (PipeWire follows it on its own)
    sink_desc: str = ""
    source: str = ""             # microphone Jarvis should open ("" = PortAudio default)
    source_desc: str = ""
    headset: bool = False        # speaker and mic are the same headset/headphones
    echo_possible: bool = True   # can the mic hear the speaker?
    tail: float = 0.4            # seconds to keep ignoring the mic after Jarvis stops talking

    @property
    def label(self) -> str:
        if self.headset:
            return f"{self.sink_desc or 'headset'} (mic + speaker)"
        return f"mic: {self.source_desc or '?'} · speaker: {self.sink_desc or '?'}"


def pick_route(prefer_headset: bool | None = None) -> Route:
    """Follow the system's default output, and use the mic that belongs to it.

    - Headphones/headset connected (Bluetooth, USB or 3.5 mm) -> speak through them AND listen through their
      own mic. A Bluetooth headset flips to call mode while its mic is open (the system does that itself).
    - Otherwise the default input if it is a real microphone, else the best real one (PC/USB mic).
    The default input is not always a mic: with a Bluetooth speaker connected it can be that speaker's
    '.monitor' (a loopback of whatever plays), which would make Jarvis hear music and himself, not you."""
    import config
    prefer_headset = config.PREFER_HEADSET_MIC if prefer_headset is None else prefer_headset
    sinks = [x for x in _pactl_json("list", "sinks") if not x["name"].endswith(".monitor")]
    sources = [x for x in _pactl_json("list", "sources") if not x["name"].endswith(".monitor")]
    sink = next((x for x in sinks if x["name"] == _default("sink")), None)
    info = lambda x, k: (x.get("properties") or {}).get(k)                                   # noqa: E731
    desc = lambda x: (x.get("description") or x["name"]) if x else ""                        # noqa: E731

    mate = None                                       # the mic that is physically part of the default output
    if sink is not None and info(sink, "device.id") is not None:
        mate = next((x for x in sources if info(x, "device.id") == info(sink, "device.id")), None)
    headset_like = sink is not None and (info(sink, "device.form_factor") in HEADSET_FORMS)
    if mate is not None and headset_like and prefer_headset:
        return Route(sink["name"], desc(sink), mate["name"], desc(mate), True, False,
                     0.9 if sink["name"].startswith("bluez") else 0.4)

    default = _default("source")
    src = next((x for x in sources if x["name"] == default), None)
    if src is None and sources:                       # default is a monitor/missing: best real input
        sources.sort(key=lambda x: (x["name"].startswith("bluez"), x["state"] == "SUSPENDED"))
        src = sources[0]
    tail = 0.9 if (sink and sink["name"].startswith("bluez")) else 0.4
    return Route(sink["name"] if sink else "", desc(sink), src["name"] if src else "", desc(src), False, True, tail)


def pick_mic() -> str:
    return pick_route().source


def echo_route() -> tuple[bool, float]:
    r = pick_route()
    return r.echo_possible, r.tail
