"""The user's three scheduled Claude tasks (morning plan, night log, weekly review), run on demand by voice.

Each is the task's own instruction text (routines/*.md, copied verbatim from the Claude account) plus a short
adapter for a live voice conversation. Claude runs headless in a fixed folder so a conversation can be resumed:
the night log asks questions, Jarvis relays them, and the answers go back into the SAME Claude session."""
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass

import config
from daymem import DayMemory
from jobs import Job

log = logging.getLogger("jarvis.routines")
DIR = config.ROOT / "routines"
WORKDIR = config.STATE_DIR / "routines"            # fixed cwd: `claude --resume` finds sessions per folder
STATE = config.STATE_DIR / "routines.json"
AWAIT, DONE = "[[AWAITING_USER]]", "[[DONE]]"
NOT_SAVED = re.compile(r"(couldn'?t|could not|unable to|failed to|wasn'?t able to|can'?t|cannot|did not|didn'?t)\s+"
                       r"(?:\w+\s+){0,2}(save|write|update|store)", re.I)
TASKS_RE = re.compile(r"```jarvis-tasks\s*(\[.*?\])\s*```", re.S)
NIGHT_LOG_AFTER_MIDNIGHT = 5          # a check-in before 05:00 is about the day that just ended

SYSTEM_NOTE = (
    "You are running one of {user}'s routines for Jarvis, his voice assistant. He is at his PC listening: "
    "everything you write is read aloud, and his replies arrive as text transcribed from speech, so expect the odd "
    "misheard word.\n"
    "- Write for the ear: short plain sentences, no tables, no emoji, no markdown headings; simple dashes for "
    "lists are fine. Never read out URLs or IDs.\n"
    "- If you need answers from him, ask them as a short numbered list (at most 8 items), then STOP and end your "
    "message with the exact line [[AWAITING_USER]]. Don't carry on until he answers.\n"
    "- When the routine is completely finished, end with the exact line [[DONE]] on its own line.\n"
    "- Before each major step write ONE short first-person sentence (under 15 words) about what you are doing; "
    "it is spoken as progress.\n"
    "- The AskUserQuestion tool is not available here; ask in plain text as described. Reading his Notion and his "
    "Night Log never needs permission.")

REPORTS = {   # what Jarvis (the voice) should tell him when the routine finishes
    "morning_plan": "up to 6 short sentences: yesterday in one line, then today's tasks (theory, build task, DSA, "
                    "gate) and the focus for today. Say the tasks are saved in memory for the day",
    "weekly_review": "up to 6 short sentences: the key numbers, the blunt verdict, and next week's focus. Say the "
                     "full review and the X thread are on screen and offer to copy the thread to the clipboard",
    "night_log": "up to 5 short sentences: what went right, what went wrong, the recap versus his 7-day average, and "
                 "that the entry is saved. Say the X post draft is on screen and offer to copy it to the clipboard, "
                 "and that today's memory is now closed",
}


@dataclass
class Routine:
    key: str
    title: str
    trigger_id: str
    prompt: str
    interactive: bool
    starts_day: bool
    closes_day: bool


def load() -> dict[str, Routine]:
    out = {}
    for f in sorted(DIR.glob("*.md")):
        m = re.match(r"---\n(.*?)\n---\n(.*)", f.read_text(), re.S)
        if not m:
            continue
        meta = dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)
        out[meta["key"]] = Routine(meta["key"], meta["title"], meta.get("trigger_id", ""), m.group(2).strip(),
                                   meta.get("interactive") == "true", meta.get("starts_day") == "true",
                                   meta.get("closes_day") == "true")
    return out


def _now_text(now: time.struct_time) -> str:
    return time.strftime("%A %-d %B %Y, %-I:%M %p", now)


def night_log_day(which: str, now: time.struct_time | None = None) -> tuple[str, str]:
    """(ISO date, why) for the day the night log should record."""
    now = now or time.localtime()
    yesterday = which == "yesterday" or (which in ("", "auto") and now.tm_hour < NIGHT_LOG_AFTER_MIDNIGHT)
    t = time.mktime(now) - (86400 if yesterday else 0)
    why = ("it's after midnight, so the day that just ended" if which in ("", "auto") and yesterday
           else "the day he asked for" if yesterday else "today, as he is checking in tonight")
    return time.strftime("%Y-%m-%d", time.localtime(t)), why


