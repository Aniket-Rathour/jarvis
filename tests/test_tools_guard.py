"""The repeat guard: looking at the page again (read/scroll/...) is never a 'loop'; the same click three times is.
Run: .venv/bin/python tests/test_tools_guard.py"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tools  # noqa: E402


async def main():
    tb = tools.Toolbox(SimpleNamespace(jobs=SimpleNamespace(find_running=lambda k: None), ui=SimpleNamespace(send=lambda **k: None)))
    tb.utterance = 1
    reads = [await tb.execute("browser", {"action": "read"}, None, 0) for _ in range(5)]
    scrolls = [await tb.execute("browser", {"action": "scroll", "direction": "down"}, None, 0) for _ in range(4)]
    clicks = [await tb.execute("browser", {"action": "click", "text": "Send"}, None, 0) for _ in range(3)]
    checks = {
        "reading the page 5 times in one request is allowed": not any(str(r).startswith("LOOP") for r in reads),
        "scrolling down 4 times is allowed": not any(str(r).startswith("LOOP") for r in scrolls),
        "the same click is allowed twice (a retry)": not any(str(r).startswith("LOOP") for r in clicks[:2]),
        "a third identical click is stopped": str(clicks[2]).startswith("LOOP"),
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    return 0 if all(checks.values()) else 1

sys.exit(asyncio.run(main()))
