"""Turn-taking on calls, in real time with synthetic speech (no network): a caller who pauses mid-sentence must not be
talked over, a finished question gets a quick answer, an 'mm-hm' doesn't cut Jarvis off, a real interruption does.
Run: .venv/bin/python tests/test_turn_taking.py"""
import sys
import time
from pathlib import Path

import logging

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import turn_taking as tt  # noqa: E402
from tts import TTS  # noqa: E402

logging.basicConfig(level=logging.INFO, format='   %(name)s: %(message)s')
tts = TTS()
tts.voice = tts.k.get_voice_style("af_bella")
rng = np.random.default_rng(0)


def speech(text):
    x = np.frombuffer(tts.synth(text), dtype=np.int16).astype(np.float32)
    n = int(len(x) * 16000 / 24000)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)


def silence(sec):
    return (rng.normal(0, 40, int(16000 * sec))).astype(np.int16)        # a quiet line, not digital zero


def play(tk, parts, jarvis_speaking=False):
    """Feed audio in real time; returns [(seconds, event)] and the time each part ended."""
    audio = [speech(p) if isinstance(p, str) else silence(p) for p in parts]     # synthesise first: timing must be real
    events, marks, t0 = [], [], time.monotonic()
    for x in audio:
        for i in range(0, len(x), 640):
            for e in tk.on_frame(x[i:i + 640].tobytes(), jarvis_speaking):
                if e != "audio":
                    events.append((round(time.monotonic() - t0, 2), e))
            time.sleep(0.04)
        marks.append(round(time.monotonic() - t0, 2))
    return events, marks


rec = tt.Recogniser.shared()
for _ in range(100):
    if rec.model:
        break
    time.sleep(0.2)
assert rec.model, "recogniser did not load"
results = {}

# A. someone who pauses for 2.5 s mid-thought
tk = tt.TurnTaker(rec)
ev, marks = play(tk, [0.5, "I think we should", 2.5, "move the meeting to Friday at five.", 2.5])
ends = [t for t, e in ev if e == "end"]
print("A pausing caller:", ev, "| part ends:", marks)
results["no turn end during the 2.5 s mid-sentence pause"] = len(ends) == 1 and ends[0] > marks[3]
results["turn ends soon after they finish (< 1.8 s)"] = bool(ends) and ends[-1] - marks[3] < 1.8

# B. a finished question gets a fast reply
tk = tt.TurnTaker(rec)
ev, marks = play(tk, [0.5, "Can you hear me?", 2.0])
ends = [t for t, e in ev if e == "end"]
print("B question:", ev, "| part ends:", marks, "| turn over", round(ends[0] - marks[1], 2) if ends else None, "s after she stopped")
results["question: turn over within ~1.2 s"] = bool(ends) and ends[0] - marks[1] < 1.25

# C. while Jarvis talks: 'mm-hm' must not interrupt, a real sentence must
tk = tt.TurnTaker(rec)
ev, _ = play(tk, [0.4, "Mm-hm.", 1.5], jarvis_speaking=True)
print("C backchannel:", ev)
results["a short 'mm-hm' only pauses Jarvis, then he carries on"] = (not any(e == "barge" for _, e in ev)
                                                                    and [e for _, e in ev] == ["duck", "unduck"])
tk = tt.TurnTaker(rec)
ev, marks = play(tk, [0.4, "Wait, wait, sorry, can I say something?", 1.0], jarvis_speaking=True)
barge = [t for t, e in ev if e == "barge"]
print("C interruption:", ev)
duck = [t for t, e in ev if e == "duck"]
results["a real interruption pauses Jarvis at once (< 0.5 s)"] = bool(duck) and duck[0] - 0.4 < 0.5
results["...and stops him for good within ~1.6 s"] = bool(barge) and barge[0] - 0.4 < 1.65

for k, v in results.items():
    print(("PASS " if v else "FAIL ") + k)
sys.exit(0 if all(results.values()) else 1)
