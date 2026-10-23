"""Turn-taking for Jarvis on calls: when has the other person really finished speaking?

Gemini's own detector ends a turn after a fixed ~0.5 s of silence, so a person who pauses mid-thought ("I think we
should... [2 s] ...move it to Friday") gets talked over. Here the call agent decides instead:

  * a small speech-energy detector (adaptive to the line's noise) tells speech from silence, frame by frame;
  * when the person pauses, a small local Whisper model (CPU) reads the last few seconds, and the pause allowed depends on
    how the words end:
        clearly finished (a question, "yes", "okay", "bye"...)      -> answer after ~0.55 s
        an ordinary sentence                                         -> ~1.0 s
        mid-thought ("...and", "so", "to", "um", "because"...)       -> wait up to ~3 s
  * while Jarvis is talking and the other person starts speaking, Jarvis PAUSES at once ("duck"); if it was only an
    "mm-hm" he carries on from where he paused ("unduck"), if they keep talking (~0.8 s of speech) he stops and listens
    ("barge"). Speech is counted over a sliding window, so the gaps between words don't reset it.

Whisper adds full stops even to unfinished speech ("...the meeting to" comes back as "...the meeting too."), so the
decision looks at the last words, not at the punctuation."""
import logging
import re
import threading
import time
from collections import deque

import numpy as np

log = logging.getLogger("jarvis.call.turns")
RATE = 16000
FRAME_MS = 40

# How the last word sounds when someone has NOT finished (English and Hindi, Devanagari and romanised)
INCOMPLETE = set("""and or but so because cause cuz since though although if when while then than that which who whom whose
where what how to too for of with without about from into onto at by on in as like um uh uhh umm er erm hmm the a an my
your our their his her its this these those some any is are was were be been am will would should could can may might must
shall do does did have has had i we you he she they it it's i'm we're you're they're let's just also maybe actually basically
literally really very more most not no-one also plus whether either neither until unless means mean think thought guess wanted
want need going gonna wanna kinda sorta well thinking wondering saying hoping planning trying meaning feeling""".split()) | set("""aur lekin par toh to ki ke ka ko mein se jo agar matlab kyunki
kyonki ya phir fir wala wali bhi hi ek और लेकिन पर तो कि के का को में से जो अगर मतलब क्योंकि या फिर वाला वाली भी ही एक""".split())
# endings of two or three words that leave the thought open ("...so I was thinking.", "...the thing is.")
INCOMPLETE_TAIL = {"you know", "i mean", "the thing", "thing is", "i think", "i guess", "i feel", "i believe", "i was",
                   "what i", "the problem", "problem is", "the point", "point is", "let me", "let's see", "i wanted", "i want",
                   "me see", "as in", "i'd say", "tell him", "tell her", "tell them", "ask him", "ask her", "ask them", "text him", "text her", "message him",
                   "message her", "and tell", "and ask", "saying that", "that he", "that she", "that they", "remind him", "remind her", "i would", "we could", "you could", "we should", "you should", "it's like", "was like"}

# Short replies that are complete on their own
COMPLETE_SHORT = set("""yes yeah yep yup no nope nah ok okay sure fine right correct exactly done thanks thank bye goodbye hello hi
hey alright great perfect absolutely definitely certainly good noted cool haan han nahi nahin theek thik accha acha shukriya dhanyavad
haanji ji bilkul""".split())
FILLER = set("""it that's is sounds very much you see i oh ah hai ho gaya ji so then that works me sir""".split())


def classify(text: str) -> str:
    """'complete' | 'incomplete' | 'neutral' for what the person has said so far."""
    t = text.strip().lower()
    if not t:
        return "neutral"
    words = re.findall(r"[\w'ऀ-ॿ]+", t)
    if not words:
        return "neutral"
    last = words[-1]
    if len(words) >= 3 and all(len(w) == 1 for w in words[-3:]):     # spelling something out ("c-h-u-t-i-y-a"): judge by timing
        return "neutral"
    if t.rstrip().endswith("?"):
        return "complete"
    if t.rstrip().endswith(("...", "…", ",", "-")):
        return "incomplete"
    if last in INCOMPLETE and not (len(words) <= 2 and last in ("too", "so", "well")):   # "me too", "so so", "oh well"
        return "incomplete"
    if len(words) >= 2 and (" ".join(words[-2:]) in INCOMPLETE_TAIL):
        return "incomplete"
    if len(words) <= 4 and all(w in COMPLETE_SHORT or w in FILLER for w in words) and any(w in COMPLETE_SHORT for w in words):
        return "complete"
    lead = words[1] if words[0] in ("jarvis", "please", "hey", "ok", "okay") and len(words) > 1 else words[0]
    if lead in COMMAND_START and len(words) <= 8:
        return "complete"
    return "neutral"


