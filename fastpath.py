"""Instant, deterministic handling of the most common commands, used by the Qwen brain.
A 27B model on this GPU takes seconds per reply; these patterns can't misroute and answer immediately.
Anything that doesn't match exactly goes to the model as usual. handle() returns the spoken reply, or None."""
import json
import re
import time
from types import SimpleNamespace

import desktop
from jobs import Job

_P = r"(?:please |can you |could you |jarvis,? )*"
OPEN = re.compile(rf"^{_P}(?:open|launch|start|bring up|show)(?: up)? (?:my |the |a )?(?P<what>.+?)(?: for me)?(?: please)?[.!?]*$", re.I)
VOLUME = re.compile(rf"^{_P}(?:turn |set )?(?:the )?(?:volume|sound)(?: (?P<how>up|down|louder|quieter|off|on))?(?: to (?P<pct>\d+)(?: ?%| percent)?)?[.!?]*$|"
                    rf"^{_P}(?P<how2>mute|unmute)(?: the)?(?: volume| sound)?[.!?]*$|"
                    rf"^{_P}turn (?:it |the volume |the sound )?(?P<how3>up|down)[.!?]*$", re.I)
MEDIA = re.compile(rf"^{_P}(?P<how>pause|play|resume|stop|skip|next|previous)(?: (?:the )?(?:song|music|video|track|it|this))?[.!?]*$", re.I)
TIME = re.compile(rf"^{_P}(?:what(?:'s| is) the time|what time is it|time)(?: now)?[.!?]*$", re.I)
DATE = re.compile(rf"^{_P}(?:what(?:'s| is) (?:the date|today'?s date)|what day is it(?: today)?)[.!?]*$", re.I)
MORNING = re.compile(rf"^{_P}(?:good morning[ ,.!]*)?(?:what(?:'s| is) my morning plan|(?:run|start|do|give me) (?:my )?morning plan|"
                     rf"plan my day|my morning plan|morning plan)(?: for today)?[.!?]*$|^good morning[.!]*$", re.I)
NIGHT = re.compile(rf"^{_P}(?:run|start|do|begin|open) (?:my )?night ?log(?: check[- ]?in)?[.!?]*$|^{_P}(?:night ?log|log my day)[.!?]*$", re.I)
WEEKLY = re.compile(rf"^{_P}(?:run |start |do |give me )?(?:my )?weekly (?:roadmap )?review[.!?]*$|^{_P}how was my week[.?!]*$", re.I)
LEFT = re.compile(rf"^{_P}(?:what(?:'s| is| are)(?: the)?(?: tasks?)? (?:left|remaining)(?: for today| today)?|what do i have left|"
                  rf"what(?:'s| is) left(?: for today| today)?|how many tasks (?:are )?left|what have i (?:done|finished)(?: today)?)[.?!]*$", re.I)
SEARCH = re.compile(rf"^{_P}(?:search(?: google)?(?: for)?|google|look up|look for|find (?:me )?(?:info(?:rmation)? (?:on|about))) (?P<q>.{{3,}}?)[.!?]*$", re.I)
GOTO = re.compile(rf"^{_P}(?:go to|open|take me to|navigate to) (?P<site>(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|in|ai|app|co|edu|gov)(?:/\S*)?)[.!?]*$", re.I)
REMIND = re.compile(rf"^{_P}remind me (?:in|after) (?P<n>\d+(?:\.\d+)?|an?|half an?) ?(?P<u>seconds?|minutes?|mins?|hours?|hrs?)(?: from now)? (?:to|that|about) (?P<what>.+?)[.!?]*$", re.I)
SCREEN = re.compile(rf"^{_P}(?:what(?:'s| is) on my screen|what do you see(?: on (?:my|the) screen)?|look at (?:my|the) screen|"
                    rf"what am i looking at|read (?:my|the) screen|can you see my screen)[.?!]*$", re.I)
