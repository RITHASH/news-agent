from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Recognized intent names. Kept as constants so callers never string-match.
MORNING_BRIEF = "MorningBrief"
LATEST_NEWS = "LatestNews"
TECHNOLOGY_NEWS = "TechnologyNews"
STARTUP_NEWS = "StartupNews"
WORLD_NEWS = "WorldNews"
SPORTS_NEWS = "SportsNews"
AI_NEWS = "AINews"
BUSINESS_NEWS = "BusinessNews"
SCIENCE_NEWS = "ScienceNews"
POLITICS_NEWS = "PoliticsNews"
FINANCE_NEWS = "FinanceNews"
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

# Category alias sets, in priority order (first match wins). Each alias is
# matched as a whole word / phrase via a word-boundary regex, so e.g. "ai"
# never matches inside "said" and "tech" never matches inside "biotech".
# The narrower "ai" is listed ahead of the broader "technology".
_CATEGORY_ALIASES: List[Tuple[str, str, List[str]]] = [
    ("ai", AI_NEWS, [
        "ai", "artificial intelligence", "machine learning",
        "ml", "llm", "gpt", "chatbot", "neural network",
    ]),
    ("technology", TECHNOLOGY_NEWS, [
        "tech", "technology", "software", "hardware", "gadget",
        "computing", "computer", "chip", "silicon",
    ]),
    ("startup", STARTUP_NEWS, [
        "startup", "startups", "venture", "funding", "entrepreneur",
    ]),
    ("business", BUSINESS_NEWS, [
        "business", "businesses", "company", "companies", "corporate",
        "corporation", "ceo", "enterprise",
    ]),
    ("science", SCIENCE_NEWS, [
        "science", "scientific", "scientist", "research", "researcher",
        "study", "physics", "chemistry", "biology", "space", "nasa", "astronomy",
    ]),
    ("politics", POLITICS_NEWS, [
        "politics", "political", "election", "government", "president",
        "senate", "congress", "parliament", "policy", "policies", "vote", "campaign",
    ]),
    ("finance", FINANCE_NEWS, [
        "finance", "financial", "market", "markets", "stock", "stocks",
        "economy", "economic", "money", "crypto", "bitcoin", "trading",
        "invest", "investor",
    ]),
    ("world", WORLD_NEWS, [
        "world", "global", "international", "foreign", "country", "countries",
        "nation", "nations",
    ]),
    ("sports", SPORTS_NEWS, [
        "sport", "sports", "football", "soccer", "basketball", "nba",
        "cricket", "olympic", "match", "game",
    ]),
]

# Precompile once at import time so per-call matching stays O(phrases) and
# adds no per-request regex cost.
_CATEGORY_PATTERNS: List[Tuple[str, str, List[re.Pattern[str]]]] = [
    (cat, intent, [re.compile(r"\b" + re.escape(kw) + r"\b") for kw in kws])
    for cat, intent, kws in _CATEGORY_ALIASES
]

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

        # 4) Category-scoped news (whole-word / phrase match, priority order).
        for cat, intent, patterns in _CATEGORY_PATTERNS:
            if any(p.search(lowered) for p in patterns):
                return Intent(intent, text, category=cat)

        # 5) Generic latest news.
        if any(k in lowered for k in _LATEST):
            return Intent(LATEST_NEWS, text)

        # 6) Repeat the last response.
        if any(k in lowered for k in _REPEAT):
            return Intent(REPEAT, text)

        # 7) Fallback.
        return Intent(UNKNOWN, text, confidence=0.0)