# Sounds that acknowledge without taking the turn
BACKCHANNEL = set("""mm hmm mhm mmhmm mm-hm mm-hmm uh-huh uhhuh huh yeah yes yep yup ok okay right sure oh ah i see got it alright
haan han haanji ji accha acha achha hmm-hmm theek thik hai""".split())


def _squash(w: str) -> str:
    """'MMHM' / 'mm-hmm' / 'mhmm' / 'hmmm' -> one spelling each (no hyphens, no repeated letters)."""
    return re.sub(r"(.)\1+", r"\1", w.lower().replace("-", ""))


_BACK = {_squash(w) for w in BACKCHANNEL}


# Words that mean "stop / let me talk" even when said briefly
STOP_WORDS = set("""stop wait no hold sorry excuse hang jarvis listen actually nahi ruko suno""".split())


def is_interruption(text: str) -> bool:
    """A short sound over Jarvis counts as an interruption only if it says so: a stop-word, or a few real words."""
    words = re.findall(r"[\w']+", text.lower())
    if not words or is_backchannel(text):
        return False
    return any(w in STOP_WORDS for w in words) or len(words) >= 3


def is_backchannel(text: str) -> bool:
    words = re.findall(r"[\w'-]+", text.lower())
    return 0 < len(words) <= 3 and all(_squash(w) in _BACK for w in words)


# A short instruction that starts with one of these and doesn't end mid-thought is finished ("open YouTube", "pause it")
COMMAND_START = set("""open close play pause stop resume skip call send search google find show go scroll click type turn set
remind mute unmute switch read tell what's whats what where who when how start end hang take join answer add remove delete
mark save remember forget cancel next previous volume""".split())


WAIT = {"complete": 0.55, "neutral": 1.0, "incomplete": 3.0, None: 1.3}    # seconds of silence before the turn is over


class Recogniser:
    """Small Whisper on the CPU, loaded in the background; returns None until ready (then timing alone decides)."""

    _shared = None

    def __init__(self, model: str = "base"):
        self.model = None
        self.name = model
        threading.Thread(target=self._load, daemon=True).start()

    @classmethod
    def shared(cls):
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def _load(self):
        try:
            from faster_whisper import WhisperModel
            t0 = time.monotonic()
            self.model = WhisperModel(self.name, device="cpu", compute_type="int8", cpu_threads=4)
            self.text(np.zeros(RATE, dtype=np.int16).tobytes())       # the first run is slow: do it now, not mid-conversation
            log.info("turn-taking recogniser (%s) ready in %.1fs", self.name, time.monotonic() - t0)
        except Exception as e:
            log.warning("turn-taking recogniser unavailable (%s); using timing only", e)

    def text(self, pcm: bytes) -> str | None:
        if self.model is None:
            return None
        pcm = pcm[-RATE * 2 * 3:]                                       # the ending is all that matters; shorter is faster
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
        segs, _ = self.model.transcribe(x, beam_size=1, vad_filter=False, without_timestamps=True,
                                        condition_on_previous_text=False)
        return " ".join(s.text for s in segs).strip()


