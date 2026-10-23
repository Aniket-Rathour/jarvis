"""Jarvis' long-term memory: one SQLite file on this PC (~/.local/share/jarvis/memory.db) that survives restarts.

  notes     facts and preferences he told Jarvis ("my mum's birthday is…", "I prefer…")
  todos     his to-do list (add by voice, or pulled from Notion / anything Jarvis read)
  research  the FULL result of every Claude / OpenCode / research job, searchable, so Jarvis can tell it all later
  turns     the conversation log (oldest are pruned first when the file passes MEMORY_MAX_MB)
  focus     what he was last doing: current site/chat/project (so "open Aryan's chat" means the open WhatsApp)
Search uses SQLite FTS5 (like grep, but ranked) with a LIKE fallback."""
import asyncio
import datetime
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import config

# JARVIS_MEMORY_DB points tests (or a second profile) at another file, so the real memory is never touched
PATH = Path(os.environ.get("JARVIS_MEMORY_DB") or config.DATA_DIR / "memory.db")
_lock = threading.RLock()
_db: sqlite3.Connection | None = None
FTS = [False]
REPORT_PATH = re.compile(r"(~|/home/[\w.-]+)/[\w./ -]+\.md")


def _conn() -> sqlite3.Connection:
    global _db
    if _db is None:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(PATH, check_same_thread=False, timeout=10)
        _db.row_factory = sqlite3.Row
        _db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, kind TEXT, text TEXT, tags TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY, text TEXT, status TEXT DEFAULT 'open',
                                             source TEXT, ts REAL, done_ts REAL);
            CREATE TABLE IF NOT EXISTS research(id INTEGER PRIMARY KEY, kind TEXT, topic TEXT, summary TEXT,
                                                full TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY, role TEXT, text TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS focus(key TEXT PRIMARY KEY, value TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS digests(day TEXT PRIMARY KEY, text TEXT, n INTEGER);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS vectors(kind TEXT, ref INTEGER, vec BLOB, PRIMARY KEY(kind, ref));
        """)
        try:
            _db.execute("ALTER TABLE todos ADD COLUMN due TEXT")
        except sqlite3.OperationalError:
            pass                                                   # already there
        try:
            _db.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(text, tags, content='notes', content_rowid='id');
                CREATE VIRTUAL TABLE IF NOT EXISTS research_fts USING fts5(topic, full, content='research', content_rowid='id');
                CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
                    INSERT INTO notes_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END;
                CREATE TRIGGER IF NOT EXISTS research_ai AFTER INSERT ON research BEGIN
                    INSERT INTO research_fts(rowid, topic, full) VALUES (new.id, new.topic, new.full); END;
            """)
            FTS[0] = True
        except sqlite3.OperationalError:
            FTS[0] = False
    return _db


STOP = set("a an and are as at be by did do does for from had has have he her his how i i'm in is it its me my of on or our "
           "she should so that the their them they this to was we were what when where which who why will with you your "
           "about tell know remember any anything something".split())


def _fts_query(q: str) -> str:
    words = [w for w in re.findall(r"[\w]+", q.lower()) if len(w) > 2 and w not in STOP]
    return " OR ".join(f'"{w}"' for w in words[:12])


def _ago(ts: float) -> str:
    d = time.time() - ts
    return "just now" if d < 90 else f"{int(d // 60)} min ago" if d < 5400 else f"{int(d // 3600)} h ago" if d < 172800 \
        else time.strftime("%-d %b", time.localtime(ts))


# ---------------------------------------------------------------- notes
def remember(text: str, kind: str = "fact", tags: str = "") -> str:
    text = text.strip()
    if not text:
        return "error: nothing to remember"
    with _lock:
        db = _conn()
        dup = db.execute("SELECT id FROM notes WHERE lower(text)=lower(?)", (text,)).fetchone()
        if dup:
            return "already remembered"
        db.execute("INSERT INTO notes(kind,text,tags,ts) VALUES (?,?,?,?)", (kind, text, tags, time.time()))
        db.commit()
    return "remembered"


def forget(match: str) -> str:
    with _lock:
        db = _conn()
        rows = db.execute("SELECT id,text FROM notes WHERE text LIKE ?", (f"%{match}%",)).fetchall()
        for r in rows:
            db.execute("DELETE FROM notes WHERE id=?", (r["id"],))
            db.execute("DELETE FROM vectors WHERE kind='note' AND ref=?", (r["id"],))
        try:
            db.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
        except sqlite3.OperationalError:
            pass
        db.commit()
    return f"forgot {len(rows)} note(s)" if rows else "nothing matched"


def recall(query: str, limit: int = 8) -> str:
    """Search notes, research, to-dos and past conversation for `query`."""
    out = []
    with _lock:
        db = _conn()
        q = _fts_query(query)
        like = f"%{query.strip()}%"
        if FTS[0] and q:
            notes = db.execute("SELECT n.* FROM notes_fts f JOIN notes n ON n.id=f.rowid WHERE notes_fts MATCH ? "
                               "ORDER BY rank LIMIT ?", (q, limit)).fetchall()
            res = db.execute("SELECT r.id,r.kind,r.topic,r.summary,r.ts FROM research_fts f JOIN research r ON r.id=f.rowid "
                             "WHERE research_fts MATCH ? ORDER BY rank LIMIT 4", (q,)).fetchall()
        else:
            notes = db.execute("SELECT * FROM notes WHERE text LIKE ? ORDER BY ts DESC LIMIT ?", (like, limit)).fetchall()
            res = db.execute("SELECT id,kind,topic,summary,ts FROM research WHERE topic LIKE ? OR full LIKE ? "
                             "ORDER BY ts DESC LIMIT 4", (like, like)).fetchall()
        todos = db.execute("SELECT * FROM todos WHERE text LIKE ? ORDER BY ts DESC LIMIT 5", (like,)).fetchall() if q else []
        turns = db.execute("SELECT * FROM turns WHERE text LIKE ? ORDER BY ts DESC LIMIT 5", (like,)).fetchall()
    out += [f"NOTE ({_ago(n['ts'])}): {n['text']}" for n in notes]
    out += [f"TODO [{t['status']}]: {t['text']}" for t in todos]
    out += [f"{r['kind'].upper()} #{r['id']} ({_ago(r['ts'])}) {r['topic'][:80]}: {(r['summary'] or '')[:300]}" for r in res]
    out += [f"SAID ({_ago(t['ts'])}) {t['role']}: {t['text'][:200]}" for t in turns]
    with _lock:
        dg = _conn().execute("SELECT * FROM digests WHERE text LIKE ? ORDER BY day DESC LIMIT 3", (like,)).fetchall()
    out += [f"EARLIER ({d['day']}) he said: {d['text'][:300]}" for d in dg]
    return "\n".join(out) or f"nothing in memory about '{query}'"


# ---------------------------------------------------------------- to-dos
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_due(text: str) -> str:
    """'today', 'tomorrow', 'friday', 'in 3 days', 'next week', '25 oct', '2026-11-02' -> ISO date, or '' if not a date."""
    t = (text or "").lower().strip(" .,")
    today = datetime.date.today()
    if t in ("today", "tonight", "eod", "end of day"):
        return today.isoformat()
    if t == "tomorrow":
        return (today + datetime.timedelta(days=1)).isoformat()
    if t in ("next week",):
        return (today + datetime.timedelta(days=7)).isoformat()
    if m := re.fullmatch(r"(?:next |this |on )?(" + "|".join(_DAYS) + ")", t):
        ahead = (_DAYS.index(m.group(1)) - today.weekday()) % 7 or 7
        return (today + datetime.timedelta(days=ahead)).isoformat()
    if m := re.fullmatch(r"in (\d+) (day|days|week|weeks)", t):
        return (today + datetime.timedelta(days=int(m.group(1)) * (7 if m.group(2).startswith("week") else 1))).isoformat()
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t):
        return t
    if m := re.fullmatch(r"(?:on )?(\d{1,2})(?:st|nd|rd|th)? (" + "|".join(_MONTHS) + r")[a-z]*(?: (\d{4}))?", t) or \
            re.fullmatch(r"(?:on )?(" + "|".join(_MONTHS) + r")[a-z]* (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", t):
        g = m.groups()
        day, mon = (int(g[0]), g[1]) if g[0].isdigit() else (int(g[1]), g[0])
        try:
            d = datetime.date(int(g[2]) if g[2] else today.year, _MONTHS.index(mon[:3]) + 1, day)
            if not g[2] and d < today:
                d = d.replace(year=today.year + 1)
            return d.isoformat()
        except ValueError:
            return ""
    return ""


