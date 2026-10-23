"""Instant voice commands: each phrase must reach the right pattern (no model involved)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fastpath as f  # noqa: E402

CASES = {
    "close this tab": "B_CLOSE", "close the other tabs": "B_CLOSE_OTHERS", "new tab": "B_NEW", "open a new tab": "B_NEW",
    "next tab": "B_NEXT", "switch to youtube tab": "B_SWITCH", "close the github tab": "B_CLOSE_NAMED", "go back": "B_NAV",
    "scroll down": "B_SCROLL", "click sign in": "B_CLICK", "press enter": "B_KEY", "pin this tab": "B_TABOP",
    "reopen the closed tab": "B_REOPEN", "zoom in": "B_ZOOM", "bookmark this page": "B_BOOKMARK", "skip 10 seconds": "B_SEEK",
    "go back 30 seconds": "B_SEEK", "fullscreen": "B_FULL", "speed 1.5": "B_SPEED", "open Aryan's chat": "B_WA_CHAT",
    "open chat with Mom": "B_WA_CHAT", "type hello in the search box and press enter": "B_TYPE",
    "use opencode to add a login page to my flask app": "OPENCODE",
}
bad = []
for text, want in CASES.items():
    if not getattr(f, want).match(text):
        bad.append(f"{text!r} should match {want}")
for text in ("open my youtube", "what is the time", "tell me a joke"):
    if f.B_WA_CHAT.match(text) or f.B_CLICK.match(text):
        bad.append(f"{text!r} must not be a Chrome command")
assert not bad, "\n".join(bad)
print(f"fastpath: all {len(CASES)} patterns passed")