UI_NOUN = r"(?:button|icon|tab|bookmark|menu|link|window|field|bar|logo|panel|toggle|checkbox|box|folder|file|app|extension|notification|popup|dialog|text|video|picture|image|chat|message|search|address|settings|terminal|editor)"
POINT = re.compile(rf"^{_P}(?:point (?:at|to)|show me where(?: is| are)?|show me|where(?:'s| is| are)) (?P<t>(?:the |my )?(?:[\w'-]+ ){{0,4}}{UI_NOUN}s?(?: [\w'-]+){{0,3}}?)(?: on (?:my|the) screen)?[.?!]*$", re.I)
HIDE = re.compile(rf"^{_P}(?:hide|clear|remove|dismiss) (?:the |your )?(?:cursor|pointer)[.!?]*$", re.I)
DONE = re.compile(rf"^{_P}(?:i(?:'ve| have)? (?:just )?(?:finished|completed|did|solved|wrapped up|done with|done)|"
                  rf"(?:just )?(?:finished|completed|done with|solved|wrapped up)) (?:with )?(?:the |my |all the |all of the )?"
                  rf"(?!you\b|he\b|she\b|they\b|it\b|we\b|yet\b|anything\b|everything\b|that\b|this\b)(?P<what>.{{3,}}?)[.!]*$", re.I)
# --- Chrome (Jarvis Hands extension) and OpenCode: answered by code, no model pass ---
THIS = r"(?:this |the |current |my )*"
B_CLOSE_OTHERS = re.compile(rf"^{_P}close (?:all )?(?:the )?other tabs[.!?]*$", re.I)
B_CLOSE = re.compile(rf"^{_P}close {THIS}tab[.!?]*$|^{_P}close it[.!?]*$", re.I)
B_CLOSE_NAMED = re.compile(rf"^{_P}close (?:the |my )?(?P<t>[\w .'-]{{2,40}}?) tab[.!?]*$", re.I)
B_NEW = re.compile(rf"^{_P}(?:open |create |make )?(?:a )?new tab[.!?]*$", re.I)
B_NEXT = re.compile(rf"^{_P}(?:go to |switch to )?(?P<d>next|previous) tab[.!?]*$", re.I)
B_SWITCH = re.compile(rf"^{_P}(?:switch|go) to (?:the |my )?(?P<t>[\w .'-]{{2,40}}?) tab[.!?]*$", re.I)
B_NAV = re.compile(rf"^{_P}(?P<a>go back|go forward|reload|refresh)(?: the page| this page| page)?[.!?]*$", re.I)
B_SCROLL = re.compile(rf"^{_P}scroll (?:(?P<d>down|up)|to (?:the )?(?P<e>top|bottom))(?: a bit| a little)?[.!?]*$", re.I)
B_CLICK = re.compile(rf"^{_P}(?:click|press|tap|hit)(?: on)? (?:the )?(?P<t>.{{2,60}}?)(?: button| link| tab)?[.!?]*$", re.I)
B_TYPE = re.compile(rf"^{_P}type (?P<x>.+?)(?: (?:in|into) (?:the )?(?P<f>[\w ]{{2,30}}))?(?P<s>(?:,? and| then) (?:press )?(?:enter|search|submit))?[.!?]*$", re.I)
B_KEY = re.compile(rf"^{_P}press (?:the )?(?P<k>enter|escape|tab|backspace|space)(?: key)?[.!?]*$", re.I)
OPENCODE = re.compile(rf"^{_P}(?:use |ask |tell |let |get )?open ?code(?: to| and)? (?P<task>.{{6,}}?)[.!?]*$|"
                      rf"^{_P}(?P<task2>(?:code|build|write|create|fix|refactor|make|add).{{6,}}?) (?:with|using|in|on) (?:open ?code|qwen)[.!?]*$", re.I)
APPS = set(desktop.APP_ALIASES) | {"nautilus", "gnome-terminal", "ptyxis"}


