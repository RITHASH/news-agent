from __future__ import annotations

from pathlib import Path
from typing import Optional

# Vosk imported lazily in methods so module imports cleanly without mic/Vosk

# Grammar constants shared between wake word and STT
WAKE_GRAMMAR = '["jarvis", "[unk]"]'
STT_GRAMMAR = None  # Unrestricted


class SharedVoskModel:
    """Singleton manager for Vosk model and audio I/O.

    - Loads model once at startup
    - Manages single audio input stream
    - Provides recognizers for both wake word and STT
    - Thread-safe access for mutually exclusive consumers
    """

    def __init__(
        self,
        model_dir: Optional[str] = None,
        sample_rate: int = 16000,
        block_ms: int = 100,
    ):
        self._model_dir = model_dir
        self.sample_rate = sample_rate
        self.block_size = int(sample_rate * block_ms / 1000)
        self._model = None
        self._stream = None
        self._running = False

    # ------------------------------------------------------------------ #
    # Model loading (lazy, shared with STT module)
    # ------------------------------------------------------------------ #
    def _ensure_model(self) -> str:
        """Download + unpack the small English Vosk model on first use."""
        from app.voice.stt import _ensure_model as stt_ensure_model
        return stt_ensure_model(Path(self._model_dir) if self._model_dir else None)

    def _get_model(self):
        """Get or create the Vosk Model instance."""
        if self._model is None:
            from vosk import Model
            model_path = self._ensure_model()
            self._model = Model(model_path)
        return self._model

    # ------------------------------------------------------------------ #
    # Audio stream management
    # ------------------------------------------------------------------ #
    def start_stream(self) -> None:
        """Open persistent audio input stream."""
        if self._stream is not None:
            return
        import sounddevice as sd

        self._running = True
        self._stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            dtype="int16",
            channels=1,
        )
        self._stream.start()

    def stop_stream(self) -> None:
        """Close audio stream."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------ #
    # Recognizer factories
    # ------------------------------------------------------------------ #
    def create_wake_recognizer(self):
        """Create KaldiRecognizer with wake word grammar."""
        from vosk import KaldiRecognizer
        return KaldiRecognizer(self._get_model(), self.sample_rate, WAKE_GRAMMAR)

    def create_stt_recognizer(self):
        """Create KaldiRecognizer for full STT (unrestricted grammar)."""
        from vosk import KaldiRecognizer
        return KaldiRecognizer(self._get_model(), self.sample_rate, STT_GRAMMAR)

    # ------------------------------------------------------------------ #
    # Stream read (used by wake word detector and STT)
    # ------------------------------------------------------------------ #
    def read_block_sync(self, block_size: Optional[int] = None) -> bytes:
        """Read audio data from the shared input stream.

        Args:
            block_size: Number of frames to read. If None, uses self.block_size.
        """
        if self._stream is None:
            raise RuntimeError("Audio stream not started")

        size = block_size if block_size is not None else self.block_size
        def _read():
            data, _ = self._stream.read(size)
            return bytes(data)

        return bytes(_read())


# Global singleton instance (set by NewsAgent on startup)
_shared_instance: Optional[SharedVoskModel] = None


def get_shared_model(
    model_dir: Optional[str] = None,
    sample_rate: int = 16000,
    block_ms: int = 100,
) -> SharedVoskModel:
    """Get or create the global SharedVoskModel singleton."""
    global _shared_instance
    if _shared_instance is None:
        _shared_instance = SharedVoskModel(
            model_dir=model_dir, sample_rate=sample_rate, block_ms=block_ms
        )
    return _shared_instance


def set_shared_model(instance: Optional[SharedVoskModel]) -> None:
    """Replace the global singleton (mainly for testing)."""
    global _shared_instance
    _shared_instance = instance