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
ACTIONS = ("tabs", "open", "close", "switch", "navigate", "back", "forward", "reload", "read", "click", "type",
           "key", "scroll", "find", "wait")


class Bridge:
    def __init__(self):
        self.ws = None
        self.pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._server = None

    @property
    def connected(self) -> bool:
        return self.ws is not None

    async def start(self):
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
                elif "id" in msg and (fut := self.pending.pop(msg["id"], None)) and not fut.done():
                    fut.set_result(msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            if self.ws is ws:
                self.ws = None
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
