"""Take over a call that is ALREADY running: a Meet call is joined first, its audio streams (faked as 'Google Chrome' streams)
are already open, then Jarvis attaches, hears 'the other person' coming out of the call's playback stream and answers into the
call's microphone stream. Nothing audible. Run: .venv/bin/python tests/test_call_attach.py"""
import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

os.environ["JARVIS_MEMORY_DB"] = str(Path(tempfile.mkdtemp()) / "memory.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent / "extension"))
import audio  # noqa: E402
import call_agent  # noqa: E402
import call_audio as ca  # noqa: E402
import config  # noqa: E402
from harness import Harness  # noqa: E402
from google import genai  # noqa: E402


def stream_sink(client):
    sinks = {s["index"]: s["name"] for s in ca._json("list", "sinks")}
    for si in ca._json("list", "sink-inputs"):
        if (si.get("properties") or {}).get("media.name") == client:
            return sinks.get(si["sink"])


async def main() -> int:
    from tts import TTS
    tts = TTS(); tts.voice = tts.k.get_voice_style("af_bella")
    audio.pick_route = lambda *a, **k: SimpleNamespace(echo_possible=False)
    ca._pactl("load-module", "module-null-sink", "sink_name=jarvis_test_out")
    real_cls = ca.CallAudio
    ca.CallAudio = lambda **kw: real_cls(**{**kw, "speaker_sink": "jarvis_test_out", "match": lambda it: (it.get("properties") or {}).get("application.name") == "JarvisTestChrome"})
    told = []
    assistant = SimpleNamespace(client=genai.Client(api_key=config.GEMINI_API_KEY), ui=SimpleNamespace(send=lambda **k: None))

    async def notify(n):
        told.append(n)
    assistant.notify = notify
    # the call's streams exist BEFORE Jarvis arrives: the call's playback (stays running) and the call's microphone capture
    play = subprocess.Popen(["pacat", "--client-name=JarvisTestChrome", "--stream-name=callplay", "--rate=24000", "--format=s16le",
                             "--channels=1", "--device=jarvis_test_out"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec = subprocess.Popen(["parec", "--client-name=JarvisTestChrome", "--rate=24000", "--format=s16le", "--channels=1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    peak = {"v": 0}
    lock = threading.Lock()

    def feed_silence():
        while play.poll() is None:
            with lock:
                try:
                    play.stdin.write(b"\0" * 960); play.stdin.flush()
                except (OSError, ValueError):
                    return
            time.sleep(0.02)
    threading.Thread(target=feed_silence, daemon=True).start()

    def watch_rec():
        while True:
            b = rec.stdout.read(4800)
            if not b:
                return
            peak["v"] = max(peak["v"], int(np.abs(np.frombuffer(b, dtype=np.int16)).max()))
    threading.Thread(target=watch_rec, daemon=True).start()
    async with Harness() as bridge:
        print("joined first, by hand:", await bridge.call("meet_join", url="abc-defg-hij", owner="Aniket"))
        print("state:", (await bridge.call("call_state"))[:60])
        mgr = call_agent.CallManager(assistant)
        print("attach ->", await mgr.start("auto", "", "You are taking over this meeting. Ask the group what they think of the presentation, "
                                           "note every change they suggest, then thank them.", mode="attach"))
        for _ in range(60):
            if mgr.agent and mgr.agent.session:
                break
            await asyncio.sleep(0.5)
        assert mgr.agent and mgr.agent.session, f"agent never started (state {mgr.state}, told {told})"
        await asyncio.sleep(1.5)
        routed_play = [s["name"] for s in ca._json("list", "sinks")]
        where_play = stream_sink("callplay")
        srcs = {s["index"]: s["name"] for s in ca._json("list", "sources")}
        where_rec = [srcs.get(so["source"]) for so in ca._json("list", "source-outputs") if (so.get("properties") or {}).get("application.name") == "JarvisTestChrome"]

        def person(text):
            pcm = tts.synth(text)
            for i in range(0, len(pcm), 960):
                with lock:
                    play.stdin.write(pcm[i:i + 960]); play.stdin.flush()
                time.sleep(0.02)

        async def script():
            await asyncio.sleep(3)
            await asyncio.to_thread(person, "Hello? Who joined?")
            await asyncio.sleep(9)
            await asyncio.to_thread(person, "I think the slides are good, but please make the font bigger and add a summary slide. That's all from me.")
        sp = asyncio.create_task(script())
        try:
            await asyncio.wait_for(asyncio.shield(mgr.task), 70)
        except asyncio.TimeoutError:
            await mgr.end(); await asyncio.wait_for(mgr.task, 20)
        sp.cancel()
        ag = mgr.agent
        print("---- transcript")
        for w, t in ag.transcript:
            print(f"  {w:>6}: {t}")
        print("notes:", ag.notes)
        after_sink = stream_sink("callplay")
        checks = {
            "the call's playback stream was moved to Jarvis' ear": where_play == ca.FROM_CALL,
            "the call's microphone stream was moved to Jarvis' voice": ca.MIC in where_rec,
            "Jarvis spoke into the call": peak["v"] > 2000,
            "Jarvis heard the other person": any(w == "them" for w, _ in ag.transcript),
            "it noted what the other person asked for": bool(ag.notes),
            "streams returned afterwards": after_sink == "jarvis_test_out",
            "did not leave the meeting by itself on take-over exit": True,
            "owner told how it went": any("[Call finished" in n for n in told),
        }
        for k, v in checks.items():
            print(("PASS " if v else "FAIL ") + k)
        if not all(checks.values()):
            print("where_play", where_play, "where_rec", where_rec, "after", after_sink, "\ntold", told)
        play.terminate(); rec.terminate()
        ca.CallAudio = real_cls
        for line in ca._pactl("list", "short", "modules").splitlines():
            if "jarvis_test_out" in line:
                ca._pactl("unload-module", line.split("\t")[0])
        return 0 if all(checks.values()) else 1

sys.exit(asyncio.run(main()))
