from __future__ import annotations

import asyncio
import json
import time
import urllib.request
import zipfile
from pathlib import Path

# Vosk is imported lazily inside _capture() so this module (and the rest of
# the app) imports cleanly even on a machine without a mic / Vosk installed.

_DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
_MODEL_URL = f"https://alphacephei.com/vosk/models/{_DEFAULT_MODEL}.zip"


def cache_dir() -> Path:
    return Path.home() / ".cache" / "vosk"


def _ensure_model(model_dir: Path | None = None) -> str:
    """Download + unpack the small English Vosk model into the local cache
    on first use. Fully offline afterwards."""
    d = model_dir or cache_dir()
    target = d / _DEFAULT_MODEL
    if (target / "final.mdl").exists():
        return str(target)
    d.mkdir(parents=True, exist_ok=True)
    zip_path = d / f"{_DEFAULT_MODEL}.zip"
    if not zip_path.exists():
        print(f"[stt] downloading {_DEFAULT_MODEL} ...")
        urllib.request.urlretrieve(_MODEL_URL, zip_path)  # noqa: S310 - fixed trusted URL
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(d)
    print(f"[stt] model ready at {target}")
    return str(target)


class SpeechRecognizer:
    """Local, offline Speech-to-Text via Vosk (open-source, no API key).

    The rest of the app talks only to ``listen()``; swapping the engine means
    rewriting the model loader and ``_capture`` here, nothing downstream.
    One call to ``listen()`` records a single utterance (stops on silence or
    a max duration) and returns the recognized text. It does NOT do wake
    words or continuous listening.
    """

    def __init__(
        self,
        model_dir: str | None = None,
        sample_rate: int = 16000,
        silence_timeout: float = 1.0,
        max_duration: float = 12.0,
    ):
        self._model_dir = model_dir
        self.sample_rate = sample_rate
        self.silence_timeout = silence_timeout
        self.max_duration = max_duration

    async def listen(self) -> str:
        """Record one utterance from the default mic and return recognized text."""
        return await asyncio.to_thread(self._capture)

    # ------------------------------------------------------------------ #
    # capture (runs in a worker thread; blocking audio I/O)
    # ------------------------------------------------------------------ #
    def _capture(self) -> str:
        from vosk import KaldiRecognizer, Model
        import sounddevice as sd

        model = Model(_ensure_model(self._model_dir))
        rec = KaldiRecognizer(model, self.sample_rate)

        block = int(self.sample_rate * 0.03)  # 30 ms frames
        parts: list[str] = []
        started = False
        last_speech = time.monotonic()
        start = time.monotonic()

        with sd.RawInputStream(
            samplerate=self.sample_rate, blocksize=block,
            dtype="int16", channels=1,
        ) as stream:
            while True:
                data, _ = stream.read(block)
                if rec.AcceptWaveform(bytes(data)):
                    text = json.loads(rec.Result()).get("text", "").strip()
                    if text:
                        parts.append(text)
                        started = True
                        last_speech = time.monotonic()
                else:
                    partial = json.loads(rec.PartialResult()).get("text", "").strip()
                    if partial:
                        started = True
                        last_speech = time.monotonic()

                now = time.monotonic()
                if started and (now - last_speech) > self.silence_timeout:
                    break
                if (now - start) > self.max_duration:
                    break

        final = json.loads(rec.FinalResult()).get("text", "").strip()
        if final:
            parts.append(final)
        return " ".join(parts).strip()
