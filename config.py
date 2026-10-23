"""Settings, all overridable from .env (see .env.example)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _device(name: str):
    v = _env(name)
    return int(v) if v.isdigit() else (v or None)


# Which brain starts: auto = the one you used last, or gemini / qwen to pin it. Switch any time in the window
# (the Gemini | Qwen toggle) or by saying "switch to Qwen" / "switch to Gemini".
BRAIN = _env("BRAIN", "auto").lower()

# ---- Qwen brain: runs on this PC (Ollama + Whisper + Kokoro), no internet needed ----
OLLAMA_HOST = _env("OLLAMA_HOST", "http://127.0.0.1:11434")
# Voice brain: qwen3:8b fits entirely on the 8 GB GPU (~40 tokens/s, a tool call in under a second). The 27B
# (qwen3.8-oc) is smarter but runs mostly on the CPU (~2.5 tokens/s), so it is used for OpenCode coding jobs instead.
LOCAL_MODEL = _env("LOCAL_MODEL", "qwen3:8b")
LOCAL_CTX = int(_env("LOCAL_CTX", "8192"))                  # context window; smaller = faster and lighter
# GPU layers for Qwen: auto = chosen from the VRAM left after the speech model loads (more layers = faster, but
# too many runs out of memory), or a fixed number such as 20
LOCAL_GPU_LAYERS = _env("LOCAL_GPU_LAYERS", "auto").lower()
LOCAL_THREADS = int(_env("LOCAL_THREADS", "0"))             # CPU threads for the layers that don't fit on the GPU (0 = auto)
LOCAL_KEEP_WARM_MIN = int(_env("LOCAL_KEEP_WARM_MIN", "2"))  # minutes the model stays in memory after you switch away
WHISPER_MODEL = _env("WHISPER_MODEL", "large-v3-turbo")     # speech-to-text; small.en is lighter
WHISPER_COMPUTE = _env("WHISPER_COMPUTE", "int8_float16")
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "en")
# Kokoro voices: bm_george, bm_lewis, bm_daniel, bm_fable (British male); a blend like "bm_george:0.6,bm_lewis:0.4"
LOCAL_VOICE = _env("LOCAL_VOICE", "bm_george")
LOCAL_VOICE_SPEED = float(_env("LOCAL_VOICE_SPEED", "1.0"))
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "jarvis"

# ---- Gemini brain (cloud). The key also powers screen vision, pointing and ask_gemini for both brains ----
GEMINI_API_KEY = _env("GEMINI_API_KEY")
LIVE_MODEL = _env("GEMINI_MODEL", "gemini-3.8-live")
# Text models for ask_gemini (heavy thinking). Best first; the next starts if one is slow or overloaded.
BRAIN_MODELS = [m.strip() for m in _env("GEMINI_BRAIN_MODELS",
                "gemini-3.8-flash,gemini-3.5-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest").split(",")
                if m.strip()]
# Deep male voices: Algieba (smooth), Charon (informative), Alnilam (firm), Gacrux (mature), Puck
VOICE = _env("GEMINI_VOICE", "Algieba")
USER_NAME = _env("USER_NAME", "Aniket")
# Vision models for look_at_screen / point_at. Lite is accurate to a few pixels and almost never overloaded.
VISION_MODELS = [m.strip() for m in _env("GEMINI_VISION_MODELS",
                 "gemini-3.5-flash-lite,gemini-3.5-flash,gemini-3.6-flash,gemini-3.8-flash").split(",") if m.strip()]

# OpenCode coding agent, run on the local Qwen model (provider "ollama" in ~/.config/opencode/opencode.json)
OPENCODE_BIN = _env("OPENCODE_BIN")
OPENCODE_MODEL = _env("OPENCODE_MODEL", "ollama/qwen3.8-oc")

# Stop the Ollama server (not just unload the model) when no brain or OpenCode job needs it; it restarts on demand
STOP_OLLAMA_WHEN_IDLE = _env("STOP_OLLAMA_WHEN_IDLE", "on").lower() != "off"

# Long-term memory file (~/.local/share/jarvis/memory.db). No fixed cap by default: old conversation is folded into
# daily digests, and only if the disk runs low is the oldest raw data dropped. A number here sets a hard cap in MB.
MEMORY_MAX_MB = int(_env("MEMORY_MAX_MB", "0"))        # 0 = dynamic: grows as needed, tidies itself (see memory.py)

# Chrome extension bridge: the extension in ./chrome_extension connects here (only from that extension's origin)
BRIDGE_PORT = int(_env("BRIDGE_PORT", "8765"))

CLAUDE_BIN = _env("CLAUDE_BIN")
# bypassPermissions = Claude may do anything (Jarvis still asks you before installs/deletes)
CLAUDE_PERMISSION_MODE = _env("CLAUDE_PERMISSION_MODE", "bypassPermissions")
# Claude model per task complexity (Gemini rates each delegated task basic / medium / high)
CLAUDE_MODEL_BASIC = _env("JARVIS_CLAUDE_MODEL_BASIC", "claude-haiku-5-5")
CLAUDE_MODEL_COMPLEX = _env("JARVIS_CLAUDE_MODEL_COMPLEX", "claude-sonnet-5-5")     # medium and high
CLAUDE_EFFORT = _env("JARVIS_CLAUDE_EFFORT", "low")                                # low | medium | high | xhigh | max


def claude_model_for(complexity: str) -> tuple[str, str]:
    """(model, effort) for a task rated basic / medium / high."""
    return (CLAUDE_MODEL_BASIC if complexity == "basic" else CLAUDE_MODEL_COMPLEX), CLAUDE_EFFORT


# Test switch: routines never write to the real Night Log (the answers turn runs with its write tool removed)
ROUTINES_DRY_RUN = _env("JARVIS_ROUTINES_DRY_RUN") == "1"
# Show a terminal window while a routine (morning plan / night log / weekly review) runs? Off: progress is
# narrated and shown in the Jarvis window instead.
ROUTINES_TERMINAL = _env("ROUTINES_TERMINAL", "off").lower() == "on"
CLAUDE_TIMEOUT_MIN = int(_env("CLAUDE_TIMEOUT_MIN", "45"))
DEFAULT_WORKDIR = _env("DEFAULT_WORKDIR", "~")
PROJECTS_DIR = _env("PROJECTS_DIR", "~/Documents/Aniket")

# on = when headphones/a headset are the output, also use THEIR microphone (a Bluetooth headset then switches to
# call quality while Jarvis listens); off = keep listening with the PC's own mic and only play through the headphones
PREFER_HEADSET_MIC = _env("PREFER_HEADSET_MIC", "on").lower() != "off"
MIC_DEVICE = _device("MIC_DEVICE")          # index or name substring, see: ./run.sh --devices
SPEAKER_DEVICE = _device("SPEAKER_DEVICE")
ECHO_GUARD = _env("ECHO_GUARD", "auto")     # auto | on | off  (on = mic muted while Jarvis speaks)
# on = only send audio to Gemini while you're speaking above the room's noise level (stops it reacting to noise)
NOISE_GATE = _env("NOISE_GATE", "on").lower() != "off"
VAD_SILENCE_MS = int(_env("VAD_SILENCE_MS", "700"))

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "jarvis"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
TASKS_DIR = ROOT / "tasks"
