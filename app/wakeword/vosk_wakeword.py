from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from .engine import WakeWordDetector
from .shared_model import SharedVoskModel, get_shared_model


def create(
    shared_model: Optional[SharedVoskModel] = None,
    model_dir: Optional[str] = None,
    sample_rate: int = 16000,
    block_ms: int = 100,
    **kwargs,
) -> "VoskWakeWord":
    """Factory function.

    Args:
        shared_model: Shared Vosk model manager. If None, creates/gets global singleton.
        model_dir: Model directory (used if shared_model not provided).
        sample_rate: Audio sample rate.
        block_ms: Audio block size in milliseconds.
    """
    if shared_model is None:
        shared_model = get_shared_model(model_dir=model_dir, sample_rate=sample_rate, block_ms=block_ms)
    return VoskWakeWord(shared_model=shared_model)


class VoskWakeWord(WakeWordDetector):
    """Local, open-source wake-word detector built on Vosk.

    Uses a shared Vosk model and persistent audio stream. The grammar-restricted
    KaldiRecognizer only considers the word "jarvis" — minimal CPU while idle,
    no network, no API key. ``wait_for_wake()`` blocks until the word is heard.
    """

    name = "vosk"

    def __init__(
        self,
        shared_model: SharedVoskModel,
    ):
        self._shared = shared_model
        self._running = False
        self._wake_event: Optional[asyncio.Event] = None

    async def start(self) -> None:
        """Start the audio stream and prepare for wake word detection."""
        if self._running:
            return
        self._shared.start_stream()
        self._running = True
        print("[wakeword] Started - listening for 'JARVIS'")

    async def stop(self) -> None:
        """Stop detection and release resources."""
        self._running = False
        # Wake up any waiting wait_for_wake() call
        if self._wake_event is not None:
            self._wake_event.set()
        # Note: We don't stop the shared stream here - it's managed by NewsAgent
        # and shared with STT. The shared model's stop_stream() handles it.
        print("[wakeword] Stopped")

    async def wait_for_wake(self) -> bool:
        """Block until the wake word is detected. Returns True."""
        if not self._running:
            await self.start()

        self._wake_event = asyncio.Event()
        rec = self._shared.create_wake_recognizer()

        # Run detection loop in a worker thread to avoid blocking event loop
        return await asyncio.to_thread(self._listen, rec)

    def _listen(self, rec) -> bool:
        """Blocking detection loop (runs in worker thread)."""
        try:
            while self._running:
                # Read audio block from shared stream (blocking call)
                if self._shared._stream is None:
                    return False

                data = self._shared.read_block_sync()

                if rec.AcceptWaveform(data):
                    if self._is_jarvis(rec.Result()):
                        print("[JARVIS] Wake word detected.")
                        return True
                if self._is_jarvis(rec.PartialResult()):
                    print("[JARVIS] Wake word detected.")
                    return True
        except Exception as e:
            print(f"[wakeword] detection error: {e}")
        return False

    @staticmethod
    def _is_jarvis(result_json: str) -> bool:
        try:
            return "jarvis" in json.loads(result_json).get("text", "").strip().lower()
        except Exception:
            return False