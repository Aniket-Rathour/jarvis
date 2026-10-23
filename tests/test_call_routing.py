"""Call-audio routing: only the call's own streams may move; a stream that already existed (his music, another meeting) must
stay exactly where it was. Uses fake 'Google Chrome' streams made with pacat/parec. Run: .venv/bin/python tests/test_call_routing.py"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import call_audio as ca  # noqa: E402

def STRICT(item):                                   # tests: only streams made by the test itself, never his real Chrome
    return (item.get("properties") or {}).get("application.name") == "JarvisTestChrome"




def sink_of(client_name: str) -> str | None:
    sinks = {s["index"]: s["name"] for s in ca._json("list", "sinks")}
    for si in ca._json("list", "sink-inputs"):
        if (si.get("properties") or {}).get("application.name") == client_name:
            return sinks.get(si["sink"])


def source_of(client_name: str) -> str | None:
    srcs = {s["index"]: s["name"] for s in ca._json("list", "sources")}
    for so in ca._json("list", "source-outputs"):
        if (so.get("properties") or {}).get("application.name") == client_name:
            return srcs.get(so["source"])


def start_play(name):
    p = subprocess.Popen(["pacat", f"--client-name={name}", "--rate=24000", "--format=s16le", "--channels=1", "--device=@DEFAULT_SINK@"],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.stdin.write(b"\0" * 48000); p.stdin.flush()
    return p


def start_rec(name):
    return subprocess.Popen(["parec", f"--client-name={name}", "--rate=16000", "--format=s16le", "--channels=1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# fake Chrome streams must look like Chrome to the router: name it "JarvisTestChrome"; the old one gets a distinct id by order
old_music = start_play("JarvisTestChrome")            # his music, already playing before the call
old_rec = start_rec("JarvisTestChrome")               # another meeting already using the microphone
time.sleep(0.6)
orig_sink, orig_src = sink_of("JarvisTestChrome"), source_of("JarvisTestChrome")
a = ca.CallAudio(loop_to_speakers=False, match=STRICT)
a.setup()
fails = []
try:
    time.sleep(2.2)                                # let the router run a few rounds
    if sink_of("JarvisTestChrome") != orig_sink:
        fails.append(f"his existing music was moved ({orig_sink} -> {sink_of('Google Chrome')})")
    if source_of("JarvisTestChrome") != orig_src:
        fails.append(f"his existing microphone stream was moved ({orig_src} -> {source_of('Google Chrome')})")
    # the call's own streams open after setup: they must move
    call_play, call_rec = start_play("JarvisTestChrome"), start_rec("JarvisTestChrome")
    time.sleep(2.5)
    sinks = [s for s in ca._json("list", "sink-inputs") if (s.get("properties") or {}).get("application.name") == "JarvisTestChrome"]
    names = {s["name"]: s["index"] for s in ca._json("list", "sinks")}
    moved_in = [s for s in sinks if s["sink"] == names.get(ca.FROM_CALL)]
    if len(moved_in) != 1:
        fails.append(f"expected exactly 1 new playback stream in the call sink, found {len(moved_in)}")
    srcs = {s["name"]: s["index"] for s in ca._json("list", "sources")}
    moved_out = [s for s in ca._json("list", "source-outputs") if (s.get("properties") or {}).get("application.name") == "JarvisTestChrome"
                 and s["source"] == srcs.get(ca.MIC)]
    if len(moved_out) != 1:
        fails.append(f"expected exactly 1 new capture stream on the call microphone, found {len(moved_out)}")
    a.hand_mic_to_owner(True); time.sleep(0.5)
    if [s for s in ca._json("list", "source-outputs") if s["source"] == srcs.get(ca.MIC)]:
        fails.append("takeover: the call's capture stream is still on Jarvis' microphone")
    a.hand_mic_to_owner(False); time.sleep(0.5)
finally:
    a.teardown()
time.sleep(0.5)
if sink_of("JarvisTestChrome") is None:
    fails.append("lost the playback stream")
left = [ln for ln in ca._pactl("list", "short", "modules").splitlines() if "jarvis_" in ln]
if left:
    fails.append(f"devices left behind: {left}")
for p in (old_music, old_rec, call_play, call_rec):
    p.terminate()
if fails:
    print("FAIL\n  " + "\n  ".join(fails)); sys.exit(1)

# --- he must hear BOTH sides live, with little delay (a throwaway sink stands in for his speakers: nothing is audible)
import numpy as np  # noqa: E402
ca._pactl("load-module", "module-null-sink", "sink_name=jarvis_test_out")
b = ca.CallAudio(loop_to_speakers=True, speaker_sink="jarvis_test_out", match=STRICT); b.setup()
try:
    time.sleep(0.8)
    cap = subprocess.Popen(["parec", "--device=jarvis_test_out.monitor", "--rate=24000", "--format=s16le", "--channels=1", "--latency-msec=20"], stdout=subprocess.PIPE)
    tone = (np.sin(2 * np.pi * 440 * np.arange(12000) / 24000) * 12000).astype(np.int16).tobytes()
    for label, sink in (("caller's voice", ca.FROM_CALL), ("Jarvis' voice", ca.TO_CALL)):
        w = subprocess.Popen(["pacat", f"--device={sink}", "--rate=24000", "--format=s16le", "--channels=1", "--latency-msec=20"], stdin=subprocess.PIPE)
        w.stdin.write(b"\0" * 4800); w.stdin.flush(); time.sleep(0.4)
        cap.stdout.read(24000)                                   # discard what piled up
        t0 = time.monotonic(); w.stdin.write(tone); w.stdin.flush()
        delay = None
        while time.monotonic() - t0 < 1.5:
            x = np.frombuffer(cap.stdout.read(480), dtype=np.int16)
            if x.size and int(np.abs(x).max()) > 3000:
                delay = (time.monotonic() - t0) * 1000; break
        w.terminate()
        if delay is None:
            fails.append(f"{label} never reached his speakers")
        elif delay > 400:
            fails.append(f"{label} reached his speakers too late ({delay:.0f} ms)")
        else:
            print(f"  {label}: heard on his speakers after {delay:.0f} ms")
    cap.terminate()
finally:
    b.teardown()
    for line in ca._pactl("list", "short", "modules").splitlines():
        if "jarvis_test_out" in line:
            ca._pactl("unload-module", line.split("\t")[0])
if fails:
    print("FAIL\n  " + "\n  ".join(fails)); sys.exit(1)
print("call routing: all checks passed (existing streams untouched, call streams moved, takeover works, everything restored)")