async def _chrome(j, t: str) -> str | None:
    from browser_bridge import bridge
    if not bridge.connected:
        return None
    ok = lambda out: not out.startswith("error")                                          # noqa: E731
    if B_CLOSE_OTHERS.match(t):
        return _say(j, "Done, sir." if ok(await _tool(j, "browser", {"action": "close", "tab": "others"})) else "I couldn't, sir.")
    if B_CLOSE.match(t):
        return _say(j, "Closed, sir." if ok(await _tool(j, "browser", {"action": "close", "tab": "current"})) else "I couldn't, sir.")
    if B_NEW.match(t):
        return _say(j, "New tab, sir." if ok(await _tool(j, "browser", {"action": "open"})) else "I couldn't, sir.")
    if m := B_NEXT.match(t):
        return _say(j, "Done, sir." if ok(await _tool(j, "browser", {"action": "switch", "tab": m.group("d").lower()})) else "I couldn't, sir.")
    if m := B_NAV.match(t):
        act = {"go back": "back", "go forward": "forward"}.get(m.group("a").lower(), "reload")
        return _say(j, "Done, sir." if ok(await _tool(j, "browser", {"action": act})) else "I couldn't, sir.")
    if m := B_SCROLL.match(t):
        return _say(j, "Done, sir." if ok(await _tool(j, "browser", {"action": "scroll", "direction": (m.group("d") or m.group("e")).lower()})) else "I couldn't, sir.")
    if m := B_KEY.match(t):
        return _say(j, "Done, sir." if ok(await _tool(j, "browser", {"action": "key", "key": m.group("k").capitalize()})) else "I couldn't, sir.")
    if m := B_CLOSE_NAMED.match(t):
        out = await _tool(j, "browser", {"action": "close", "tab": m.group("t").strip()})
        return _say(j, f"Closed the {m.group('t').strip()} tab, sir." if ok(out) else f"I can't find a {m.group('t').strip()} tab, sir.")
    if m := B_SWITCH.match(t):
        out = await _tool(j, "browser", {"action": "switch", "tab": m.group("t").strip()})
        if ok(out):
            return _say(j, "Done, sir.")
    if m := B_TYPE.match(t):
        a = {"action": "type", "text": m.group("x").strip(), "submit": bool(m.group("s"))}
        if m.group("f"):
            a["field"] = m.group("f").strip()
        out = await _tool(j, "browser", a)
        return _say(j, "Typed, sir." if ok(out) else "I can't find a field to type in, sir.")
    if m := B_CLICK.match(t):
        what = m.group("t").strip()
        if not re.match(r"^(?:on )?(?:play|pause|resume|stop|skip|next|previous)\b", what, re.I) or len(what.split()) > 1:
            out = await _tool(j, "browser", {"action": "click", "text": what})
            if ok(out):
                return _say(j, "Clicked, sir.")
            return _say(j, f"I can't find {what[:40]} on the page, sir.")
    return None


