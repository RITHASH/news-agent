from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from app.agents import NewsSummarizer
from app.briefing import MorningBriefing, present_streaming, SPEAK_THRESHOLD
from app.conversation import (
    AI_NEWS,
    BUSINESS_NEWS,
    ConversationManager,
    EXPLAIN_ARTICLE,
    FINANCE_NEWS,
    LATEST_NEWS,
    MORNING_BRIEF,
    NEXT,
    POLITICS_NEWS,
    PREVIOUS,
    REPEAT,
    SCIENCE_NEWS,
    SELECT_STORY,
    SPORTS_NEWS,
    STARTUP_NEWS,
    STOP,
    TELL_MORE,
    TECHNOLOGY_NEWS,
    WORLD_NEWS,
)
from app.fetchers import NewsFetcher
from app.memory import CacheEntry, NewsCache
from app.models import NewsArticle
from app.processors import NewsProcessor
from app.voice import SpeechRecognizer, VoiceAgent, get_tts_engine
from app.wakeword import WakeWordDetector, get_wakeword_detector

# category intent -> query used by the fetcher.
_QUERY_FOR = {
    LATEST_NEWS: "latest news",
    TECHNOLOGY_NEWS: "technology news",
    STARTUP_NEWS: "startup news",
    WORLD_NEWS: "world news",
    SPORTS_NEWS: "sports news",
    AI_NEWS: "artificial intelligence news",
    BUSINESS_NEWS: "business news",
    SCIENCE_NEWS: "science news",
    POLITICS_NEWS: "politics news",
    FINANCE_NEWS: "finance news",
}


class AgentState(Enum):
    """The three observable states of the assistant, each logged on entry.

    SLEEPING  - waiting only for the wake word; normal voice commands ignored.
    LISTENING - inside a conversation session, accepting and routing commands.
    SPEAKING  - producing audio; the microphone stays idle until it finishes.
    """

    SLEEPING = "sleeping"
    LISTENING = "listening"
    SPEAKING = "speaking"


# Sleep commands are session-lifecycle events handled by the state machine,
# not by the news-content intent router (which stays purely about news).
_SLEEP_RE = re.compile(
    r"\b(sleep mode|go to sleep|good night|stop listening|sleep)\b",
    re.IGNORECASE,
)


def _is_sleep_command(text: Optional[str]) -> bool:
    """Return True if ``text`` is a request to enter Sleep Mode."""
    return bool(text) and bool(_SLEEP_RE.search(text))


@dataclass
class ConversationContext:
    """Lightweight, LLM-free memory of the current conversation session.

    Persists only while a session is live; :meth:`NewsAgent._clear_context`
    resets it on every session boundary (Sleep / Stop / idle timeout). It
    captures exactly what follow-up commands need:

    * ``category``       - the news category in focus (e.g. "technology news").
    * ``articles``       - the ordered, ranked list the user is reading.
    * ``selected_index`` - which story is "current" (0-based).
    * ``last_response``  - the most recent thing JARVIS said, for continuity.

    Nothing here is cross-session; the next wake starts blank.
    """

    category: Optional[str] = None
    articles: List["NewsArticle"] = field(default_factory=list)
    selected_index: int = 0
    last_response: str = ""

    @property
    def has_articles(self) -> bool:
        return bool(self.articles)


_ORDINAL_WORDS = ["first", "second", "third", "fourth", "fifth"]

# --- Natural voice wording -------------------------------------------------
# Short, human acknowledgments drawn at random so JARVIS stops sounding like a
# fixed tape loop. These are PURE wording variants: they never change routing,
# the three-state machine (Sleeping/Listening/Speaking), the cache, the fetch
# pipeline, or the streaming pipeline. A caller picks one via ``_pick``.
_WAKE_LINES = ["Yes?", "I'm listening.", "Go ahead."]
_SLEEP_LINES = ["Going to sleep.", "See you later.", "Sleeping now."]
_END_LINES = ["Goodbye.", "Talk to you later.", "See you soon."]
_NO_MORE_LINES = [
    "That's all for now.",
    "You've heard all the stories.",
    "Nothing else in this category.",
]
_START_LINES = [
    "That's the first story.",
    "We're already at the start.",
    "This is the first one.",
]
_ERROR_LINES = [
    "I didn't catch that.",
    "Could you repeat that?",
    "I'm not sure what you meant.",
]


