"""Gemini brain: one Gemini Live session (auto-reconnecting, resumable) that hears, thinks and speaks."""
import asyncio
import logging
import re
import time

from google.genai import types

import config
from audio import IN_RATE
from jobs import Job
from prompt import system_prompt
from tools import TOOLS

log = logging.getLogger("jarvis")

# Safety net: the model said it would act but made no tool call since the user last spoke.
PROMISE = re.compile(r"\b(i'?ll|i will|i'?m going to|let me|have claude|handing|passing (it|this)|on it|opening|"
                     r"launching|starting|sending|creating|running|searching|installing)\b", re.I)
NUDGE = ("[System note] You told the user you would act but did not call any tool, so nothing happened. "
         "Call the correct tool now (delegate_to_claude if unsure). Do not apologise at length.")


def promised_but_idle(said: str) -> bool:
    said = said.strip()
    return bool(said) and not said.endswith("?") and bool(PROMISE.search(said))


class Reconnect(Exception):
    pass


class GeminiBrain:
    key = "gemini"
    label = "Gemini"

    def __init__(self, a):
        self.a = a                                  # the shared assistant (ui, speaker, jobs, tools, memory…)
        self.mic_q: asyncio.Queue = asyncio.Queue(maxsize=60)
        self.session = None
        self.gen = 0                                # increments per connection; stale tool responses are dropped
        self.resume_handle = None
        self.turn_said = ""
        self.tools_since_user = False
        self.nudged = False
        self.skip_nudge = False
        self._logged_utt = ""
        self.handoff = ""                           # recent conversation from the other brain, for the next connect

    # ---- availability / lifecycle --------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        return (True, "") if self.a.client else (False, "no GEMINI_API_KEY in .env")

    async def prepare(self):
        """Nothing to warm up: the cloud connection is made when this brain starts running."""

    async def release(self):
        pass

    def adopt(self, history: list[tuple[str, str]]):
        """Take over the conversation from the other brain: goes into the instructions at the next connect."""
        self.resume_handle = None                   # a resumed session would still hold the OLD conversation
        self.handoff = "\n".join(f"{'Sir' if r == 'user' else 'You'}: {t}" for r, t in history[-10:])

    async def greet(self):
        if self.session:
            self.skip_nudge = True
            await self.session.send_realtime_input(
                text="[System note] Sir just switched you in from the other brain. Say 'Gemini here, sir.' and "
                     "nothing else. Do not call tools.")

    # ---- configuration -------------------------------------------------------------------
    def _config(self) -> types.LiveConnectConfig:
        prompt = system_prompt()
        if self.handoff:
            prompt += ("\n\nCONVERSATION SO FAR (you have just taken over from the other brain mid-conversation; "
                       "carry on naturally, don't re-introduce yourself):\n" + self.handoff)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=prompt,
            tools=TOOLS,
            input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-IN", "en-US"]),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(language_code="en-US", voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.VOICE))),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    prefix_padding_ms=300, silence_duration_ms=config.VAD_SILENCE_MS)),
            session_resumption=types.SessionResumptionConfig(handle=self.resume_handle),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        )

    # ---- hooks from the assistant ---------------------------------------------------------
    def feed_audio(self, chunks, voice, now):
        for c in chunks:
            self.a.loop.call_soon_threadsafe(self._enqueue, c)

    def _enqueue(self, data: bytes):
        if not self.mic_q.full():
            self.mic_q.put_nowait(data)

    def on_new_request(self):
        self.tools_since_user = False
        self.nudged = False

    async def send_text(self, text: str):
        if not self.session:
            self.a.ui.send(type="notice", text="Not connected yet; try again in a moment.")
            return
        self.a.awaiting, self.a.await_since = True, time.monotonic()
        try:
            await self.session.send_realtime_input(text=text)
        except Exception as e:
            log.warning("send_text failed: %s", e)

    async def notify(self, note: str):
        if self.session:
            self.skip_nudge = True
            await self.session.send_realtime_input(text=note)
        else:
            self.a.pending_notes.append(note)

    async def deliver_job(self, job: Job):
        if self.session and job.fc_id and job.session_gen == self.gen:
            try:
                self.skip_nudge = True
                await self.session.send_tool_response(function_responses=[types.FunctionResponse(
                    id=job.fc_id, name=job.fc_name, scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
                    response={"result": f"{job.id} {job.status}: {job.result[:job.deliver_chars]}\n"
                                        + (job.instruction or f"(Tell {config.USER_NAME} the outcome in {job.report}. "
                                                              "Don't add details that aren't in it.)")})])
                return
            except Exception as e:
                log.warning("could not deliver %s as a function response: %s", job.id, e)
        await self.notify(self.a.job_note(job))

    # ---- session -----------------------------------------------------------------------------
    async def run(self):
        a, fails = self.a, 0
        while True:
            t0 = time.monotonic()
            try:
                async with a.client.aio.live.connect(model=config.LIVE_MODEL, config=self._config()) as session:
                    self.gen += 1
                    self.session = session
                    a.connected = True
                    a.ui.send(type="notice", text="")
                    log.info("gemini connected (session %d%s)", self.gen, ", resumed" if self.resume_handle else "")
                    self.handoff = ""
                    while not self.mic_q.empty():
                        self.mic_q.get_nowait()
                    for n in a.take_notes():
                        await self.notify(n)
                    workers = [asyncio.create_task(self._send_mic(session)), asyncio.create_task(self._receive(session))]
                    try:
                        done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for w in workers:
                            w.cancel()
                    for d in done:
                        d.result()
            except asyncio.CancelledError:
                raise
            except Reconnect as e:
                log.info("reconnecting: %s", e)
                fails = 0
                continue
            except Exception as e:
                msg = (str(e).strip().splitlines() or [type(e).__name__])[0][:150]
                log.warning("session error: %r", e)
                a.ui.send(type="notice", text=f"Reconnecting… ({msg})")
            finally:
                self.session = None
                a.connected = False
                if a.speaker:
                    a.speaker.clear()
            fails = fails + 1 if time.monotonic() - t0 < 15 else 0
            if fails >= 2:
                self.resume_handle = None             # a stale handle may be what keeps failing
            await asyncio.sleep(min(1.5 * fails, 15))

    async def _send_mic(self, session):
        while True:
            data = await self.mic_q.get()
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={IN_RATE}"))

    async def _receive(self, session):
        a = self.a
        while True:
            async for msg in session.receive():
                upd = msg.session_resumption_update
                if upd and upd.resumable and upd.new_handle:
                    self.resume_handle = upd.new_handle
                if msg.go_away:
                    raise Reconnect(f"server asked to reconnect (time left {msg.go_away.time_left})")
                sc = msg.server_content
                if sc:
                    if sc.interrupted:
                        log.info("interrupted (said so far: %r)", self.turn_said[-60:])
                        if a.speaker:
                            a.speaker.clear()
                        a.mute_output = False
                    if sc.input_transcription and sc.input_transcription.text:
                        a.user_said(sc.input_transcription.text)
                    if sc.output_transcription and sc.output_transcription.text:
                        a.caption("jarvis", sc.output_transcription.text)
                        self.turn_said += sc.output_transcription.text
                    if sc.model_turn and sc.model_turn.parts:
                        for p in sc.model_turn.parts:
                            if p.inline_data and p.inline_data.data and not p.thought:
                                self._play(p.inline_data.data)
                if msg.tool_call:
                    self.tools_since_user = True
                    a.awaiting = False
                    asyncio.create_task(self._on_tool_call(session, self.gen, msg.tool_call))
                if msg.tool_call_cancellation:
                    log.info("tool calls cancelled by server: %s", msg.tool_call_cancellation.ids)
                if sc and sc.turn_complete:
                    await self._turn_complete(session)

    def _play(self, pcm: bytes):
        a = self.a
        a.awaiting = False
        if a.mute_output:
            return
        a.last_audio = time.monotonic()
        if a.speaker:
            a.speaker.write(pcm)

    async def _turn_complete(self, session):
        a = self.a
        if a.user_utt and self._logged_utt != a.user_utt:
            log.info("heard: %r -> said: %r", a.user_utt.strip()[:200], self.turn_said.strip()[:120])
            self._logged_utt = a.user_utt
            a.remember("user", a.user_utt)
            if self.turn_said.strip():
                a.remember("assistant", self.turn_said)
        a.awaiting = False
        a.cap_who = None
        a.mute_output = False
        if (not self.tools_since_user and not self.nudged and not self.skip_nudge
                and promised_but_idle(self.turn_said)):
            self.nudged = True
            a.awaiting, a.await_since = True, time.monotonic()
            log.warning("promise without a tool call (%r); nudging", self.turn_said.strip()[:80])
            await session.send_realtime_input(text=NUDGE)
        self.turn_said, self.skip_nudge = "", False

    async def _on_tool_call(self, session, gen: int, tool_call):
        replies = []
        for fc in tool_call.function_calls:
            out = await self.a.run_tool(fc.name, dict(fc.args or {}), fc, gen)
            if isinstance(out, Job):
                await self._respond(session, gen, [types.FunctionResponse(
                    id=fc.id, name=fc.name, will_continue=True,
                    scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
                    response={"result": f"Started {out.id} in the background. Give a very short ack; "
                                        "the result arrives later."})])
            else:
                replies.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": out}))
        if replies:
            await self._respond(session, gen, replies)

    async def _respond(self, session, gen: int, replies):
        if gen != self.gen or session is not self.session:
            log.info("dropping tool response for an old session")
            return
        try:
            await session.send_tool_response(function_responses=replies)
        except Exception as e:
            log.warning("send_tool_response failed: %s", e)
