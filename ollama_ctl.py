"""Start / unload / stop the local Ollama server, so Qwen only uses the GPU and RAM while it is actually needed."""
import json
import logging
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import config

log = logging.getLogger("jarvis.ollama")
_used = False           # only stop what Jarvis itself started or loaded; never someone else's Ollama session


def mark_used():
    global _used
    _used = True


def _get(path: str, timeout: float = 2):
    return json.load(urllib.request.urlopen(config.OLLAMA_HOST + path, timeout=timeout))


def up() -> bool:
    try:
        _get("/api/version", 1.5)
        return True
    except (OSError, ValueError):
        return False


def ensure_running(wait: float = 25) -> bool:
    """Start the server if it is off (it is no longer left running in the background)."""
    mark_used()
    if up():
        return True
    if subprocess.run(["systemctl", "--user", "start", "ollama"], capture_output=True).returncode != 0:
        exe = shutil.which("ollama") or str(Path.home() / ".local/bin/ollama")
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait:
        if up():
            return True
        time.sleep(0.4)
    return False


def loaded() -> list[str]:
    try:
        return [m["name"] for m in _get("/api/ps").get("models", [])]
    except (OSError, ValueError):
        return []


def unload_all():
    """Free the GPU and RAM right now."""
    for name in loaded():
        try:
            req = urllib.request.Request(config.OLLAMA_HOST + "/api/generate", method="POST",
                                         data=json.dumps({"model": name, "keep_alive": 0}).encode())
            urllib.request.urlopen(req, timeout=10).read()
            log.info("unloaded %s", name)
        except OSError as e:
            log.warning("could not unload %s: %s", name, e)


def release():
    """Nothing needs Qwen any more: unload the model and, unless told otherwise, stop the server too."""
    if not _used or not up():
        return
    unload_all()
    if config.STOP_OLLAMA_WHEN_IDLE:
        if subprocess.run(["systemctl", "--user", "stop", "ollama"], capture_output=True).returncode == 0:
            log.info("ollama server stopped (nothing is using it)")
