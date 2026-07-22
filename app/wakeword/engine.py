from __future__ import annotations

import importlib
import os
from abc import ABC, abstractmethod


class WakeWordDetector(ABC):
    """A local, always-idle detector that blocks until its wake word is heard.

    The rest of the app talks only to ``wait_for_wake()``; swapping the engine
    (Vosk -> openWakeWord, Porcupine, etc.) means adding one module and pointing
    the factory at it, with no downstream changes.

    Lifecycle:
        start()  - Called once at application startup. Initializes resources.
        wait_for_wake() - Called repeatedly by the state machine when SLEEPING.
        stop()   - Called once at application shutdown. Releases resources.
    """

    name: str = "base"

    async def start(self) -> None:
        """Initialize detector resources (model, audio stream). Called once at startup."""
        pass

    @abstractmethod
    async def wait_for_wake(self) -> bool:
        """Block (cheaply) until the wake word is detected. Returns True."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Release detector resources. Called once at shutdown."""
        pass


def get_wakeword_detector(
    name: str | None = None,
    shared_model: "SharedVoskModel | None" = None,
    **kwargs,
) -> WakeWordDetector:
    """Factory: load ``app.wakeword.<name>_wakeword`` and call its create()."""
    name = (name or os.environ.get("WAKEWORD_ENGINE") or "vosk").lower()
    module = importlib.import_module(f"app.wakeword.{name}_wakeword")
    # Pass shared_model to create() if the module supports it
    return module.create(shared_model=shared_model, **kwargs)
