from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from app.agents import NewsSummarizer
from app.fetchers import NewsFetcher
from app.models import NewsArticle
from app.processors import NewsProcessor
from app.voice import VoiceAgent

# A story is read aloud the moment its importance score reaches this
# threshold — no need to wait for the whole pipeline to drain. It mirrors the
# summarizer's own min_importance so the articles we surface first are exactly
# the ones worth hearing.
SPEAK_THRESHOLD = 55

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


def _analyzed_line(n: int) -> str:
    if n == 0:
        return "I couldn't find any news to analyze just now."
    if n == 1:
        return "I analyzed 1 article from across the web for you today."
    return (
        f"I analyzed {n} articles from across the web to find what "
        "matters most to you today."
    )


async def present_streaming(
    fetcher: NewsFetcher,
    processor: NewsProcessor,
    summarizer: NewsSummarizer,
    voice: VoiceAgent,
    top_n: int,
    intro_lines: List[str],
    completion_factory: Callable[[int], List[str]],
) -> Tuple[List[NewsArticle], str]:
    """Fetch, rank and speak with zero idle wait.

    The intro is spoken immediately, then each source's articles are pulled in
    as that source finishes: process + rank, then summarize-and-speak any
    high-priority story right away. Speech runs in a worker thread (via the
    event loop's executor) so the loop keeps fetching and processing the
    remaining sources while Kokoro is talking. Once every source is done, the
    canonical top-N (most recent) are guaranteed spoken in order, and the
    closing lines (built from the final article count) are read.

    Returns ``(all_articles, spoken_text)`` so callers can reuse the result and
    record the last thing said (for "repeat").
    """
    loop = asyncio.get_running_loop()
    running: List[NewsArticle] = []
    spoken_ids: set = set()
    spoken_text: List[str] = []
    summarized_ids: set = set()
    index = 0
    spoken_count = 0

    async def say(line: str) -> None:
        print(f"[agent] {line}")
        await loop.run_in_executor(None, voice.speak, line)
        spoken_text.append(line)

    async def ensure_summarized(articles: List[NewsArticle]) -> None:
        # Enrich only the important, not-yet-summarized stories. The summarizer
        # self-filters by importance, so we pass the exact subset we want rather
        # than letting it re-pick a global top-N. Per call this is one source's
        # worth of new stories, well under the summarizer's internal cap.
        targets = [
            a for a in articles
            if a.id not in summarized_ids and a.importance_score >= SPEAK_THRESHOLD
        ]
        if not targets:
            return
        await summarizer.summarize(targets)
        summarized_ids.update(a.id for a in targets)

    # 1) Acknowledge instantly — the user hears a response before any fetch.
    for line in intro_lines:
        await say(line)

    # 2) Stream sources as they finish; speak high-priority stories at once.
    async for batch in fetcher.fetch_stream():
        running.extend(batch)
        processed = processor.process(running)
        await ensure_summarized(processed)
        for a in processed:
            if a.id in spoken_ids or a.importance_score < SPEAK_THRESHOLD:
                continue
            if spoken_count >= top_n:
                # Enough early highlights; the flush below orders the rest.
                break
            index += 1
            spoken_count += 1
            await say(VoiceAgent._to_speech(a, index))
            spoken_ids.add(a.id)

    # 3) Guarantee the canonical top-N (most recent) are all read, in order,
    #    filling any gaps the early phase left. Capped at top_n so we never
    #    read more than the original fixed-length briefing.
    processed = processor.process(running)
    await ensure_summarized(processed)
    for a in processed[:top_n]:
        if a.id in spoken_ids:
            continue
        if spoken_count >= top_n:
            break
        index += 1
        spoken_count += 1
        await say(VoiceAgent._to_speech(a, index))
        spoken_ids.add(a.id)

    # 4) Closing line(s) — built now that we know the final article count.
    for line in completion_factory(len(processed)):
        await say(line)

    print("-" * 60)
    print("SOURCES:", " | ".join(f"{s}:{fetcher.status.get(s, '?')}" for s in _SOURCES))
    print("SUMMARIZER:", summarizer.status)

    return running, " ".join(spoken_text)


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
        ordered_queries: Optional[List[str]] = None,
    ):
        self.voice = voice_agent
        self.query = query
        self.top_n = top_n
        # Personalization: the ordered list of category queries to present.
        # Preferred categories come first, then the default. When None, the
        # single ``query`` is used (original behavior).
        self.ordered_queries = ordered_queries
        self._make_fetcher = fetcher_factory or (
            lambda q: NewsFetcher(query=q, max_per_source=5)
        )
        self._processor = processor or NewsProcessor()
        self._summarizer = summarizer or NewsSummarizer()

    async def run(self) -> List[NewsArticle]:
        ordered = self.ordered_queries or [self.query]
        now = datetime.now()
        greeting = (
            f"{_greeting(now)}! It's {now.strftime('%A')}, "
            f"{now.strftime('%B')} {_ordinal(now.day)}, "
            f"{now.strftime('%Y')}, and the time is {_time_phrase(now)}."
        )

        all_articles: List[NewsArticle] = []
        seen: set = set()
        for i, q in enumerate(ordered):
            fetcher = self._make_fetcher(q)
            # Greeting is spoken once (first category); later categories just
            # continue the briefing without repeating it.
            intro = [greeting] if i == 0 else []
            is_last = i == len(ordered) - 1

            def completion(count: int, _last: bool = is_last) -> List[str]:
                # Closing line only after the final category.
                return [_analyzed_line(count), _CLOSING] if _last else []

            arts, _ = await present_streaming(
                fetcher,
                self._processor,
                self._summarizer,
                self.voice,
                self.top_n,
                intro,
                completion,
            )
            for a in arts:
                if a.id not in seen:
                    seen.add(a.id)
                    all_articles.append(a)
        return all_articles
