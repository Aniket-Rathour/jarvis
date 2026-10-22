"""Tools Gemini can call, and how they run: duplicate guard, approval gate, plans, background jobs."""
import asyncio
import json
import logging
import re
import time

from google.genai import types

import config
import desktop
from jobs import Job

log = logging.getLogger("jarvis")
DEDUPE_SECONDS = 25
# A short spoken/typed answer while a plan waits for approval (checked in code, so a "yes" is never lost).
YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok(ay)?|go ahead|go for it|do it|approved?|proceed|confirm(ed)?|"
                 r"please do|sounds good|alright|haan?|kar do|theek hai)\b", re.I)
NO = re.compile(r"^\W*(no|nope|nah|cancel|stop|don'?t|do not|wait|nahi|mat karo)\b", re.I)
APPROVAL_MINUTES = 15
# Things done on websites that can't be undone: need the user's OK first.
IRREVERSIBLE_WEB = re.compile(r"\b(book|buy|purchase|order|pay|payment|checkout|send|reply|post|tweet|publish|transfer|"
                              r"donate|subscribe|unsubscribe|delete|remove|cancel|submit|apply)\w*\b", re.I)
# "message Rahul…", "tell mom…", "text Priya…": messaging verbs followed by a person (not "message me", "my messages")
SEND_TO = re.compile(r"\b(?:message|msg|dm|text|tell|ping|whatsapp)\s+(?!me\b|my\b|if\b|whether\b|what\b|how\b|the\b|any\b|"
                     r"us\b|and\b|to\b|for\b|on\b|in\b|web\b)[A-Za-z]", re.I)
# Delegated tasks matching this change the system, so they need the user's OK first.
RISKY_TASK = re.compile(r"\b(install|uninstall|remove|delete|erase|wipe|format|upgrade|downgrade|sudo|pkexec|dnf|"
                        r"flatpak|systemctl|reboot|shutdown|partition|chmod|chown|purge)\b", re.I)


def _s(t, d="", **kw):
    return types.Schema(type=t, description=d or None, **kw)


def _fn(name, desc, props=None, required=None, background=False):
    return types.FunctionDeclaration(
        name=name, description=desc,
        parameters=types.Schema(type="OBJECT", properties=props or {}, required=required or []),
        behavior=types.Behavior.NON_BLOCKING if background else None)


