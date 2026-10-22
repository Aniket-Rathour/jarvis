"""Readable terminal view of a `claude -p --output-format stream-json` run.
Also records progress/result files that Jarvis polls. Usage: claude ... | claude_view.py <task_dir>"""
import json
import os
import re
import select
import shutil
import sys
import textwrap
import time
from pathlib import Path

d = Path(sys.argv[1])
B, DIM, RED, GRN, CYN, MAG, RST = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[36m", "\033[35m", "\033[0m"
MARK = re.compile(r"\[\[STEP (\d+) (DONE|FAILED)\]\]", re.I)
WIDTH = max(40, min(110, shutil.get_terminal_size((100, 30)).columns) - 4)


def short(s, n=90) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def describe_tool(name: str, inp: dict) -> str:
    name = name.split("__")[-1]                 # mcp__claude-in-chrome__navigate -> navigate
    for key in ("command", "file_path", "url", "query", "pattern", "path", "text", "description", "prompt"):
        if isinstance(inp.get(key), str) and inp[key]:
            return f"{name}: {short(inp[key], 80)}"
    return name


def _domain(url: str) -> str:
    m = re.match(r"(?:https?://)?(?:www\.)?([^/\s?#]+)", url or "")
    return m.group(1) if m else (url or "a page")[:40]


def humanize(name: str, inp: dict) -> str:
    """What Claude is doing, as a short phrase Jarvis can say aloud ('searching the web for …')."""
    n = name.split("__")[-1]
    if "notion" in name.lower():
        return "reading your Notion plan" if re.search(r"fetch|search|query|get|read", n) else "updating Notion"
    if n == "ArtifactData":
        return "saving your Night Log entry" if inp.get("action") in ("set", "update", "batch", "delete") \
            else "reading your Night Log"
    base = lambda p: Path(str(p or "")).name or "a file"                                   # noqa: E731
    if n == "WebSearch":
        return f"searching the web for {short(inp.get('query', ''), 60)}"
    if n == "WebFetch":
        return f"reading {_domain(inp.get('url', ''))}"
    if n in ("Read", "NotebookRead"):
        return f"reading {base(inp.get('file_path'))}"
    if n in ("Grep", "Glob", "LS"):
        return "searching through the files"
    if n in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"editing {base(inp.get('file_path') or inp.get('notebook_path'))}"
    if n == "Write":
        return f"writing {base(inp.get('file_path'))}"
    if n == "Bash":
        d, c = (inp.get("description") or "").strip(), inp.get("command", "")
        if d:
            return d[0].lower() + d[1:][:70]
        if re.search(r"\b(pip|npm|pnpm|yarn|dnf|apt|cargo|go) (install|add|get)\b", c):
            return "installing dependencies"
        if re.search(r"\b(pytest|jest|vitest|go test|cargo test|npm (run )?test)\b", c):
            return "running the tests"
        if c.strip().startswith("git "):
            return "working with git"
        return ""                                        # "running a command" says nothing worth announcing
    if n in ("Task", "Agent"):
        return f"researching {short(inp.get('description', 'a sub-question'), 50)}"
    if n == "navigate":
        return f"opening {_domain(inp.get('url', ''))}"
    if n == "computer":
        act = str(inp.get("action", ""))
        return {"screenshot": "looking at the page", "type": "typing", "key": "pressing a key",
                "scroll": "scrolling the page"}.get(act, "clicking through the page")
    if n in ("find", "read_page", "get_page_text"):
        return "reading the page"
    if n == "form_input":
        return "filling in a form"
    if n == "javascript_tool":
        return "checking the page"
    if n.startswith("tabs_"):
        return "switching tabs"
    return ""                                          # ToolSearch, TodoWrite, … internal plumbing: stay quiet


def sentence(text: str) -> str:
    """Claude's own commentary, cut to its first spoken-sized sentence."""
    t = re.sub(r"[`*_#>]+|\[\[STEP.*?\]\]", "", text).strip()
    t = re.sub(r"\s+", " ", t)
    first = re.split(r"(?<=[.!?])\s", t, maxsplit=1)[0]
    return first if 12 <= len(first) <= 130 else ""


def write(name: str, text: str):
    tmp = d / f".{name}.tmp"
    tmp.write_text(text)
    tmp.replace(d / name)


print(f"{MAG}{B}◆ Jarvis → Claude{RST}  {DIM}{short((d / 'prompt.txt').read_text(), WIDTH - 20)}{RST}\n", flush=True)
result, is_error, last_tool, marks = None, True, "", {}
with open(d / "events.jsonl", "w") as log:
    for line in sys.stdin:
        log.write(line)
        log.flush()
        try:
            e = json.loads(line)
        except ValueError:
            print(DIM + line.rstrip() + RST, flush=True)
            continue
        kind = e.get("type")
        if e.get("session_id") and not (d / "session_id").exists():
            write("session_id", e["session_id"])
        if kind == "system" and e.get("subtype") == "init":
            print(f"{DIM}model {e.get('model')} · {e.get('cwd')} · {e.get('permissionMode')}{RST}\n", flush=True)
        elif kind == "assistant":
            for c in e.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    text = c["text"]
                    for n, st in MARK.findall(text):
                        marks[n] = "done" if st.upper() == "DONE" else "failed"
                        sym = f"{GRN}✓" if marks[n] == "done" else f"{RED}✗"
                        print(f"{sym} Step {n} {marks[n]}{RST}", flush=True)
                        write("steps.json", json.dumps(marks))
                    text = MARK.sub("", text).strip()
                    if (line := sentence(text)):
                        write("progress", line[0].lower() + line[1:] if not line[:2].isupper() else line)
                    for para in text.splitlines():
                        print(textwrap.fill(para, WIDTH) if para.strip() else "", flush=True)
                    if text:
                        print()
                elif c.get("type") == "tool_use":
                    last_tool = describe_tool(c.get("name", "tool"), c.get("input") or {})
                    print(f"{CYN}●{RST} {B}{last_tool}{RST}", flush=True)
                    if (phrase := humanize(c.get("name", "tool"), c.get("input") or {})):
                        write("progress", phrase)
        elif kind == "user":
            for c in e.get("message", {}).get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                    color = RED if c.get("is_error") else DIM
                    print(f"  {color}⎿ {short(body or 'ok', WIDTH - 6)}{RST}", flush=True)
        elif kind == "result":
            result = MARK.sub("", e.get("result") or "").strip()
            is_error = bool(e.get("is_error")) or e.get("subtype") != "success"

ok = result is not None and not is_error
write("result.txt", result if result is not None else "Claude stopped without a result (see this terminal).")
write("exitcode", "0" if ok else "1")

if os.environ.get("JARVIS_HEADLESS"):          # no window to keep open
    sys.exit(0)
if ok:
    print(f"\n{GRN}{B}✓ Done.{RST} {DIM}Jarvis has the result. This window closes in 5 s…{RST}", flush=True)
    time.sleep(5)
else:
    print(f"\n{RED}{B}✗ Claude did not finish cleanly.{RST} {DIM}Press Enter to close (auto-closes in 10 min).{RST}",
          flush=True)
    try:
        with open("/dev/tty") as tty:
            select.select([tty], [], [], 600)
    except OSError:
        time.sleep(600)
