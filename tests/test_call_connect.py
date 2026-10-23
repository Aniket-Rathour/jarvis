"""When does a call count as connected? Ringing (and the ring tone) must NEVER count; the call timer / Leave button must.
Run: .venv/bin/python tests/test_call_connect.py"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import call_agent  # noqa: E402


SEEN = []


def stub(states):
    it = iter(states)
    calls = {"n": 0}

    async def call(cmd, timeout=20, **kw):
        SEEN.append((cmd, kw))
        calls["n"] += 1
        return next(it, states[-1])
    return SimpleNamespace(call=call), calls


async def run(states, platform="whatsapp", seconds=40):
    real_sleep = asyncio.sleep
    asyncio.sleep = lambda s, *a, **k: real_sleep(0)           # no waiting in the test
    try:
        b, calls = stub(states)
        mgr = call_agent.CallManager(SimpleNamespace())
        mgr.scope = {"platform": platform}
        return await mgr._wait_connected(b, seconds, platform), calls["n"]
    finally:
        asyncio.sleep = real_sleep


async def main():
    ring = "phase: ringing; mic: on; platform: whatsapp; timer: -1; ui: ringing end call"
    live = "phase: in_call; mic: on; platform: whatsapp; timer: 3; ui: 00:03 end call"
    conn = "phase: connecting; mic: on; platform: whatsapp; timer: -1; ui: end call mute"
    r1, n1 = await run([ring] * 12 + [live] * 3)
    r2, n2 = await run([live])
    r3, n3 = await run([ring] * 6 + [conn] * 20)
    r4, _ = await run([ring] * 50, seconds=20)
    r5, _ = await run(["phase: prejoin; mic: off; platform: meet"] * 3 + ["phase: in_call; mic: on; platform: meet; timer: -1"] * 3, "meet")
    r6, _ = await run(["phase: none"], seconds=5)
    checks = {
        "every call_state carries the call's own platform (another meeting can't be mistaken for it)":
            bool(SEEN) and all(kw.get("platform") for cmd, kw in SEEN if cmd == "call_state"),
        "ringing never counts as connected (waited for the timer)": r1 == "in_call" and n1 >= 14,
        "a single in_call reading is not trusted yet": n2 >= 2,
        "a layout with no timer and no 'ringing' is accepted after ~8 s": r3 == "in_call" and 8 <= n3 - 6 <= 11,
        "an unanswered call stays 'ringing'": r4 == "ringing",
        "Meet: in the lobby is not connected, Leave button is": r5 == "in_call",
        "no call page at all": r6 == "none",
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    return 0 if all(checks.values()) else 1

sys.exit(asyncio.run(main()))