DECLARATIONS = [
    # --- instant actions ---
    _fn("open_bookmark", "Open one of the user's saved Chrome bookmarks, or a whole bookmark folder, by name. "
        "Fuzzy matching, typos are fine.", {"query": _s("STRING", "Bookmark or folder name as the user said it")},
        ["query"]),
    _fn("find_bookmarks", "List bookmarks matching a query without opening them.", {"query": _s("STRING")}, ["query"]),
    _fn("open_url", "Open a website in Chrome.", {"url": _s("STRING"), "new_window": _s("BOOLEAN")}, ["url"]),
    _fn("web_search", "Open a Google search in Chrome.", {"query": _s("STRING")}, ["query"]),
    _fn("search_files", "Find files or folders in the home directory by words in their name (newest first).",
        {"query": _s("STRING"), "root": _s("STRING", "Folder to search, default ~")}, ["query"]),
    _fn("open_path", "Open a file or folder with its default app.", {"path": _s("STRING")}, ["path"]),
    _fn("open_app", "Launch an app: terminal, files, settings, chrome, or any installed app by name.",
        {"name": _s("STRING")}, ["name"]),
    _fn("window", "Control a window matched by app name or title.", {
        "action": _s("STRING", enum=["maximize", "unmaximize", "minimize", "close", "focus", "resize", "move", "list"]),
        "app": _s("STRING"), "width": _s("INTEGER"), "height": _s("INTEGER"), "x": _s("INTEGER"),
        "y": _s("INTEGER")}, ["action"]),
    _fn("read_file", "Read a text file (code, notes, config, logs).", {"path": _s("STRING")}, ["path"]),
    _fn("write_file", "Create or update a text file: notes, small scripts, configs, code. Overwritten files are "
        "kept as .bak.", {"path": _s("STRING"), "content": _s("STRING", "Full file content"),
                          "append": _s("BOOLEAN", "Append instead of replacing")}, ["path", "content"]),
    _fn("list_dir", "List the files in a folder.", {"path": _s("STRING")}, ["path"]),
    _fn("volume", "Change or read the speaker volume.", {
        "action": _s("STRING", enum=["up", "down", "set", "mute", "unmute", "get"]),
        "percent": _s("INTEGER", "Step for up/down (default 10) or the level for set")}, ["action"]),
    _fn("media", "Control music/video that is playing (YouTube in Chrome, Spotify, …).",
        {"action": _s("STRING", enum=["play", "pause", "toggle", "next", "previous"])}, ["action"]),
    _fn("clipboard", "Read or set the clipboard.", {"action": _s("STRING", enum=["get", "set"]),
                                                   "text": _s("STRING")}, ["action"]),
    _fn("set_reminder", "Remind the user after some minutes (you will say it out loud and show a notification).",
        {"minutes": _s("NUMBER"), "message": _s("STRING")}, ["minutes", "message"]),
    _fn("look_at_screen", "Take a screenshot of the user's screen and answer a question about it: what is on "
        "it, read an error or text, explain what a window shows. Takes a few seconds.",
        {"question": _s("STRING", "What to find out, e.g. 'what is on my screen' or 'read the error in the terminal'")}),
    _fn("point_at", "Show the user where something is on their screen: Jarvis' glowing cursor glides to it and "
        "highlights it. Use for 'where is…', 'show me…', 'point at…', 'which button…'. Takes a few seconds.",
        {"target": _s("STRING", "What to point at, described as precisely as the user said it"),
         "hold_seconds": _s("INTEGER", "How long the cursor stays (default 8)")}, ["target"]),
    _fn("clear_pointer", "Hide Jarvis' cursor from the screen."),
    _fn("deep_research", "Research a topic thoroughly on the web with Claude: searches many sources, reads and "
        "compares them, cross-checks facts, saves a report and summarises. Takes minutes; progress is narrated "
        "out loud. Use for 'research…', 'find out everything about…', 'compare… in depth'.",
        {"topic": _s("STRING", "The full question or topic, with every detail the user gave"),
         "report_file": _s("BOOLEAN", "Also save a Markdown report in ~/Documents/Research (default true)")},
        ["topic"], background=True),
    _fn("run_routine", "Run one of the user's three scheduled Claude tasks right now: morning_plan (his Notion "
        "plan for today plus last night's log), night_log (the nightly check-in where he tells Claude what he did; "
        "Claude asks questions), weekly_review (the Sunday roadmap review). Takes 1-2 minutes; progress is narrated. "
        "morning_plan also saves today's tasks to your memory until the night log.",
        {"routine": _s("STRING", enum=["morning_plan", "night_log", "weekly_review"]),
         "refresh": _s("BOOLEAN", "morning_plan only: run again even if today's plan is already in memory"),
         "day": _s("STRING", "night_log only: which day to log; default auto (after midnight = the day that just "
                   "ended)", enum=["auto", "today", "yesterday"])}, ["routine"], background=True),
    _fn("routine_reply", "Send the user's words into a routine's Claude conversation: his answers to the questions "
        "Claude asked (night log), or a follow-up about a routine's result. One message with everything he said.",
        {"routine": _s("STRING", enum=["morning_plan", "night_log", "weekly_review"]),
         "message": _s("STRING", "His answers or follow-up, faithful and complete, in his own words")},
        ["routine", "message"], background=True),
    _fn("day_status", "Read today's memory: the morning plan's tasks, which are done and which are left, plus any "
        "notes. Use it when he asks what is left or what he has done."),
    _fn("day_update", "Update today's memory when he tells you what he did or needs: mark tasks done or not done, "
        "add a task or something extra he did, or save a note. Pass his words for each item; they are matched to "
        "the plan.", {"done": _s("ARRAY", "Things he finished (task wording or number)", items=_s("STRING")),
                      "pending": _s("ARRAY", "Tasks to mark not done again", items=_s("STRING")),
                      "add": _s("ARRAY", "New tasks to add to today's list", items=_s("STRING")),
                      "note": _s("STRING", "A short note to remember today")}),
    _fn("switch_brain", "Switch which AI brain is listening and speaking: gemini (cloud, fast, best quality) or qwen "
        "(runs on this PC, private, works offline, slower). Use when he asks to switch, change or go back to one. "
        "The other brain warms up first and takes over by itself, with the conversation carried over.",
        {"brain": _s("STRING", enum=["gemini", "qwen"])}, ["brain"]),
    _fn("run_command", "Run a shell command on the PC (full user permissions) and get its output. For quick things "
        "only; commands that change the system need an approved plan.",
        {"command": _s("STRING"), "timeout": _s("INTEGER", "seconds, default 30, max 300")}, ["command"]),
    # --- planning & approval ---
    _fn("show_plan", "Show the user a checklist plan in the Jarvis window. Use for any task with 3+ steps and before "
        "anything that installs, deletes or changes the system (needs_approval=true).", {
        "title": _s("STRING"), "steps": _s("ARRAY", "Short step descriptions", items=_s("STRING")),
        "needs_approval": _s("BOOLEAN")}, ["title", "steps", "needs_approval"]),
    _fn("update_step", "Mark a plan step's progress. Call with running before a step and done/failed after it.", {
        "step": _s("INTEGER", "1-based step number"),
        "status": _s("STRING", enum=["running", "done", "failed", "skipped"]), "note": _s("STRING")},
        ["step", "status"]),
    _fn("record_approval", "Record the user's spoken answer to the plan: approved=true only after a clear yes.",
        {"approved": _s("BOOLEAN")}, ["approved"]),
    # --- background jobs ---
    _fn("delegate_to_claude", "Give a task to Claude. where=terminal: Claude Code CLI in a terminal (coding, files, "
        "projects, setup, research). where=chrome: Claude in Chrome (anything done inside websites). Runs in the "
        "background; the result arrives later.", {
        "task": _s("STRING", "Complete, self-contained instructions with all details the user gave"),
        "where": _s("STRING", enum=["terminal", "chrome"]),
        "interactive": _s("BOOLEAN", "Open a live session the user watches/steers instead of a background run"),
        "workdir": _s("STRING", "Folder to work in"),
        "changes_system": _s("BOOLEAN", "True ONLY if it installs or uninstalls software, deletes things, or changes "
                              "system settings. Creating or editing project files and code is NOT a system change"),
        "complexity": _s("STRING", "basic = one clear, well-defined job (open a page, star a repo, a small edit or "
                         "fix in one file, a known command sequence). medium = several files or steps, some "
                         "judgement. high = building/refactoring/debugging a project, research, long web flows, "
                         "anything ambiguous or risky.", enum=["basic", "medium", "high"])},
        ["task", "where", "complexity"], background=True),
    _fn("delegate_to_opencode", "Give a CODING task to OpenCode, the open-source coding agent running on the local Qwen "
        "model: build/edit/fix/refactor code, scaffold projects, run tests, work on files in a project folder. It "
        "reads and edits files itself and reports each step. Use this for coding when he says 'opencode' or 'use qwen "
        "to code', or whenever the job is real coding work. Runs in the background.", {
        "task": _s("STRING", "Complete, self-contained coding instructions with every detail the user gave"),
        "workdir": _s("STRING", "Project folder to work in, e.g. ~/Documents/Aniket/myapp"),
        "interactive": _s("BOOLEAN", "Open the live OpenCode screen for him to steer instead of a background run")},
        ["task"], background=True),
    _fn("browser", "Control the user's real Chrome (his logins, his tabs) through the Jarvis Hands extension: instant, "
        "no extra agent. Actions: tabs (list), open (url), close (tab = current | others | title/url words), switch "
        "(tab = next | previous | title words), navigate (url), back, forward, reload, read (page text and numbered "
        "interactive elements), click (ref number from read, or text on it), type (text into a field: field = its "
        "label, or a ref; submit=true presses Enter), key (e.g. Enter, Escape, ctrl+a), scroll (direction "
        "up/down/top/bottom), find (text). For a web task do it step by step: read, then click/type, then read again "
        "to check. Say in a few words what you do before each step.", {
        "action": _s("STRING", enum=["tabs", "open", "close", "switch", "navigate", "back", "forward", "reload",
                                     "read", "click", "type", "key", "scroll", "find"]),
        "url": _s("STRING"), "tab": _s("STRING", "current, others, next, previous, or words from a tab's title/url"),
        "ref": _s("STRING", "Element number shown by read"), "text": _s("STRING", "Text to click/find, or text to type"),
        "field": _s("STRING", "Label/placeholder of the field to type into"), "submit": _s("BOOLEAN"),
        "key": _s("STRING"), "direction": _s("STRING", enum=["up", "down", "top", "bottom", "left", "right"])},
        ["action"]),
    _fn("install_packages", "Install system packages (dnf) or apps (flatpak from Flathub). Needs an approved plan "
        "listing the packages. Runs in the background.", {
        "packages": _s("ARRAY", items=_s("STRING")), "source": _s("STRING", enum=["dnf", "flatpak"])},
        ["packages", "source"], background=True),
    _fn("install_gnome_extension", "Install and enable a GNOME Shell extension from extensions.gnome.org by name or "
        "uuid. GNOME shows its own confirmation dialog. Needs an approved plan.", {"name": _s("STRING")}, ["name"],
        background=True),
    _fn("ask_gemini", "Ask a stronger Gemini text model a question that needs careful reasoning, a long explanation, "
        "math, or a written draft. Runs in the background.", {"question": _s("STRING")}, ["question"],
        background=True),
    _fn("job_status", "List background jobs and their progress."),
    _fn("cancel_job", "Stop a running background job.", {"job_id": _s("STRING")}, ["job_id"]),
]
TOOLS = [types.Tool(function_declarations=DECLARATIONS)]
# Left out for the local model: handled by code or the window instead (fewer tools = a shorter, faster prompt).
LOCAL_EXCLUDE = {"find_bookmarks", "record_approval", "job_status", "cancel_job", "clear_pointer"}


