from __future__ import annotations

import importlib
import os
from abc import ABC, abstractmethod


class WakeWordDetector(ABC):
    """A local, always-idle detector that blocks until its wake word is heard.

    The rest of the app talks only to ``wait_for_wake()``; swapping the engine
    (Vosk -> openWakeWord, Porcupine, etc.) means adding one module and pointing
    the factory at it, with no downstream changes.
    """

    name: str = "base"

    @abstractmethod
    async def wait_for_wake(self) -> bool:
        """Block (cheaply) until the wake word is detected. Returns True."""
        raise NotImplementedError


def get_wakeword_detector(name: str | None = None, **kwargs) -> WakeWordDetector:
    """Factory: load ``app.wakeword.<name>_wakeword`` and call its create()."""
    name = (name or os.environ.get("WAKEWORD_ENGINE") or "vosk").lower()
    module = importlib.import_module(f"app.wakeword.{name}_wakeword")
    return module.create(**kwargs)
