"""The shared assistant: mic gating, the window, tools/plans/jobs/routines, reminders, the status orb, and the
switch between brains (Gemini in the cloud, Qwen on this PC). A brain only implements how speech reaches a model
and back, so everything running (jobs, memory, cursor) survives a switch."""
import asyncio
import collections
import logging
import re
import time

import config
import screen
from audio import pick_route
from daymem import DayMemory
from jobs import Job, Jobs
from routines import Routines
from tools import Toolbox, describe, is_failure

log = logging.getLogger("jarvis")
QUIET_TOOLS = {"update_step", "record_approval", "job_status"}     # shown in the plan card, not as activity rows


BRAIN_NAMES = {
    # Whisper spells "Qwen" many ways: Quen, QN, Gwen, Quinn, Kwan, Q W E N...
    "qwen": r"(?:qwen|quen|kwen|gwen|queen|chwen|qn|q n|q w e n|quin|quinn|kwan|cuen|kuen|coin|local|offline|on[- ]device)",
    "gemini": r"(?:gemini|jemini|gemin[iy]|jiminy|cloud|online)",
}
SWITCH_VERB = r"(?:switch|change|swap|move|go|come|get)(?: back)?(?: over)?(?: to| with| into| onto)?"
STATE_FILE = config.STATE_DIR / "brain.txt"


def parse_switch(text: str) -> str | None:
    """'switch to qwen', 'go back to gemini', 'use the local one'... -> 'qwen' | 'gemini' (short commands only)."""
    t = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or len(t.split()) > 10:
        return None
    for name, pat in BRAIN_NAMES.items():
        tail = r"(?: brain| model| engine| one| mode| now| instead| please| sir)*$"
        if re.search(rf"\b{SWITCH_VERB} (?:the |your )?(?:brain |model |engine )?{pat}{tail}", t) or \
           re.search(rf"\b(?:use|activate|bring in|bring back|put on|talk to|let me talk to) (?:the |your )?{pat}{tail}", t):
            return name
    return None