def _due_label(due: str) -> str:
    if not due:
        return ""
    d = datetime.date.fromisoformat(due)
    delta = (d - datetime.date.today()).days
    when = "today" if delta == 0 else "tomorrow" if delta == 1 else f"{d.strftime('%a %-d %b')}"
    return f" (due {when}{', OVERDUE' if delta < 0 else ''})"


def todo_add(items: list[str], source: str = "voice", due: str = "") -> str:
    added = 0
    due_iso = parse_due(due) if due else ""
    with _lock:
        db = _conn()
        for t in items:
            t = t.strip()
            item_due = due_iso
            if m := re.search(r"\s*\b(?:by|due|before|on)\s+(.+?)[.!]?$", t, re.I):     # "call Rahul by friday"
                if (pd := parse_due(m.group(1))):
                    item_due, t = pd, t[:m.start()].strip()
            if t and not db.execute("SELECT 1 FROM todos WHERE status='open' AND lower(text)=lower(?)", (t,)).fetchone():
                db.execute("INSERT INTO todos(text,source,ts,due) VALUES (?,?,?,?)", (t, source, time.time(), item_due or None))
                added += 1
        db.commit()
    return f"added {added} to-do(s); {len(todo_rows('open'))} open"


def todo_rows(status: str = "open") -> list[sqlite3.Row]:
    with _lock:
        if status == "all":
            return _conn().execute("SELECT * FROM todos ORDER BY status DESC, ts").fetchall()
        return _conn().execute("SELECT * FROM todos WHERE status=? ORDER BY (due IS NULL), due, ts", (status,)).fetchall()


