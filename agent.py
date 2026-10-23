"""Jarvis entry point.  ./run.sh [--text] [--devices] [--list-models] [--no-window]"""
import argparse
import asyncio
import fcntl
import logging
import logging.handlers
import signal
import sys
import time

import config
from ui import UI, open_window

LOCK = config.RUNTIME_DIR / "jarvis.lock"
URL_FILE = config.RUNTIME_DIR / "jarvis.url"


def setup_logging():
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    file = logging.handlers.RotatingFileHandler(config.STATE_DIR / "jarvis.log", maxBytes=2_000_000, backupCount=3)
    file.setFormatter(fmt)
    handlers = [file]
    if sys.stderr.isatty():
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    for noisy in ("httpx", "httpcore", "google_genai", "google_genai.types"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    logging.getLogger("websockets").setLevel(logging.CRITICAL)   # Chrome opens empty preconnect sockets


def prune_tasks(days: int = 7):
    """tasks/<id>/ holds each Claude run's prompt, live events and result; keep a week of them."""
    import shutil
    cutoff = time.time() - days * 86400
    for d in config.TASKS_DIR.glob("t*"):
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def single_instance():
    """Two Jarvis processes would both hear you and do everything twice. If one is already
    running, just bring its window back and exit."""
    fh = open(LOCK, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            open_window(URL_FILE.read_text().strip())
            print("Jarvis is already running; reopened its window.")
        except OSError:
            print("Jarvis is already running.")
        sys.exit(0)
    return fh          # keep the handle open for the life of the process


async def amain(args):
    from audio import Mic, Speaker
    from jarvis import Jarvis

    log = logging.getLogger("jarvis")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    holder = {}
    ui = UI(lambda m: holder["j"].on_ui(m) if "j" in holder else None)
    await ui.start()
    URL_FILE.write_text(ui.url)
    if not args.no_window:
        open_window(ui.url)
    else:
        print(ui.url, flush=True)

    speaker = mic = None
    if not args.text:
        try:
            speaker = Speaker(config.SPEAKER_DEVICE)
        except Exception as e:
            log.error("speaker unavailable (%s); continuing without sound", e)
    jarvis = holder["j"] = Jarvis(ui, speaker)
    if not args.text:
        try:
            mic = jarvis.mic = Mic(jarvis.on_mic, config.MIC_DEVICE)
            log.info("microphone: %s", mic.source or config.MIC_DEVICE)
        except Exception as e:
            log.error("microphone unavailable (%s); type commands in the window", e)
            ui.send(type="notice", text="Microphone unavailable — type in the box below.")
    for dev in (speaker, mic):
        if dev:
            dev.start()

    async def watch_window():
        gone = None
        while True:
            await asyncio.sleep(1)
            if ui.ever_connected and not ui.clients:
                gone = gone or time.monotonic()
                if time.monotonic() - gone > 6:
                    log.info("window closed; quitting")
                    stop.set()
            else:
                gone = None

    watcher = asyncio.create_task(watch_window()) if not args.no_window else None
    try:
        await jarvis.run(stop)
    finally:
        if watcher:
            watcher.cancel()
        for dev in (mic, speaker):
            if dev:
                dev.close()
        URL_FILE.unlink(missing_ok=True)
        try:                                           # leave nothing of Qwen running in the background
            import ollama_ctl
            await asyncio.to_thread(ollama_ctl.release)
        except Exception:
            pass


def mic_test():
    """Record 7 s and show a live level meter, to check that the right microphone is in use."""
    import numpy as np
    from audio import Mic
    levels = []
    mic = Mic(lambda d, r: levels.append(r), config.MIC_DEVICE)
    from audio import pick_route
    print(f"Audio: {pick_route().label}\nMicrophone: {mic.source or config.MIC_DEVICE}\nSpeak now (7 seconds)…\n")
    mic.start()
    t0 = time.time()
    while time.time() - t0 < 7:
        time.sleep(0.25)
        print(f"\r  level {'█' * min(60, int(mic.rms * 400)):<60} {mic.rms:.3f}", end="", flush=True)
    mic.close()
    peak = max(levels) if levels else 0
    print(f"\n\nloudest {peak:.3f}, typical quiet {float(np.median(levels)) if levels else 0:.3f}")
    print("-> Almost silent. Wrong or muted microphone: set MIC_DEVICE in .env (see ./run.sh --devices)."
          if peak < 0.03 else "-> Your voice is being picked up.")


def main():
    p = argparse.ArgumentParser(description="Jarvis voice assistant")
    p.add_argument("--text", action="store_true", help="no microphone/speaker; type in the window")
    p.add_argument("--no-window", action="store_true", help="don't open the window; print its URL instead")
    p.add_argument("--devices", action="store_true", help="list audio devices for MIC_DEVICE / SPEAKER_DEVICE")
    p.add_argument("--mic-test", action="store_true", help="record 7 s and show a live microphone level meter")
    p.add_argument("--doctor", action="store_true", help="check everything (keys, audio, Chrome, Qwen, memory) and say how to fix it")
    p.add_argument("--list-models", action="store_true", help="list the models each brain can use")
    args = p.parse_args()

    if args.devices:
        import sounddevice as sd
        from audio import pick_route
        print(f"Jarvis would use: {pick_route().label}\n")
        print(sd.query_devices())
        return
    if args.mic_test:
        return mic_test()
    if args.doctor:
        import doctor
        return doctor.run()
    if args.list_models:
        if config.GEMINI_API_KEY:
            from google import genai
            print("Gemini Live models:")
            for m in genai.Client(api_key=config.GEMINI_API_KEY).models.list():
                if "bidiGenerateContent" in (m.supported_actions or []):
                    print("  ", m.name)
        import subprocess
        print("Local models (Ollama):")
        subprocess.run(["ollama", "list"])
        return

    setup_logging()
    _lock = single_instance()  # noqa: F841
    prune_tasks()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
