"""Reads `opencode run` output on stdin: shows it, keeps the last meaningful line as the live progress phrase
(Jarvis narrates it aloud) and the full text as the result. Usage: opencode run ... | python oc_view.py <task_dir>"""
import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
d = Path(sys.argv[1])
progress, result = d / "progress", d / "result.txt"
lines = []
for raw in sys.stdin:
    print(raw, end="", flush=True)
    line = ANSI.sub("", raw).strip()
    if not line:
        continue
    lines.append(line)
    # OpenCode prefixes tool use with a marker such as "→ Read file" / "$ cmd" / "✱ Glob"; any short line is fine
    progress.write_text(line[:110])
result.write_text("\n".join(lines[-60:]))