def _pick(lines: List[str]) -> str:
    """Choose one acknowledgment at random (always a valid, listed phrase)."""
    return random.choice(lines)


class NewsAgent:
    """The brain / top-level entry point for the news application.

    Owns every long-lived component and orchestrates the assistant's lifecycle
    as a small state machine:

        SLEEPING  -> (wake word "JARVIS")      -> LISTENING
        LISTENING -> (speak)                    -> SPEAKING -> LISTENING
        LISTENING -> (Stop / Sleep / idle)      -> SLEEPING

    The microphone only listens for commands while LISTENING; in SLEEPING it
    waits solely for the wake word and ignores everything else. Speech is
    produced only while SPEAKING, and the mic stays idle during it.
    """

    def __init__(
        self,
        query: str = "technology news",
        top_n: int = 5,
        max_per_source: int = 5,
        session_timeout: float = 25.0,
        cache_ttl: float = 300.0,
    ):
        self.query = query
        self.top_n = top_n
        self.max_per_source = max_per_source
        self.session_timeout = session_timeout
        self.voice: Optional[VoiceAgent] = None
        self.briefing: Optional[MorningBriefing] = None
        self.conversation = ConversationManager()
        self.stt = SpeechRecognizer()
        self.wakeword: WakeWordDetector = get_wakeword_detector()
        self._last_articles: List[NewsArticle] = []
        self._last_spoken: str = ""
        # Short-term news memory: within the TTL, repeated commands reuse the
        # already fetched/processed/summarized result instead of refetching.
        self.cache = NewsCache(ttl_seconds=cache_ttl)
        # Fresh pipeline components per call keep the fetcher/summarizer stateless
        # across queries (status dicts don't leak); overridable for tests.
        self._make_fetcher = lambda q: NewsFetcher(
            query=q, max_per_source=self.max_per_source
        )
        self._make_processor = NewsProcessor
        self._make_summarizer = NewsSummarizer
        # Set within a session to end it (Stop, or idle timeout) and return
        # to wake-word listening. NOT a process exit.
        self._shutdown = asyncio.Event()
        # Explicit lifecycle state (see AgentState). Drives idle behavior and
        # guards against accidental or duplicate wake / transition events.
        self.state = AgentState.SLEEPING
        # In-session conversation memory (category / articles / selection).
        # Reset on every session boundary so a new wake starts blank.
        self.context = ConversationContext()

    def _set_state(self, new: "AgentState") -> None:
        """Transition to ``new`` state, logging only on an actual change."""
        if new is self.state:
            return
        prev = self.state
        self.state = new
        print(f"[state] {prev.value} -> {new.value}")

    def _clear_context(self) -> None:
        """Drop all in-session memory. Called on every session boundary so a
        fresh wake starts with a clean slate (no leaked category/articles)."""
        self.context = ConversationContext()

    def _set_context(self, category: Optional[str], articles: List["NewsArticle"]) -> None:
        """Record the category and ordered article list now in focus.

        Selection resets to the first story and ``last_response`` is synced to
        whatever was just spoken, keeping follow-up commands anchored to the
        list the user actually heard.
        """
        self.context.category = category
        self.context.articles = list(articles)
        self.context.selected_index = 0
        self.context.last_response = self._last_spoken

    async def start(self) -> None:
        self.voice = VoiceAgent(get_tts_engine(), top_n=self.top_n)
        self.briefing = MorningBriefing(self.voice, query=self.query, top_n=self.top_n)

        # 1) Morning Brief at startup (the news pipeline is unchanged).
        self._set_state(AgentState.SPEAKING)
        self._last_articles = await self.briefing.run()

        # 2) Wake-word lifetime, modelled as a clean state machine:
        #    SLEEPING -> (wake) -> LISTENING -> (stop/sleep/timeout) -> SLEEPING.
        #    The microphone only listens for commands while LISTENING; in
        #    SLEEPING it waits solely for the wake word and ignores commands.
        while True:
            self._set_state(AgentState.SLEEPING)
            print("[JARVIS] Sleeping - waiting for wake word 'JARVIS'...")
            try:
                await self.wakeword.wait_for_wake()
            except Exception as e:
                # Mic unavailable / backend error: don't take down the
                # process. Log and retry after a short pause.
                print(f"[wakeword] detection failed: {e}")
                await asyncio.sleep(2.0)
                continue
            await self._wake()
            await self._run_session()

    async def _run_session(self) -> None:
        self._shutdown.clear()
        self._clear_context()  # defensive: start every session with a blank slate
        self._set_state(AgentState.LISTENING)
        print("[JARVIS] Listening...")
        last_speech = time.monotonic()
        # One conversation session: listen -> route -> speak -> listen again,
        # until the user says Stop / Sleep or goes quiet for session_timeout.
        while not self._shutdown.is_set():
            try:
                text = await self.stt.listen()  # mic idle until now
            except Exception as e:
                print(f"[stt] listening failed: {e}")
                text = ""
            if not text:
                if time.monotonic() - last_speech > self.session_timeout:
                    # Idle too long: go back to sleep.
                    self._shutdown.set()
                    await self._respond("Going back to sleep.")
                    self._clear_context()
                    self._set_state(AgentState.SLEEPING)
                    return
                continue
            last_speech = time.monotonic()
            print(f"[you] {text}")
            # Sleep is a session-lifecycle command handled by the state machine,
            # not the content intent router.
            if _is_sleep_command(text):
                await self._sleep()
                return
            intent = await self.conversation.handle(text)
            await self._route(intent)

    # ------------------------------------------------------------------ #
    # wake / sleep lifecycle
    # ------------------------------------------------------------------ #
    async def _wake(self) -> None:
        """Wake from SLEEPING into a listening session.

        Guarded so a stray/duplicate wake event (e.g. the detector firing
        twice) can never start a second session while one is already live.
        """
        if self.state is not AgentState.SLEEPING:
            return
        self._set_state(AgentState.SPEAKING)  # short acknowledgment
        try:
            self.voice.speak(_pick(_WAKE_LINES))
        except Exception as e:
            print(f"[voice] wake acknowledgment failed: {e}")
        self._set_state(AgentState.LISTENING)
        print("[JARVIS] Listening...")

    async def _sleep(self) -> None:
        """Enter Sleep Mode: confirm, then stop accepting commands.

        Sets the session-shutdown event first so the speaking state is not
        reset to LISTENING by ``_respond``; the loop then returns to the
        wake-word wait without terminating the process. Context is dropped so
        the next wake starts fresh.
        """
        self._shutdown.set()
        await self._respond(_pick(_SLEEP_LINES))
        self._clear_context()
        self._set_state(AgentState.SLEEPING)
        print("[JARVIS] Sleeping - voice commands paused.")

    # ------------------------------------------------------------------ #
    # routing
    # ------------------------------------------------------------------ #
    async def _route(self, intent) -> None:
        name = intent.name
        if name == STOP:
            # End this session and return to wake-word listening.
            self._shutdown.set()
            await self._respond(_pick(_END_LINES))
            self._clear_context()
            self._set_state(AgentState.SLEEPING)
            return
        if name == REPEAT:
            if self._last_spoken:
                await self._respond(self._last_spoken)
            else:
                await self._respond("I haven't said anything yet.")
            return
        if name == MORNING_BRIEF:
            self._set_state(AgentState.SPEAKING)
            self._last_articles = await self.briefing.run()
            self._set_context("morning brief", self._last_articles)
            if not self._shutdown.is_set():
                self._set_state(AgentState.LISTENING)
            return
        if name == EXPLAIN_ARTICLE:
            await self._explain(intent)
            return
        if name == TELL_MORE:
            # Expand the currently selected story (no new fetch).
            await self._explain_current()
            return
        if name == NEXT:
            await self._navigate(1)
            return
        if name == PREVIOUS:
            await self._navigate(-1)
            return
        if name == SELECT_STORY:
            await self._select_ordinal(intent)
            return
        if name in _QUERY_FOR:
            self._last_articles = await self._fetch_and_speak(_QUERY_FOR[name])
            self._set_context(_QUERY_FOR[name], self._last_articles)
            return
        # Unknown / fallback: a brief, natural nudge (no long menu read-out).
        await self._respond(_pick(_ERROR_LINES))

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    async def _fetch_and_speak(self, query: str) -> List[NewsArticle]:
        """Fetch (or serve from cache) news for ``query`` and speak it.

        * Fresh cache hit -> replay the cached stories instantly, no I/O.
        * Stale cache hit -> replay cached stories instantly AND kick off a
          single background refresh that repopulates the cache when done.
        * Cold miss       -> stream (instant first word) and populate the cache.

        The streaming pipeline (``present_streaming``) itself is untouched;
        the cache only decides whether to run it at all.
        """
        self._set_state(AgentState.SPEAKING)
        result: List[NewsArticle]
        async with self.cache.lock(query):
            entry = self.cache.get(query)
            if entry is None:
                mode, start_refresh = "cold", False
            elif self.cache.is_fresh(entry):
                mode, start_refresh = "warm", False
            else:
                mode, start_refresh = "stale", not entry.refreshing
                if start_refresh:
                    entry.refreshing = True  # serialized by the lock
        # Decision made under the per-key lock; act without holding it.
        if mode == "cold":
            self.cache.misses += 1
            result = await self._pipeline_and_speak(query)
            self.cache.put(query, result)
        else:
            # Warm or stale-serving: replay the cache immediately.
            self.cache.hits += 1
            await self._speak_cached(entry)
            self._last_articles = entry.articles
            result = entry.articles
            if start_refresh:
                # Fire-and-forget; the user never waits for the refresh.
                asyncio.ensure_future(self._refresh(query))
        if not self._shutdown.is_set():
            self._set_state(AgentState.LISTENING)
        return result

    async def _pipeline_and_speak(self, query: str) -> List[NewsArticle]:
        """Cold path: stream the news (instant first word) and return it ranked."""
        fetcher = self._make_fetcher(query)
        intro = [f"Here are the top {self.top_n} {query} stories."]
        # Stream: speak the intro instantly, then read high-priority stories as
        # their source finishes while the rest keep fetching in the background.
        articles, spoken = await present_streaming(
            fetcher,
            self._make_processor(),
            self._make_summarizer(),
            self.voice,
            self.top_n,
            intro,
            lambda n: [],
        )
        self._last_spoken = spoken
        # Store the canonical ranked list (deduped + sorted) so cache replay
        # and "explain" see the same ordering the user heard.
        return self._make_processor().process(articles)

    async def _pipeline_silent(self, query: str) -> List[NewsArticle]:
        """Background refresh path: build the ranked, summarized list, no speech."""
        fetcher = self._make_fetcher(query)
        processor = self._make_processor()
        summarizer = self._make_summarizer()
        running: List[NewsArticle] = []
        summarized: set = set()
        async for batch in fetcher.fetch_stream():
            running.extend(batch)
            processed = processor.process(running)
            targets = [
                a for a in processed
                if a.importance_score >= SPEAK_THRESHOLD and a.id not in summarized
            ]
            if targets:
                await summarizer.summarize(targets)
                summarized.update(a.id for a in targets)
        # Atomic replace happens in NewsCache.put (called by _refresh).
        return processor.process(running)

    async def _speak_cached(self, entry: CacheEntry) -> None:
        """Replay a cached result immediately (data is already fully ready)."""
        loop = asyncio.get_event_loop()
        spoken: List[str] = []

        async def say(line: str) -> None:
            print(f"[agent] {line}")
            await loop.run_in_executor(None, self.voice.speak, line)
            spoken.append(line)

        await say(f"Here are the top {self.top_n} {entry.query} stories.")
        for i, a in enumerate(entry.articles[: self.top_n], 1):
            await say(VoiceAgent._to_speech(a, i))
        self._last_spoken = " ".join(spoken)

    async def _refresh(self, query: str) -> None:
        """Repopulate a stale cache entry in the background; never blocks a user."""
        try:
            articles = await self._pipeline_silent(query)
        except Exception as e:
            # Leave the previous (stale) entry in place so a later request
            # can still be served, and let the next staleness retry refresh.
            print(f"[cache] background refresh failed for {query!r}: {e}")
            async with self.cache.lock(query):
                entry = self.cache.get(query)
                if entry is not None:
                    entry.refreshing = False
            return
        # Entirely new list built before this swap; readers never see a
        # partially updated cache.
        self.cache.put(query, articles)

    async def _explain_at(self, arts: List["NewsArticle"], idx: Optional[int]) -> None:
        """Read the deep-dive for story ``idx`` (title + summary + why/impact)."""
        if idx is None or idx < 0 or idx >= len(arts):
            await self._respond(
                "I'm not sure which story you mean. "
                "Try 'tell me more about the first story'."
            )
            return
        a = arts[idx]
        lines = [f"Here's more on: {a.title}."]
        if getattr(a, "one_line_summary", None):
            lines.append(a.one_line_summary)
        elif a.summary:
            lines.append(a.summary[:240])
        if getattr(a, "why_it_matters", None):
            lines.append("Why it matters: " + a.why_it_matters)
        if getattr(a, "possible_impact", None):
            lines.append("Possible impact: " + a.possible_impact)
        await self._respond(*lines)

    async def _explain(self, intent) -> None:
        """Deep-dive on a story referenced by an explicit ExplainArticle intent."""
        arts = self._last_articles
        if not arts:
            await self._respond("I haven't read any stories yet.")
            return
        idx = self._resolve_index(intent.query, len(arts))
        await self._explain_at(arts, idx)

    async def _explain_current(self) -> None:
        """Deep-dive on whatever story is currently selected in the context."""
        if not self.context.has_articles:
            await self._respond("I haven't read any stories yet.")
            return
        await self._explain_at(self.context.articles, self.context.selected_index)

    async def _read_story(self, i: int) -> None:
        """Read a single story (by index) from the current context aloud."""
        arts = self.context.articles
        if not arts or i < 0 or i >= len(arts):
            await self._respond("I'm not sure which story you mean.")
            return
        line = VoiceAgent._to_speech(arts[i], i + 1)
        await self._respond(line)

    async def _navigate(self, delta: int) -> None:
        """Move the selection by ``delta`` (+1 next / -1 previous) and read it.

        At either end of the list the selection stays put and JARVIS says so,
        rather than silently wrapping or erroring.
        """
        arts = self.context.articles
        if not arts:
            await self._respond("I haven't read any stories yet.")
            return
        cur = self.context.selected_index
        new = cur + delta
        if new < 0:
            await self._respond(_pick(_START_LINES))
            return
        if new >= len(arts):
            await self._respond(_pick(_NO_MORE_LINES))
            return
        # Navigation is confirmation-free by design: just read the next story
        # instead of announcing "moving to the next story...".
        self.context.selected_index = new
        await self._read_story(new)

    async def _select_ordinal(self, intent) -> None:
        """Jump straight to the ordinal story named in a SelectStory intent."""
        arts = self.context.articles
        if not arts:
            await self._respond("I haven't read any stories yet.")
            return
        idx = intent.index or 0
        if idx >= len(arts):
            top = _ORDINAL_WORDS[min(len(arts), 5) - 1]
            await self._respond(
                f"I only have {len(arts)} stories, from the first to the {top}."
            )
            return
        self.context.selected_index = idx
        await self._read_story(idx)

    @staticmethod
    def _resolve_index(query: Optional[str], n: int) -> Optional[int]:
        if not query:
            return 0
        q = query.lower()
        ordinals = {
            "first": 0, "1st": 0, "1": 0,
            "second": 1, "2nd": 1, "2": 1,
            "third": 2, "3rd": 2, "3": 2,
            "fourth": 3, "4th": 3, "4": 3,
            "fifth": 4, "5th": 4, "5": 4,
        }
        for word, i in ordinals.items():
            if word in q:
                return i
        if any(w in q for w in ("last", "previous", "that", "it")):
            return max(n - 1, 0)
        return 0

    async def _respond(self, *lines: str) -> None:
        self._set_state(AgentState.SPEAKING)
        text = " ".join(lines)
        for line in lines:
            print(f"[agent] {line}")
            loop = asyncio.get_event_loop()
            # Speak off the event loop so the mic stays idle until done.
            await loop.run_in_executor(None, self.voice.speak, line)
        self._last_spoken = text
        # Mirror into the session context so follow-up continuity ("what was
        # that?") and any future response-aware logic can read the last line.
        self.context.last_response = text
        # Stay in SPEAKING only if the session is ending (shutdown set);
        # otherwise we're back to listening for the next command.
        if not self._shutdown.is_set():
            self._set_state(AgentState.LISTENING)