def todo_list(status: str = "open", when: str = "all") -> str:
    """when: all | today (due today or overdue) | overdue | week (next 7 days or overdue)"""
    rows = todo_rows(status)
    today = datetime.date.today().isoformat()
    if when == "today":
        rows = [r for r in rows if r["due"] and r["due"] <= today]
    elif when == "overdue":
        rows = [r for r in rows if r["due"] and r["due"] < today]
    elif when == "week":
        end = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
        rows = [r for r in rows if r["due"] and r["due"] <= end]
    if not rows:
        return "the to-do list is empty" if status == "open" and when == "all" else f"nothing {when if when != 'all' else status} on the list"
    return "\n".join(f"{i}. [{r['status']}] {r['text']}{_due_label(r['due'] or '')}"
                     + (f" (from {r['source']})" if r["source"] not in ("voice", "") else "")
                     for i, r in enumerate(rows, 1))


def todo_done(match: str, undo: bool = False, remove: bool = False) -> str:
    """Mark the to-dos matching `match` (words, or a list number) done / open again / removed."""
    with _lock:
        db = _conn()
        rows = todo_rows("done" if undo else "open")
        if match.strip().isdigit() and 0 < int(match) <= len(rows):
            hit = [rows[int(match) - 1]]
        else:
            words = [w for w in re.findall(r"\w+", match.lower()) if len(w) > 2]
            hit = [r for r in rows if words and all(w in r["text"].lower() for w in words)] or \
                  [r for r in rows if words and any(w in r["text"].lower() for w in words)][:1]
        for r in hit:
            if remove:
                db.execute("DELETE FROM todos WHERE id=?", (r["id"],))
            else:
                db.execute("UPDATE todos SET status=?, done_ts=? WHERE id=?",
                           ("open" if undo else "done", None if undo else time.time(), r["id"]))
        db.commit()
    verb = "removed" if remove else "reopened" if undo else "marked done"
    return f"{verb}: " + "; ".join(r["text"][:60] for r in hit) if hit else f"no matching to-do for '{match}'"


