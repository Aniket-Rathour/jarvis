"""Shared harness: an isolated Chrome + the extension on port 8799 + mock pages (a copy of the extension, a throwaway
profile, HTTPS mock sites for WhatsApp Web, Google Meet and test pages). Used by run.py and the call tests.
Original description of the suite: end-to-end test of the Jarvis Hands extension, fully isolated from your real Chrome and your running Jarvis:
a throwaway headless Chrome profile, a copy of the extension pointed at port 8799, and mock pages (including a
WhatsApp-like page) served over HTTPS on 127.0.0.1. Run:  .venv/bin/python tests/extension/run.py"""
import asyncio
import http.server
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

config.BRIDGE_PORT = 8799                       # never the real 8765 your Chrome connects to
import websockets  # noqa: E402
from browser_bridge import bridge  # noqa: E402

PAGES = Path(__file__).parent / "pages"
HTTPS_PORT = 8443


def serve(tmp: Path):
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", tmp / "k.pem", "-out", tmp / "c.pem",
                    "-days", "1", "-subj", "/CN=test"], capture_output=True, check=True)

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(PAGES), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            host = self.headers.get("Host", "")
            if host.startswith("meet.google"):
                self.path = "/meet.html"                  # any Meet link shows the mock pre-join page
            elif self.path in ("/", ""):
                self.path = "/wa.html" if host.startswith("web.whatsapp") else "/meet.html" if host.startswith("meet.google") else "/complex.html" if host.startswith("complex") else "/form.html"
            super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", HTTPS_PORT), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp / "c.pem", tmp / "k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv




class Harness:
    """async with Harness() as bridge:  ->  the extension is connected to `bridge` (port 8799)."""

    async def __aenter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-ext-test-"))
        ext = self.tmp / "ext"
        shutil.copytree(ROOT / "chrome_extension", ext)
        bg = ext / "background.js"
        bg.write_text(bg.read_text().replace("127.0.0.1:8765", f"127.0.0.1:{config.BRIDGE_PORT}"))
        self.srv = serve(self.tmp)
        await bridge.start()
        self.chrome = subprocess.Popen(
            ["google-chrome", "--headless=new", "--no-sandbox", f"--user-data-dir={self.tmp / 'profile'}", "--remote-debugging-port=9333",
             "--enable-unsafe-extension-debugging", "--ignore-certificate-errors", "--window-size=1200,800",
             f"--host-resolver-rules=MAP web.whatsapp.com 127.0.0.1:{HTTPS_PORT}, MAP meet.google.com 127.0.0.1:{HTTPS_PORT}, "
             f"MAP testsite.local 127.0.0.1:{HTTPS_PORT}, MAP complex.local 127.0.0.1:{HTTPS_PORT}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                v = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json/version", timeout=1))
                break
            except OSError:
                await asyncio.sleep(0.25)
        async with websockets.connect(v["webSocketDebuggerUrl"], max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Extensions.loadUnpacked", "params": {"path": str(ext)}}))
            await ws.recv()
        for _ in range(60):
            if bridge.connected:
                break
            await asyncio.sleep(0.25)
        assert bridge.connected, "the extension never connected"
        return bridge

    async def __aexit__(self, *exc):
        self.chrome.terminate()
        self.srv.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)
