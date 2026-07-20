from __future__ import annotations

import importlib
import os
from abc import ABC, abstractmethod


class TTSEngine(ABC):
    """Minimal text-to-speech contract. Concrete engines implement ``speak``."""

    name: str = "base"

    @abstractmethod
    def speak(self, text: str) -> None:
        raise NotImplementedError


def get_tts_engine(name: str | None = None) -> TTSEngine:
    """Return the configured engine.

    Swapping engines is a one-file change: drop in
    ``app/voice/<name>_tts.py`` exposing ``create() -> TTSEngine`` and set
    ``TTS_ENGINE=<name>`` (or pass ``name=``). The ``VoiceAgent`` and this
    interface never need to change.
    """
    name = (name or os.environ.get("TTS_ENGINE") or "kokoro").lower()
    module = importlib.import_module(f"app.voice.{name}_tts")
    return module.create()