# ---------------------------------------------------------------- jobs & research
def save_job(kind: str, topic: str, result: str, status: str = "done") -> int:
    """Keep the whole result of a finished job. If it mentions a saved Markdown report, keep that text too."""
    full = result or ""
    for m in REPORT_PATH.finditer(result or ""):
        p = Path(m.group(0).replace("~", str(Path.home()), 1))
        if p.is_file() and p.stat().st_size < 400_000:
            full += f"\n\n--- report file {p.name} ---\n" + p.read_text(errors="replace")
            break
    with _lock:
        db = _conn()
        cur = db.execute("INSERT INTO research(kind,topic,summary,full,ts) VALUES (?,?,?,?,?)",
                         (kind if status == "done" else kind + "-" + status, topic[:300], (result or "")[:1500], full, time.time()))
        db.commit()
        return cur.lastrowid


def get_research(query: str = "", rid: int = 0, limit_chars: int = 9000) -> str:
    """The full text of a stored job/research result: by id, by search words, or the newest one."""
    with _lock:
        db = _conn()
        row = None
        if rid:
            row = db.execute("SELECT * FROM research WHERE id=?", (rid,)).fetchone()
        elif query.strip():
            q = _fts_query(query)
            if FTS[0] and q:
                row = db.execute("SELECT r.* FROM research_fts f JOIN research r ON r.id=f.rowid WHERE research_fts MATCH ? "
                                 "ORDER BY rank LIMIT 1", (q,)).fetchone()
            row = row or db.execute("SELECT * FROM research WHERE topic LIKE ? OR full LIKE ? ORDER BY ts DESC LIMIT 1",
                                    (f"%{query}%", f"%{query}%")).fetchone()
        else:
            row = db.execute("SELECT * FROM research ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return "nothing stored yet"
    full = row["full"] or row["summary"] or ""
    more = f"\n[truncated; {len(full)} chars in total - ask for a specific part]" if len(full) > limit_chars else ""
    return f"#{row['id']} {row['kind']} ({_ago(row['ts'])}) {row['topic']}\n{full[:limit_chars]}{more}"


# ---------------------------------------------------------------- conversation + focus
def add_turn(role: str, text: str):
    text = text.strip()
    if not text:
        return
    with _lock:
        db = _conn()
        db.execute("INSERT INTO turns(role,text,ts) VALUES (?,?,?)", (role, text[:1500], time.time()))
        db.commit()


def set_focus(key: str, value: str):
    with _lock:
        db = _conn()
        db.execute("INSERT INTO focus(key,value,ts) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=?, ts=?",
                   (key, value, time.time(), value, time.time()))
        db.commit()


def get_focus() -> dict[str, str]:
    with _lock:
        return {r["key"]: r["value"] for r in _conn().execute("SELECT * FROM focus WHERE ts>?", (time.time() - 6 * 3600,))}


def _limit_bytes() -> int:
    """No fixed cap: MEMORY_MAX_MB=0 lets it use up to a tenth of the free disk (at least 2 GB, at most 40 GB)."""
    if config.MEMORY_MAX_MB > 0:
        return config.MEMORY_MAX_MB * 1024 * 1024
    free = shutil.disk_usage(PATH.parent).free
    size = PATH.stat().st_size if PATH.exists() else 0
    return int(min(40e9, max(2e9, (free + size) * 0.10)))


def _meta(key: str, default: str = "") -> str:
    r = _conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _set_meta(key: str, value: str):
    _conn().execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?", (key, value, value))
    _conn().commit()


def tidy(force: bool = False) -> str:
    """Keeps memory healthy without ever throwing useful things away early:
      1. conversation older than 45 days is folded into one short digest per day (the digests are kept forever);
      2. only if the file outgrows the dynamic limit are the oldest raw turns, then the oldest job outputs, removed."""
    with _lock:
        db = _conn()
        today = datetime.date.today().isoformat()
        if not force and _meta("last_tidy") == today:
            return "already tidied today"
        cutoff = time.time() - 45 * 86400
        days: dict[str, list[str]] = {}
        old = db.execute("SELECT id,role,text,ts FROM turns WHERE ts<? ORDER BY ts", (cutoff,)).fetchall()
        for r in old:
            if r["role"] == "user":
                days.setdefault(time.strftime("%Y-%m-%d", time.localtime(r["ts"])), []).append(r["text"][:160])
        for day, lines in days.items():
            text = " | ".join(lines)[:4000]
            db.execute("INSERT INTO digests(day,text,n) VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET text=text||' | '||?, n=n+?",
                       (day, text, len(lines), text, len(lines)))
        if old:
            db.execute("DELETE FROM turns WHERE ts<?", (cutoff,))
        db.commit()
        dropped = 0
        try:
            limit = _limit_bytes()
            for table, where in (("turns", ""), ("research", "WHERE kind IN ('job','browser')")):
                while PATH.stat().st_size > limit:
                    n = db.execute(f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} {where} ORDER BY ts LIMIT 500)").rowcount
                    db.commit()
                    if not n:
                        break
                    dropped += n
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except (OSError, sqlite3.Error):
            pass
        _set_meta("last_tidy", today)
        return f"folded {len(old)} old turns into {len(days)} daily digests; dropped {dropped} rows to fit the disk"


