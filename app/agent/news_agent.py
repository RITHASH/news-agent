from __future__ import annotations

import asyncio
import time
from typing import List, Optional

from app.agents import NewsSummarizer
from app.briefing import MorningBriefing, present_streaming
from app.conversation import (
    AI_NEWS,
    BUSINESS_NEWS,
    ConversationManager,
    EXPLAIN_ARTICLE,
    FINANCE_NEWS,
    LATEST_NEWS,
    MORNING_BRIEF,
    POLITICS_NEWS,
    REPEAT,
    SCIENCE_NEWS,
    SPORTS_NEWS,
    STARTUP_NEWS,
    STOP,
    TECHNOLOGY_NEWS,
    WORLD_NEWS,
)
from app.fetchers import NewsFetcher
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


class NewsAgent:
    """The brain / top-level entry point for the news application.

    Owns every long-lived component and orchestrates the wake-word lifetime:
    idle (listen for "JARVIS") -> wake -> conversation session -> idle again.
    The microphone stays idle while a response is being spoken.
    """

    def __init__(
        self,
        query: str = "technology news",
        top_n: int = 5,
        max_per_source: int = 5,
        session_timeout: float = 25.0,
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
        # Set within a session to end it (Stop, or idle timeout) and return
        # to wake-word listening. NOT a process exit.
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        self.voice = VoiceAgent(get_tts_engine(), top_n=self.top_n)
        self.briefing = MorningBriefing(self.voice, query=self.query, top_n=self.top_n)

        # 1) Morning Brief at startup (the news pipeline is unchanged).
        self._last_articles = await self.briefing.run()

        # 2) Wake-word lifetime: idle -> session -> idle ...
        while True:
            print("[JARVIS] Listening for wake word...")
            try:
                await self.wakeword.wait_for_wake()
            except Exception as e:
                # Mic unavailable / backend error: don't take down the
                # process. Log and retry after a short pause.
                print(f"[wakeword] detection failed: {e}")
                await asyncio.sleep(2.0)
                continue
            try:
                self.voice.speak("Yes?")  # short acknowledgment
            except Exception as e:
                print(f"[voice] acknowledgment failed: {e}")
            await self._run_session()

    async def _run_session(self) -> None:
        self._shutdown.clear()
        print("[JARVIS] Listening...")
        last_speech = time.monotonic()
        # One conversation session: listen -> route -> speak -> listen again,
        # until the user says Stop or goes quiet for session_timeout seconds.
        while not self._shutdown.is_set():
            try:
                text = await self.stt.listen()  # mic idle until now
            except Exception as e:
                print(f"[stt] listening failed: {e}")
                text = ""
            if not text:
                if time.monotonic() - last_speech > self.session_timeout:
                    self.voice.speak("Going back to sleep.")
                    return
                continue
            last_speech = time.monotonic()
            print(f"[you] {text}")
            intent = await self.conversation.handle(text)
            await self._route(intent)

    # ------------------------------------------------------------------ #
    # routing
    # ------------------------------------------------------------------ #
    async def _route(self, intent) -> None:
        name = intent.name
        if name == STOP:
            # End this session and return to wake-word listening.
            await self._respond("Goodbye for now.")
            self._shutdown.set()
            return
        if name == REPEAT:
            if self._last_spoken:
                await self._respond(self._last_spoken)
            else:
                await self._respond("I haven't said anything yet.")
            return
        if name == MORNING_BRIEF:
            self._last_articles = await self.briefing.run()
            return
        if name == EXPLAIN_ARTICLE:
            await self._explain(intent)
            return
        if name in _QUERY_FOR:
            self._last_articles = await self._fetch_and_speak(_QUERY_FOR[name])
            return
        # Unknown / fallback.
        await self._respond(
            "Sorry, I didn't catch that. You can ask for latest, technology, "
            "startup, world, or sports news."
        )

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #
    async def _fetch_and_speak(self, query: str) -> List[NewsArticle]:
        fetcher = NewsFetcher(query=query, max_per_source=self.max_per_source)
        intro = [f"Here are the top {self.top_n} {query} stories."]
        # Stream: speak the intro instantly, then read high-priority stories as
        # their source finishes while the rest keep fetching in the background.
        articles, spoken = await present_streaming(
            fetcher,
            NewsProcessor(),
            NewsSummarizer(),
            self.voice,
            self.top_n,
            intro,
            lambda n: [],
        )
        self._last_spoken = spoken
        return articles

    async def _explain(self, intent) -> None:
        arts = self._last_articles
        if not arts:
            await self._respond("I haven't read any stories yet.")
            return
        idx = self._resolve_index(intent.query, len(arts))
        if idx is None or idx >= len(arts):
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
        }
        for word, i in ordinals.items():
            if word in q:
                return i
        if any(w in q for w in ("last", "previous", "that", "it")):
            return max(n - 1, 0)
        return 0

    async def _respond(self, *lines: str) -> None:
        text = " ".join(lines)
        for line in lines:
            print(f"[agent] {line}")
            loop = asyncio.get_event_loop()
            # Speak off the event loop so the mic stays idle until done.
            await loop.run_in_executor(None, self.voice.speak, line)
        self._last_spoken = text
