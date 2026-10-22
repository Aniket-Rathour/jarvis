"""Jarvis = the shared assistant + its two brains."""
from base import Assistant
from brain_gemini import GeminiBrain
from brain_local import LocalBrain


class Jarvis(Assistant):
    def __init__(self, ui, speaker=None):
        super().__init__(ui, speaker)
        self.brains = {"gemini": GeminiBrain(self), "qwen": LocalBrain(self)}
