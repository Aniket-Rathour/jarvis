"""How fast and how right is a local model at Jarvis' job?  .venv/bin/python tests/bench_local.py MODEL [num_gpu]
Sends realistic requests with Jarvis' real system prompt and compact tool list and reports the time to the tool call
and whether it picked the right tool. Stop Jarvis' Qwen brain first (it uses the same GPU)."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ollama  # noqa: E402

import config  # noqa: E402
import prompt  # noqa: E402
import tools  # noqa: E402

CASES = [  # (what he says, tool that must be called, a check on its arguments)
    ("Open Aryan's chat.", "browser", lambda a: a.get("action") in ("wa_chat", "context")),
    ("Send Mom the message I'll be late.", "browser", lambda a: a.get("action") in ("wa_chat", "context", "wa_send")),
    ("Remember that my favourite editor is Neovim.", "remember", lambda a: "neovim" in json.dumps(a).lower()),
    ("Add buy milk to my to-do list for tomorrow.", "todo", lambda a: a.get("action") == "add"),
    ("What did we find in the keyboard research?", "recall_research", lambda a: True),
    ("Type hello world in the search box and press enter.", "browser", lambda a: a.get("action") in ("type", "context")),
    ("Use opencode to add a login page to my flask app.", "delegate_to_opencode", lambda a: True),
    ("Search my files for the resume pdf.", "search_files", lambda a: "resume" in json.dumps(a).lower()),
]


async def main(model: str, num_gpu: int | None):
    client = ollama.AsyncClient(host=config.OLLAMA_HOST)
    system = prompt.local_system_prompt()
    schemas = tools.ollama_tools()
    opts = {"temperature": 0.3, "num_ctx": config.LOCAL_CTX, "num_predict": 120, **({"num_gpu": num_gpu} if num_gpu else {})}
    print(f"model {model} | ctx {config.LOCAL_CTX} | gpu layers {num_gpu or 'auto'} | {len(schemas)} tools")
    t0 = time.time()
    await client.chat(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": "hello"}],
                      tools=schemas, think=False, keep_alive="5m", options={**opts, "num_predict": 4})
    print(f"load + prompt cache: {time.time() - t0:.1f}s\n")
    good = 0
    for said, want, check in CASES:
        t0 = time.time()
        r = await client.chat(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": said}],
                              tools=schemas, think=False, keep_alive="5m", options=opts)
        dt = time.time() - t0
        calls = r.message.tool_calls or []
        got = calls[0].function.name if calls else None
        args = dict(calls[0].function.arguments) if calls else {}
        ok = got == want and check(args)
        good += ok
        n = getattr(r, "eval_count", 0) or 0
        print(f"{'PASS' if ok else 'FAIL'} {dt:5.1f}s ({n} tok, {n / max(dt, .1):.1f}/s)  {said!r}\n        -> {got} {json.dumps(args)[:110]}"
              + ("" if calls else f"  SAID: {(r.message.content or '')[:80]!r}"))
    print(f"\n{good}/{len(CASES)} correct")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None))
