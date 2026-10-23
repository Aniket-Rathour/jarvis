"""Local websocket server the Jarvis Hands Chrome extension connects to. Only that extension's origin is accepted
(a web page can't open this socket). call() sends one command and waits for the extension's reply."""
import asyncio
import itertools
import json
import logging

import websockets
from websockets.asyncio.server import serve

import config

log = logging.getLogger("jarvis.bridge")
EXTENSION_ID = "acgjeghbhoabchdclfeckhokjjaikoaa"        # fixed by the "key" in chrome_extension/manifest.json
ORIGIN = f"chrome-extension://{EXTENSION_ID}"
ACTIONS = ("context", "tabs", "read", "text", "selected", "links", "find", "open", "new_window", "close", "switch", "navigate",
           "back", "forward", "reload", "pin", "unpin", "mute", "unmute", "duplicate", "reopen", "zoom", "group", "history",
           "bookmark", "click", "hover", "drag", "check", "type", "clear", "fill", "key", "scroll", "wait_for", "media", "batch",
           "wa_chat", "wa_send", "wait")


class Bridge:
    def __init__(self):
        self.ws = None
        self.pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._server = None
        self.active: dict = {}          # the tab he is looking at: {id, title, url, app}, pushed by the extension
        self.loop = None
        self.on_change = None           # called with the new screen note whenever what he looks at changes

    @property
    def connected(self) -> bool:
        return self.ws is not None

    def screen_note(self) -> str:
        """One line for the models: what is on his screen right now (empty when Chrome isn't connected)."""
        a = self.active
        if not (self.ws and a):
            return ""
        note = f"{a.get('app') or a.get('url', '')[:50]} - {a.get('title', '')[:60]}".strip(" -")
        return note + (f" (chat with {a['chat']} is open)" if a.get("chat") else "")

    async def start(self):
        self.loop = asyncio.get_running_loop()
        try:
            self._server = await serve(self._handler, "127.0.0.1", config.BRIDGE_PORT, origins=[ORIGIN])
            log.info("browser bridge on 127.0.0.1:%s", config.BRIDGE_PORT)
        except OSError as e:
            log.error("browser bridge not started (%s)", e)

    async def _handler(self, ws):
        self.ws = ws
        log.info("Chrome extension connected")
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if "ping" in msg:
                    await ws.send('{"pong":1}')
                elif "state" in msg:
                    self.active = msg["state"]
                    if self.on_change:
                        try:
                            self.on_change(self.screen_note())
                        except Exception:
                            log.exception("screen change handler")
                    try:
                        import memory
                        if self.active.get("app"):
                            memory.set_focus("on screen", self.screen_note())
                    except Exception:
                        pass
                elif "id" in msg and (fut := self.pending.pop(msg["id"], None)) and not fut.done():
                    fut.set_result(msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            if self.ws is ws:
                self.ws = None
                self.active = {}
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Chrome disconnected"))
            self.pending.clear()
            log.info("Chrome extension disconnected")

    async def call(self, cmd: str, timeout: float = 20, **args) -> str:
        if not self.ws:
            return ("error: the Chrome extension isn't connected. Open Chrome with the Jarvis Hands extension loaded "
                    "(chrome://extensions, Developer mode, Load unpacked, the chrome_extension folder).")
        i = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[i] = fut
        try:
            await self.ws.send(json.dumps({"id": i, "cmd": cmd, **{k: v for k, v in args.items() if v is not None}}))
            r = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(i, None)
            return f"error: Chrome took more than {timeout:.0f}s to {cmd}"
        except (ConnectionError, websockets.ConnectionClosed) as e:
            return f"error: {e}"
        return str(r.get("result")) if r.get("ok") else f"error: {r.get('error')}"


bridge = Bridge()


def open_in_chrome(url: str, new: bool = False) -> str | None:
    """For desktop.py (runs in a worker thread): open a URL through the extension so an already open tab is reused.
    Returns the extension's answer, or None when the extension isn't connected (the caller then launches Chrome)."""
    if not bridge.connected or bridge.loop is None:
        return None
    fut = asyncio.run_coroutine_threadsafe(bridge.call("open", timeout=15, url=url, new=True if new else None), bridge.loop)
    try:
        out = fut.result(timeout=18)
    except Exception:
        return None
    return None if out.startswith("error") else out