def build_prompt(r: Routine, day: DayMemory, which: str = "auto", dry_run: bool = False) -> str:
    now = time.localtime()
    head = [f"[Jarvis context] It is {_now_text(now)} (India time). Jarvis is running his scheduled task "
            f"\"{r.title}\" on demand, with {config.USER_NAME} present and listening, instead of the unattended "
            "schedule. The task's own instructions follow, word for word; the notes below only adapt it to a live "
            "voice conversation and override anything in it that says otherwise."]
    if r.key == "morning_plan":
        head.append("He is present, so this is a spoken brief: keep it under a minute when read aloud. After the "
                    "brief, and before [[DONE]], add a fenced block exactly like:\n```jarvis-tasks\n[\"...\", \"...\"]\n```"
                    "\nlisting today's concrete to-dos as short strings: the Theory topic, the Practical/build task, "
                    "each DSA problem (or 'DSA: N problems on <topic>' if the page doesn't name them) and any Gate "
                    "items due. Nothing else goes in that block.")
    elif r.key == "night_log":
        date, why = night_log_day(which, now)
        head.append(f"He is present, so UNLIKE the unattended schedule you DO ask the step-2 questions, as plain "
                    f"numbered, speakable questions (not AskUserQuestion), then wait for his answers. The day to log "
                    f"is {date} ({why}); use that date wherever the task says 'yesterday' or the date.")
        head.append("What Jarvis noted during the day (use it to ask smarter questions and to pre-fill obvious "
                    "answers for him to confirm; never treat it as his answers):\n" + day.status(with_brief=False))
        head.append("Save the entry (step 5) only after he has answered. If he skipped some answers, ask for the "
                    "missing ones once; then save what he gave and never invent values. Show the X post draft as "
                    "plain lines. When it is saved and recapped, end with [[DONE]].")
        if dry_run:
            head.append("DRY RUN (a test): do NOT call ArtifactData with set/update or write anything anywhere; "
                        "say exactly what you would have saved, then finish with [[DONE]].")
    elif r.key == "weekly_review":
        head.append("Write the review as spoken-style sections (The numbers, Roadmap check, Verdict, Next week), then "
                    "the X thread as numbered short posts. Then [[DONE]].")
    return "\n\n".join(head) + "\n\n=== THE TASK (verbatim) ===\n" + r.prompt + "\n=== END OF TASK ==="


def split_markers(text: str) -> tuple[str, bool, bool]:
    awaiting, done = AWAIT in text, DONE in text
    return text.replace(AWAIT, "").replace(DONE, "").strip(), awaiting, done


def open_conversations() -> str:
    """For the voice model's instructions: which routine conversations are waiting on him (survives restarts)."""
    try:
        state, routines = json.loads(STATE.read_text()), load()
    except (OSError, ValueError):
        return ""
    return "\n".join(f"{routines[k].title}: Claude asked him questions and is WAITING for his answers - when he "
                     f"answers, call routine_reply('{k}', ...)." for k, st in state.items()
                     if k in routines and st.get("status") == "awaiting_user")


