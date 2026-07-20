from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, List

from app.models import NewsArticle

# Category -> keyword fragments (lowercased). Longer / more specific
# categories are checked first so e.g. "AI" wins over the broader "Tech".
_CATEGORY_KEYWORDS: List[tuple[str, tuple[str, ...]]] = [
    ("AI", (
        "ai ", " a.i.", "artificial intelligence", "machine learning", "deep learning",
        "neural network", "llm", "chatgpt", "openai", "anthropic", "gemini",
        "generative ai", "gpt-", "transformer model",
    )),
    ("Tech", (
        "tech", "technology", "software", "hardware", "chip", "semiconductor",
        "apple", "google", "microsoft", "android", "iphone", "ipad", "macos",
        "cyber", "hacker", "malware", "ransomware", "data breach", "cloud ",
        "quantum", "robot", "app store", "operating system", "gpu", "silicon",
    )),
    ("Science", (
        "science", "scientist", "research", "study finds", "physics", "chemistry",
        "biology", "space", "nasa", "astronomy", "telescope", "climate",
        "discovery", "experiment", "particle", "genome", "asteroid", "galaxy",
    )),
    ("Health", (
        "health", "covid", "virus", "pandemic", "disease", "medical", "medicine",
        "vaccine", "hospital", "cancer", "mental health", "drug", "fda",
        "outbreak", "epidemic", "surgeon",
    )),
    ("Sports", (
        "sport", "football", "soccer", "nba", "nfl", "cricket", "olympic",
        "championship", "world cup", "tennis", "rugby", "baseball", "hockey",
        "league", "match", "tournament", "athlete", "medal", "coach",
    )),
    ("Politics", (
        "politic", "election", "president", "government", "senate", "congress",
        "parliament", "policy", "parliament", "bill ", "lawmaker", "minister",
        "vote", "campaign", "democrat", "republican", "prime minister", "sanction",
    )),
    ("Business", (
        "business", "company", "startup", "ceo", "merger", "acquisition",
        "takeover", "buyout", "layoff", "layoffs", "earnings", "revenue",
        "corporate", "founder", "ipo", "venture capital", "funding round",
        "workforce", "billion", "million", "profit", "losses", "quarterly",
        "shares", "brand",
    )),
    ("Finance", (
        "finance", "stock", "stocks", "share price", "nasdaq", "dow jones",
        "interest rate", "inflation", "bitcoin", "crypto", "economy", " gdp",
        "recession", "tariff", "central bank", "federal reserve", "bond",
        "forex", "market", "investor", "dividend",
    )),
    ("World", (
        "world", "nation", "country", "war", "conflict", "un ", "nato", "europe",
        "asia", "africa", "middle east", "russia", "ukraine", "china", "israel",
        "palestine", "international", "diplomacy", "embassy", "border",
    )),
    ("Entertainment", (
        "movie", "film", "music", "celebrity", "actor", "actress", "album",
        "netflix", "box office", "tv show", "streaming", "concert", "award",
        "hollywood", "single",
    )),
]

# Sources that carry broad editorial weight -> recency-independent boost.
_MAJOR_SOURCES: Dict[str, int] = {
    "bbc": 15, "cnn": 15, "reuters": 15, "nytimes": 12, "the guardian": 12,
    "associated press": 12, "ap": 12, "washington post": 12, "bloomberg": 12,
    "the verge": 10, "techcrunch": 10, "arstechnica": 10, "wired": 10,
    "financial times": 12, "ft": 12, "al jazeera": 12, "npr": 12,
}

# Title fragments that signal high news value.
_HIGH_SIGNAL = (
    "breaking", "urgent", "exclusive", "first", "live", "death", "dead",
    "attack", "killed", "explosion", "emergency", "warning", "crisis",
    "record", "ban", "arrest", "convicted", "resign", "resigns", "election",
    "disaster", "earthquake", "shooting", "strike", "default", "collapse",
)


def _as_utc(dt: datetime) -> datetime:
    """Normalize a possibly-naive datetime to timezone-aware UTC.

    The fetcher emits a mix: naive ``datetime.utcnow()`` defaults and
    tz-aware parsed dates. Comparing/sorting them directly raises, so the
    pipeline treats everything as UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm_url(url) -> str:
    s = str(url).strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("?")[0].split("#")[0].rstrip("/")
    return s


def _norm_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _titles_similar(a: str, b: str, threshold: float) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # A short title fully contained in a longer one is a near-duplicate.
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


class NewsProcessor:
    """Deterministic, dependency-free news pipeline.

    Dedup -> categorize -> score -> sort. No network, no LLM, no state.
    """

    def __init__(self, title_similarity_threshold: float = 0.90):
        self.title_similarity_threshold = title_similarity_threshold

    def process(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        unique = self._dedupe(articles)
        for a in unique:
            a.category = self._categorize(a)
            a.importance_score = self._score(a)
        unique.sort(key=lambda a: _as_utc(a.published_at), reverse=True)
        return unique

    # ------------------------------------------------------------------ #
    # dedupe
    # ------------------------------------------------------------------ #
    def _dedupe(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        seen_urls: set[str] = set()
        seen_titles: List[str] = []
        out: List[NewsArticle] = []
        for a in articles:
            u = _norm_url(a.url)
            if u and u in seen_urls:
                continue
            nt = _norm_title(a.title)
            if any(_titles_similar(nt, s, self.title_similarity_threshold)
                   for s in seen_titles):
                continue
            if u:
                seen_urls.add(u)
            if nt:
                seen_titles.append(nt)
            out.append(a)
        return out

    # ------------------------------------------------------------------ #
    # categorize
    # ------------------------------------------------------------------ #
    @staticmethod
    def _categorize(article: NewsArticle) -> str:
        text = f"{article.title or ''} {article.summary or ''}".lower()
        best_cat, best_hits = "General", 0
        for cat, keywords in _CATEGORY_KEYWORDS:
            hits = sum(1 for kw in keywords if kw in text)
            if hits > best_hits:
                best_cat, best_hits = cat, hits
        return best_cat

    # ------------------------------------------------------------------ #
    # score
    # ------------------------------------------------------------------ #
    @staticmethod
    def _score(article: NewsArticle) -> int:
        score = 20  # baseline

        # Recency: newer -> higher.
        now = datetime.now(timezone.utc)
        age_h = (now - _as_utc(article.published_at)).total_seconds() / 3600.0
        if age_h < 0:  # clock skew / future-dated
            age_h = 0
        if age_h < 6:
            score += 30
        elif age_h < 24:
            score += 25
        elif age_h < 72:
            score += 18
        elif age_h < 168:
            score += 12
        else:
            score += 6

        # Source weight.
        src = (article.source or "").lower()
        score += max((v for k, v in _MAJOR_SOURCES.items() if k in src), default=5)

        # Category weight.
        cat_weights = {
            "World": 12, "Politics": 12, "Business": 11, "Finance": 11,
            "AI": 10, "Tech": 10, "Science": 10, "Health": 10, "Sports": 8,
            "Entertainment": 7, "General": 5,
        }
        score += cat_weights.get(article.category or "General", 5)

        # Title high-signal keywords.
        low_title = (article.title or "").lower()
        if any(sig in low_title for sig in _HIGH_SIGNAL):
            score += 8

        # Presence of author / summary adds a little substance.
        if article.author:
            score += 3
        if article.summary:
            score += 4

        return max(0, min(100, score))