class Assistant:
    def __init__(self, ui, speaker=None):
        self.ui, self.speaker = ui, speaker
        self.loop = asyncio.get_running_loop()
        self.client = None            # Gemini client: ask_gemini, screen vision and the Gemini brain use it
        if config.GEMINI_API_KEY:
            from google import genai
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.brains: dict = {}        # name -> brain, filled in by Jarvis
        self.active = ""              # name of the brain currently listening and speaking
        self.switching = ""           # name of the brain being warmed up for a switch
        self.run_task: asyncio.Task | None = None
        self.recent: collections.deque = collections.deque(maxlen=24)   # (role, text) carried over on a switch
        self.pending_notes: list[str] = []
        self._switch_asked = ""       # utterance already handled as a voice switch
        self.jobs = Jobs(self._job_changed, self._job_finished)
        self.day = DayMemory()        # today's plan and what is done/left, until the night log
        self.routines = Routines(self)
        self.tools = Toolbox(self)
        self.connected = False
        # audio / visual state
        self.muted = False
        self.half_duplex = False
        self.echo_tail = 0.4          # seconds the mic stays muted after Jarvis stops talking
        self.mic_level = 0.0
        self.noise_floor = 0.01       # adaptive room-noise estimate (RMS)
        self.mic_chunks = 0
        self.preroll = collections.deque(maxlen=10)   # ~320 ms kept so the first syllable isn't clipped
        self.gate_until = 0.0         # noise gate stays open until this time
        self.last_voice = 0.0
        self.last_audio = 0.0
        self.heard = False
        self.awaiting = False
        self.await_since = 0.0
        self.busy = 0
        self.mic = None               # audio.Mic, set by agent.py; re-pointed when headphones come and go
        self.route_label = ""
        self.pointer = None           # on-screen cursor (created on first use)
        self.last_narration = 0.0
        self.mute_output = False      # user pressed stop: drop audio until the turn ends
        # turn bookkeeping
        self.seq = 0
        self.cap_who = None
        self.user_utt = ""

    # ---- the active brain --------------------------------------------------------------
    @property
    def brain(self):
        return self.brains.get(self.active)

    def feed_audio(self, chunks: list[bytes], voice: bool, now: float):
        """Audio thread: gated mic audio (silence while the gate is closed) goes to the active brain."""
        if self.brain and self.connected:
            self.brain.feed_audio(chunks, voice, now)

    async def send_text(self, text: str):
        """A typed command or a button press, as the user's words."""
        if self.brain:
            await self.brain.send_text(text)

    async def notify(self, note: str):
        """A system note for the model (reminder due, job finished) - not a user request."""
        if self.brain and self.connected:
            await self.brain.notify(note)
        else:
            self.pending_notes.append(note)

    def job_note(self, job: Job) -> str:
        return (f"[Background job update - a system notification, not a request from {config.USER_NAME}] "
                f"{job.label[:100]} -> {job.id} {job.status}: {job.result[:job.deliver_chars]}\n"
                + (job.instruction or f"(Tell {config.USER_NAME} the outcome in {job.report}. Don't add "
                                      "details that aren't in this result.)"))

    async def deliver_job(self, job: Job):
        if self.brain and self.connected:
            await self.brain.deliver_job(job)
        else:
            self.pending_notes.append(self.job_note(job))

    def take_notes(self) -> list[str]:
        notes, self.pending_notes = self.pending_notes, []
        return notes

    def remember(self, role: str, text: str):
        """Conversation so far, handed to the other brain when you switch."""
        text = text.strip()
        if text and not (self.recent and self.recent[-1] == (role, text)):
            self.recent.append((role, text[:400]))

    # ---- switching brains ----------------------------------------------------------------
    def brain_status(self) -> dict:
        out = {}
        for k, b in self.brains.items():
            ok, why = b.available()
            out[k] = {"label": b.label, "ok": ok, "why": why}
        return out

    def send_engine(self):
        self.ui.send(type="engine", active=self.active, switching=self.switching, brains=self.brain_status())

    def initial_brain(self) -> str:
        want = config.BRAIN
        if want == "auto":
            try:
                want = STATE_FILE.read_text().strip()
            except OSError:
                want = "gemini"
        order = [want] + [k for k in self.brains if k != want]
        return next((k for k in order if k in self.brains and self.brains[k].available()[0]), "")

    def request_switch(self, name: str, source: str = "voice") -> str:
        """Start a switch in the background; returns what to tell the user."""
        name = {"local": "qwen", "cloud": "gemini"}.get(name, name)
        brain = self.brains.get(name)
        if not brain:
            return f"error: unknown brain '{name}' (known: {', '.join(self.brains)})"
        if name == self.active and not self.switching:
            return f"{brain.label} is already the active brain; nothing to do."
        if self.switching == name:
            return f"Already switching to {brain.label}."
        ok, why = brain.available()
        if not ok:
            self.ui.send(type="notice", text=f"{brain.label} isn't available: {why}")
            return f"error: {brain.label} isn't available ({why})"
        asyncio.create_task(self._do_switch(name, source))
        return (f"Switching to {brain.label} now: it warms up while you keep talking, then takes over by itself. "
                "Say a few words that you are switching.")

    async def _wait_quiet(self, cap: float):
        """Let the current brain finish the sentence it is saying; never wait for it to *think* (a slow model
        could still be pondering the very command that asked for the switch)."""
        t0, quiet_since = time.monotonic(), None
        while time.monotonic() - t0 < cap:
            await asyncio.sleep(0.15)
            quiet_since = None if self.speaking() else (quiet_since or time.monotonic())
            if quiet_since and time.monotonic() - quiet_since > 0.4:
                return

    async def _do_switch(self, name: str, source: str):
        brain = self.brains[name]
        self.switching = name
        self.send_engine()
        t0 = time.monotonic()
        try:
            self.ui.send(type="notice", text=f"Warming up {brain.label}…")
            await brain.prepare()                         # the old brain keeps serving meanwhile
            if source == "voice":
                await self._wait_quiet(5)
            old, old_task = self.brain, self.run_task
            if old_task:
                old_task.cancel()
                await asyncio.gather(old_task, return_exceptions=True)
            if self.speaker:
                self.speaker.clear()
            self.mute_output = False
            self.awaiting = False
            brain.adopt(list(self.recent))
            self.active = name
            self.run_task = asyncio.create_task(brain.run())
            try:
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                STATE_FILE.write_text(name)
            except OSError:
                pass
            log.info("switched to %s in %.1fs", name, time.monotonic() - t0)
            if old:
                asyncio.create_task(old.release())        # e.g. free the GPU after a while
            asyncio.create_task(self._greet(brain))
        except Exception as e:
            log.exception("switch to %s failed", name)
            self.ui.send(type="notice", text=f"Couldn't switch to {brain.label}: {str(e)[:120]}")
        finally:
            self.switching = ""
            self.send_engine()
            self.ui.send(type="notice", text="")

    async def _greet(self, brain):
        for _ in range(100):                              # up to ~10 s for the new brain to be ready
            if self.connected and self.brain is brain:
                break
            await asyncio.sleep(0.1)
        else:
            return
        try:
            await brain.greet()
        except Exception as e:
            log.warning("greeting failed: %s", e)

    # ---- inputs from the window and the microphone -------------------------
    def on_ui(self, m: dict):
        t = m.get("type")
        if t == "mute":
            self.muted = bool(m.get("value"))
        elif t == "text":
            text = str(m.get("text", "")).strip()[:2000]
            if text:
                self.user_said(text, typed=True)
                asyncio.create_task(self.send_text(text))
        elif t == "stop":
            self.stop_speaking()
        elif t == "approve":
            ok = bool(m.get("value"))
            self.tools.set_approval(ok, "pressed the button")
            asyncio.create_task(self.send_text(
                "(I pressed Approve on the plan, go ahead.)" if ok else "(I pressed Cancel on the plan, don't do it.)"))
        elif t == "cancel_job":
            self.jobs.cancel(str(m.get("id", "")))
        elif t == "engine":
            self.request_switch(str(m.get("value", "")), "ui")

    def on_mic(self, data: bytes, rms: float):
        """Audio thread. While Jarvis talks (and echo is possible) the mic is muted so he doesn't hear and
        obey himself; otherwise a noise gate only lets real speech through."""
        now = time.monotonic()
        echo = self.half_duplex and self.speaker is not None and (
            self.speaker.playing or now - self.speaker.last_sound < self.echo_tail)
        if not echo:      # noise floor: falls fast, rises slowly, so speech doesn't drag it up
            f = self.noise_floor
            self.mic_chunks += 1
            rate = 0.2 if self.mic_chunks < 45 else (0.1 if rms < f else 0.002)   # calibrate in the first ~1.5 s
            self.noise_floor = f + rate * (rms - f)
        voice = rms > max(0.004, self.noise_floor * 2.5) and not echo and not self.muted
        self.mic_level = 0.0 if echo or self.muted else min(1.0, max(0.0, rms - self.noise_floor) * 10)
        if voice:
            self.last_voice, self.heard = now, True

        if not self.connected or self.muted:
            return
        chunks = [data]
        if echo:
            chunks = [bytes(len(data))]      # keep the stream flowing, but silent
            self.preroll.clear()
        elif config.NOISE_GATE:
            if voice:
                if now >= self.gate_until:   # gate opening: send the audio just before the voice too
                    chunks = list(self.preroll) + chunks
                    self.preroll.clear()
                self.gate_until = now + 0.6
            if now >= self.gate_until:
                self.preroll.append(data)
                chunks = [bytes(len(data))]
        self.feed_audio(chunks, voice, now)

    def stop_speaking(self):
        if self.speaker:
            self.speaker.clear()
        self.mute_output = True
        if self.pointer and self.pointer.ok:
            self.pointer.hide()

    # ---- UI helpers ----------------------------------------------------------------
    def caption(self, who: str, text: str, new: bool | None = None):
        new = self.cap_who != who if new is None else new
        self.cap_who = who
        self.ui.send(type="caption", who=who, text=text, new=new)

    def activity(self, aid: str, kind: str, label: str, state: str, detail: str = "", job: str = ""):
        self.ui.send(type="activity", id=aid, kind=kind, label=label, state=state, detail=detail, job=job)

    def user_said(self, text: str, typed: bool = False, new: bool | None = None) -> bool:
        """Show the user's words; returns True when this starts a new request."""
        fresh = (self.cap_who != "you" or typed) if new is None else new
        if fresh:
            self.tools.utterance += 1
            self.tools.calls.clear()
            self.user_utt = ""
            if self.brain:
                self.brain.on_new_request()
        self.user_utt += text
        self.caption("you", text, new=True if typed else (True if new else None))
        decision = self.tools.on_user_words(self.user_utt)
        if decision:
            log.info("plan %s by the user's words: %r", decision, self.user_utt)
        target = parse_switch(self.user_utt)               # "switch to qwen": handled in code, not left to a model
        if target and target != self._switch_asked and (target != self.active or self.switching):
            self._switch_asked = target
            log.info("voice command: switch to %s", target)
            self.request_switch(target, "voice")
        elif not target:
            self._switch_asked = ""
        return fresh

    # ---- tools -------------------------------------------------------------------------
    async def run_tool(self, name: str, a: dict, fc, gen: int = 0):
        """Run one tool call with activity rows, plan-step tracking and logging. Returns str or Job."""
        kind, label = describe(name, a)
        self.seq += 1
        aid = f"a{self.seq}"
        quiet = name in QUIET_TOOLS
        if not quiet:
            self.activity(aid, kind, label, "running")
        steps = self.tools.link_steps(name)
        log.info("→ %s %s", name, a)
        self.busy += 1
        try:
            out = await self.tools.execute(name, a, fc, gen, steps)
        except Exception as e:
            log.exception("tool %s crashed", name)
            out = f"error: {e}"
        finally:
            self.busy -= 1
        if isinstance(out, Job):
            out.activity_id, out.plan_steps = aid, steps
            self.activity(aid, out.kind, label, "running", out.progress or "starting…", job=out.id)
            return out
        out = str(out)
        log.info("← %s", out[:200].replace("\n", " | "))
        if not quiet:
            fail = is_failure(out)
            self.activity(aid, kind, label, "fail" if fail else "ok", out.splitlines()[0][:110] if fail else "")
            if steps:
                self.tools.finish_steps(steps, not fail)
        return out

    # ---- seeing the screen, pointing -----------------------------------------------
    def _capture_clean(self):
        """Screenshot without Jarvis' own cursor in it (it would cover, or be mistaken for, the target)."""
        if self.pointer and self.pointer.ok:
            self.pointer.hide(now=True)
            time.sleep(0.2)
        return screen.capture()

    async def look_at_screen(self, question: str) -> str:
        if not self.client:
            return "error: screen understanding needs the Gemini engine"
        try:
            img = await asyncio.to_thread(self._capture_clean)
            return await screen.describe(self.client, question, img)
        except Exception as e:
            return f"error: couldn't look at the screen ({str(e)[:150]})"

    async def point_at(self, target: str, hold: float = 8.0) -> str:
        if not self.client:
            return "error: pointing needs the Gemini engine"
        try:
            img = await asyncio.to_thread(self._capture_clean)
            hit = await screen.locate(self.client, target, img)
        except Exception as e:
            return f"error: couldn't look at the screen ({str(e)[:150]})"
        if not hit:
            return f"not found: I can't see '{target}' on the screen right now"
        if self.pointer is None:
            from pointer import Pointer
            self.pointer = await asyncio.to_thread(Pointer)
        if not self.pointer.ok:
            return f"error: the on-screen pointer isn't available ({self.pointer.error[:100]})"
        self.pointer.point(hit["x"], hit["y"], hit["label"], hit["box"], max(2.0, min(float(hold or 8), 30.0)))
        log.info("pointing at %r (%.0f, %.0f) via %s", hit["label"], hit["x"], hit["y"], hit["model"])
        return f"pointing at {hit['label']} (done; the cursor stays on screen for a few seconds)"

    def clear_pointer(self) -> str:
        if self.pointer and self.pointer.ok:
            self.pointer.hide()
        return "pointer hidden"

    # ---- narration of long jobs ------------------------------------------------------
    async def _narrator(self):
        """Long Claude jobs shouldn't be silent: say what is happening now, at most every ~16 s, and only when
        nobody is talking. If nothing new happens for a while, say it is still going."""
        MIN_GAP, FIRST_AFTER, HEARTBEAT, GLOBAL_GAP = 16.0, 6.0, 40.0, 8.0
        seen: dict[str, dict] = {}
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            quiet = (self.connected and not self.speaking() and not self.awaiting and not self.busy
                     and now - self.last_voice > 2.0 and now - self.last_narration > GLOBAL_GAP)
            for job in self.jobs.running():
                if job.kind not in ("claude", "chrome") or not job.task_id:
                    continue
                st = seen.setdefault(job.id, {"seq": 0, "t": now - (MIN_GAP - FIRST_AFTER), "said": ""})
                fresh = (job.progress_seq > st["seq"] and job.progress and job.progress != st["said"]
                         and not re.match(r"(here'?s|here is|done|all done|finished|that'?s)\b", job.progress, re.I))
                beat = not fresh and now - st["t"] >= HEARTBEAT and job.progress
                if not quiet or not (fresh and now - st["t"] >= MIN_GAP or beat):
                    continue
                what = job.label[:70]
                if fresh:
                    note = (f"[Progress narration - a system note, not a request] Tell {config.USER_NAME}, in the "
                            f"first person and at most 12 words, what you are doing right now: \"{job.progress}\". "
                            "Just say it. Do not call tools or ask anything.")
                else:
                    note = (f"[Progress narration - a system note, not a request] The long job \"{what}\" is still "
                            f"running; the latest step was \"{job.progress}\". In at most 12 words, tell "
                            f"{config.USER_NAME} you are still on it and what you are doing. Do not call tools.")
                st.update(seq=job.progress_seq, t=now, said=job.progress)
                self.last_narration = now
                log.info("narrating %s: %s", job.id, job.progress)
                await self.notify(note)
                break                                     # one job per tick

    # ---- reminders & jobs ------------------------------------------------------------
    def set_reminder(self, minutes: float, message: str) -> str:
        if not 0 < minutes <= 24 * 60:
            return "error: minutes must be between 0 and 1440"
        when = time.strftime("%H:%M", time.localtime(time.time() + minutes * 60))
        self.loop.call_later(minutes * 60, lambda: asyncio.create_task(self._remind(message)))
        return f"reminder set for {when}"

    async def _remind(self, message: str):
        import desktop
        await asyncio.to_thread(desktop.notify, "Reminder", message)
        await self.notify(f"[Reminder due - a system notification] Politely remind {config.USER_NAME} now: {message}")

    def _job_changed(self, job: Job):
        if job.step_marks and job.plan_steps:
            self.tools.mark_steps(job.step_marks, job.plan_steps)
        state = {"running": "running", "done": "ok"}.get(job.status, "fail")
        detail = job.progress if job.status == "running" else job.result.splitlines()[0][:110]
        self.activity(job.activity_id or job.id, job.kind, job.label, state, detail, job=job.id)

    def _job_finished(self, job: Job):
        if job.plan_steps:
            self.tools.finish_steps(job.plan_steps, job.status == "done")
        asyncio.create_task(self._finish_job(job))

    async def _finish_job(self, job: Job):
        try:
            await self.routines.on_finished(job)       # routine jobs: save tasks, close the day, show the report
        except Exception:
            log.exception("post-processing %s failed", job.id)
        await self.deliver_job(job)

    # ---- main loop & visual state ----------------------------------------------------
    async def run(self, stop: asyncio.Event):
        self._apply_route(await asyncio.to_thread(pick_route))
        first = self.initial_brain()
        log.info("brains: %s | starting with %s | echo guard %s (tail %.1fs)", ", ".join(self.brains), first or "none",
                 "on" if self.half_duplex else "off", self.echo_tail)
        from browser_bridge import bridge
        await bridge.start()
        tasks = [asyncio.create_task(c) for c in (self._ticker(), self._watch_audio_route(), self._narrator())]
        self.send_engine()
        if not first:
            self.ui.send(type="notice", text="No brain available: add GEMINI_API_KEY or install the local model.")
        else:
            try:
                self.ui.send(type="notice", text=f"Starting {self.brains[first].label}…")
                await self.brains[first].prepare()
            except Exception as e:                       # e.g. the local model failed to load: fall back
                log.exception("could not start %s", first)
                other = next((k for k in self.brains if k != first and self.brains[k].available()[0]), "")
                self.ui.send(type="notice", text=f"{self.brains[first].label} failed to start ({str(e)[:80]})"
                                                 + (f"; using {self.brains[other].label}" if other else ""))
                first = other
            if first:
                self.active = first
                self.run_task = asyncio.create_task(self.brains[first].run())
                self.ui.send(type="notice", text="")
        self.send_engine()
        try:
            await stop.wait()
        finally:
            for t in tasks + ([self.run_task] if self.run_task else []):
                t.cancel()

    def _apply_route(self, route):
        self.half_duplex = self._want_half_duplex(route.echo_possible)
        self.echo_tail = route.tail
        if route.label != self.route_label:
            self.route_label = route.label
            log.info("audio: %s | echo guard %s", route.label, "on" if self.half_duplex else "off")
            self.ui.send(type="route", label=route.label, headset=route.headset)

    def _want_half_duplex(self, echo_possible: bool) -> bool:
        return self.speaker is not None and (
            config.ECHO_GUARD == "on" or (config.ECHO_GUARD == "auto" and echo_possible))

    async def _watch_audio_route(self):
        """Follow the system's audio devices: when headphones connect or disconnect, the speaker moves by itself
        (PipeWire), and the microphone is re-pointed here without restarting Jarvis."""
        while True:
            await asyncio.sleep(3)
            route = await asyncio.to_thread(pick_route)
            if self.mic and self.mic.device is None and route.source != self.mic.source:
                log.info("microphone %s -> %s", self.mic.source, route.source)
                try:
                    await asyncio.to_thread(self.mic.reopen, route.source)
                    self.noise_floor, self.mic_chunks = 0.01, 0          # recalibrate to the new mic
                except Exception as e:
                    log.warning("could not switch microphone: %s", e)
            self._apply_route(route)

    def speaking(self) -> bool:
        return self.speaker.playing if self.speaker else time.monotonic() - self.last_audio < 0.4

    def compute_state(self) -> str:
        now = time.monotonic()
        if not self.connected:
            return "working" if self.switching else "offline"
        if self.speaking():
            return "speaking"
        if not self.muted and now - self.last_voice < 0.6:
            return "listening"
        if self.awaiting:
            return "thinking"
        if self.busy or self.jobs.running():
            return "working"
        if self.tools.plan and self.tools.plan.get("approval") == "pending":
            return "waiting"
        return "idle"

    async def _ticker(self):
        """~20 Hz: derive the visual state and push it to the window."""
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            if self.heard and now - self.last_voice > 0.7 and not self.awaiting and not self.speaking():
                self.heard, self.awaiting, self.await_since = False, True, now
            if self.awaiting and now - self.await_since > 30:
                self.awaiting = False
            self.ui.send(type="tick", state=self.compute_state(), mic=round(self.mic_level, 3),
                         out=round(self.speaker.level if self.speaker else 0.0, 3), tasks=len(self.jobs.running()))
