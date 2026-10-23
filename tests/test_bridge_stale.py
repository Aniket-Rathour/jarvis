"""An out-of-date extension must be reported plainly, never as a silent failure. Run: .venv/bin/python tests/test_bridge_stale.py"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

config.BRIDGE_PORT = 8798
import websockets  # noqa: E402
import browser_bridge as bb  # noqa: E402


async def main() -> int:
    seen = []
    bb.bridge.on_stale = lambda have, want: seen.append((have, want))
    await bb.bridge.start()
    async with websockets.connect(f"ws://127.0.0.1:{config.BRIDGE_PORT}", origin=bb.ORIGIN) as ws:
        await ws.send(json.dumps({"hello": "jarvis-hands", "version": "3.1.0"}))     # an old build
        await asyncio.sleep(0.3)

        async def old_extension():
            async for raw in ws:
                m = json.loads(raw)
                await ws.send(json.dumps({"id": m["id"], "ok": False, "error": f"unknown command {m['cmd']}"}))
        task = asyncio.create_task(old_extension())
        out = await bb.bridge.call("call_state")
        task.cancel()
    ok = [("stale detected", bool(seen) and seen[0][0] == "3.1.0"), ("error says to reload", "reload" in out and "out of date" in out),
          ("names both versions", "3.1.0" in out and bb.wanted_version() in out)]
    for k, v in ok:
        print(("PASS " if v else "FAIL ") + k)
    if not all(v for _, v in ok):
        print(out)
    return 0 if all(v for _, v in ok) else 1

sys.exit(asyncio.run(main()))
