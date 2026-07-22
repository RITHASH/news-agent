from __future__ import annotations

import asyncio
import json
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

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

    Uses a shared Vosk model and audio stream. One call to ``listen()``
    records a single utterance (stops on silence or a max duration) and
    returns the recognized text. It does NOT do wake words or continuous
    listening.

    Lifecycle:
        start() - Called when entering LISTENING state. Prepares recognizer.
        listen() - Called repeatedly during LISTENING state.
        stop()  - Called when leaving LISTENING state. Cleans up.
    """

    def __init__(
        self,
        shared_model=None,  # SharedVoskModel, imported lazily to avoid circular deps
        model_dir: str | None = None,
        sample_rate: int = 16000,
        silence_timeout: float = 1.0,
        max_duration: float = 12.0,
    ):
        self._shared_model = shared_model
        self._model_dir = model_dir
        self.sample_rate = sample_rate
        self.silence_timeout = silence_timeout
        self.max_duration = max_duration
        self._running = False
        self._rec = None

    async def start(self) -> None:
        """Prepare for listening (model already loaded via shared model)."""
        if self._running:
            return
        if self._shared_model is not None:
            # Use shared model's recognizer (unrestricted grammar for STT)
            self._rec = self._shared_model.create_stt_recognizer()
        else:
            # Fallback: create own model (for backward compatibility/testing)
            from vosk import KaldiRecognizer, Model
            model_path = _ensure_model(Path(self._model_dir) if self._model_dir else None)
            model = Model(model_path)
            self._rec = KaldiRecognizer(model, self.sample_rate)
        self._running = True

    async def stop(self) -> None:
        """Cleanup."""
        self._running = False
        self._rec = None

    async def listen(self) -> str:
        """Record one utterance from the default mic and return recognized text."""
        if not self._running:
            await self.start()
        return await asyncio.to_thread(self._capture)

    # ------------------------------------------------------------------ #
    # capture (runs in a worker thread; blocking audio I/O)
    # ------------------------------------------------------------------ #
    def _capture(self) -> str:
        if self._shared_model is not None:
            # Use shared audio stream - read blocks via asyncio.to_thread
            return self._capture_shared()
        else:
            # Fallback: open own stream (backward compatibility)
            return self._capture_standalone()

    def _capture_shared(self) -> str:
        """Capture using shared model's audio stream."""
        rec = self._rec
        parts: list[str] = []
        started = False
        last_speech = time.monotonic()
        start = time.monotonic()

        while True:
            # Read from shared stream using the shared model's block size
            if self._shared_model is None or self._shared_model._stream is None:
                break
            try:
                data = self._shared_model.read_block_sync()
            except Exception:
                break
            if rec.AcceptWaveform(data):
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

    def _capture_standalone(self) -> str:
        """Fallback: open own stream (original behavior)."""
        from vosk import KaldiRecognizer, Model
        import sounddevice as sd

        model = Model(_ensure_model(Path(self._model_dir) if self._model_dir else None))
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