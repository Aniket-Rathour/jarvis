"""Qwen brain: runs on this PC. Mic -> Whisper (GPU) -> Qwen via Ollama (with tools) -> Kokoro voice.
Each reply is spoken sentence by sentence while the model is still writing the rest, and you can interrupt it."""
import asyncio
import importlib.util
import json
import logging
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import config
import fastpath
from audio import IN_RATE
from jobs import Job
from prompt import local_system_prompt, vocabulary
from tools import ollama_tools

log = logging.getLogger("jarvis")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
MAX_TOOL_ROUNDS = 6
HISTORY_MESSAGES = 30

WEB_ACTION = re.compile(
    r"\b(?:(?:send|write|compose|draft|reply(?: to)?|forward)\b.*\b(?:e-?mails?|mails?|messages?|whatsapp|dm|text)\b|"
    r"(?:book|buy|order|purchase|checkout|reserve)\b|log ?in to|sign ?in to|fill (?:in|out)|apply (?:for|to|on)|"
    r"(?:post|tweet|publish)\b|(?:message|msg|dm|text|tell|ping)\s+(?!me\b|my\b|if\b|what\b|how\b|the\b|any\b)[a-z]|"
    r"(?:star|follow|subscribe to|unfollow|like|comment on)\b.*\b(?:github|repo|channel|account|video|page)\b)", re.I)
REPORT_NOTE = ("[System note] Report the outcome to sir now, in one short sentence, using only what the tool "
               "results said.")
WEB_HINT = ("\n[System note: this is an ACTION inside a website, done with his logins. Do NOT use web_search. If any "
            "detail is missing (recipient, exact date, amount, which account), ask ONE short question and wait. "
            "Otherwise call delegate_to_claude with where=\"chrome\". Irreversible actions need a show_plan with "
            "needs_approval=true first.]")


def _text_tool_call(text: str, names: set[str]):
    """The model occasionally prints `name arg="value" …` instead of making a structured call. Parse it back."""
    m = re.match(r"^\s*(\w+)\s*(?:\((.*)\))?(.*)$", text.strip(), re.S)
    if not m or m.group(1) not in names:
        return None
    body = (m.group(2) or m.group(3) or "").strip()
    args = {}
    for k, v in re.findall(r"(\w+)\s*=\s*(\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'|\[.*?\]|[^\s,]+)", body, re.S):
        v = v.strip()
        try:
            args[k] = json.loads(v) if v[:1] in '"[' else (v.strip("'") if v[:1] == "'" else json.loads(v))
        except ValueError:
            args[k] = v.strip("\"'")
    return SimpleNamespace(function=SimpleNamespace(name=m.group(1), arguments=args))