class Routines:
    def __init__(self, assistant):
        self.a = assistant
        self.routines = load()
        WORKDIR.mkdir(parents=True, exist_ok=True)
        try:
            self.state: dict = json.loads(STATE.read_text())
        except (OSError, ValueError):
            self.state = {}

    def _save(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(self.state, indent=1))

    def status_block(self) -> str:
        return open_conversations()

    # ---- starting ------------------------------------------------------------------------
    def run(self, key: str, fc, gen: int, refresh: bool = False, which: str = "auto", dry_run: bool = False):
        r = self.routines.get(key)
        if not r:
            return f"error: unknown routine '{key}' (known: {', '.join(self.routines)})"
        if (dup := next((j for j in self.a.jobs.running() if j.meta.get("routine") == key), None)):
            return f"ALREADY RUNNING as {dup.id}; don't start it again."
        day = self.a.day
        if r.starts_day and day.active and not refresh and day.data.get("plan_date") == time.strftime("%Y-%m-%d"):
            return ("Today's morning plan is already in memory, so I did not run it again. Read it to him briefly "
                    "and offer to refresh it from Notion if he wants.\n\n" + day.status(with_brief=True))
        sid = str(uuid.uuid4())
        self.state[key] = {"session_id": sid, "status": "running", "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "turns": 1, "which": which}
        self._save()
        return self._launch(r, build_prompt(r, day, which, dry_run), fc, gen, turn=1, session_id=sid, dry_run=dry_run)

    def reply(self, key: str, message: str, fc, gen: int, dry_run: bool = False):
        r = self.routines.get(key)
        st = self.state.get(key)
        if not r:
            return f"error: unknown routine '{key}'"
        if not st or not st.get("session_id"):
            return f"error: there is no {r.title} conversation to continue yet; start it with run_routine first"
        if (dup := next((j for j in self.a.jobs.running() if j.meta.get("routine") == key), None)):
            return f"error: {r.title} is still working ({dup.id}); wait for it to finish"
        message = message.strip()
        if not message:
            return "error: empty reply"
        st.update(status="running", turns=st.get("turns", 1) + 1)
        self._save()
        text = f"{config.USER_NAME} says (by voice, transcribed):\n{message}"
        if dry_run:
            text += "\n(This is still a DRY RUN: do not write anything.)"
        return self._launch(r, text, fc, gen, turn=st["turns"], resume=st["session_id"], dry_run=dry_run)

    def _launch(self, r: Routine, prompt: str, fc, gen: int, turn: int, session_id: str = "", resume: str = "",
                dry_run: bool = False) -> Job:
        # A dry run must not be able to write to his real Night Log, whatever the model decides: take the tool away.
        block = "--disallowedTools ArtifactData" if dry_run and turn > 1 else ""
        job = self.a.jobs.claude(
            prompt, False, False, str(WORKDIR), fc, gen, None, "high", report=REPORTS.get(r.key, ""),
            label=r.title if turn == 1 else f"{r.title} (continuing)",
            start_kw={"session_id": session_id, "resume": resume, "headless": not config.ROUTINES_TERMINAL,
                      "system_note": SYSTEM_NOTE.format(user=config.USER_NAME), "extra_flags": block},
            meta={"routine": r.key, "turn": turn, "dry_run": dry_run}, deliver_chars=6000)
        return job

    # ---- finishing -------------------------------------------------------------------------
    async def on_finished(self, job: Job):
        """Called for every finished job before Jarvis reads the result: routine jobs get post-processed."""
        key = job.meta.get("routine")
        if not key or key not in self.routines:
            return
        r, st = self.routines[key], self.state.setdefault(key, {})
        if job.status != "done":
            st["status"] = "failed"
            self._save()
            job.instruction = (f"The {r.title} routine did not finish ({job.status}). Tell him plainly in one "
                               "sentence, and offer to try again.")
            return
        text, awaiting, done = split_markers(job.result)
        if r.interactive and not awaiting and not done and text.rstrip().endswith("?"):
            awaiting = True                                   # forgot the marker but clearly asked something
        summary = ""
        if r.starts_day and not awaiting:
            text, tasks = self._take_tasks(text)
            if not tasks:
                tasks = await self._extract_tasks(text)
            self.a.day.start(time.strftime("%Y-%m-%d"), text, tasks)
            summary = f"{len(tasks)} tasks saved to today's memory"
        if awaiting:
            st["status"] = "awaiting_user"
            job.instruction = (
                f"{r.title} is waiting for {config.USER_NAME}'s answers. Ask him these questions in your own words, "
                "a couple at a time (not the whole list at once), listening to each answer. Keep track of every "
                "answer. When you have them all (or he says that's all), call routine_reply with routine="
                f"'{key}' and ONE message containing all his answers, item by item, faithfully, in his own words. "
                "Never answer for him or invent values.")
        else:
            st["status"] = "done"
            not_saved = r.closes_day and NOT_SAVED.search(text)
            if r.closes_day and not job.meta.get("dry_run") and not not_saved:
                summary = "today's memory closed (" + (self.a.day.close() or "nothing was planned") + ")"
            elif not_saved:
                st["status"] = "awaiting_user"              # keep the conversation open so he can retry
                summary = "the entry may NOT have been saved, so today's memory was kept"
            job.instruction = (f"Tell {config.USER_NAME} the outcome in {REPORTS.get(r.key) or job.report}. "
                               "Stay close to what the result says, using its own facts and numbers; add no "
                               "judgments, praise or claims of your own." + (f" Note: {summary}." if summary else ""))
        st["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save()
        job.result = text + (f"\n\n[Jarvis: {summary}]" if summary else "")
        self.a.ui.send(type="report", title=r.title + (" - waiting for you" if awaiting else ""), text=text)

    @staticmethod
    def _take_tasks(text: str) -> tuple[str, list[str]]:
        m = TASKS_RE.search(text)
        if not m:
            return text, []
        try:
            tasks = [str(t) for t in json.loads(m.group(1))]
        except ValueError:
            tasks = []
        return TASKS_RE.sub("", text).strip(), tasks

    async def _extract_tasks(self, brief: str) -> list[str]:
        """Fallback when Claude forgot the task block: have a Gemini text model pull the to-dos out."""
        if not self.a.client:
            return []
        try:
            from google.genai import types
            from llm import hedged
            cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)
            _, raw = await hedged(self.a.client, list(config.BRAIN_MODELS),
                                  "From this morning brief, list TODAY's concrete to-dos as a JSON array of short "
                                  "strings (theory topic, build task, each DSA problem or the DSA count and topic, "
                                  "gate items). Only what the brief says.\n\n" + brief, cfg, hedge_s=6, timeout=30)
            tasks = json.loads(raw)
            return [str(t) for t in tasks][:12] if isinstance(tasks, list) else []
        except Exception as e:
            log.warning("could not extract tasks: %s", e)
            return []
