"""Popup UI: a tiny local web server + a frameless Chrome app window showing the animated orb.
Only this process talks to it (random port, per-run token, Origin check)."""
import json
import logging
import secrets
import shutil
import subprocess
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.asyncio.server import serve

HTML = Path(__file__).parent / "ui" / "index.html"
logging.getLogger("websockets").setLevel(logging.CRITICAL)   # Chrome opens empty preconnect sockets
PROFILE = Path.home() / ".cache" / "jarvis-ui"


class UI:
    def __init__(self, on_message):
        self.token = secrets.token_urlsafe(16)
        self.on_message = on_message
        self.clients = set()
        self.ever_connected = False
        self.port = 0
        self.history = []          # activity messages replayed to a (re)connecting window
        self.caps = {}             # latest caption per speaker, replayed too
        self.plan_msg = None       # current plan card, replayed too
        self.route_msg = None      # which mic/speaker is in use, replayed too
        self.report_msg = None     # the last routine report card, replayed too
        self.engine_msg = None     # which brain is active, replayed too

    async def start(self):
        self.server = await serve(self._handler, "127.0.0.1", 0, process_request=self._process)
        self.port = self.server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def _process(self, conn, req):
        path, _, query = req.path.partition("?")
        if f"t={self.token}" not in query.split("&"):
            return conn.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        if path == "/ws":
            if req.headers.get("Origin") != f"http://127.0.0.1:{self.port}":
                return conn.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
            return None                       # proceed with websocket handshake
        if path == "/":
            resp = conn.respond(HTTPStatus.OK, HTML.read_text())
            resp.headers["Content-Type"] = "text/html; charset=utf-8"
            return resp
        return conn.respond(HTTPStatus.NOT_FOUND, "not found\n")

    async def _handler(self, ws):
        self.clients.add(ws)
        self.ever_connected = True
        try:
            for who, text in self.caps.items():
                await ws.send(json.dumps({"type": "caption", "who": who, "text": text, "new": True}))
            for m in self.history + [x for x in (self.plan_msg, self.route_msg, self.report_msg, self.engine_msg) if x]:
                await ws.send(m)
            async for raw in ws:
                try:
                    self.on_message(json.loads(raw))
                except ValueError:
                    pass
        finally:
            self.clients.discard(ws)

    def send(self, **msg):
        data = json.dumps(msg)
        if msg.get("type") == "activity":
            self.history = [m for m in self.history if json.loads(m).get("id") != msg.get("id")][-11:] + [data]
        if msg.get("type") == "engine":
            self.engine_msg = data
        if msg.get("type") == "report":
            self.report_msg = data
        if msg.get("type") == "route":
            self.route_msg = data
        if msg.get("type") == "plan":
            self.plan_msg = data
        if msg.get("type") == "caption":
            self.caps[msg["who"]] = ("" if msg.get("new") else self.caps.get(msg["who"], "")) + msg["text"]
        if self.clients:
            websockets.broadcast(self.clients, data)


def open_window(url: str):
    """Chrome --app window with its own profile, so it never touches your normal Chrome windows."""
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
    if not chrome:
        subprocess.Popen(["xdg-open", url], start_new_session=True)
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([chrome, f"--app={url}", "--window-size=440,720", f"--user-data-dir={PROFILE}",
                      "--no-first-run", "--no-default-browser-check", "--class=Jarvis"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