def _json_schema(s: types.Schema) -> dict:
    d = {"type": s.type.value.lower() if hasattr(s.type, "value") else str(s.type).lower()}
    if s.description:
        d["description"] = s.description
    if s.enum:
        d["enum"] = list(s.enum)
    if s.items:
        d["items"] = _json_schema(s.items)
    if s.properties:
        d["properties"] = {k: _json_schema(v) for k, v in s.properties.items()}
        d["required"] = list(s.required or [])
    return d


def ollama_tools() -> list[dict]:
    """The same tools in the OpenAI/Ollama function-calling format."""
    return [{"type": "function", "function": {"name": f.name, "description": f.description,
                                               "parameters": _json_schema(f.parameters)}}
            for f in DECLARATIONS if f.name not in LOCAL_EXCLUDE]

JOB_TOOLS = {"delegate_to_claude", "delegate_to_opencode", "install_packages", "install_gnome_extension"}
ACTION_TOOLS = JOB_TOOLS | {"browser", "open_bookmark", "open_url", "web_search", "open_path", "open_app", "window", "run_command",
                            "write_file", "volume", "media", "clipboard"}
NO_DEDUPE = {"show_plan", "update_step", "record_approval", "job_status", "find_bookmarks", "search_files",
             "cancel_job", "window", "browser", "read_file", "list_dir", "volume", "media", "look_at_screen", "point_at",
             "clear_pointer", "day_status", "day_update"}
