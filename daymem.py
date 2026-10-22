"""Jarvis' memory for the current day: what the morning plan said, and what is done and left.

It lives from the morning plan until the night log (not per session, not per 24 h): Jarvis can be restarted any
number of times and still knows. The night log itself is Claude's to remember, so closing the day just deletes
this file. Small by design."""
import difflib
import json
import os
import re
import time
from pathlib import Path

import config

FILE = config.STATE_DIR / "day.json"
CLOSED = config.STATE_DIR / "day_closed.json"       # only: when the last day was closed, for "did I log yet?"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _pretty(date: str) -> str:
    try:
        return time.strftime("%A %-d %B", time.strptime(date, "%Y-%m-%d"))
    except ValueError:
        return date


class DayMemory:
    def __init__(self, path: Path = FILE):
        self.path = path
        self.data: dict = self._load()

    # ---- storage -------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) and d.get("tasks") is not None else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    @property
    def active(self) -> bool:
        return bool(self.data)

    # ---- lifecycle -------------------------------------------------------------------
    def start(self, plan_date: str, brief: str, tasks: list[str]):
        """A new morning plan replaces whatever was left of the previous day."""
        self.data = {"version": 1, "plan_date": plan_date, "started": _now(), "brief": brief.strip()[:6000],
                     "tasks": [{"id": i, "text": t.strip(), "done": False, "note": "", "at": None}
                               for i, t in enumerate([t for t in tasks if t and t.strip()], 1)],
                     "extra_done": [], "notes": []}
        self._save()

    def close(self) -> str:
        """The night log has been done: forget the day. Returns a one-line summary of how it went."""
        if not self.active:
            return ""
        done, total = sum(t["done"] for t in self.data["tasks"]), len(self.data["tasks"])
        summary = f"{done} of {total} planned tasks done" + (f", plus {len(self.data['extra_done'])} extra"
                                                             if self.data["extra_done"] else "")
        CLOSED.parent.mkdir(parents=True, exist_ok=True)
        CLOSED.write_text(json.dumps({"closed": _now(), "plan_date": self.data.get("plan_date"), "summary": summary}))
        self.data = {}
        self.path.unlink(missing_ok=True)
        return summary

    # ---- updates ---------------------------------------------------------------------
    def _find(self, query) -> int | None:
        """Index of the task the user means: by number, exact text, containment, or closest wording."""
        tasks = self.data.get("tasks", [])
        q = str(query).strip().lower()
        if not q or not tasks:
            return None
        m = re.fullmatch(r"(?:task\s*)?#?(\d+)", q)
        if m:
            return int(m.group(1)) - 1 if 1 <= int(m.group(1)) <= len(tasks) else None
        texts = [t["text"].lower() for t in tasks]
        for i, t in enumerate(texts):
            if q == t:
                return i
        hits = [i for i, t in enumerate(texts) if q in t or t in q]
        if len(hits) == 1:
            return hits[0]
        qt = set(re.findall(r"[a-z0-9]{3,}", q))
        best, best_s = None, 0.0
        for i, t in enumerate(texts):
            tt = set(re.findall(r"[a-z0-9]{3,}", t))
            overlap = len(qt & tt) / max(1, min(len(qt), len(tt)))
            s = max(difflib.SequenceMatcher(None, q, t).ratio(), overlap * 0.9)
            if s > best_s:
                best, best_s = i, s
        return best if best_s >= 0.55 else None

    def update(self, done=(), pending=(), add=(), note: str = "") -> dict:
        """Apply what the user said. Returns {matched, unmatched, added} so Jarvis can confirm accurately."""
        out = {"marked_done": [], "marked_pending": [], "logged_extra": [], "added": []}
        for q in done or ():
            i = self._find(q)
            if i is None:       # finished something that wasn't on the plan: still worth remembering
                self.data.setdefault("extra_done", []).append({"text": str(q).strip(), "at": _now()})
                out["logged_extra"].append(str(q).strip())
            else:
                t = self.data["tasks"][i]
                t["done"], t["at"] = True, _now()
                out["marked_done"].append(t["text"])
        for q in pending or ():
            i = self._find(q)
            if i is not None:
                self.data["tasks"][i].update(done=False, at=None)
                out["marked_pending"].append(self.data["tasks"][i]["text"])
        for text in add or ():
            if str(text).strip():
                n = len(self.data["tasks"]) + 1
                self.data["tasks"].append({"id": n, "text": str(text).strip(), "done": False, "note": "", "at": None})
                out["added"].append(str(text).strip())
        if note and note.strip():
            self.data.setdefault("notes", []).append({"text": note.strip()[:400], "at": _now()})
        if any(out.values()) or note:
            self._save()
        return out

    # ---- reading ---------------------------------------------------------------------
    def counts(self) -> tuple[int, int]:
        t = self.data.get("tasks", [])
        return sum(x["done"] for x in t), len(t)

    def status(self, with_brief: bool = False) -> str:
        if not self.active:
            return ("There is no plan for today in memory: the morning plan hasn't been run since the last night "
                    "log. Offer to run it.")
        d = self.data
        done, total = self.counts()
        lines = [f"Plan from {_pretty(d.get('plan_date', ''))} (kept until the night log). "
                 f"{done} of {total} tasks done, {total - done} left."]
        for t in d["tasks"]:
            lines.append(f"{t['id']}. [{'x' if t['done'] else ' '}] {t['text']}" + (f" ({t['note']})" if t["note"] else ""))
        if d.get("extra_done"):
            lines.append("Also done (not on the plan): " + "; ".join(x["text"] for x in d["extra_done"]))
        if d.get("notes"):
            lines.append("Notes he gave today: " + "; ".join(x["text"] for x in d["notes"][-8:]))
        if with_brief and d.get("brief"):
            lines.append("\nThe full morning brief:\n" + d["brief"])
        return "\n".join(lines)

    def prompt_block(self) -> str:
        """Goes into the voice model's instructions at every (re)connect, so a restart doesn't forget the day."""
        if not self.active:
            return "No morning plan has been run since the last night log, so there is no plan for today in memory."
        return self.status(with_brief=False) + "\n(Kept until he runs the night log. Update it with day_update when he tells you what he did.)"
