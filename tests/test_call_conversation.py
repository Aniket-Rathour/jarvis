"""A natural phone conversation with the call agent (real Gemini Live session, synthetic caller, no browser, nothing audible):
  - the caller says hello; Jarvis answers; the caller INTERRUPTS him ("Sorry, who is this?") -> he must stop and answer that
  - the caller pauses 2.5 s mid-sentence -> Jarvis must NOT talk in the pause, then must answer the whole sentence
  - the caller says bye -> Jarvis says goodbye and hangs up.
Run: .venv/bin/python tests/test_call_conversation.py"""
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
import browser_bridge  # noqa: E402
import call_agent  # noqa: E402
import call_audio as ca  # noqa: E402
import config  # noqa: E402
from google import genai  # noqa: E402


async def fake_call(cmd, timeout=20, **kw):
    return "phase: in_call; mic: on; platform: whatsapp; timer: 5"


class JarvisVoice:
    """Records when Jarvis is audibly speaking into the call (the virtual microphone)."""

    def __init__(self):
        self.frames: list[tuple[float, float]] = []
        self.p = subprocess.Popen(["parec", f"--device={ca.MIC}", "--rate=16000", "--format=s16le", "--channels=1", "--latency-msec=20"],
                                  stdout=subprocess.PIPE)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            b = self.p.stdout.read(1280)
            if not b:
                return
            x = np.frombuffer(b, dtype=np.int16).astype(np.float32)
            self.frames.append((time.monotonic(), float(np.sqrt(np.mean(x ** 2)))))

    def speaking_between(self, t0: float, t1: float) -> float:
        """Seconds of Jarvis speech in [t0, t1]."""
        return sum(0.04 for t, r in self.frames if t0 <= t <= t1 and r > 400)

    def last_speech(self) -> float:
        return max((t for t, r in self.frames if r > 400), default=0.0)

    async def wait_start(self, after: float, timeout: float = 20) -> float | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            hit = [t for t, r in self.frames if t > after and r > 400]
            if hit:
                return hit[0]
            await asyncio.sleep(0.05)
        return None

    async def wait_quiet(self, sec: float = 1.2, timeout: float = 25):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if time.monotonic() - self.last_speech() > sec and self.frames:
                return
            await asyncio.sleep(0.05)


async def main() -> int:
    from tts import TTS
    tts = TTS(); tts.voice = tts.k.get_voice_style("af_bella")
    browser_bridge.bridge.call = fake_call
    told = []

    async def notify(n):
        told.append(n)
    assistant = SimpleNamespace(client=genai.Client(api_key=config.GEMINI_API_KEY), ui=SimpleNamespace(send=lambda **k: None), notify=notify)
    audio = ca.CallAudio(loop_to_speakers=False, match=lambda it: False)
    audio.setup()
    play = subprocess.Popen(["pacat", f"--device={ca.FROM_CALL}", "--rate=24000", "--format=s16le", "--channels=1", "--latency-msec=20"], stdin=subprocess.PIPE)
    voice = JarvisVoice()
    pre = {t: tts.synth(t) for t in ["Hello?", "Sorry, who is this?", "Oh okay. So I was thinking", "he could also pick up some vegetables on the way home.", "Okay, bye bye."]}

    def say(text):
        pcm = pre[text]
        for i in range(0, len(pcm), 960):
            play.stdin.write(pcm[i:i + 960]); play.stdin.flush(); time.sleep(0.02)
        return time.monotonic()

    def pause(sec):
        for _ in range(int(sec / 0.02)):
            play.stdin.write(b"\0" * 960); play.stdin.flush(); time.sleep(0.02)
    results = {}
    try:
        agent = call_agent.CallAgent(assistant, "Tell Mom that Aniket will be home 30 minutes late for dinner, and ask if she needs "
                                     "anything from the shop.", "Mom", "whatsapp", audio)
        run = asyncio.create_task(agent.run())
        await asyncio.sleep(3.0)
        t_hello = await asyncio.to_thread(say, "Hello?")
        t_j1 = await voice.wait_start(t_hello)
        print(f"Jarvis started {t_j1 - t_hello:.1f}s after her hello" if t_j1 else "Jarvis never answered her hello")
        results["answered her hello"] = t_j1 is not None
        # interrupt him 1.2 s into his opening
        await asyncio.sleep(1.2)
        t_int0 = time.monotonic()
        await asyncio.to_thread(say, "Sorry, who is this?")
        t_int1 = time.monotonic()
        # he must have gone silent shortly after she started, and stayed silent while she talked
        talk_during = voice.speaking_between(t_int0 + 1.6, t_int1)
        print(f"Jarvis speech while she interrupted (after the first 1.6 s): {talk_during:.2f}s")
        results["stopped talking when she interrupted"] = talk_during < 0.3
        t_ans = await voice.wait_start(t_int1)
        results["then answered her question"] = t_ans is not None
        await voice.wait_quiet(1.5)
        # mid-sentence pause
        t_a = await asyncio.to_thread(say, "Oh okay. So I was thinking")
        await asyncio.to_thread(pause, 2.5)
        t_b0 = time.monotonic()
        await asyncio.to_thread(say, "he could also pick up some vegetables on the way home.")
        in_pause = voice.speaking_between(t_a + 0.2, t_b0)
        print(f"Jarvis speech during her 2.5 s pause: {in_pause:.2f}s")
        results["did NOT talk over her mid-sentence pause"] = in_pause < 0.2
        t_rep = await voice.wait_start(time.monotonic() - 0.05)
        results["answered once she really finished"] = t_rep is not None
        await voice.wait_quiet(1.5)
        await asyncio.to_thread(say, "Okay, bye bye.")
        try:
            await asyncio.wait_for(asyncio.shield(run), 25)
        except asyncio.TimeoutError:
            agent.end("test timeout")
        summary = await run
        print("---- transcript")
        for w, t in agent.transcript:
            print(f"  {w:>6}: {t}")
        print("notes:", agent.notes, "\nsummary:", summary)
        said = " ".join(t for w, t in agent.transcript if w == "jarvis").lower()
        results["understood the whole sentence (vegetables)"] = "vegetable" in said or any("vegetable" in n.lower() for n in agent.notes) or "vegetable" in summary.lower()
        results["hung up by itself after 'bye'"] = "test timeout" not in summary
    finally:
        play.terminate(); voice.p.terminate()
        audio.teardown()
    for k, v in results.items():
        print(("PASS " if v else "FAIL ") + k)
    return 0 if all(results.values()) else 1

sys.exit(asyncio.run(main()))
