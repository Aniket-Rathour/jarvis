"""Background jobs: Claude runs, package installs, GNOME extensions, Gemini 'brain' questions.
Each job remembers the Gemini function call that started it, so Jarvis can answer that call when it ends."""
import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field

from google.genai import types

import config
import delegate
from llm import hedged

log = logging.getLogger("jarvis.jobs")
HEDGE_S = 6
BRAIN_SYSTEM = ("You are the reasoning engine behind a voice assistant. Answer accurately and concisely in plain "
                "spoken sentences: no markdown, no lists, no code blocks, under 120 words unless more is truly needed.")


@dataclass
class Job:
    id: str
    kind: str                  # claude | chrome | install | extension | think
    label: str
    key: str = ""              # what it does, for duplicate detection
    fc_id: str | None = None   # Gemini function call to answer when finished
    fc_name: str | None = None
    session_gen: int = 0
    activity_id: str = ""
    status: str = "running"    # running | done | failed | cancelled
    result: str = ""
    progress: str = ""
    progress_seq: int = 0      # bumps whenever the progress phrase changes (the narrator watches this)
    meta: dict = field(default_factory=dict)          # e.g. {"routine": "night_log", "turn": 2}
    deliver_chars: int = 2500                         # how much of the result Gemini gets to read
    instruction: str = ""                             # overrides the default "tell him the outcome in <report>" line
    report: str = "ONE short sentence"     # how much to say when it finishes ("up to 4 short sentences" for research)
    started: float = field(default_factory=time.time)
    task_id: str | None = None
    plan_steps: list = field(default_factory=list)   # plan steps this job performs
    step_marks: dict = field(default_factory=dict)   # steps Claude reported done/failed
    proc: asyncio.subprocess.Process | None = None