LABELS = {"open_bookmark": "Open bookmark", "find_bookmarks": "Look up bookmark", "open_url": "Open",
          "web_search": "Search", "search_files": "Find file", "open_path": "Open", "open_app": "Launch",
          "window": "Window", "run_command": "Run", "install_packages": "Install", "show_plan": "Plan",
          "install_gnome_extension": "Install extension", "ask_gemini": "Ask Gemini", "job_status": "Check jobs",
          "update_step": "Step", "record_approval": "Approval", "cancel_job": "Cancel", "read_file": "Read",
          "write_file": "Write", "list_dir": "List", "volume": "Volume", "media": "Media", "clipboard": "Clipboard",
          "set_reminder": "Reminder", "look_at_screen": "Looking at your screen", "point_at": "Pointing at",
          "clear_pointer": "Hide pointer", "deep_research": "Research", "day_status": "Today's memory",
          "day_update": "Updating today's memory", "run_routine": "Routine", "routine_reply": "Routine reply",
          "switch_brain": "Switch brain", "browser": "Chrome", "delegate_to_opencode": "OpenCode"}


CODE_TASK = re.compile(r"\b(code|script|project|repo(?:sitory)? (?:in|at|on) (?:my |the )?(?:disk|folder|pc)|function|"
                       r"class|bug|debug|refactor|test|file|folder|directory|install|build|compile|git (?:commit|push|"
                       r"init)|python|javascript|typescript|golang|ui|page|component|readme|\.py|\.js|\.html|"
                       r"settings page|web ?ui)\b", re.I)


SITE = re.compile(r"\b(github|gmail|e-?mail|linkedin|twitter|instagram|facebook|whatsapp|youtube|amazon|flipkart|irctc|"
                  r"website|browser|chrome|tab|log ?in|sign ?in|google (?:doc|drive|sheet)|notion|star|follow|subscribe|"
                  r"flights?|tickets?|hotels?|cheapest|trains?)\b",
                  re.I)
LOCAL = re.compile(r"\b(my (?:project|code|script|files?|folders?|pc|laptop|disk)|local|in (?:my |the )?[\w-]+ "
                   r"(?:project|folder|repo|directory)|(?:into|to|inside) (?:my |the )?[\w-]+ (?:folder|directory)|clone|"
                   r"(?:this|the) (?:project|folder|repo)|~/|/home/)\b", re.I)


def pick_target(task: str, said: str) -> str:
    """Terminal or Chrome? Small models mix these up, so decide from the words of the task itself:
    acting on a named website with the user's logins -> chrome; working on files/code on this PC -> terminal."""
    site, local, code = bool(SITE.search(task)), bool(LOCAL.search(task)), bool(CODE_TASK.search(task))
    if site and not local:
        return "chrome"
    if local or code:
        return "terminal"
    return said if said in ("terminal", "chrome") else "terminal"