async def handle(j, text: str) -> str | None:
    t = text.strip()
    if m := OPENCODE.match(t):
        task = (m.group("task") or m.group("task2")).strip()
        out = await j.run_tool("delegate_to_opencode", {"task": task}, SimpleNamespace(id=None, name="delegate_to_opencode"))
        if isinstance(out, Job):
            return _say(j, "OpenCode is on it, sir.")
        return _say(j, str(out)[:200])
    if quick := await _chrome(j, t):
        return quick
    if TIME.match(t):
        return _say(j, time.strftime("It's %-I:%M %p, sir."))
    if DATE.match(t):
        return _say(j, time.strftime("Today is %A, %-d %B, sir."))
    if LEFT.match(t):
        return _say(j, _left(j))
    if HIDE.match(t):
        await _tool(j, "clear_pointer", {})
        return _say(j, "Done, sir.")
    if m := REMIND.match(t):
        n = m.group("n").lower()
        n = {"a": 1, "an": 1, "half a": 0.5, "half an": 0.5}.get(n, None) or float(n)
        unit = m.group("u").lower()
        minutes = n / 60 if unit.startswith("sec") else n * 60 if unit.startswith(("hour", "hr")) else n
        out = await _tool(j, "set_reminder", {"minutes": minutes, "message": m.group("what").strip()})
        return _say(j, f"I shall remind you at {out.rsplit(' ', 1)[-1]}, sir." if out.startswith("reminder set") else "I couldn't set that, sir.")
    if SCREEN.match(t):
        _say(j, "One moment, sir.")
        out = await _tool(j, "look_at_screen", {"question": "What is on my screen?"})
        return _say(j, out[:500] if not out.startswith("error") else "I couldn't look at the screen, sir.")
    if DONE.match(t) and j.day.active:
        what = DONE.match(t).group("what").strip()
        res = json.loads((await _tool(j, "day_update", {"done": [what]})).split("\n")[0])
        done, total = j.day.counts()
        if res["marked_done"]:
            return _say(j, f"Marked done, sir: {res['marked_done'][0][:70]}. {total - done} left.")
        return _say(j, f"Noted, sir. That wasn't on the plan, so I logged it as extra. {total - done} left.")
    if m := POINT.match(t):
        _say(j, "One moment, sir.")
        out = await _tool(j, "point_at", {"target": m.group("t").strip()})
        return _say(j, "There it is, sir." if out.startswith("pointing") else "I can't see that on the screen, sir.")
    if m := GOTO.match(t):
        out = await _tool(j, "open_url", {"url": m.group("site")})
        return _say(j, f"Opening {re.sub(r'^https?://|/.*$', '', m.group('site'))}, sir." if out.startswith("opened") else None)
    if m := SEARCH.match(t):
        q = m.group("q").strip()
        if not re.match(r"^(?:my |the )?(?:morning plan|night log|weekly review)\b", q, re.I):
            out = await _tool(j, "web_search", {"query": q})
            return _say(j, f"Searching for {q[:60]}, sir." if out.startswith("opened") else None)
    for pat, routine in ((MORNING, "morning_plan"), (NIGHT, "night_log"), (WEEKLY, "weekly_review")):
        if pat.match(t):
            out = await j.run_tool("run_routine", {"routine": routine}, SimpleNamespace(id=None, name="run_routine"))
            if isinstance(out, Job):
                return _say(j, "Right away, sir.")
            return _say(j, _from_memory(j) if routine == "morning_plan" and j.day.active else str(out)[:300])
    if m := VOLUME.match(t):
        how = (m.group("how") or m.group("how2") or m.group("how3") or "").lower()
        pct = m.group("pct")
        action = {"louder": "up", "quieter": "down", "off": "mute", "on": "unmute"}.get(how, how)
        if pct:
            action = "set"
        if action in ("up", "down", "set", "mute", "unmute"):
            out = await _tool(j, "volume", {"action": action, **({"percent": int(pct)} if pct else {})})
            if not out.startswith("volume"):
                return _say(j, "I couldn't change the volume, sir.")
            return _say(j, {"mute": "Muted, sir.", "unmute": "Sound is back on, sir."}.get(
                action, f"Volume is at {out.split()[1]}, sir."))
    if m := MEDIA.match(t):
        action = {"resume": "play", "stop": "pause", "skip": "next"}.get(m.group("how").lower(), m.group("how").lower())
        out = await _tool(j, "media", {"action": action})
        return _say(j, "Done, sir." if "sent" in out else "There's nothing playing, sir.")
    if m := OPEN.match(t):
        what = m.group("what").strip().lower()
        what = re.sub(r"\s+(?:bookmark|page|site|website|link|app|application)$", "", what)
        if what in APPS:
            out = await _tool(j, "open_app", {"name": what})
            return _say(j, f"Opening {what}, sir." if out.startswith("launched") else None)
        best = _bookmark(what)
        if best:
            out = await _tool(j, "open_bookmark", {"query": best})
            return _say(j, f"Opening {_spoken(what)}, sir." if out.startswith("opened") else None)
    return None


def _left(j) -> str:
    d = j.day
    if not d.active:
        return "There's no plan in memory yet, sir. Shall I run your morning plan?"
    done, total = d.counts()
    left = [t["text"] for t in d.data["tasks"] if not t["done"]]
    if not left:
        return f"Everything on today's plan is done, sir: all {total} tasks."
    shown = "; ".join(x[:60] for x in left[:4]) + (f"; and {len(left) - 4} more" if len(left) > 4 else "")
    return f"{done} of {total} done, sir. Left: {shown}."


def _from_memory(j) -> str:
    return "Today's plan is already in memory, sir. " + _left(j)


