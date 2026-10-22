"""Hedged Gemini calls: the free tier often answers 503/429 on the best models, so start the best one and,
if it is slow or fails, start the next in parallel. The first good answer wins; the rest are cancelled."""
import asyncio
import logging

from google.genai import types

log = logging.getLogger("jarvis.llm")


async def hedged(client, models: list[str], contents, cfg: types.GenerateContentConfig | None = None,
                 hedge_s: float = 6.0, timeout: float = 45.0, on_try=None) -> tuple[str, str]:
    """Returns (model, text). Raises RuntimeError with every model's error if all of them fail."""
    async def ask(model: str):
        r = await asyncio.wait_for(client.aio.models.generate_content(model=model, contents=contents, config=cfg),
                                   timeout)
        if not r.text:
            raise RuntimeError("empty answer")
        return model, r.text

    pending: set = set()
    errors: list[str] = []
    todo = list(models)
    try:
        while todo or pending:
            if todo:
                model = todo.pop(0)
                if on_try:
                    on_try(model)
                pending.add(asyncio.create_task(ask(model)))
            done, pending = await asyncio.wait(pending, timeout=hedge_s if todo else None,
                                               return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                try:
                    return d.result()
                except Exception as e:                  # overloaded / quota / timeout: the others keep going
                    errors.append(str(e)[:80])
    finally:
        for p in pending:
            p.cancel()
    raise RuntimeError("; ".join(errors)[:300] or "no models configured")
