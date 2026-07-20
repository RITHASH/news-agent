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

# Conversation-context follow-ups (no LLM). These act on the session's
# remembered category / article list / selected article rather than fetching
# fresh news. Kept as constants so NewsAgent routes on names, never strings.
TELL_MORE = "TellMore"       # "tell me more" / "more" -> explain selected story
NEXT = "Next"                # "next" / "skip" / "continue" -> advance selection
PREVIOUS = "Previous"        # "previous" / "back" -> step back selection
SELECT_STORY = "SelectStory" # "first story" / "third one" -> jump to ordinal


@dataclass
class Intent:
    """Structured result of classification. Routing layers act on this
    instead of speaking directly, so voice/text and NewsAgent stay decoupled."""

    name: str
    raw: str
    category: Optional[str] = None   # set for *News category intents
    query: Optional[str] = None      # set for ExplainArticle (the article ref)
    index: Optional[int] = None      # set for SelectStory (0-based ordinal)
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

# --- Conversation-context follow-ups -------------------------------------
# These only make sense inside an active session (NewsAgent supplies the
# context). The regexes are deliberately narrow so they never steal a phrase
# that belongs to EXPLAIN / CATEGORY / LATEST:
#  * "more about X" / "more on X" must stay EXPLAIN  -> handled at step 3,
#    and TELL_MORE's negative lookahead also rejects "more news".
#  * "more technology news" must stay a CATEGORY fetch -> CATEGORY is checked
#    before TELL_MORE, and "news" is excluded by the lookahead anyway.
_TELL_MORE_RE = re.compile(
    r"\b(tell me more|more)\b(?!\s+(?:about|on|news))",
    re.I,
)

# Advance / step back through the current article list.
_NAV_NEXT = [
    "next", "next story", "next one", "skip", "skip this", "skip story",
    "continue", "continue reading", "keep going", "go on",
]
_NAV_PREV = [
    "previous", "previous story", "previous one", "back", "go back",
]
_NAV_NEXT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _NAV_NEXT) + r")\b", re.I,
)
_NAV_PREV_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _NAV_PREV) + r")\b", re.I,
)

# Jump straight to "first/second/... story" (or "... one" / "... article").
# Word ordinals and suffixed digits take an optional noun; a bare digit is
# only honored when it is immediately followed by a noun, so "top 5 stories"
# never collapses into SelectStory. Captured for the 0-based index lookup.
_ORDINALS = {
    "first": 0, "1st": 0,
    "second": 1, "2nd": 1,
    "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3,
    "fifth": 4, "5th": 4,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4,
}
_STORY_ORDINAL_RE = re.compile(
    r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b"
    r"(?:\s*(?:story|one|article))?",
    re.I,
)
_STORY_DIGIT_RE = re.compile(r"\b([1-5])\s+(?:story|one|article)\b", re.I)


def _ordinal_index(text: str) -> Optional[int]:
    """Return the 0-based index for an ordinal story reference, else None."""
    m = _STORY_ORDINAL_RE.search(text)
    if m:
        return _ORDINALS.get(m.group(1).lower())
    m = _STORY_DIGIT_RE.search(text)
    if m:
        return _ORDINALS.get(m.group(1))
    return None


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

        # 5) Pick a story by ordinal, e.g. "first story" / "third one".
        idx = _ordinal_index(lowered)
        if idx is not None:
            return Intent(SELECT_STORY, text, index=idx)

        # 6) "tell me more" / "more" -> expand the currently selected story.
        #    Negative lookahead (see _TELL_MORE_RE) keeps "more about X" and
        #    "more news" out of here; both map to EXPLAIN / CATEGORY above.
        if _TELL_MORE_RE.search(lowered):
            return Intent(TELL_MORE, text)

        # 7) Advance through the current article list.
        if _NAV_NEXT_RE.search(lowered):
            return Intent(NEXT, text)

        # 8) Step back through the current article list.
        if _NAV_PREV_RE.search(lowered):
            return Intent(PREVIOUS, text)

        # 9) Generic latest news.
        if any(k in lowered for k in _LATEST):
            return Intent(LATEST_NEWS, text)

        # 10) Repeat the last response.
        if any(k in lowered for k in _REPEAT):
            return Intent(REPEAT, text)

        # 11) Fallback.
        return Intent(UNKNOWN, text, confidence=0.0)
