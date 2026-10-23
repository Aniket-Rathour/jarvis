"""End-to-end test of the Jarvis Hands extension, fully isolated from your real Chrome and your running Jarvis:
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
            if self.path in ("/", ""):
                host = self.headers.get("Host", "")
                self.path = "/wa.html" if host.startswith("web.whatsapp") else "/complex.html" if host.startswith("complex") else "/form.html"
            super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", HTTPS_PORT), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp / "c.pem", tmp / "k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# (label, command, args, text that must appear in the answer — or "ERR:" + text for an expected error)
SCENARIO = [
    ("open WhatsApp by app name", "open", {"url": "WhatsApp"}, "WhatsApp"),
    ("reuse: whatsapp.com", "open", {"url": "whatsapp.com"}, "already open"),
    ("reuse: full URL", "open", {"url": "https://web.whatsapp.com"}, "already open"),
    ("bad address refused", "open", {"url": "nonsense:"}, "ERR:isn't a web address"),
    ("context names the app", "context", {}, "<WhatsApp>"),
    ("open a chat by name", "wa_chat", {"name": "Aryan Dce"}, 'chat "Aryan Dce Cse-1"'),
    ("message box, not search box", "type", {"field": "message", "text": "draft"}, 'textbox "Type a message"'),
    ("search box when asked", "type", {"field": "search", "text": "Mom"}, 'textbox "Search or start a new chat"'),
    ("switch chat", "wa_chat", {"name": "Mom"}, 'chat "Mom"'),
    ("send in the open chat", "wa_send", {"text": "on my way"}, "sent to Mom"),
    ("form page in a new tab", "open", {"url": "https://testsite.local/", "new": True}, "Test page"),
    ("ambiguous typing refused", "type", {"text": "oops"}, "ERR:Say which one"),
    ("password never typed", "type", {"field": "password", "text": "x"}, "ERR:never type those"),
    ("password value hidden", "read", {}, "secret field"),
    ("batch in one call", "batch", {"steps": [json.dumps({"do": "type", "field": "your name", "text": "Aniket"}),
                                              json.dumps({"do": "click", "text": "Do thing"})]}, "2. click: clicked"),
    ("type + Enter submits", "type", {"field": "search", "text": "jarvis", "submit": True}, "result.html?q=jarvis"),
    ("back after a scripted navigation", "back", {}, "went back"),
    ("forward", "forward", {}, "went forward"),
    ("complex page", "open", {"url": "https://complex.local/", "new": True}, "Complex"),
    ("sees the selection", "selected", {}, "Select this sentence"),
    ("inside shadow DOM", "click", {"text": "Shadow Btn"}, 'button "Shadow Btn"'),
    ("inside an iframe", "click", {"text": "Frame Button"}, "Frame clicked"),
    ("nth match", "click", {"text": "Reply", "nth": 2}, "Replied B"),
    ("dropdown", "type", {"field": "country", "text": "Brazil"}, '"Brazil"'),
    ("checkbox on", "check", {"text": "Subscribe to news", "on": True}, "now checked"),
    ("checkbox off", "check", {"text": "Accept ads", "on": False}, "now unchecked"),
    ("whole form", "fill", {"fields": ["Country=Germany", "Notes editor=hello"]}, "Country: set; Notes editor: set"),
    ("CSS hover menu", "hover", {"text": "Menu"}, "hovering"),
    ("item revealed by hover", "click", {"text": "Settings"}, 'menuitem "Settings"'),
    ("page that needs a real click", "click", {"text": "Trusted Only"}, "real mouse click"),
    ("drag and drop", "drag", {"text": "Drag me", "to": "Drop here"}, "onto"),
    ("waits for a late button", "click", {"text": "Late Button"}, "Late Button"),
    ("video pause", "media", {"action": "pause"}, "paused"),
    ("Enter in a text area is a newline", "key", {"key": "Enter"}, "pressed Enter"),
    ("link opening a new tab", "click", {"text": "Open in new tab"}, "opened a new tab"),
    ("pin", "pin", {}, "pinned"),
    ("zoom", "zoom", {"value": "in"}, "zoom is 110%"),
    ("history", "history", {"text": "complex"}, "complex.local"),
    ("close every matching tab", "close", {"tab": "result", "all": True}, "closed"),
    ("reopen it", "reopen", {}, "reopened"),
]


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="jarvis-ext-test-"))
    ext = tmp / "ext"
    shutil.copytree(ROOT / "chrome_extension", ext)
    bg = ext / "background.js"
    bg.write_text(bg.read_text().replace("127.0.0.1:8765", f"127.0.0.1:{config.BRIDGE_PORT}"))
    srv = serve(tmp)
    await bridge.start()
    chrome = subprocess.Popen(
        ["google-chrome", "--headless=new", "--no-sandbox", f"--user-data-dir={tmp / 'profile'}", "--remote-debugging-port=9333",
         "--enable-unsafe-extension-debugging", "--ignore-certificate-errors", "--window-size=1200,800",
         f"--host-resolver-rules=MAP web.whatsapp.com 127.0.0.1:{HTTPS_PORT}, MAP testsite.local 127.0.0.1:{HTTPS_PORT}, "
         f"MAP complex.local 127.0.0.1:{HTTPS_PORT}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failed = 0
    try:
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
        t_all = time.time()
        for label, cmd, args, want in SCENARIO:
            t0 = time.time()
            out = await bridge.call(cmd, timeout=40, **args)
            ms = (time.time() - t0) * 1000
            ok = out.startswith("error") and want[4:] in out if want.startswith("ERR:") else (not out.startswith("error") and want in out)
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'} {ms:6.0f} ms  {label}" + ("" if ok else f"\n      expected {want!r}\n      got {out[:200]!r}"))
        print(f"\n{len(SCENARIO) - failed}/{len(SCENARIO)} passed in {time.time() - t_all:.1f}s")
    finally:
        chrome.terminate()
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