class LocalBrain:
    key = "qwen"
    label = "Qwen"

    def __init__(self, a):
        self.a = a
        self.llm = None
        self.stt = self.tts = None
        self.events: asyncio.Queue = asyncio.Queue()      # ("audio", pcm) | ("text", str) | ("note", str)
        self.history: list[dict] = []
        self.system = ""
        self.tool_schemas = ollama_tools()
        self.tool_names = {t["function"]["name"] for t in self.tool_schemas}
        self.say_q: asyncio.Queue = asyncio.Queue()       # (generation, sentence)
        self.voice_gen = 0                                # bumped to drop queued speech
        self.turn_task: asyncio.Task | None = None
        self.note_q: list[str] = []
        self.warm = False
        self.gpu_layers = 0                              # 0 = let Ollama decide
        self.unload_task: asyncio.Task | None = None
        self._avail: tuple[bool, str] = (True, "")
        self._avail_t = 0.0
        # utterance segmentation (audio thread)
        self.utt = bytearray()
        self.in_utt = False
        self.utt_voiced = 0
        self.utt_last_voice = 0.0

    # ---- what fastpath.py expects of its "assistant" ----------------------------------
    @property
    def day(self):
        return self.a.day

    async def run_tool(self, name, args, fc):
        return await self.a.run_tool(name, args, fc)

    def caption(self, who, text, new=None):
        self.a.caption(who, text, new)

    # ---- availability ----------------------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """Cheap, cached check: packages present, Ollama installed, model downloaded."""
        if time.monotonic() - self._avail_t < 5:
            return self._avail
        why = ""
        if any(importlib.util.find_spec(m) is None for m in ("ollama", "faster_whisper", "kokoro_onnx")):
            why = "Python packages missing (pip install -r requirements.txt)"
        elif not (shutil.which("ollama") or (Path.home() / ".local/bin/ollama").exists()):
            why = "Ollama isn't installed (run scripts/install_local.sh)"
        else:
            name, _, tag = config.LOCAL_MODEL.partition(":")
            manifest = Path.home() / ".ollama/models/manifests/registry.ollama.ai/library" / name / (tag or "latest")
            try:          # ask Ollama itself: models made with `ollama create` don't sit under library/
                tags = json.load(urllib.request.urlopen(config.OLLAMA_HOST + "/api/tags", timeout=1.5))
                have = any(m["name"] in (config.LOCAL_MODEL, config.LOCAL_MODEL + ":latest") for m in tags.get("models", []))
            except (OSError, ValueError):
                have = True        # Ollama not running yet: it is started on switch, a missing model then fails loudly
            if not have:
                why = f"{config.LOCAL_MODEL} isn't downloaded yet (run scripts/install_local.sh)"
        self._avail, self._avail_t = (not why, why), time.monotonic()
        return self._avail

    # ---- warming up (the old brain keeps serving meanwhile) ------------------------------
    async def _ensure_ollama(self):
        def up():
            try:
                urllib.request.urlopen(config.OLLAMA_HOST + "/api/version", timeout=2)
                return True
            except OSError:
                return False
        if await asyncio.to_thread(up):
            return
        if subprocess.run(["systemctl", "--user", "start", "ollama"], capture_output=True).returncode != 0:
            exe = shutil.which("ollama") or str(Path.home() / ".local/bin/ollama")
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        for _ in range(60):
            if await asyncio.to_thread(up):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("Ollama did not start (systemctl --user status ollama)")

    async def prepare(self):
        a = self.a
        if self.unload_task:
            self.unload_task.cancel()
            self.unload_task = None
        if self.llm is None:
            import ollama
            self.llm = ollama.AsyncClient(host=config.OLLAMA_HOST)
        await self._ensure_ollama()
        if not self.warm:                               # free any copy still on the GPU, so the VRAM math is right
            try:
                await self.llm.generate(model=config.LOCAL_MODEL, prompt="", keep_alive=0)
                await asyncio.sleep(1.5)
            except Exception:
                pass
        if self.stt is None or self.tts is None:        # Whisper first, so Ollama sees the VRAM that is left
            a.ui.send(type="notice", text="Loading speech models…")
            from stt import STT
            from tts import TTS
            t0 = time.monotonic()
            if self.stt is None:
                self.stt = await asyncio.to_thread(STT, vocabulary())
            if self.tts is None:
                self.tts = await asyncio.to_thread(TTS)
            log.info("speech models ready in %.1fs", time.monotonic() - t0)
        if not self.warm:
            a.ui.send(type="notice", text=f"Loading {config.LOCAL_MODEL} (first time takes a while)…")
            self.system = local_system_prompt()
            t0 = time.monotonic()
            # Load the weights and prefill the long system prompt + tool list now, so the first real request
            # (and every later one: the prefix stays cached) doesn't pay for it.
            self.gpu_layers = self._pick_gpu_layers()
            for attempt in range(4):                     # too many GPU layers runs out of memory: back off
                try:
                    await self.llm.chat(model=config.LOCAL_MODEL, tools=self.tool_schemas, think=False,
                                        keep_alive="10m",
                                        messages=[{"role": "system", "content": self.system},
                                                  {"role": "user", "content": "hello"}],
                                        options=self._options(num_predict=4))
                    break
                except Exception as e:
                    if not self.gpu_layers or attempt == 3:
                        raise
                    log.warning("loading with %d GPU layers failed (%s); trying fewer", self.gpu_layers, str(e)[:80])
                    self.gpu_layers = self.gpu_layers - 3 if self.gpu_layers > 12 else 0
            self.warm = True
            log.info("%s loaded and prompt cached in %.1fs (%s GPU layers)", config.LOCAL_MODEL, time.monotonic() - t0,
                     self.gpu_layers or "auto")

    def _options(self, **extra) -> dict:
        """Identical on every call: Ollama reloads the model whenever load-time options change."""
        o = {"temperature": 0.3, "num_ctx": config.LOCAL_CTX, **extra}
        if config.LOCAL_THREADS:
            o["num_thread"] = config.LOCAL_THREADS
        if self.gpu_layers:
            o["num_gpu"] = self.gpu_layers
        return o

    @staticmethod
    def _free_vram_mib() -> int | None:
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
            return int(out[0])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return None

    def _pick_gpu_layers(self) -> int:
        """Layers that fit in the VRAM left after Whisper: ~270 MiB per layer, ~1100 MiB kept free for buffers/Chrome."""
        if config.LOCAL_GPU_LAYERS.isdigit():
            return int(config.LOCAL_GPU_LAYERS)
        free = self._free_vram_mib()
        return max(0, min(64, int((free - 1100) / 270))) if free else 0      # leaves room for Chrome's GPU use

    async def release(self):
        """Switched away: keep the model in memory for a while (fast to switch back), then free the GPU."""
        self.unload_task = asyncio.create_task(self._unload_later())

    async def _unload_later(self):
        try:
            await asyncio.sleep(config.LOCAL_KEEP_WARM_MIN * 60)
            await self.llm.generate(model=config.LOCAL_MODEL, prompt="", keep_alive=0)
            self.warm, self.stt, self.gpu_layers = False, None, 0   # frees ~15 GB of memory and the GPU
            log.info("local model unloaded after %d min away", config.LOCAL_KEEP_WARM_MIN)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("unload failed: %s", e)

    def adopt(self, history: list[tuple[str, str]]):
        """Take over the conversation from the other brain."""
        self.history = [{"role": "user" if r == "user" else "assistant", "content": t} for r, t in history[-10:]]
        self._trim()

    async def greet(self):
        self.say("Qwen here, sir.")

    # ---- hooks from the assistant --------------------------------------------------------------
    def on_new_request(self):
        pass

    def feed_audio(self, chunks, voice, now):
        """Audio thread: cut the gated mic stream into utterances; speaking over Jarvis interrupts him."""
        if voice and not self.in_utt:
            self.in_utt, self.utt, self.utt_voiced = True, bytearray(), 0
            if self.a.speaking() or (self.turn_task and not self.turn_task.done()):
                self.a.loop.call_soon_threadsafe(self._barge_in)
        if not self.in_utt:
            return
        for c in chunks:
            self.utt.extend(c)
        if voice:
            self.utt_voiced += 1
            self.utt_last_voice = now
        too_long = len(self.utt) > IN_RATE * 2 * 30
        if now - self.utt_last_voice > config.VAD_SILENCE_MS / 1000 or too_long:
            self.in_utt = False
            self.a.heard = False                             # the assistant must not re-arm "thinking" later
            if self.utt_voiced >= 8:                         # >= ~0.25 s of actual voice
                self.a.awaiting, self.a.await_since = True, now
                self.a.loop.call_soon_threadsafe(self.events.put_nowait, ("audio", bytes(self.utt)))

    def _barge_in(self):
        self.voice_gen += 1
        if self.a.speaker:
            self.a.speaker.clear()
        self.a.mute_output = False
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()

    async def send_text(self, text: str):
        await self.events.put(("text", text))

    async def notify(self, note: str):
        await self.events.put(("note", note))

    async def deliver_job(self, job: Job):
        await self.notify(self.a.job_note(job))

    # ---- main loop ---------------------------------------------------------------------------------
    async def run(self):
        a = self.a
        if self.llm is None or not self.warm:
            await self.prepare()
        for n in a.take_notes():
            self.events.put_nowait(("note", n))
        speaker = asyncio.create_task(self._speak_loop())
        keepalive = asyncio.create_task(self._keepalive())
        a.connected = True
        a.ui.send(type="notice", text="")
        log.info("qwen ready")
        try:
            while True:
                kind, payload = await self.events.get()
                if kind in ("audio", "text"):
                    await self._cancel_turn()
                    self.turn_task = asyncio.create_task(self._run_turn(kind, payload))
                elif self.turn_task and not self.turn_task.done():
                    self.note_q.append(payload)               # let him finish what he is saying first
                else:
                    self.turn_task = asyncio.create_task(self._run_turn("note", payload))
        finally:
            await self._cancel_turn()
            speaker.cancel()
            keepalive.cancel()
            self.voice_gen += 1
            a.connected = False
            if a.speaker:
                a.speaker.clear()

    async def _keepalive(self):
        """While Qwen is the active brain, keep the model loaded (Ollama itself only holds it 10 minutes, so a
        crash can never leave 17 GB stuck in memory)."""
        while True:
            await asyncio.sleep(240)
            try:
                await self.llm.generate(model=config.LOCAL_MODEL, prompt="", keep_alive="10m")
            except Exception as e:
                log.warning("keep-alive failed: %s", e)

    async def _cancel_turn(self):
        t = self.turn_task
        if t and not t.done():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)

    async def _run_turn(self, kind: str, payload: str):
        a = self.a
        try:
            if kind == "audio":
                t0 = time.monotonic()
                text = await asyncio.to_thread(self.stt.transcribe, payload)
                log.info("heard (%.1fs audio, %.2fs stt): %r", len(payload) / IN_RATE / 2, time.monotonic() - t0, text)
                if not text:
                    return
                a.user_said(text, new=True)
                if a.switching:                          # that was "switch to …": the switch is already under way
                    return
                await self._turn(text)
            elif kind == "text":
                if a.switching:
                    return
                await self._turn(payload)
            else:
                await self._turn(payload, note=True)
        except asyncio.CancelledError:
            self.voice_gen += 1
            if a.speaker:
                a.speaker.clear()
            raise
        except Exception:
            log.exception("turn failed")
            self.say("I'm terribly sorry, sir, something went wrong there.")
        finally:
            a.awaiting = False
            if self.note_q and not (self.turn_task and self.turn_task.cancelled()):
                self.events.put_nowait(("note", self.note_q.pop(0)))

    # ---- one request -> one reply -----------------------------------------------------------------
    async def _turn(self, content: str, note: bool = False):
        a = self.a
        a.mute_output = False
        a.awaiting, a.await_since = True, time.monotonic()
        if not note and (quick := await fastpath.handle(self, content)):
            self._remember(content, quick)
            return
        if not note and WEB_ACTION.search(content):
            content += WEB_HINT
        self.history.append({"role": "user", "content": content})
        t0 = time.monotonic()
        said = []
        for rnd in range(MAX_TOOL_ROUNDS):
            text, calls = await self._generate()
            said.append(text)
            msg = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = [{"function": {"name": c.function.name, "arguments": c.function.arguments}}
                                     for c in calls]
            self.history.append(msg)
            if not calls or rnd == MAX_TOOL_ROUNDS - 1:
                break
            direct, outs = [], []
            for c in calls:
                name, args = c.function.name, dict(c.function.arguments or {})
                out = await a.run_tool(name, args, SimpleNamespace(id=None, name=name))
                direct.append(fastpath.direct_reply(name, args, out))
                shown = f"Started {out.id} in the background; its result arrives later." if isinstance(out, Job) else str(out)
                outs.append((name, shown))
                self.history.append({"role": "tool", "content": shown[:4000], "tool_name": name})
            if all(d is not None for d in direct):
                # Code can put this in words: skip another slow model pass.
                text = " ".join(dict.fromkeys(d for d in direct if d))
                self.caption("jarvis", text, new=True)
                self.say(text)
                self.history.append({"role": "assistant", "content": text})
                said.append(text)
                break
        if (self.history and self.history[-1]["role"] == "tool") or not said[-1]:
            # The model ended on a tool result without telling him what happened: ask for the report (one-off).
            text, _calls = await self._generate(tools=False, extra=[{"role": "user", "content": REPORT_NOTE}])
            said.append(text)
            if text:
                self.history.append({"role": "assistant", "content": text})
            else:
                self.say("Done, sir.")
        self._trim()
        spoken = " ".join(s for s in said if s).strip()
        log.info("said (%.1fs): %r", time.monotonic() - t0, spoken[:160])
        if not note:
            a.remember("user", content.split("\n[System note")[0])
        if spoken:
            a.remember("assistant", spoken)

    async def _generate(self, tools: bool = True, extra: list | None = None) -> tuple[str, list]:
        """Stream one model reply; speak each sentence as soon as it is complete."""
        messages = [{"role": "system", "content": self.system}] + self.history + (extra or [])
        stream = await self.llm.chat(model=config.LOCAL_MODEL, messages=messages,
                                     tools=self.tool_schemas if tools else None, stream=True, think=False,
                                     keep_alive="10m",
                                     options=self._options(num_predict=140))
        text, buf, calls, first = "", "", [], True
        async for part in stream:
            m = part.message
            if m.tool_calls:
                calls.extend(m.tool_calls)
            if m.content:
                self.a.awaiting = False
                text += m.content
                buf += m.content
                *done, buf = SENTENCE_END.split(buf)
                for s in done:
                    if s.strip():
                        self.a.caption("jarvis", s.strip() + " ", new=first)
                        first = False
                        self.say(s)
        if not calls and (fake := _text_tool_call(text, self.tool_names)):
            log.warning("model wrote a tool call as text; running it: %r", text[:100])
            calls, text, buf = [fake], "", ""
            self.a.cap_who = None
        if buf.strip():
            self.a.caption("jarvis", buf.strip(), new=first)
            self.say(buf)
        return text.strip(), calls

    def _remember(self, user: str, reply: str):
        self.history += [{"role": "user", "content": user}, {"role": "assistant", "content": reply}]
        self._trim()
        self.a.remember("user", user)
        self.a.remember("assistant", reply)

    def _trim(self):
        h = self.history[-HISTORY_MESSAGES:]
        while h and h[0]["role"] != "user":             # never start mid tool-exchange
            h = h[1:]
        self.history = h

    # ---- speech out --------------------------------------------------------------------------------
    def say(self, text: str):
        if text.strip():
            self.say_q.put_nowait((self.voice_gen, text.strip()))

    async def _speak_loop(self):
        a = self.a
        while True:
            gen, text = await self.say_q.get()
            if gen != self.voice_gen or a.mute_output:
                continue
            pcm = await asyncio.to_thread(self.tts.synth, text)
            if gen != self.voice_gen or a.mute_output or not pcm:
                continue
            a.last_audio = time.monotonic() + len(pcm) / 2 / 24000
            if a.speaker:
                a.speaker.write(pcm)
