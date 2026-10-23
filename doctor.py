"""./run.sh --doctor : one screen that says what works and what doesn't, with the fix for each problem."""
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import config

OK, BAD, WARN = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"


def _row(mark: str, what: str, detail: str = "", fix: str = ""):
    print(f" {mark} {what:<22} {detail}")
    if fix and mark != OK:
        print(f"   {'':<22} -> {fix}")


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _log_last(*needles: str) -> str:
    try:
        lines = (config.STATE_DIR / "jarvis.log").read_text(errors="replace").splitlines()[-4000:]
    except OSError:
        return ""
    return next((ln for ln in reversed(lines) if any(n in ln for n in needles)), "")


def run():
    print("Jarvis doctor\n")
    running = (config.RUNTIME_DIR / "jarvis.url").exists()
    _row(OK if running else WARN, "Jarvis app", "running" if running else "not running", "./run.sh")

    # Gemini
    if not config.GEMINI_API_KEY:
        _row(BAD, "Gemini key", "GEMINI_API_KEY missing", "put it in .env")
    else:
        try:
            from google import genai
            t0 = time.time()
            client = genai.Client(api_key=config.GEMINI_API_KEY)          # keep a reference: a temporary one is closed mid-call
            client.models.get(model=config.BRAIN_MODELS[0])
            _row(OK, "Gemini key", f"works ({(time.time() - t0) * 1000:.0f} ms)")
        except Exception as e:
            _row(BAD, "Gemini key", str(e)[:70], "check the key / internet")

    # audio
    try:
        from audio import pick_route
        r = pick_route()
        bt = r.source.startswith("bluez")
        _row(WARN if bt else OK, "Audio", r.label, "Bluetooth mics hear worse; a USB/wired mic is best" if bt else "")
    except Exception as e:
        _row(BAD, "Audio", str(e)[:70], "./run.sh --devices")

    # Chrome extension
    if running:
        last = _log_last("Chrome extension connected", "Chrome extension disconnected")
        on = "disconnected" not in last and bool(last)
        _row(OK if on else BAD, "Chrome extension", "connected" if on else "not connected",
             "chrome://extensions -> reload Jarvis Hands (or ./install_extension.sh)")
    else:
        _row(WARN, "Chrome extension", "start Jarvis to check")
    ext = json.loads((config.ROOT / "chrome_extension/manifest.json").read_text())
    _row(OK, "Extension version", ext.get("version", "?"))

    # calls
    pa = shutil.which("pactl") and shutil.which("pacat") and shutil.which("parec")
    _row(OK if pa else BAD, "Call audio tools", "pactl / pacat / parec found" if pa else "missing",
         "sudo dnf install pulseaudio-utils" if not pa else "")
    left = [ln for ln in subprocess.run(["pactl", "list", "short", "modules"], capture_output=True, text=True).stdout.splitlines() if "jarvis_" in ln]
    if left and not running:
        _row(WARN, "Call audio devices", "left over from a crashed call", "python -c 'import call_audio; call_audio.CallAudio.teardown_leftovers()'")
    _row(OK, "Calls", f"AI disclosure {'on' if config.CALL_DISCLOSE else 'OFF'}, limit {config.CALL_MAX_MINUTES} min")

    # local brain
    oll = shutil.which("ollama") or str(Path.home() / ".local/bin/ollama")
    if not Path(oll).exists():
        _row(WARN, "Ollama (Qwen)", "not installed", "scripts/install_local.sh")
    else:
        up = _port_open(11434)
        loaded = ""
        if up:
            import ollama_ctl
            loaded = ", ".join(ollama_ctl.loaded()) or "no model loaded"
        _row(OK, "Ollama (Qwen)", f"{'running: ' + loaded if up else 'stopped (starts on demand)'}")
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    if gpu:
        _row(OK, "GPU memory", gpu)

    # coding agents
    import delegate
    try:
        _row(OK, "Claude Code", delegate.claude_bin())
    except RuntimeError as e:
        _row(BAD, "Claude Code", str(e))
    try:
        oc = delegate.opencode_bin()
        cfg = Path.home() / ".config/opencode/opencode.json"
        has = cfg.exists() and config.OPENCODE_MODEL.split("/", 1)[-1] in cfg.read_text()
        _row(OK if has else WARN, "OpenCode", f"{oc} · {config.OPENCODE_MODEL}",
             "register the model in ~/.config/opencode/opencode.json" if not has else "")
    except RuntimeError as e:
        _row(WARN, "OpenCode", str(e))

    # memory
    try:
        import memory
        _row(OK, "Memory", memory.stats())
    except Exception as e:
        _row(BAD, "Memory", str(e)[:70])
    err = _log_last("ERROR", "Traceback")
    if err:
        _row(WARN, "Last error in log", err[:90], f"see {config.STATE_DIR / 'jarvis.log'}")
    print()
