"""Virtual audio plumbing so Jarvis can take part in a Chrome call (WhatsApp Web, Google Meet) without touching your
real microphone or speakers:

    Jarvis' voice  ->  sink "jarvis_to_call"  ->  its monitor (the "microphone" the call page records)
    the call page's sound  ->  sink "jarvis_from_call"  ->  Jarvis listens to its monitor
    and BOTH monitors are looped to your real speakers/headphones, so you hear the caller and Jarvis live (~50 ms delay)

Only audio streams that Chrome opens AFTER the call is started are moved (his music or another meeting that was already
running is never touched), and everything is put back (streams returned, devices unloaded) when the call ends."""
import json
import logging
import subprocess
import threading
import time

log = logging.getLogger("jarvis.call.audio")
TO_CALL, FROM_CALL = "jarvis_to_call", "jarvis_from_call"
MIC = f"{TO_CALL}.monitor"          # what the call page records: Jarvis' voice (a sink monitor works fine as a microphone)
BROWSERS = {"chrome", "google-chrome", "google-chrome-stable", "msedge", "microsoft-edge", "chromium", "brave"}
IN_RATE, OUT_RATE = 16000, 24000


def _pactl(*args: str) -> str:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=6).stdout.strip()


def _json(*args: str) -> list[dict]:
    try:
        return json.loads(subprocess.run(["pactl", "-f", "json", *args], capture_output=True, text=True, timeout=6).stdout or "[]")
    except (ValueError, subprocess.SubprocessError, OSError):
        return []


def _is_browser(item: dict) -> bool:
    p = item.get("properties") or {}
    binary = (p.get("application.process.binary") or "").lower()
    return binary in BROWSERS or "chrome" in (p.get("application.name") or "").lower()


class CallAudio:
    def __init__(self, loop_to_speakers: bool = True, speaker_sink: str = "@DEFAULT_SINK@", match=None):
        self.match = match or _is_browser          # which streams count as "the call page's" (tests pass a strict matcher)
        self.loop_to_speakers = loop_to_speakers
        self.speaker_sink = speaker_sink
        self.modules: list[str] = []
        self.moved_in: dict[int, str] = {}       # playback streams we moved -> the sink they came from
        self.moved_out: dict[int, str] = {}      # capture streams we moved -> the source they came from
        self.owner_mic = False                   # True while the real microphone is handed back to the call
        self.baseline_in: set[int] = set()       # browser streams that already existed: his music, another meeting... never touched
        self.baseline_out: set[int] = set()
        self.active = False
        self._watch: threading.Thread | None = None

    # ---- devices -------------------------------------------------------------------------------
    def setup(self, attach: bool = False):
        """attach=True: the call is ALREADY running, so its streams already exist: take every running (not paused) browser
        stream, and leave only paused ones (his paused music) alone."""
        self.teardown_leftovers()
        for args in (
            ("module-null-sink", f"sink_name={TO_CALL}", "sink_properties=device.description=Jarvis_voice_into_call"),
            ("module-null-sink", f"sink_name={FROM_CALL}", "sink_properties=device.description=Sound_from_call_to_Jarvis"),
        ):
            out = _pactl("load-module", *args)
            if not out.isdigit():
                self.teardown()
                raise RuntimeError(f"could not create the virtual audio device ({args[0]})")
            self.modules.append(out)
        if self.loop_to_speakers:              # he hears BOTH sides live: the caller, and Jarvis' own voice
            for src in (FROM_CALL, TO_CALL):
                out = _pactl("load-module", "module-loopback", f"source={src}.monitor", f"sink={self.speaker_sink}", "latency_msec=50")
                if out.isdigit():
                    self.modules.append(out)
        if attach:
            self.baseline_in = {si["index"] for si in _json("list", "sink-inputs") if self.match(si) and si.get("corked")}
            self.baseline_out = {so["index"] for so in _json("list", "source-outputs") if self.match(so) and so.get("corked")}
        else:
            self.baseline_in = {si["index"] for si in _json("list", "sink-inputs") if self.match(si)}
            self.baseline_out = {so["index"] for so in _json("list", "source-outputs") if self.match(so)}
        self.active = True
        self._watch = threading.Thread(target=self._keep_routed, daemon=True)
        self._watch.start()
        log.info("call audio ready (modules %s)", self.modules)

    @staticmethod
    def teardown_leftovers():
        """Remove devices a crashed earlier run left behind."""
        for line in _pactl("list", "short", "modules").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[1] in ("module-null-sink", "module-virtual-source", "module-loopback") and (
                    TO_CALL in parts[2] or FROM_CALL in parts[2] or "jarvis_mic" in parts[2]):
                _pactl("unload-module", parts[0])

    def teardown(self):
        self.active = False
        self.give_back_streams()
        for m in reversed(self.modules):
            _pactl("unload-module", m)
        self.modules.clear()
        log.info("call audio removed")

    # ---- routing Chrome's streams -------------------------------------------------------------------
    def route_once(self) -> tuple[int, int]:
        """Move any Chrome playback stream to the call sink, and Chrome's capture stream to the virtual microphone."""
        n_in = n_out = 0
        sinks = {s["index"]: s["name"] for s in _json("list", "sinks")}
        sources = {s["index"]: s["name"] for s in _json("list", "sources")}
        for si in _json("list", "sink-inputs"):
            if si["index"] in self.baseline_in:
                continue                       # was already playing before the call began: not part of the call
            if self.match(si) and sinks.get(si.get("sink")) != FROM_CALL:
                self.moved_in.setdefault(si["index"], sinks.get(si.get("sink"), "@DEFAULT_SINK@"))
                log.info("call audio: Chrome playback stream %s (%s) -> Jarvis' ear", si["index"], (si.get("properties") or {}).get("media.name", "?"))
                _pactl("move-sink-input", str(si["index"]), FROM_CALL)
                n_in += 1
        target = None if self.owner_mic else MIC
        for so in _json("list", "source-outputs"):
            if not self.match(so) or so["index"] in self.baseline_out:
                continue
            cur = sources.get(so.get("source"), "")
            if cur.endswith(".monitor") and cur != MIC:
                continue                       # a stream listening to some speaker's output, not a microphone stream
            if target and cur != target:
                self.moved_out.setdefault(so["index"], cur or "@DEFAULT_SOURCE@")
                log.info("call audio: Chrome microphone stream %s (%s) -> Jarvis' voice", so["index"], (so.get("properties") or {}).get("media.name", "?"))
                _pactl("move-source-output", str(so["index"]), target)
                n_out += 1
            elif not target and cur == MIC:    # hand the real microphone back to the call
                _pactl("move-source-output", str(so["index"]), self.moved_out.get(so["index"]) or "@DEFAULT_SOURCE@")
                n_out += 1
        return n_in, n_out

    def _keep_routed(self):
        while self.active:
            try:
                self.route_once()
            except Exception:
                log.exception("routing")
            time.sleep(1.5)

    def give_back_streams(self):
        for idx, sink in list(self.moved_in.items()):
            _pactl("move-sink-input", str(idx), sink)
        for idx, src in list(self.moved_out.items()):
            _pactl("move-source-output", str(idx), src)
        self.moved_in.clear()
        self.moved_out.clear()

    def hand_mic_to_owner(self, on: bool):
        """True: the call hears his real microphone (he talks himself); False: it hears Jarvis."""
        self.owner_mic = on
        self.route_once()


