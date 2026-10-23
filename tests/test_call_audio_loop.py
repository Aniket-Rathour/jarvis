"""Audio-loop test of the call agent WITHOUT any real call: a synthetic 'other person' is played into the call's incoming
sink, the agent answers into the virtual microphone, and we check what it said. Uses the Gemini key (one short session).
Nothing is played on your real speakers. Run:  .venv/bin/python tests/test_call_audio_loop.py"""
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import call_agent  # noqa: E402
import call_audio  # noqa: E402
import config  # noqa: E402
import browser_bridge  # noqa: E402
from google import genai  # noqa: E402


async def fake_call(cmd, timeout=20, **kw):
    return "phase: in_call; mic: on" if cmd == "call_state" else "ok"


async def main():
    from tts import TTS
    tts = TTS(); tts.voice = tts.k.get_voice_style("am_adam")             # a different voice for 'the other person'
    browser_bridge.bridge.call = fake_call
    notes = []

    async def notify(n):
        notes.append(n)
    assistant = SimpleNamespace(client=genai.Client(api_key=config.GEMINI_API_KEY), ui=SimpleNamespace(send=lambda **k: None), notify=notify)
    audio = call_audio.CallAudio(loop_to_speakers=False)
    audio.setup()
    try:
        agent = call_agent.CallAgent(assistant, "Tell Rahul that Aniket will be 15 minutes late to the project meeting, and ask whether "
                                     "he should bring the printed report.", "Rahul", "WhatsApp", audio)
        spoke = {"peak": 0}
        cap = subprocess.Popen(["parec", f"--device={call_audio.MIC}", "--rate=24000", "--format=s16le", "--channels=1"], stdout=subprocess.PIPE)

        def watch():
            while True:
                b = cap.stdout.read(4800)
                if not b:
                    return
                spoke["peak"] = max(spoke["peak"], int(np.abs(np.frombuffer(b, dtype=np.int16)).max()))
        import threading
        threading.Thread(target=watch, daemon=True).start()

        play = subprocess.Popen(["pacat", f"--device={call_audio.FROM_CALL}", "--rate=24000", "--format=s16le", "--channels=1"], stdin=subprocess.PIPE)

        def say_as_rahul(text):
            pcm = tts.synth(text)
            for i in range(0, len(pcm), 960):                             # real-time pacing, like a real caller
                play.stdin.write(pcm[i:i + 960]); play.stdin.flush(); time.sleep(0.02)

        async def remote():
            await asyncio.sleep(5)
            await asyncio.to_thread(say_as_rahul, "Hello? Who is this?")
            await asyncio.sleep(14)
            await asyncio.to_thread(say_as_rahul, "Oh okay, thanks for telling me. Yes, please bring the printed report.")
        run = asyncio.create_task(agent.run())
        rem = asyncio.create_task(remote())
        try:
            await asyncio.wait_for(asyncio.shield(run), 75)
        except asyncio.TimeoutError:
            agent.end("test timeout")
        summary = await run
        rem.cancel(); play.terminate(); cap.terminate()
        print("---- transcript")
        for w, t in agent.transcript:
            print(f"  {w:>6}: {t}")
        print("notes:", agent.notes, "\nsummary:", summary, f"\nvirtual-mic peak while agent spoke: {spoke['peak']}")
        said = " ".join(t for w, t in agent.transcript if w == "jarvis").lower()
        checks = {"agent spoke into the call": spoke["peak"] > 2000, "introduced itself as an AI assistant": "ai" in said and "jarvis" in said,
                  "stated the mission (late)": "late" in said or "minutes" in said, "heard the other side": any(w == "them" for w, _ in agent.transcript)}
        for k, v in checks.items():
            print(("PASS " if v else "FAIL ") + k)
        return 0 if all(checks.values()) else 1
    finally:
        audio.teardown()

sys.exit(asyncio.run(main()))
