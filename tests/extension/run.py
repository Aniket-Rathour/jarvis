"""End-to-end test of the Jarvis Hands extension, fully isolated from your real Chrome and your running Jarvis
(see harness.py). Run:  .venv/bin/python tests/extension/run.py"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from harness import Harness  # noqa: E402

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
    ("meeting link found in the open chat", "chat_links", {}, "meet.google.com/xyz-abcd-efg"),
    ("a group call is running in this chat", "wa_chat", {"name": "Rahul"}, 'chat "Rahul Sharma"'),
    ("it is detected as joinable", "call_state", {}, "phase: joinable"),
    ("join the running call", "wa_join_call", {}, "joining"),
    ("wait for it to connect", "wait", {"ms": 1700}, "waited"),
    ("now in the call", "call_state", {}, "phase: in_call"),
    ("isolate for take-over", "call_isolate", {}, "call tab"),
    ("release the other tabs", "call_release", {}, "unmuted"),
    ("hang up the joined call", "call_end", {}, "done"),
    ("back to Mom's chat", "wa_chat", {"name": "Mom"}, 'chat "Mom"'),
    ("no call yet", "call_state", {}, "phase: none"),
    ("place a WhatsApp call", "call_start", {}, "call started"),
    ("it is ringing", "call_state", {}, "phase: ringing"),
    ("let it connect", "wait", {"ms": 1900}, "waited"),
    ("call connected", "call_state", {}, "phase: in_call"),
    ("mute the mic", "call_mic", {"on": False}, "mic off"),
    ("mic state is read back", "call_state", {}, "mic: off"),
    ("unmute", "call_mic", {"on": True}, "mic on"),
    ("hang up", "call_end", {}, "done"),
    ("call over", "call_state", {}, "phase: none"),
    ("join a Google Meet by code", "meet_join", {"url": "abc-defg-hij"}, "joining"),
    ("Meet: counted as in a call", "call_state", {}, "phase: in_call"),
    ("Meet: same meeting again reuses it (no second copy)", "meet_join", {"url": "https://meet.google.com/abc-defg-hij"}, "already in that meeting"),
    ("Meet: mute", "call_mic", {"on": False}, "mic off"),
    ("Meet: leave", "call_end", {}, "done"),
    ("Meet: after leaving", "call_state", {}, "phase: ended"),
    ("bad Meet link refused", "meet_join", {"url": "https://example.com/x"}, "ERR:isn't a Google Meet"),
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
    failed = 0
    async with Harness() as bridge:
        t_all = time.time()
        for label, cmd, args, want in SCENARIO:
            t0 = time.time()
            out = await bridge.call(cmd, timeout=40, **args)
            ms = (time.time() - t0) * 1000
            ok = out.startswith("error") and want[4:] in out if want.startswith("ERR:") else (not out.startswith("error") and want in out)
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'} {ms:6.0f} ms  {label}" + ("" if ok else f"\n      expected {want!r}\n      got {out[:200]!r}"))
        print(f"\n{len(SCENARIO) - failed}/{len(SCENARIO)} passed in {time.time() - t_all:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