class Jobs:
    def __init__(self, on_change, on_finish):
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)
        self._on_change, self._on_finish = on_change, on_finish

    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.status == "running"]

    def find_running(self, key: str) -> Job | None:
        return next((j for j in self.running() if j.key == key), None)

    def _new(self, kind: str, label: str, key: str, fc, gen: int) -> Job:
        job = Job(id=f"job{next(self._ids)}", kind=kind, label=label, key=key,
                  fc_id=getattr(fc, "id", None), fc_name=getattr(fc, "name", None), session_gen=gen)
        self.jobs[job.id] = job
        return job

    def _finish(self, job: Job, status: str, text: str):
        if job.status != "running":
            return
        job.status, job.result = status, (text or "").strip() or "(no output)"
        log.info("%s %s: %s", job.id, status, job.result[:200])
        self._on_change(job)
        self._on_finish(job)

    # ---- Claude (terminal or Chrome) ------------------------------------
    def claude(self, task: str, browser: bool, interactive: bool, workdir: str, fc, gen: int,
               steps: list[tuple[int, str]] | None = None, complexity: str = "medium", report: str = "",
               label: str = "", start_kw: dict | None = None, meta: dict | None = None,
               deliver_chars: int = 0) -> Job:
        job = self._new("chrome" if browser else "claude", label or task, task, fc, gen)
        if report:
            job.report = report
        job.meta = meta or {}
        if deliver_chars:
            job.deliver_chars = deliver_chars
        model, effort = config.claude_model_for(complexity)
        job.progress = f"{model.replace('claude-', '')} · {effort} effort"
        log.info("%s: %s task -> %s (%s effort)", job.id, complexity, model, effort)
        info = delegate.start(task, browser, interactive, workdir, steps, model, effort, **(start_kw or {}))
        job.task_id = info["task_id"]
        if interactive:
            asyncio.get_running_loop().call_later(
                0.5, self._finish, job, "done", "Opened a live Claude session in a terminal; the user is steering it.")
        else:
            asyncio.create_task(self._watch_claude(job))
        return job

    def opencode(self, task: str, interactive: bool, workdir: str, fc, gen: int,
                 steps: list[tuple[int, str]] | None = None) -> Job:
        """OpenCode on the local Qwen model; watched exactly like a Claude run."""
        job = self._new("claude", f"[OpenCode·Qwen] {task}", task, fc, gen)
        job.progress = f"opencode · {config.OPENCODE_MODEL}"
        info = delegate.start_opencode(task, workdir, interactive, steps=steps)
        job.task_id = info["task_id"]
        if interactive:
            asyncio.get_running_loop().call_later(
                0.5, self._finish, job, "done", "Opened a live OpenCode session in a terminal; the user is steering it.")
        else:
            asyncio.create_task(self._watch_claude(job))
        return job

    async def _watch_claude(self, job: Job):
        while job.status == "running":
            await asyncio.sleep(1.5)
            status, text, progress = await asyncio.to_thread(delegate.poll, job.task_id, job.started)
            marks = await asyncio.to_thread(delegate.step_marks, job.task_id)
            if (progress and progress != job.progress) or marks != job.step_marks:
                if progress and progress != job.progress:
                    job.progress_seq += 1
                job.progress, job.step_marks = progress or job.progress, marks
                self._on_change(job)
            if status != "running":
                self._finish(job, status, text)

    # ---- plain processes (dnf/flatpak/gnome extension) ------------------
    def process(self, argv: list[str], kind: str, label: str, fc, gen: int, interpret=None) -> Job:
        job = self._new(kind, label, " ".join(argv), fc, gen)
        asyncio.create_task(self._run_proc(job, argv, interpret))
        return job

    async def _run_proc(self, job: Job, argv: list[str], interpret):
        lines: list[str] = []
        try:
            job.proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            last_push = 0.0
            async for raw in job.proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                lines.append(line)
                job.progress = line[-100:]
                if time.monotonic() - last_push > 0.5:
                    last_push = time.monotonic()
                    self._on_change(job)
            rc = await job.proc.wait()
        except OSError as e:
            self._finish(job, "failed", str(e))
            return
        out = "\n".join(lines[-15:])
        if interpret:
            status, text = interpret(rc, out)
        elif rc == 0:
            status, text = "done", out[-800:] or "finished"
        elif argv[0] == "pkexec" and rc in (126, 127):
            status, text = "cancelled", "the password prompt was dismissed or authorization failed"
        else:
            status, text = "failed", f"exit {rc}: {out[-800:]}"
        self._finish(job, status, text)

    # ---- Gemini text models ---------------------------------------------
    def think(self, question: str, client, fc, gen: int) -> Job:
        job = self._new("think", question, question, fc, gen)
        asyncio.create_task(self._think(job, question, client))
        return job

    async def _think(self, job: Job, question: str, client):
        cfg = types.GenerateContentConfig(system_instruction=BRAIN_SYSTEM)

        def progress(model):
            job.progress = f"asking {model}"
            self._on_change(job)
        try:
            model, text = await hedged(client, list(config.BRAIN_MODELS), question, cfg, hedge_s=HEDGE_S, on_try=progress)
        except RuntimeError as e:
            self._finish(job, "failed", "the Gemini text models are busy right now; answer the question yourself, "
                                        f"briefly ({e})")
            return
        log.info("brain answer from %s", model)
        self._finish(job, "done", text)

    # ---- control ----------------------------------------------------------
    def cancel(self, job_id: str) -> str:
        job = self.jobs.get(job_id)
        if not job or job.status != "running":
            return f"no running job {job_id}"
        if job.task_id:
            delegate.cancel(job.task_id)
        if job.proc and job.proc.returncode is None:
            job.proc.terminate()
        self._finish(job, "cancelled", "cancelled by the user")
        return f"cancelled {job_id}"

    def summary(self) -> str:
        if not self.jobs:
            return "no jobs yet"
        rows = []
        for j in list(self.jobs.values())[-8:]:
            age = int(time.time() - j.started)
            extra = j.progress if j.status == "running" else j.result[:160]
            rows.append(f"{j.id} [{j.kind}] {j.status} ({age}s): {j.label[:70]} -- {extra}")
        return "\n".join(rows)
