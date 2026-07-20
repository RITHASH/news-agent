from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Recognized intent names. Kept as constants so callers never string-match.
MORNING_BRIEF = "MorningBrief"
LATEST_NEWS = "LatestNews"
TECHNOLOGY_NEWS = "TechnologyNews"
STARTUP_NEWS = "StartupNews"
WORLD_NEWS = "WorldNews"
SPORTS_NEWS = "SportsNews"
EXPLAIN_ARTICLE = "ExplainArticle"
REPEAT = "Repeat"
STOP = "Stop"
UNKNOWN = "Unknown"


@dataclass
class Intent:
    """Structured result of classification. Routing layers act on this
    instead of speaking directly, so voice/text and NewsAgent stay decoupled."""

    name: str
    raw: str
    category: Optional[str] = None   # set for *News category intents
    query: Optional[str] = None      # set for ExplainArticle (the article ref)
    confidence: float = 1.0

    def __str__(self) -> str:
        return self.name


# "good morning" / "briefing" win outright.
_MORNING = ["good morning", "morning briefing", "morning brief", "daily brief", "brief me", "briefing"]

# End the session.
_STOP = ["stop", "exit", "quit", "goodbye", "bye", "that's all", "that is all", "end it", "shut down"]

# Re-state the last response.
_REPEAT = ["repeat", "say again", "once more", "again"]

# Category keyword sets -> the intent that represents them.
_CATEGORY_KEYWORDS = {
    "technology": ["technology", "tech", "software", "ai", "artificial intelligence", "gadget", "computing"],
    "startup": ["startup", "startups", "venture", "funding", "entrepreneur"],
    "world": ["world", "global", "international", "foreign"],
    "sports": ["sport", "sports", "football", "soccer", "basketball", "nba", "cricket", "match", "game"],
}
_CATEGORY_INTENT = {
    "technology": TECHNOLOGY_NEWS,
    "startup": STARTUP_NEWS,
    "world": WORLD_NEWS,
    "sports": SPORTS_NEWS,
}

# Generic "give me news" phrasing.
_LATEST = ["latest", "recent", "headline", "headlines", "news", "what's new",
           "what is new", "tell me the news", "top stories", "update", "new today"]

# Explain a specific story; capture whatever follows as the article reference.
_EXPLAIN_RE = re.compile(
    r"\b(explain|tell me more about|more about|more on|"
    r"details (?:on|about)|elaborate(?: on)?)\b[:\s]*(.*)",
    re.I,
)


class ConversationManager:
    """Deterministic intent router. No LLM — pure keyword/rule matching,
    evaluated in priority order. This is the permanent routing layer for all
    future voice and text interactions."""

    async def handle(self, user_message: str) -> Intent:
        text = (user_message or "").strip()
        lowered = text.lower()

        # 1) Morning briefing.
        if any(k in lowered for k in _MORNING):
            return Intent(MORNING_BRIEF, text)

        # 2) End the session.
        if any(k in lowered for k in _STOP):
            return Intent(STOP, text)

        # 3) Explain a specific article (context-dependent; keep the reference).
        m = _EXPLAIN_RE.search(lowered)
        if m:
            query = m.group(2).strip()
            return Intent(EXPLAIN_ARTICLE, text, query=query or None)

        # 4) Category-scoped news.
        for category, keywords in _CATEGORY_KEYWORDS.items():
            if any(k in lowered for k in keywords):
                return Intent(_CATEGORY_INTENT[category], text, category=category)

        # 5) Generic latest news.
        if any(k in lowered for k in _LATEST):
            return Intent(LATEST_NEWS, text)

        # 6) Repeat the last response.
        if any(k in lowered for k in _REPEAT):
            return Intent(REPEAT, text)

        # 7) Fallback.
        return Intent(UNKNOWN, text, confidence=0.0)
