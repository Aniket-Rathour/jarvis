"""Speech-to-text for the Qwen brain: faster-whisper on the GPU (CPU fallback)."""
import ctypes
import glob
import logging
import os
import re
import site

import numpy as np

import config

log = logging.getLogger("jarvis.stt")
# Whisper's favourite inventions on silence/noise; dropped when the audio is short.
HALLUCINATIONS = {"thank you.", "thanks for watching!", "thank you for watching.", "you", "bye.", ".", "okay.",
                  "thanks.", "so", "uh", "um", "hmm.", "mm-hmm.", "yeah."}
EMAIL = re.compile(r"\b([A-Za-z0-9._-]+)\s+at\s+([A-Za-z0-9-]+(?:\s+dot\s+[A-Za-z0-9-]+)+)\b", re.I)


def clean(text: str) -> str:
    """Spoken e-mail addresses -> real ones ('rahul at example dot com' -> 'rahul@example.com')."""
    if not EMAIL.search(text):
        return text
    return EMAIL.sub(lambda m: f"{m.group(1)}@" + re.sub(r"\s+dot\s+", ".", m.group(2), flags=re.I), text).lower()


def _preload_cuda_libs():
    """pip's nvidia-cublas / nvidia-cudnn wheels live in site-packages/nvidia/*/lib, which the loader doesn't
    search; load them up front so CTranslate2 finds them."""
    libs = []
    for sp in site.getsitepackages():
        for pat in ("nvidia/cublas/lib/libcublasLt.so.*", "nvidia/cublas/lib/libcublas.so.*",
                    "nvidia/cudnn/lib/libcudnn.so.*", "nvidia/cudnn/lib/libcudnn_*.so.*"):
            libs += sorted(glob.glob(os.path.join(sp, pat)))
    for _ in range(2):                        # second pass resolves inter-library dependencies
        for f in libs:
            try:
                ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


class STT:
    def __init__(self, vocabulary: str = ""):
        _preload_cuda_libs()
        from faster_whisper import WhisperModel
        self.prompt = vocabulary
        try:
            try:      # cached already? then start without touching the network
                self.model = WhisperModel(config.WHISPER_MODEL, device="cuda", compute_type=config.WHISPER_COMPUTE,
                                          local_files_only=True)
            except Exception:
                self.model = WhisperModel(config.WHISPER_MODEL, device="cuda", compute_type=config.WHISPER_COMPUTE)
            self.device = "cuda"
        except Exception as e:                # no usable GPU: smaller model on the CPU
            log.warning("Whisper on GPU failed (%s); using CPU", str(e)[:120])
            self.model = WhisperModel("small.en" if config.WHISPER_LANGUAGE == "en" else "small",
                                      device="cpu", compute_type="int8")
            self.device = "cpu"
        self.transcribe(np.zeros(16000, np.int16).tobytes())     # warm-up
        log.info("whisper ready on %s", self.device)

    def _cpu_fallback(self):
        from faster_whisper import WhisperModel
        log.warning("Whisper ran out of GPU memory; switching to the CPU model")
        self.model = WhisperModel("small.en" if config.WHISPER_LANGUAGE == "en" else "small", device="cpu", compute_type="int8")
        self.device = "cpu"

    def transcribe(self, pcm: bytes) -> str:
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
        kw = dict(language=config.WHISPER_LANGUAGE or None, beam_size=5, vad_filter=False,
                  condition_on_previous_text=False, initial_prompt=self.prompt or None, without_timestamps=True)
        try:
            segments = list(self.model.transcribe(audio, **kw)[0])
        except RuntimeError as e:             # CUDA out of memory (another model took the GPU): don't lose the sentence
            if self.device == "cpu":
                raise
            log.warning("Whisper GPU error: %s", str(e)[:100])
            self._cpu_fallback()
            segments = list(self.model.transcribe(audio, **kw)[0])
        kept = []
        for s in segments:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:
                continue                      # probably noise
            kept.append(s.text.strip())
        text = clean(" ".join(kept).strip())
        if text.lower() in HALLUCINATIONS and len(audio) < 16000 * 2.5:
            return ""
        return text