def describe(name: str, a: dict) -> tuple[str, str]:
    """(kind, label) for the activity list in the window."""
    if name == "delegate_to_claude":
        tag = {"basic": "[Haiku] ", "medium": "[Sonnet] ", "high": "[Sonnet] "}.get(a.get("complexity"), "")
        return ("chrome" if a.get("where") == "chrome" else "claude"), (tag + a.get("task", ""))[:120]
    if name == "delegate_to_opencode":
        return "claude", ("[OpenCode·Qwen] " + a.get("task", ""))[:120]
    if name == "browser":
        return "chrome", " ".join(str(v) for v in (a.get("action"), a.get("url") or a.get("text") or a.get("tab") or
                                                   a.get("ref") or a.get("key") or a.get("direction") or "") if v)[:120]
    if name == "ask_gemini":
        return "gemini", a.get("question", "")[:120]
    if name == "install_packages":
        return "self", f"Install {', '.join(a.get('packages') or [])} ({a.get('source')})"
    if name == "show_plan":
        return "self", f"Plan: {a.get('title', '')}"
    if name == "write_file":
        return "self", f"Write {a.get('path', '')}"[:120]
    if name in ("run_routine", "routine_reply"):
        title = {"morning_plan": "Morning Plan", "night_log": "Night Log", "weekly_review": "Weekly Review"}.get(
            a.get("routine"), "Routine")
        return "claude", f"[Sonnet] {title}{' (your answers)' if name == 'routine_reply' else ''}"
    if name == "look_at_screen":
        return "self", f"Looking at your screen{': ' + a['question'] if a.get('question') else ''}"[:120]
    if name == "deep_research":
        return "claude", f"[Sonnet] Research: {a.get('topic', '')}"[:120]
    val = next((str(v) for v in a.values() if v not in (None, "", [])), "")
    return "self", f"{LABELS.get(name, name)} {val}"[:120]


def is_failure(out: str) -> bool:
    o = out.strip().lower()
    return o.startswith(("error", "loop", "not found", "no ", "refused", "needs approval", "window control unavailable",
                         "timed out", "invalid")) or (o.startswith("exit ") and not o.startswith("exit 0"))


