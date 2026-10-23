"""Main voice conversation with smart turns: mic audio -> turn-taker -> what is sent to Gemini (activity markers + audio),
and Jarvis' speaker is paused/stopped when talked over. Real time, synthetic speech, no network.
Run: .venv/bin/python tests/test_main_turns.py"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import logging

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import brain_gemini  # noqa: E402
import config  # noqa: E402
from tts import TTS  # noqa: E402
from turn_taking import Recogniser  # noqa: E402

logging.basicConfig(level=logging.INFO, format='   %(name)s: %(message)s')
logging.getLogger('faster_whisper').setLevel(logging.ERROR)
tts = TTS(); tts.voice = tts.k.get_voice_style("am_adam")


def speech(text):
    x = np.frombuffer(tts.synth(text), dtype=np.int16).astype(np.float32)
    return np.interp(np.linspace(0, len(x) - 1, int(len(x) * 16000 / 24000)), np.arange(len(x)), x).astype(np.int16)


def quiet(sec):
    return np.random.default_rng(1).normal(0, 40, int(16000 * sec)).astype(np.int16)


class FakeSession:
    def __init__(self):
        self.log = []

    async def send_realtime_input(self, audio=None, activity_start=None, activity_end=None, **k):
        t = time.monotonic()
        if activity_start is not None:
            self.log.append((t, "START"))
        elif activity_end is not None:
            self.log.append((t, "END"))
        elif audio is not None:
            self.log.append((t, "audio"))


async def main():
    assert config.SMART_TURNS
    rec = Recogniser.shared()
    while rec.model is None:
        await asyncio.sleep(0.2)
    loop = asyncio.get_running_loop()
    held, stopped = [], []
    spk = SimpleNamespace(hold=lambda on: held.append((time.monotonic(), on)), playing=False)
    speaking = {"v": False}
    a = SimpleNamespace(loop=loop, speaker=spk, speaking=lambda: speaking["v"], stop_speaking=lambda: stopped.append(time.monotonic()))
    b = brain_gemini.GeminiBrain(a)
    b.turns.rec = rec
    sess = FakeSession()
    sender = asyncio.create_task(b._send_mic(sess))

    async def say(parts, jarvis=False):
        speaking["v"] = jarvis
        audio = [speech(p) if isinstance(p, str) else quiet(p) for p in parts]
        marks = []
        for x in audio:
            pcm = x.tobytes()
            for i in range(0, len(pcm), 1024):                      # the mic delivers 32 ms chunks
                b.feed_audio([pcm[i:i + 1024]], True, time.monotonic())
                await asyncio.sleep(0.032)
            marks.append(time.monotonic())
        return marks
    res = {}
    m = await say([0.4, "Send Mom a message saying", 2.4, "I will be late for dinner.", 2.0])
    starts = [t for t, e in sess.log if e == "START"]; ends = [t for t, e in sess.log if e == "END"]
    res["one turn for a sentence with a 2.4 s pause in it"] = len(starts) == 1 and len(ends) == 1 and ends[0] > m[3]
    res["the turn ends soon after he finishes (< 1.6 s)"] = bool(ends) and ends[0] - m[3] < 1.6
    res["audio is sent only inside the turn"] = all(starts[0] <= t <= ends[0] + 0.05 for t, e in sess.log if e == "audio") if starts and ends else False
    sess.log.clear()
    m = await say([0.3, "Open YouTube.", 1.5])
    ends = [t for t, e in sess.log if e == "END"]
    print("command: turn ended", round(ends[0] - m[1], 2) if ends else None, "s after he stopped; verdict:", b.turns.verdict, repr(b.turns.verdict_text))
    res["a short command ends quickly (< 1.0 s)"] = bool(ends) and ends[0] - m[1] < 1.0
    sess.log.clear(); held.clear()
    await say([0.3, "Mm-hm.", 1.5], jarvis=True)
    print("mm-hm: held", [on for _, on in held], "stopped", bool(stopped), "check", b.turns.duck_check)
    res["'mm-hm' while Jarvis talks: paused then resumed, not stopped"] = [on for _, on in held] == [True, False] and not stopped
    held.clear()
    await say([0.3, "No no, wait, I meant the other one.", 1.5], jarvis=True)
    res["a real interruption stops Jarvis"] = bool(stopped)
    sender.cancel()
    for k, v in res.items():
        print(("PASS " if v else "FAIL ") + k)
    return 0 if all(res.values()) else 1

sys.exit(asyncio.run(main()))
