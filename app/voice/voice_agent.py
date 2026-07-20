from __future__ import annotations

import re
from typing import List, Optional

from app.models import NewsArticle
from app.voice.engine import TTSEngine

# Emoji / symbol ranges TTS engines mangle; stripped before speaking.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0000FE00-\U0000FE0F\U0000200D]"
)


class VoiceAgent:
    """Speaks news aloud via a pluggable :class:`TTSEngine` (Kokoro by default).

    Intentionally minimal for V1: no STT, conversation, or wake words. Only
    ``speak(text)`` is required; ``speak_news`` is a convenience for the
    pipeline. Swapping the engine is a one-file change (see ``engine.py``).
    """

    def __init__(self, engine: TTSEngine, top_n: int = 3):
        self.engine = engine
        self.top_n = top_n

    def speak(self, text: str) -> None:
        self.engine.speak(_EMOJI_RE.sub("", text).strip())

    def speak_news(
        self,
        articles: List[NewsArticle],
        top_n: Optional[int] = None,
        intro: str = "Here are your top stories.",
    ) -> None:
        n = top_n if top_n is not None else self.top_n
        self.speak(intro)
        for i, article in enumerate(articles[:n], 1):
            self.speak(self._to_speech(article, i))

    @staticmethod
    def _to_speech(article: NewsArticle, index: int) -> str:
        bits = [f"Story {index}. {article.title}."]
        if getattr(article, "one_line_summary", None):
            bits.append(article.one_line_summary)
        elif article.summary:
            bits.append(article.summary[:240])
        if getattr(article, "why_it_matters", None):
            bits.append("Why it matters: " + article.why_it_matters)
        return " ".join(b for b in bits if b)