def _prune(db: sqlite3.Connection):
    pass            # superseded by tidy(); kept so older callers keep working


def stats() -> str:
    with _lock:
        db = _conn()
        c = lambda t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]       # noqa: E731
        size = PATH.stat().st_size if PATH.exists() else 0
        return (f"memory.db {size / 1e6:.1f} MB (limit {_limit_bytes() / 1e9:.1f} GB, dynamic) | notes {c('notes')} | to-dos open "
                f"{len(todo_rows('open'))} / done {len(todo_rows('done'))} | research+jobs {c('research')} | turns {c('turns')} | digests {c('digests')}")


# ---------------------------------------------------------------- meaning-based recall (Gemini embeddings)
EMBED_MODEL, EMBED_DIM = "gemini-embedding-001", 768


def _embed(client, texts: list[str], task: str):
    import numpy as np
    from google.genai import types
    r = client.models.embed_content(model=EMBED_MODEL, contents=texts,
                                    config=types.EmbedContentConfig(task_type=task, output_dimensionality=EMBED_DIM))
    v = np.array([e.values for e in r.embeddings], dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def embed_pending(client, batch: int = 64) -> int:
    """Give every note and stored result that has no vector yet one (blocking; call from a thread)."""
    with _lock:
        db = _conn()
        rows = [("note", r["id"], r["text"]) for r in db.execute(
            "SELECT n.id, n.text FROM notes n LEFT JOIN vectors v ON v.kind='note' AND v.ref=n.id WHERE v.ref IS NULL LIMIT ?", (batch,))]
        rows += [("research", r["id"], (r["topic"] + ": " + (r["summary"] or ""))[:2000]) for r in db.execute(
            "SELECT r.id, r.topic, r.summary FROM research r LEFT JOIN vectors v ON v.kind='research' AND v.ref=r.id "
            "WHERE v.ref IS NULL LIMIT ?", (batch,))]
    if not rows:
        return 0
    vecs = _embed(client, [t for _, _, t in rows], "RETRIEVAL_DOCUMENT")
    with _lock:
        db = _conn()
        db.executemany("INSERT OR REPLACE INTO vectors(kind,ref,vec) VALUES (?,?,?)",
                       [(k, i, v.tobytes()) for (k, i, _), v in zip(rows, vecs)])
        db.commit()
    return len(rows)


def semantic_recall(client, query: str, k: int = 6, floor: float = 0.62) -> list[str]:
    """Things in memory that MEAN the same as the query, even with different words (blocking)."""
    import numpy as np
    try:
        embed_pending(client)
        q = _embed(client, [query], "RETRIEVAL_QUERY")[0]
    except Exception:
        return []
    with _lock:
        db = _conn()
        rows = db.execute("SELECT kind, ref, vec FROM vectors").fetchall()
        if not rows:
            return []
        mat = np.frombuffer(b"".join(r["vec"] for r in rows), dtype=np.float32).reshape(len(rows), EMBED_DIM)
        scores = mat @ q
        out = []
        for i in np.argsort(-scores)[:k]:
            if scores[i] < floor:
                break
            kind, ref = rows[i]["kind"], rows[i]["ref"]
            if kind == "note":
                n = db.execute("SELECT * FROM notes WHERE id=?", (ref,)).fetchone()
                if n:
                    out.append(f"NOTE ({_ago(n['ts'])}, match {scores[i]:.2f}): {n['text']}")
            else:
                r = db.execute("SELECT id,kind,topic,summary,ts FROM research WHERE id=?", (ref,)).fetchone()
                if r:
                    out.append(f"{r['kind'].upper()} #{r['id']} ({_ago(r['ts'])}, match {scores[i]:.2f}) {r['topic'][:80]}: {(r['summary'] or '')[:300]}")
    return out


# ---------------------------------------------------------------- learning from conversation
LEARN_PROMPT = ("Below is a conversation between a user and his voice assistant Jarvis. Extract ONLY durable things worth "
                "remembering long-term: facts about the user, people in his life and their relation, preferences, plans and "
                "commitments, and important decisions. Ignore one-off commands (open X, volume, time), small talk, what is on "
                "his screen right now, and anything about the assistant. NEVER include passwords, card or bank numbers, OTPs or keys.\n"
                "Facts: only NEW information. Do not repeat or rephrase anything in ALREADY KNOWN; if a fact only adds a detail to a "
                "known one, write one combined sentence with the new detail. Write each fact as one clear, specific sentence.\n"
                "To-dos: only tasks HE says he still has to do later, stated clearly ('I need to...', 'remind me to...', 'I have to...'). "
                "Not things done during this conversation, not things Jarvis did or will do, not vague ones like 'attend meeting' "
                "without what/when, and nothing already on HIS TO-DO LIST. Write each to-do as a short command ('Renew passport'), "
                "and put any deadline he gave into due (e.g. '2026-12-10', 'friday', 'tomorrow').\n"
                'Reply with JSON only: {"facts": ["full sentence", ...], "todos": [{"text": "...", "due": "today|tomorrow|friday|'
                '2026-11-02|"}]}. Use empty lists if nothing qualifies.\n\nCONVERSATION:\n')


def _new_only(client, items: list[str], existing: list[str], same: float = 0.86) -> list[str]:
    """Drop items whose meaning is already in `existing` (or repeated within `items`), using embeddings."""
    if not items:
        return []
    try:
        new = _embed(client, items, "SEMANTIC_SIMILARITY")
        old = _embed(client, existing, "SEMANTIC_SIMILARITY") if existing else None
    except Exception:
        return items                                   # no embeddings (offline): keep, the prompt already filtered
    keep, kept_vecs = [], []
    for text, v in zip(items, new):
        if old is not None and float((old @ v).max()) >= same:
            continue
        if kept_vecs and max(float(k @ v) for k in kept_vecs) >= same:
            continue
        keep.append(text); kept_vecs.append(v)
    return keep


async def learn(client, models: list[str]) -> str:
    """Let a Gemini text model read the new conversation and save the lasting facts and to-dos (runs in the background)."""
    import json
    from google.genai import types
    from llm import hedged
    with _lock:
        last = int(_meta("learned_turn", "0"))
        rows = _conn().execute("SELECT id,role,text FROM turns WHERE id>? ORDER BY id LIMIT 120", (last,)).fetchall()
    if sum(1 for r in rows if r["role"] == "user") < 6:
        return "not enough new conversation yet"
    convo = "\n".join(f"{'User' if r['role'] == 'user' else 'Jarvis'}: {r['text'][:300]}" for r in rows)
    with _lock:
        known = [r["text"] for r in _conn().execute("SELECT text FROM notes ORDER BY ts DESC LIMIT 60")]
    todo_now = [r["text"] for r in todo_rows("open")][:40]
    context = ("ALREADY KNOWN:\n" + ("\n".join(f"- {k}" for k in known) or "- (nothing)") +
               "\n\nHIS TO-DO LIST:\n" + ("\n".join(f"- {t}" for t in todo_now) or "- (empty)") + "\n\n")
    cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
    _model, text = await hedged(client, list(models), LEARN_PROMPT + context + "CONVERSATION:\n" + convo, cfg, hedge_s=8)
    try:
        data = json.loads(text)
    except ValueError:
        return "could not parse the answer"
    facts = [f for f in data.get("facts", []) if isinstance(f, str) and 8 < len(f) < 300]
    todos = [t for t in data.get("todos", []) if isinstance(t, dict) and t.get("text") and len(str(t["text"]).split()) >= 2]
    # second line of defence against near-duplicates: compare meanings with what is already stored
    facts = await asyncio.to_thread(_new_only, client, facts, known)
    todo_texts = await asyncio.to_thread(_new_only, client, [str(t["text"]) for t in todos], todo_now)
    for f in facts:
        remember(f, "fact", "auto")
    for t in todos:
        if str(t["text"]) in todo_texts:
            todo_add([t["text"]], "conversation", t.get("due") or "")
    todos = [t for t in todos if str(t["text"]) in todo_texts]
    with _lock:
        _set_meta("learned_turn", str(rows[-1]["id"]))
    return f"learned {len(facts)} facts and {len(todos)} to-dos"


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        print(stats())
    elif cmd == "search":
        print(recall(" ".join(sys.argv[2:])))
    elif cmd == "todos":
        print(todo_list("open"))
    elif cmd == "tidy":
        print(tidy(force=True))
    elif cmd == "export":
        import json
        out = {t: [dict(r) for r in _conn().execute(f"SELECT * FROM {t}")] for t in ("notes", "todos", "research", "digests")}
        Path(sys.argv[2] if len(sys.argv) > 2 else "memory_export.json").write_text(json.dumps(out, indent=1, default=str))
        print("exported")
    else:
        print("usage: python memory.py [stats|search WORDS|todos|tidy|export FILE]")


# ---------------------------------------------------------------- what the models are told at start
def prompt_block(max_notes: int = 25, short: bool = False) -> str:
    """Compact memory summary for the system prompt (also tells the model the tools to dig deeper)."""
    with _lock:
        db = _conn()
        notes = db.execute("SELECT * FROM notes ORDER BY ts DESC LIMIT ?", (max_notes,)).fetchall()
        research = db.execute("SELECT id,kind,topic,ts FROM research ORDER BY ts DESC LIMIT ?", (2 if short else 6,)).fetchall()
        turns = [] if short else db.execute("SELECT role,text FROM turns ORDER BY id DESC LIMIT 8").fetchall()[::-1]
    todos = todo_rows("open")
    lines = []
    if notes:
        lines.append("Things he told you to remember:\n" + "\n".join(f"- {n['text']}" for n in notes))
    lines.append(f"His to-do list ({len(todos)} open):\n" + ("\n".join(f"- {t['text']}{_due_label(t['due'] or '')}" for t in todos[:15]) if todos else "- (empty)"))
    if research:
        lines.append("Recent work you did for him (full text is stored; use recall_research to read it all out):\n"
                     + "\n".join(f"- #{r['id']} {r['topic'][:70]} ({_ago(r['ts'])})" for r in research))
    focus = get_focus()
    if focus:
        lines.append("What he was last doing: " + "; ".join(f"{k}: {v}" for k, v in focus.items()))
    if turns:
        lines.append("End of your previous conversation:\n" + "\n".join(f"{'He' if r['role'] == 'user' else 'You'}: {r['text'][:160]}" for r in turns))
    return "\n".join(lines)
