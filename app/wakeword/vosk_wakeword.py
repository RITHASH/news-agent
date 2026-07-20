from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from .engine import WakeWordDetector

# Vosk + sounddevice are imported lazily so this module (and the app)
# imports cleanly even on a machine without a mic / Vosk installed.

# Single-keyword grammar. Vosk's KaldiRecognizer expects a JSON array of
# phrase strings (NOT JSGF); "[unk]" lets the decoder map everything else to an
# unknown token so only "jarvis" is ever recognized. This keeps CPU low and
# eliminates false alarms to other words.
_GRAMMAR = '["jarvis", "[unk]"]'


def create(model_dir: Optional[str] = None, **kwargs) -> "VoskWakeWord":
    return VoskWakeWord(model_dir=model_dir, **kwargs)


class VoskWakeWord(WakeWordDetector):
    """Local, open-source wake-word detector built on Vosk.

    Uses a grammar-restricted ``KaldiRecognizer`` so the decoder only ever
    considers the word "jarvis" — minimal CPU while idle, no network, no
    API key. ``wait_for_wake()`` blocks (in a worker thread) until the word
    is heard and returns True.
    """

    name = "vosk"

    def __init__(self, model_dir: Optional[str] = None, sample_rate: int = 16000):
        self._model_dir = model_dir
        self.sample_rate = sample_rate

    async def wait_for_wake(self) -> bool:
        return await asyncio.to_thread(self._listen)

    def _listen(self) -> bool:
        from vosk import KaldiRecognizer, Model
        import sounddevice as sd

        # Local model download is shared with the STT layer.
        from app.voice.stt import _ensure_model
        model = Model(_ensure_model(self._model_dir))
        rec = KaldiRecognizer(model, self.sample_rate, _GRAMMAR)

        block = int(self.sample_rate * 0.10)  # 100 ms frames -> low wake latency
        with sd.RawInputStream(
            samplerate=self.sample_rate, blocksize=block,
            dtype="int16", channels=1,
        ) as stream:
            while True:
                data, _ = stream.read(block)
                if rec.AcceptWaveform(bytes(data)):
                    if _is_jarvis(rec.Result()):
                        print("[JARVIS] Wake word detected.")
                        return True
                if _is_jarvis(rec.PartialResult()):
                    print("[JARVIS] Wake word detected.")
                    return True
        return False  # unreachable; loop only exits on detection


def _is_jarvis(result_json: str) -> bool:
    try:
        return "jarvis" in json.loads(result_json).get("text", "").strip().lower()
    except Exception:
        return False
