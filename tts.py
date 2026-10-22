"""Text-to-speech for the Qwen brain: Kokoro (82M, ONNX) with a deep British male voice, fully local."""
import logging
import re
import urllib.request

import numpy as np

import config

log = logging.getLogger("jarvis.tts")
BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {"kokoro-v1.0.onnx": 300_000_000, "voices-v1.0.bin": 20_000_000}     # name -> minimum sane size
OUT_RATE = 24000


def _clean(text: str) -> str:
    """Make model text speakable: no markdown, URLs or emoji."""
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_#`>|~]+", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class TTS:
    def __init__(self):
        from kokoro_onnx import Kokoro
        d = config.DATA_DIR / "kokoro"
        d.mkdir(parents=True, exist_ok=True)
        for name, min_size in FILES.items():
            f = d / name
            if not f.exists() or f.stat().st_size < min_size:
                log.info("downloading %s", name)
                tmp = f.with_suffix(".part")
                urllib.request.urlretrieve(BASE + name, tmp)
                tmp.replace(f)
        self.k = Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))
        self.voice = self._voice(config.LOCAL_VOICE)
        self.synth("Ready.")                  # warm-up
        log.info("kokoro voice %s ready", config.LOCAL_VOICE)

    def _voice(self, spec: str):
        """'bm_george' or a blend 'bm_george:0.6,bm_lewis:0.4'."""
        if ":" not in spec:
            return spec
        style = None
        for part in spec.split(","):
            name, weight = part.split(":")
            v = self.k.get_voice_style(name.strip()) * float(weight)
            style = v if style is None else style + v
        return style

    def synth(self, text: str) -> bytes:
        text = _clean(text)
        if not text:
            return b""
        samples, rate = self.k.create(text, voice=self.voice, speed=config.LOCAL_VOICE_SPEED, lang="en-gb")
        if rate != OUT_RATE:
            n = int(len(samples) * OUT_RATE / rate)
            samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
        return (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
