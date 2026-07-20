from __future__ import annotations

import os
import re
import tempfile
import urllib.request
import wave
from pathlib import Path

import numpy as np

from app.voice.engine import TTSEngine

DEFAULT_MODEL = "kokoro-v1.0.onnx"
DEFAULT_VOICES = "voices-v1.0.bin"
_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_DEFAULT_VOICE = "af_heart"

# Emoji / symbol ranges TTS engines mangle; stripped before synthesis.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0000FE00-\U0000FE0F\U0000200D]"
)
# URLs read badly aloud; drop them from spoken text.
_URL_RE = re.compile(r"https?://\S+")


def cache_dir() -> Path:
    return Path.home() / ".cache" / "kokoro"


def _clean(text: str) -> str:
    if not text:
        return ""
    text = _EMOJI_RE.sub("", text)
    text = _URL_RE.sub("", text)
    return " ".join(text.split()).strip()


def download_kokoro(model_dir: Path | None = None) -> Path:
    """One-time download of the ONNX model + voices into the local cache."""
    d = model_dir or cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name in (DEFAULT_MODEL, DEFAULT_VOICES):
        out = d / name
        if out.exists():
            continue
        url = f"{_RELEASE}/{name}"
        print(f"[kokoro] downloading {name} ...")
        urllib.request.urlretrieve(url, out)  # noqa: S310 - fixed trusted URL
        print(f"[kokoro] saved {out.stat().st_size} bytes")
    return d


class KokoroTTS(TTSEngine):
    """Local, offline neural TTS via kokoro-onnx (no API key, no network at runtime)."""

    name = "kokoro"

    def __init__(
        self,
        voice: str = _DEFAULT_VOICE,
        speed: float = 1.0,
        model_path: str | None = None,
        voices_path: str | None = None,
    ):
        from kokoro_onnx import Kokoro

        model_path = model_path or str(cache_dir() / DEFAULT_MODEL)
        voices_path = voices_path or str(cache_dir() / DEFAULT_VOICES)
        if not (Path(model_path).exists() and Path(voices_path).exists()):
            download_kokoro()

        self._kokoro = Kokoro(model_path=model_path, voices_path=voices_path)
        self._voice = voice
        self._speed = speed

    def speak(self, text: str) -> None:
        text = _clean(text)
        if not text:
            return
        audio, sr = self._kokoro.create(text, voice=self._voice, speed=self._speed)
        self._play(audio, sr, text)

    # ------------------------------------------------------------------ #
    # playback (best-effort, headless-safe)
    # ------------------------------------------------------------------ #
    def _play(self, audio, sr, text: str) -> None:
        # 1) Direct playback to the default output device.
        try:
            import sounddevice as sd

            sd.play(np.asarray(audio, dtype=np.float32), sr)
            sd.wait()
            return
        except Exception:
            pass
        # 2) Write a WAV and open it in the OS default player.
        try:
            path = os.path.join(tempfile.gettempdir(), "kokoro_tts.wav")
            _write_wav(path, audio, sr)
            os.startfile(path)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
        # 3) No audio path available (headless/CI): at least surface the text.
        print("[speak]", text)

    @staticmethod
    def _write_wav(path: str, audio, sr: int) -> None:
        pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())


def create() -> TTSEngine:
    """Factory entry point used by :func:`app.voice.engine.get_tts_engine`."""
    return KokoroTTS()