class TurnTaker:
    """Feed it every 40 ms frame of the caller's audio; it says when their turn starts and ends.

    on_frame(pcm, jarvis_speaking) returns a list of events: "start" (they began a turn; send the preroll first),
    "audio" (forward this frame), "end" (their turn is over), "duck" (they may be interrupting: pause Jarvis' voice),
    "unduck" (it was only a short sound: carry on), "barge" (they really interrupted: drop what Jarvis was saying)."""

    def __init__(self, recogniser: Recogniser | None = None):
        self.rec = recogniser
        self.floor = 60.0                        # noise floor of the line (RMS)
        self.in_turn = False
        self.voice_streak = 0
        self.window: deque[bool] = deque(maxlen=25)            # the last second, frame by frame: speech or not
        self.ducked = False
        self.duck_audio = bytearray()                          # what they said while Jarvis was paused
        self.duck_check: str | None = None                     # None, "pending", "backchannel" or "speech"
        self.duck_voiced = 0                                   # frames of speech since Jarvis was paused
        self.last_voice = 0.0
        self.turn_started = 0.0
        self.preroll: deque[bytes] = deque(maxlen=10)          # ~400 ms kept so the first syllable isn't cut off
        self.utterance = bytearray()
        self.verdict: str | None = None
        self.verdict_text = ""
        self._deciding = False
        self._lock = threading.Lock()

    def threshold(self) -> float:
        return max(220.0, self.floor * 3.2)

    def _decide(self, pcm: bytes, started: float):
        try:
            text = self.rec.text(pcm) if self.rec else None
        except Exception as e:
            log.debug("recogniser failed: %s", e)
            text = None
        with self._lock:
            if self.in_turn and self.turn_started == started and text is not None:
                self.verdict, self.verdict_text = classify(text), text
            self._deciding = False

    def _check_duck(self, pcm: bytes):
        try:
            text = self.rec.text(pcm) if self.rec else ""
        except Exception:
            text = ""
        self.duck_check = "speech" if is_interruption(text or "") else "backchannel"
        log.info("over Jarvis they said %r -> %s", text, self.duck_check)

    def _open(self, now: float):
        self.in_turn, self.turn_started, self.last_voice = True, now, now
        self.verdict, self.verdict_text = None, ""
        self.utterance = bytearray(b"".join(self.preroll))

    def on_frame(self, pcm: bytes, jarvis_speaking: bool, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        x = np.frombuffer(pcm, dtype=np.int16)
        rms = float(np.sqrt(np.mean(x.astype(np.float32) ** 2))) if x.size else 0.0
        voice = rms > self.threshold()
        if not voice:                                        # follow the line noise; fall fast, rise slowly
            self.floor += (0.08 if rms < self.floor else 0.004) * (rms - self.floor)
        self.voice_streak = self.voice_streak + 1 if voice else 0
        self.window.append(voice)
        w = list(self.window)
        ev: list[str] = []
        if not self.in_turn:
            self.preroll.append(pcm)
            if jarvis_speaking or self.ducked:
                if not self.ducked and sum(w[-8:]) >= 6:            # ~0.25 s of speech over him: pause his voice
                    self.ducked, self.duck_check, self.duck_voiced = True, None, 0
                    self.duck_audio = bytearray(b"".join(list(self.preroll)[-8:]))
                    ev.append("duck")
                    return ev
                if not self.ducked:
                    return ev
                self.duck_audio.extend(pcm)
                self.duck_voiced += voice
                quiet = not any(w[-6:])
                if self.duck_check == "backchannel" and quiet:      # it was an "mm-hm" and they stopped: carry on
                    self.ducked = False
                    return ev + ["unduck"]
                long_talk = self.duck_voiced >= 40                    # ~1.6 s of speech: clearly taking the turn, whatever was heard
                no_rec = (self.rec is None or self.rec.model is None) and sum(w) >= 19   # no recogniser: duration decides
                if self.duck_check == "speech" or long_talk or no_rec:
                    self.ducked = False                             # a real interruption: drop his sentence and listen
                    self._open(now)
                    self.utterance = bytearray(self.duck_audio)     # everything they said since he paused, not just the last 0.4 s
                    return ev + ["barge", "start", "audio"]
                if sum(w) >= 19 and self.duck_check is None:        # ~0.8 s of sound: words or just an "mm-hm"? check
                    self.duck_check = "pending"
                    threading.Thread(target=self._check_duck, args=(bytes(self.duck_audio),), daemon=True).start()
                if quiet and self.duck_check is None:               # a short sound that already stopped: carry on
                    self.ducked = False
                    return ev + ["unduck"]
                return ev
            if sum(w[-4:]) >= 3:                                     # he is silent: ~0.12 s of speech starts their turn
                self._open(now)
                ev += ["start", "audio"]
            return ev
        # in their turn
        ev.append("audio")
        self.utterance.extend(pcm)
        if len(self.utterance) > RATE * 2 * 12:
            del self.utterance[: len(self.utterance) - RATE * 2 * 12]
        if voice:
            if now - self.last_voice > 0.3:                 # they started again after a pause: reconsider
                with self._lock:
                    self.verdict, self.verdict_text = None, ""
            self.last_voice = now
            return ev
        silence = now - self.last_voice
        if silence >= 0.3 and self.verdict is None and not self._deciding and self.rec is not None:
            self._deciding = True
            tail = bytes(self.utterance[-RATE * 2 * 4:])     # the last ~4 s is enough to judge how it ends (and is fast)
            threading.Thread(target=self._decide, args=(tail, self.turn_started), daemon=True).start()
        limit = WAIT[self.verdict]
        if self.verdict is None and self._deciding:
            limit = 2.0                                      # a check is still running: don't guess, give it a moment
        if silence >= limit or silence >= 3.2:
            self.in_turn = False
            self.voice_streak = 0
            self.window.clear()
            self.preroll.clear()
            ev.append("end")
            log.info("turn over after %.1fs silence (%s: %r)", silence, self.verdict or "timing only", self.verdict_text[-60:])
        return ev
