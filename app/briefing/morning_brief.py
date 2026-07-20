from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Optional

from app.agents import NewsSummarizer
from app.fetchers import NewsFetcher
from app.models import NewsArticle
from app.processors import NewsProcessor
from app.voice import VoiceAgent

_CLOSING = (
    "Would you like technology news, startup news, world news, "
    "sports news, or AI news?"
)
_SOURCES = ["rss", "exa", "youtube", "x", "reddit", "linkedin"]


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _greeting(now: datetime) -> str:
    h = now.hour
    if h < 12:
        return "Good morning"
    if h < 17:
        return "Good afternoon"
    return "Good evening"


def _time_phrase(now: datetime) -> str:
    return now.strftime("%I:%M %p").lstrip("0")


class MorningBriefing:
    """Runs the news pipeline once and delivers it as a spoken briefing.

    Owns a single fetch (requirement: no double fetch) and reuses that result
    for both the "N articles analyzed" line and the top-3 read. The spoken
    script is built from live data (clock, article count, story content) — no
    hardcoded facts. Conversation is intentionally out of scope here.
    """

    def __init__(
        self,
        voice_agent: VoiceAgent,
        query: str = "technology news",
        top_n: int = 3,
        fetcher_factory: Optional[Callable[[str], NewsFetcher]] = None,
        processor: Optional[NewsProcessor] = None,
        summarizer: Optional[NewsSummarizer] = None,
    ):
        self.voice = voice_agent
        self.query = query
        self.top_n = top_n
        self._make_fetcher = fetcher_factory or (
            lambda q: NewsFetcher(query=q, max_per_source=5)
        )
        self._processor = processor or NewsProcessor()
        self._summarizer = summarizer or NewsSummarizer()

    async def run(self) -> List[NewsArticle]:
        fetcher = self._make_fetcher(self.query)
        articles = await fetcher.fetch()
        articles = self._processor.process(articles)
        await self._summarizer.summarize(articles)
        top = articles[: self.top_n]
        self._present(articles, top, fetcher.status, self._summarizer.status)
        return articles

    # ------------------------------------------------------------------ #
    # script
    # ------------------------------------------------------------------ #
    def build_script(self, articles: List[NewsArticle]) -> List[str]:
        now = datetime.now()
        segments: List[str] = []
        segments.append(f"{_greeting(now)}! It's {now.strftime('%A')}, "
                        f"{now.strftime('%B')} {_ordinal(now.day)}, "
                        f"{now.strftime('%Y')}, and the time is {_time_phrase(now)}.")

        n = len(articles)
        if n == 0:
            segments.append("I couldn't find any news to analyze just now.")
        elif n == 1:
            segments.append("I analyzed 1 article from across the web for you today.")
        else:
            segments.append(
                f"I analyzed {n} articles from across the web to find what "
                "matters most to you today."
            )

        count = min(self.top_n, n)
        if count == 0:
            segments.append("There are no stories to read right now.")
        else:
            segments.append(f"Here are the top {count} stories.")
            connectors = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]
            for i, a in enumerate(articles[:count]):
                if i == count - 1 and count > 1:
                    label = "Finally"
                elif i < len(connectors):
                    label = connectors[i]
                else:
                    label = f"Story {i + 1}"
                segments.append(self._story_line(a, label))

        segments.append(_CLOSING)
        return segments

    @staticmethod
    def _story_line(article: NewsArticle, connector: str) -> str:
        bits = [f"{connector}, {article.title}."]
        if getattr(article, "one_line_summary", None):
            bits.append(article.one_line_summary.rstrip(". ") + ".")
        elif article.summary:
            bits.append(article.summary[:240].rstrip(". ") + ".")
        if getattr(article, "why_it_matters", None):
            bits.append("Why it matters: " + article.why_it_matters.rstrip(". ") + ".")
        return " ".join(bits)

    # ------------------------------------------------------------------ #
    # delivery
    # ------------------------------------------------------------------ #
    def _present(self, articles, top, fetcher_status, summarizer_status) -> None:
        for seg in self.build_script(articles):
            print(seg)
            self.voice.speak(seg)
        print("-" * 60)
        print("SOURCES:", " | ".join(f"{s}:{fetcher_status.get(s, '?')}" for s in _SOURCES))
        print("SUMMARIZER:", summarizer_status)