def _bookmark(what: str) -> str | None:
    """Only a confident bookmark match counts; otherwise let the model decide."""
    best, best_s = None, 0.0
    for b in desktop._bookmarks():
        s = desktop._score(b, what)
        if s > best_s:
            best, best_s = b["name"], s
    return best if best_s >= 3 else None      # >= 3: every word in the name/URL plus a name match


KNOWN = {"youtube": "YouTube", "linkedin": "LinkedIn", "github": "GitHub", "leetcode": "LeetCode", "whatsapp": "WhatsApp",
         "dsa": "DSA", "gmail": "Gmail", "chatgpt": "ChatGPT"}


def _spoken(what: str) -> str:
    return " ".join(KNOWN.get(w, w) for w in what.split())


async def _tool(j, name: str, args: dict) -> str:
    return str(await j.run_tool(name, args, SimpleNamespace(id=None, name=name)))


def _say(j, reply: str | None) -> str | None:
    if reply:
        j.caption("jarvis", reply, new=True)
        j.say(reply)
    return reply


# Tools whose successful result needs no model to put into words: code confirms in a few words. This skips a whole
# second model pass (several seconds on this hardware).
CONTINUE_WITH_MODEL = {"update_step", "show_plan", "record_approval", "run_command", "read_file", "list_dir",
                       "search_files", "find_bookmarks", "clipboard", "routine_reply", "job_status", "cancel_job",
                       "ask_gemini"}
RETRY_MARKERS = ("NEEDS APPROVAL", "ALREADY", "LOOP")


def direct_reply(name: str, args: dict, out) -> str | None:
    """The spoken confirmation for a finished tool call, or None when the model must interpret the result."""
    from tools import is_failure
    if isinstance(out, Job):
        return "Right away, sir."
    out = str(out)
    if out.startswith(RETRY_MARKERS):
        return None
    if name == "show_plan" and args.get("needs_approval"):
        return f"Shall I proceed with {str(args.get('title', 'that'))[:60]}, sir?"
    if name in CONTINUE_WITH_MODEL:
        return None
    if is_failure(out):
        reason = re.sub(r"^(error|not found|no|refused)[: ]*", "", out.splitlines()[0], flags=re.I)[:90]
        return f"I'm afraid that failed, sir. {reason}".strip()
    if name == "open_bookmark":
        m = re.search(r'bookmark "(.+?)"', out)
        return f"Opening {_clean_name(m.group(1)) if m else 'it'}, sir."
    if name == "open_url":
        return f"Opening {re.sub(r'^https?://(www\\.)?|/.*$', '', str(args.get('url', 'it')))}, sir."
    if name == "web_search":
        return f"Searching for {str(args.get('query', ''))[:60]}, sir."
    if name == "open_app":
        return f"Opening {args.get('name', 'it')}, sir."
    if name == "open_path":
        return f"Opening {str(args.get('path', 'it')).rstrip('/').rsplit('/', 1)[-1]}, sir."
    if name == "volume":
        m = re.match(r"volume (\d+)%", out)
        return f"Volume is at {m.group(1)} percent, sir." if m else "Done, sir."
    if name == "set_reminder":
        return f"I shall remind you at {out.rsplit(' ', 1)[-1]}, sir."
    if name == "write_file":
        return "Saved, sir."
    if name == "point_at":
        return "There it is, sir."
    if name == "look_at_screen":
        return out[:500]
    if name == "day_status":
        return None
    if name == "day_update":
        try:
            r = json.loads(out.split("\n")[0])
        except ValueError:
            return "Noted, sir."
        parts = []
        if r.get("marked_done"):
            parts.append(f"Marked done: {r['marked_done'][0][:60]}")
        if r.get("logged_extra"):
            parts.append("Logged as extra")
        if r.get("added"):
            parts.append("Added")
        return ", ".join(parts) + ", sir." if parts else "Noted, sir."
    if name == "switch_brain":
        return None                                   # the new brain greets; the old one says nothing more
    return "Done, sir."


def _clean_name(name: str) -> str:
    name = re.sub(r"^\(\d+\)\s*", "", name)
    return KNOWN.get(name.lower(), re.split(r"\s[|\-–]\s", name)[0][:40])
