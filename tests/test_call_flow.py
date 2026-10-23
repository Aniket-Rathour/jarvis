"""Whole call flow with no real call: an isolated Chrome (mock WhatsApp Web), the real CallManager, the real call-audio
devices, a real Gemini Live session, and a synthetic 'Mom' talking through the virtual speaker. Checks that Jarvis opens
the chat, places the call, waits for it to connect, introduces itself as an AI, delivers the message, hangs up, saves the
transcript and cleans up the audio devices. Nothing is played on your real speakers. Run:
    .venv/bin/python tests/test_call_flow.py"""
import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["JARVIS_MEMORY_DB"] = str(Path(tempfile.mkdtemp()) / "memory.db")     # never the real memory
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent / "extension"))
import audio  # noqa: E402
import call_agent  # noqa: E402
import call_audio  # noqa: E402
import config  # noqa: E402
import memory  # noqa: E402
from harness import Harness  # noqa: E402
from google import genai  # noqa: E402


async def main() -> int:
    from tts import TTS
    tts = TTS(); tts.voice = tts.k.get_voice_style("af_bella")                   # the 'other person'
    audio.pick_route = lambda *a, **k: SimpleNamespace(echo_possible=True)
    call_audio._pactl("load-module", "module-null-sink", "sink_name=jarvis_test_out")   # stands in for his speakers: stays silent
    real_cls = call_audio.CallAudio
    call_audio.CallAudio = lambda **kw: real_cls(**{**kw, "speaker_sink": "jarvis_test_out", "match": lambda it: (it.get("properties") or {}).get("application.name") == "JarvisTestChrome"})
    told, ui = [], []
    assistant = SimpleNamespace(client=genai.Client(api_key=config.GEMINI_API_KEY), ui=SimpleNamespace(send=lambda **k: ui.append(k)))

    async def notify(n):
        told.append(n)
    assistant.notify = notify
    async with Harness() as bridge:
        print("a Meet meeting is already open and in progress:", await bridge.call("meet_join", url="abc-defg-hij", owner="Aniket"))
        mgr = call_agent.CallManager(assistant)
        t_start = time.monotonic()
        print("start ->", await mgr.start("whatsapp", "Mom", "Tell Mom that Aniket will be home 30 minutes late for dinner, "
                                          "and ask if she needs anything from the shop."))
        # wait for the call to connect (mock page: ringing -> in_call after 1.5 s), then speak as Mom
        t_agent = None
        for _ in range(240):
            if mgr.agent and mgr.agent.session:
                t_agent = time.monotonic() - t_start
                break
            await asyncio.sleep(0.25)
        assert mgr.agent and mgr.agent.session, f"the agent never started (state {mgr.state}, told {told})"
        play = subprocess.Popen(["pacat", f"--device={call_audio.FROM_CALL}", "--rate=24000", "--format=s16le", "--channels=1"], stdin=subprocess.PIPE)

        def mom(text):
            pcm = tts.synth(text)
            for i in range(0, len(pcm), 960):
                play.stdin.write(pcm[i:i + 960]); play.stdin.flush(); time.sleep(0.02)

        async def script():
            await asyncio.sleep(3)
            await asyncio.to_thread(mom, "Hello? Aniket?")
            await asyncio.sleep(7)
            await asyncio.to_thread(mom, "Oh, I see, that's fine. Yes, please ask him to buy some milk on the way. Okay, bye.")
        sp = asyncio.create_task(script())
        try:
            await asyncio.wait_for(asyncio.shield(mgr.task), 100)
        except asyncio.TimeoutError:
            await mgr.end()
            await asyncio.wait_for(mgr.task, 20)
        sp.cancel()
        try:
            play.stdin.close()
        except OSError:
            pass
        play.terminate()
        ag = mgr.agent
        print("---- transcript")
        for w, t in ag.transcript:
            print(f"  {w:>6}: {t}")
        print("notes:", ag.notes, "\nsummary:", ag.summary)
        state_after = await bridge.call("call_state", platform="whatsapp")
        meet_after = await bridge.call("call_state", platform="meet")
        stored = memory.get_research("call")
        left = [ln for ln in call_audio._pactl("list", "short", "modules").splitlines() if "jarvis_to_call" in ln or "jarvis_from_call" in ln]
        said = " ".join(t for w, t in ag.transcript if w == "jarvis").lower()
        first_line = next((t for w, t in ag.transcript if w == "jarvis"), "").lower().strip()
        elapsed = time.monotonic() - t_start
        checks = {
            "greeted by name: 'Hello Mom, this is Jarvis'": first_line.startswith("hello mom") and "this is jarvis" in first_line,
            "did NOT call itself an assistant / say whose": "assistant" not in first_line and "aniket" not in first_line.split("jarvis")[0],
            "did not start talking while it was ringing (waited for the call timer)": t_agent is not None and t_agent >= 2.0,
            "waited for HER hello before saying anything": bool(ag.transcript) and ag.transcript[0][0] == "them",
            "the other meeting was left alone": "phase: in_call" in meet_after,
            "hung up by itself after the talk": "owner ended" not in (ag.summary or "").lower() and elapsed < 80,
            "agent delivered the message (late)": "late" in said or "30" in said,
            "agent heard Mom": any(w == "them" for w, _ in ag.transcript),
            "agent noted something for the owner": bool(ag.notes) or bool(ag.summary),
            "hung up on the page": "phase: none" in state_after or "phase: ended" in state_after,
            "transcript saved to memory": "Transcript" in stored and "Mom" in stored,
            "owner was told how it went": any("[Call finished" in n for n in told),
            "audio devices removed": not left,
            "manager idle again": mgr.state == "idle",
        }
        for k, v in checks.items():
            print(("PASS " if v else "FAIL ") + k)
        if not all(checks.values()):
            print("told:", told, "\nstate_after:", state_after)
        call_audio.CallAudio = real_cls
        for line in call_audio._pactl("list", "short", "modules").splitlines():
            if "jarvis_test_out" in line:
                call_audio._pactl("unload-module", line.split("\t")[0])
        return 0 if all(checks.values()) else 1

sys.exit(asyncio.run(main()))