class Toolbox:
    def __init__(self, jarvis):
        self.j = jarvis
        self.recent: dict[str, tuple[float, str]] = {}
        self.calls: dict[str, int] = {}      # tool+args -> times called during the current user request
        self.utterance = 0
        self.actions_since_step = 0          # real actions since the last update_step
        self.plan: dict | None = None
        self.approved_until = 0.0

    # ---- approval & plans --------------------------------------------------
    @property
    def approved(self) -> bool:
        return time.time() < self.approved_until

    def needs_approval(self, name: str, a: dict) -> str | None:
        if self.approved:
            return None
        if name in ("install_packages", "install_gnome_extension"):
            return "it installs software"
        if name == "run_command" and desktop.RISKY.search(a.get("command", "")):
            return "the command changes the system"
        if name == "delegate_to_opencode" and RISKY_TASK.search(a.get("task", "")):
            return "the task installs, deletes or reconfigures things"
        if name == "delegate_to_claude" and (a.get("changes_system") or RISKY_TASK.search(a.get("task", ""))):
            return "the task installs, deletes or reconfigures things"
        if name == "delegate_to_claude" and a.get("where") == "chrome" and (IRREVERSIBLE_WEB.search(a.get("task", "")) or SEND_TO.search(a.get("task", ""))):
            return ("it does something on a website that can't be undone (buying, booking, sending, posting…); "
                    "read back the exact details - recipient, amount, date - in the plan")
        return None

    def set_approval(self, approved: bool, source: str) -> str:
        if not self.plan or self.plan.get("approval") is None:
            if not approved:
                return "Okay, nothing will be done."
            self.plan = self.plan or {"title": "Approved", "steps": [], "approval": None}
        self.plan["approval"] = "approved" if approved else "declined"
        self.approved_until = time.time() + APPROVAL_MINUTES * 60 if approved else 0
        self._push_plan()
        if approved:
            return (f"Approved by the user ({source}). Now do the steps ONE at a time: update_step(n, running), do it, "
                    "check the result, update_step(n, done/failed).")
        return "The user declined. Do not do it; ask what to change."

    def on_user_words(self, text: str) -> str | None:
        """Called with the user's current utterance; records a clear yes/no to a pending plan."""
        if not self.plan or self.plan.get("approval") != "pending" or len(text.split()) > 10:
            return None
        if NO.search(text):
            self.set_approval(False, "said no")
            return "declined"
        if YES.search(text):
            self.set_approval(True, "said yes")
            return "approved"
        return None

    def link_steps(self, name: str) -> list[int]:
        """Plan steps an action is about to perform (1-based). Steps the model marked running are linked to
        the action; if the model isn't ticking steps itself, the next step (or all remaining steps, for a
        background job such as one Claude run doing the whole plan) are marked running automatically."""
        p = self.plan
        if name not in ACTION_TOOLS or not p or p.get("approval") in ("pending", "declined"):
            return []
        steps = p["steps"]
        running = [i for i, st in enumerate(steps, 1) if st["status"] == "running"]
        pending = [i for i, st in enumerate(steps, 1) if st["status"] == "pending"]
        if name == "delegate_to_claude":
            # Claude sees the remaining steps and ticks off the ones it does ([[STEP n DONE]]).
            if not running and not p.get("managed"):
                for i in pending:
                    steps[i - 1]["status"] = "running"
                self._push_plan()
            return running + pending
        if running or p.get("managed"):
            return running
        link = pending if name in JOB_TOOLS else pending[:1]
        for i in link:
            steps[i - 1]["status"] = "running"
        if link:
            self._push_plan()
        return link

    def plan_texts(self, linked: list[int]) -> list[tuple[int, str]]:
        return [(i, self.plan["steps"][i - 1]["text"]) for i in linked] if self.plan else []

    def mark_steps(self, marks: dict[int, str], linked: list[int]):
        """Apply Claude's [[STEP n DONE]] reports."""
        if not self.plan:
            return
        changed = False
        for n, status in marks.items():
            if n in linked and self.plan["steps"][n - 1]["status"] not in ("done", "failed"):
                self.plan["steps"][n - 1]["status"] = status
                changed = True
        if changed:
            self._check_plan_done()
            self._push_plan()

    def finish_steps(self, linked: list[int], ok: bool):
        if not self.plan:
            return
        for i in linked:
            if i <= len(self.plan["steps"]) and self.plan["steps"][i - 1]["status"] == "running":
                self.plan["steps"][i - 1]["status"] = "done" if ok else "failed"
        self._check_plan_done()
        self._push_plan()

    def _check_plan_done(self) -> bool:
        if self.plan and self.plan["steps"] and all(
                x["status"] in ("done", "failed", "skipped") for x in self.plan["steps"]):
            self.approved_until = 0               # approval ends with the plan
            return True
        return False

    RESEARCH_STEPS = ["Break the question into what must be answered", "Search the web for good sources",
                      "Read and compare the best sources", "Cross-check the key facts", "Write up the findings"]

    def _deep_research(self, a: dict, fc, gen: int):
        topic = (a.get("topic") or "").strip()
        if not topic:
            return "error: no topic"
        if (dup := self.j.jobs.find_running(topic)):
            return f"ALREADY RUNNING as {dup.id}; don't start it again."
        slug = re.sub(r"[^a-z0-9]+", "-", topic.lower())[:40].strip("-") or "research"
        save = a.get("report_file", True) is not False
        task = (f"Do deep research on this: {topic}\n\nMethod: (1) split it into the sub-questions that must be "
                "answered; (2) use WebSearch to find diverse, authoritative sources (at least 5, newest first when "
                "recency matters) and WebFetch to read the most relevant ones; (3) cross-check the key claims "
                "across sources and note disagreements and dates; "
                + (f"(4) save the full report as Markdown, with the source URLs, to ~/Documents/Research/{slug}.md; "
                   if save else "")
                + "(5) finish with a spoken-style summary of at most 5 short sentences with the key findings"
                + (" and the path of the saved report." if save else ".")
                + "\nNever invent facts; say plainly what you could not verify.")
        self.plan = {"title": f"Research: {topic[:50]}", "steps": [{"text": t, "status": "pending", "note": ""}
                                                                   for t in self.RESEARCH_STEPS], "approval": None}
        steps = self.link_steps("delegate_to_claude")
        self.j.ui.send(type="plan", **self.plan)
        return self.j.jobs.claude(task, False, False, "~", fc, gen, self.plan_texts(steps), "high",
                                  report="up to 4 short sentences", label=f"Research: {topic}")

    def _push_plan(self):
        if self.plan:
            self.j.ui.send(type="plan", **self.plan)

    def _show_plan(self, title: str, steps: list, needs_approval: bool) -> str:
        steps = [str(s) for s in (steps or [])][:12]
        if self.plan and [x["text"] for x in self.plan["steps"]] == steps:
            if self.plan.get("approval") == "pending":
                return "This plan is already shown and waiting. Don't show it again; wait for the user's answer."
            if self.plan.get("approval") == "approved":
                return "This plan is already approved. Carry on with the next unfinished step."
        self.plan = {"title": title, "steps": [{"text": s, "status": "pending", "note": ""} for s in steps],
                     "approval": "pending" if needs_approval else None}
        if needs_approval:
            self.approved_until = 0
        self._push_plan()
        if needs_approval:
            return ("Plan shown with Approve/Cancel buttons. In ONE short sentence ask if you should go ahead, then STOP "
                    "and wait. If the user says yes, call record_approval(approved=true) before doing anything.")
        return ("Plan shown. If this is complex work (debugging, fixing or building code, research, long web flows), "
                "hand the WHOLE plan to Claude now with ONE delegate_to_claude call (rate its complexity) - Claude "
                "ticks off the steps itself. Otherwise do step 1 yourself, one step at a time, calling update_step "
                "before and after each.")

    def _update_step(self, step: int, status: str, note: str = "") -> str:
        if not self.plan or not 1 <= int(step) <= len(self.plan["steps"]):
            return "error: no such step in the current plan"
        if self.plan.get("approval") == "pending":
            return "error: the user has not approved this plan yet. Wait for a yes."
        if self.plan.get("approval") == "declined":
            return "error: the user declined this plan. Do not do it."
        s = self.plan["steps"][int(step) - 1]
        if status == "done" and s["status"] == "running" and not self.actions_since_step and not self.j.jobs.running():
            return ("error: nothing was actually done for this step yet. Do the work with a tool first, or mark it "
                    "skipped/failed and tell sir.")
        if status == "running":
            self.actions_since_step = 0
        s["status"], s["note"] = status, (note or "")[:120]
        self.plan["managed"] = True
        self._push_plan()
        if self._check_plan_done():
            failed = sum(x["status"] == "failed" for x in self.plan["steps"])
            return f"Plan finished ({failed} failed). Tell the user the outcome in one sentence."
        return f"step {step} {status}"

    # ---- execution ---------------------------------------------------------------
    async def execute(self, name: str, a: dict, fc, gen: int, steps: list[int] | None = None):
        """Returns a result string, or a Job for background tools."""
        key = name + json.dumps(a, sort_keys=True, default=str)
        now = time.time()
        if name not in ("update_step", "job_status", "record_approval"):
            ukey = f"{self.utterance}:{key}"
            self.calls[ukey] = n = self.calls.get(ukey, 0) + 1
            if n >= 3 or (n == 2 and name not in ("read_file", "list_dir", "search_files", "find_bookmarks",
                                                   "look_at_screen", "point_at")):
                return (f"LOOP: you already called {name} with exactly these arguments {n - 1} time(s) for this "
                        "request. Stop repeating it; use the earlier result, or tell sir what is blocking you.")
        if name not in NO_DEDUPE and key in self.recent and now - self.recent[key][0] < DEDUPE_SECONDS:
            age, prev = int(now - self.recent[key][0]), self.recent[key][1]
            return (f"ALREADY DONE {age}s ago (not repeated). Result was: {prev[:200]}. "
                    "Don't call it again; just confirm to the user.")
        why = self.needs_approval(name, a)
        if why:
            return (f"NEEDS APPROVAL because {why}. Call show_plan with needs_approval=true listing exactly what will "
                    "be installed/changed, ask the user, and wait for a yes.")
        if name in ACTION_TOOLS:
            self.actions_since_step += 1
        out = await self._run(name, a, fc, gen, steps or [])
        if isinstance(out, Job) or not is_failure(str(out)):
            self.recent[key] = (now, out.label if isinstance(out, Job) else str(out))
        return out

    async def _browser(self, a: dict) -> str:
        from browser_bridge import bridge
        act = a.get("action", "")
        args = {k: a[k] for k in ("url", "tab", "ref", "text", "field", "submit", "key", "direction") if a.get(k) not in (None, "")}
        if act == "close" and args.get("tab") not in (None, "current", "others"):
            pass                                              # a tab named by words: handled by the extension
        out = await bridge.call(act, **args)
        if out.startswith("error: the Chrome extension isn't connected"):
            return ("error: the Chrome extension isn't connected yet, so do NOT retry the browser tool. Do the task with "
                    "delegate_to_claude where=chrome instead, and tell the user once: run ./install_extension.sh "
                    "(3 clicks) to give you instant Chrome control.")
        return out[:3500]

    async def _run(self, name: str, a: dict, fc, gen: int, steps: list[int]):
        t = asyncio.to_thread
        jobs = self.j.jobs
        if name == "open_bookmark":
            return await t(desktop.open_bookmark, a["query"])
        if name == "find_bookmarks":
            return await t(desktop.find_bookmarks, a["query"])
        if name == "open_url":
            return await t(desktop.open_url, a.get("url", ""), bool(a.get("new_window")))
        if name == "web_search":
            return await t(desktop.web_search, a["query"])
        if name == "search_files":
            return await t(desktop.search_files, a["query"], a.get("root") or "~")
        if name == "open_path":
            return await t(desktop.open_target, a["path"])
        if name == "open_app":
            return await t(desktop.open_app, a["name"])
        if name == "window":
            return await t(desktop.window, **{k: a[k] for k in ("action", "app", "width", "height", "x", "y") if k in a})
        if name == "run_command":
            return await t(desktop.run_command, a["command"], a.get("timeout") or 30)
        if name == "read_file":
            return await t(desktop.read_file, a["path"])
        if name == "write_file":
            return await t(desktop.write_file, a["path"], a.get("content", ""), bool(a.get("append")))
        if name == "list_dir":
            return await t(desktop.list_dir, a.get("path") or "~")
        if name == "volume":
            return await t(desktop.volume, a.get("action", "get"), int(a.get("percent") or 0))
        if name == "media":
            return await t(desktop.media, a.get("action", "toggle"))
        if name == "clipboard":
            return await t(desktop.clipboard, a.get("action", "get"), a.get("text", ""))
        if name == "look_at_screen":
            return await self.j.look_at_screen(a.get("question", ""))
        if name == "point_at":
            return await self.j.point_at(a.get("target", ""), float(a.get("hold_seconds") or 8))
        if name == "clear_pointer":
            return self.j.clear_pointer()
        if name == "deep_research":
            return self._deep_research(a, fc, gen)
        if name == "run_routine":
            return self.j.routines.run(a.get("routine", ""), fc, gen, bool(a.get("refresh")), a.get("day") or "auto",
                                       config.ROUTINES_DRY_RUN)
        if name == "routine_reply":
            return self.j.routines.reply(a.get("routine", ""), a.get("message", ""), fc, gen, config.ROUTINES_DRY_RUN)
        if name == "switch_brain":
            return self.j.request_switch(a.get("brain", ""), "voice")
        if name == "day_status":
            return self.j.day.status(with_brief=False)
        if name == "day_update":
            if not self.j.day.active and not (a.get("note") or a.get("add")):
                return "error: no plan is in memory today; only notes or new tasks can be saved"
            res = self.j.day.update(a.get("done") or [], a.get("pending") or [], a.get("add") or [], a.get("note", ""))
            return json.dumps(res) + "\n" + self.j.day.status()
        if name == "set_reminder":
            return self.j.set_reminder(float(a.get("minutes") or 0), a.get("message", ""))
        if name == "show_plan":
            return self._show_plan(a.get("title", "Plan"), a.get("steps") or [], bool(a.get("needs_approval")))
        if name == "update_step":
            return self._update_step(a.get("step", 0), a.get("status", "done"), a.get("note", ""))
        if name == "record_approval":
            return self.set_approval(bool(a.get("approved")), "said it out loud")
        if name == "job_status":
            return jobs.summary()
        if name == "cancel_job":
            return jobs.cancel(a.get("job_id", ""))
        if name == "browser":
            return await self._browser(a)
        if name == "delegate_to_opencode":
            task = a.get("task", "").strip()
            if not task:
                return "error: empty task"
            if (dup := jobs.find_running(task)):
                return f"ALREADY RUNNING as {dup.id}; don't start it again."
            try:
                return jobs.opencode(task, bool(a.get("interactive")), a.get("workdir", ""), fc, gen, self.plan_texts(steps))
            except RuntimeError as e:
                return f"error: {e}"
        if name == "delegate_to_claude":
            task = a.get("task", "").strip()
            if not task:
                return "error: empty task"
            if (dup := jobs.find_running(task)):
                return f"ALREADY RUNNING as {dup.id}; don't start it again."
            complexity = a.get("complexity") if a.get("complexity") in ("basic", "medium", "high") else "medium"
            where = pick_target(task, a.get("where", ""))
            if where != a.get("where"):
                log.info("delegate target corrected: %s -> %s", a.get("where"), where)
            a["where"] = where
            return jobs.claude(task, where == "chrome", bool(a.get("interactive")), a.get("workdir", ""),
                               fc, gen, self.plan_texts(steps), complexity)
        if name == "install_packages":
            pk, src = list(a.get("packages") or []), a.get("source", "dnf")
            try:
                argv = desktop.install_argv(pk, src)
            except ValueError as e:
                return f"error: {e}"
            return jobs.process(argv, "install", f"Install {', '.join(pk)} ({src})", fc, gen)
        if name == "install_gnome_extension":
            try:
                uuid, title = await t(desktop.resolve_extension, a.get("name", ""))
            except Exception as e:
                return f"error: {e}"

            def interpret(rc, out):
                if "successful" in out:
                    return "done", f"{title} ({uuid}) is installed and enabled."
                if "cancelled" in out:
                    return "cancelled", "the user cancelled GNOME's install dialog"
                return "failed", f"exit {rc}: {out[-300:]}"
            return jobs.process(desktop.extension_argv(uuid), "extension", f"Install {title}", fc, gen, interpret)
        if name == "ask_gemini":
            if not self.j.client:
                return "error: the Gemini brain isn't available; answer from your own knowledge"
            return jobs.think(a.get("question", ""), self.j.client, fc, gen)
        return f"error: unknown tool {name}"