class Mouth:
    """Plays 24 kHz mono PCM into the call, paced in real time, so an interruption can cut the speech off at once
    (a plain pipe would let seconds of already-queued audio keep playing)."""

    def __init__(self, sink: str = TO_CALL):
        self.proc = subprocess.Popen(["pacat", f"--device={sink}", "--rate=24000", "--format=s16le", "--channels=1",
                                      "--latency-msec=40", "--client-name=Jarvis"], stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._buf = bytearray()
        self._lock = threading.Lock()
        self.last_sound = 0.0
        self._run = True
        threading.Thread(target=self._pump, daemon=True).start()

    def write(self, pcm: bytes):
        with self._lock:
            self._buf.extend(pcm)

    def clear(self):
        with self._lock:
            self._buf.clear()

    @property
    def speaking(self) -> bool:
        return bool(self._buf) or time.monotonic() - self.last_sound < 0.3

    def _pump(self):
        chunk, period = OUT_RATE * 2 * 20 // 1000, 0.02          # 20 ms
        nxt = time.monotonic()
        while self._run:
            with self._lock:
                data = bytes(self._buf[:chunk]); del self._buf[:chunk]
            if not data:
                data = b"\0" * chunk                              # keep the stream open with silence
            else:
                self.last_sound = time.monotonic()
            try:
                self.proc.stdin.write(data); self.proc.stdin.flush()
            except (OSError, ValueError):
                return
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))

    def close(self):
        self._run = False
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.terminate()


class Ear:
    """Reads what the call sounds like (16 kHz mono int16) from the call sink's monitor."""

    def __init__(self, source: str = f"{FROM_CALL}.monitor"):
        self.proc = subprocess.Popen(["parec", f"--device={source}", "--rate=16000", "--format=s16le", "--channels=1",
                                      "--latency-msec=40", "--client-name=Jarvis"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, ms: int = 40) -> bytes:
        return self.proc.stdout.read(IN_RATE * 2 * ms // 1000)

    def close(self):
        self.proc.terminate()
