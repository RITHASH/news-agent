"""Lightweight, long-term user preferences for JARVIS (V1.1 #4).

This is a self-contained *personalization layer*. It owns:
  * the preference data model (``UserPreferences``),
  * JSON load/save with graceful handling of missing/corrupt files,
  * a deterministic, LLM-free ``PreferenceRecognizer`` that maps natural
    voice commands ("remember I like AI news", "read only three stories",
    "turn AI summaries off", ...) onto intent objects.

It deliberately knows nothing about the conversation router, the state
machine, the cache, or the fetch/streaming pipelines - those stay untouched.
The agent decides what to *do* with a ``PreferenceIntent``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# --- Canonical category data ----------------------------------------------
# Maps an internal category key -> the query string the fetcher understands.
# Kept local (not imported from the router) so this layer stays decoupled.
CATEGORY_QUERIES = {
    "ai": "artificial intelligence news",
    "technology": "technology news",
    "startup": "startup news",
    "business": "business news",
    "science": "science news",
    "politics": "politics news",
    "finance": "finance news",
    "world": "world news",
    "sports": "sports news",
}

# Spoken/alias -> canonical key, so "tech", "ml", "stocks" etc. all normalize.
CATEGORY_ALIASES = {
    "ai": ["ai", "a.i", "artificial intelligence", "ml", "machine learning"],
    "technology": ["technology", "tech", "software", "hardware"],
    "startup": ["startup", "startups", "ventures", "venture"],
    "business": ["business", "businesses", "companies", "company", "corporate"],
    "science": ["science", "scientific", "research"],
    "politics": ["politics", "political", "government"],
    "finance": ["finance", "financial", "markets", "market", "stocks", "stock",
                "economy", "crypto"],
    "world": ["world", "global", "international"],
    "sports": ["sports", "sport"],
}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
}

# --- Preference intent constants ------------------------------------------
REMEMBER_CATEGORY = "RememberCategory"
SET_NUM_STORIES = "SetNumStories"
SET_SUMMARIES = "SetSummaries"
FORGET_PREFS = "ForgetPrefs"
RECALL_PREFS = "RecallPrefs"


@dataclass
class PreferenceIntent:
    """Result of ``PreferenceRecognizer.recognize``. The agent routes on
    ``name`` and reads the optional payload fields."""

    name: str
    category: Optional[str] = None   # RememberCategory: canonical key
    first: bool = False              # RememberCategory: move category to front
    value: Optional[int] = None      # SetNumStories: number of stories
    on: Optional[bool] = None        # SetSummaries: True=on / False=off


@dataclass
class UserPreferences:
    """Long-term, user-facing preferences. Persisted as JSON.

    Never holds conversation history or anything sensitive - only the four
    knobs the user can set with their voice.
    """

    categories: List[str] = field(default_factory=list)  # ordered, preferred first
    num_stories: int = 5                                 # stories to read (default 5)
    ai_summaries: bool = True                            # LLM enrichment on/off
    loaded: bool = False                                 # True if read from a file

    # --- Serialization -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "categories": list(self.categories),
            "num_stories": self.num_stories,
            "ai_summaries": self.ai_summaries,
        }

    @classmethod
    def from_dict(cls, data: object) -> "UserPreferences":
        """Build from a parsed JSON object, coercing/validating every field.

        Raises ``ValueError`` on a structurally broken object so the caller can
        fall back to defaults; individual bad fields are clamped per-field.
        """
        if not isinstance(data, dict):
            raise ValueError("preferences root must be a JSON object")
        raw_cats = data.get("categories", [])
        if not isinstance(raw_cats, list):
            raw_cats = []
        cats = [
            c for c in raw_cats
            if isinstance(c, str) and c in CATEGORY_QUERIES
        ]
        ns = data.get("num_stories", 5)
        if not isinstance(ns, int) or not (1 <= int(ns) <= 20):
            ns = 5
        ai = data.get("ai_summaries", True)
        if not isinstance(ai, bool):
            ai = True
        return cls(categories=cats, num_stories=int(ns), ai_summaries=ai)

    def save(self, path) -> None:
        """Atomically write preferences as JSON. Never raises on I/O errors -
        it logs and returns so a bad disk can't crash the assistant."""
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
            os.replace(tmp, path)  # atomic on the same filesystem
        except OSError as e:
            print(f"[prefs] could not save preferences to {path}: {e}")

    def reset(self) -> None:
        """Forget everything (user said 'forget my preferences')."""
        self.categories = []
        self.num_stories = 5
        self.ai_summaries = True
        self.loaded = False

    # --- Derived helpers ---------------------------------------------------
    def briefing_queries(self, default_query: str) -> List[str]:
        """Ordered query list for the morning briefing: preferred categories
        first, then the default category so 'remaining categories' still
        appear. (Multiple full category fetches are kept bounded for speed.)"""
        queries = [CATEGORY_QUERIES[c] for c in self.categories if c in CATEGORY_QUERIES]
        if default_query not in queries:
            queries.append(default_query)
        return queries

    def describe(self) -> str:
        """A short, natural recap for 'what do you remember about me?'."""
        if self.categories:
            cat_part = "You like " + ", ".join(f"{c} news" for c in self.categories)
        else:
            cat_part = "You haven't told me which categories you like"
        n_part = f"I read {self.num_stories} stories at a time"
        s_part = "AI summaries are " + ("on" if self.ai_summaries else "off")
        return f"{cat_part}. {n_part}. {s_part}."


def load_preferences(path) -> UserPreferences:
    """Load preferences, always succeeding.

    * Missing file  -> defaults (loaded=False).
    * Unreadable / corrupt JSON -> defaults + a warning.
    * Structurally invalid -> defaults + a warning.
    """
    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return UserPreferences()
    except (json.JSONDecodeError, OSError) as e:
        print(f"[prefs] could not read {path} ({e}); using defaults")
        return UserPreferences()
    try:
        prefs = UserPreferences.from_dict(data)
    except Exception as e:  # broad: any validation surprise -> safe defaults
        print(f"[prefs] invalid preferences in {path} ({e}); using defaults")
        return UserPreferences()
    prefs.loaded = True
    return prefs


def default_prefs_path() -> Path:
    """``<repo>/config/user_preferences.json`` (created on first save)."""
    return Path(__file__).resolve().parent.parent.parent / "config" / "user_preferences.json"


# --- Recognition ----------------------------------------------------------
_REMEMBER_RE = re.compile(
    r"\b(remember|note|keep in mind)\b.*?\b(like|prefer|love|want|enjoy|follow)\b"
    r"\s+(?:that\s+)?(?P<cat>.+?)(?:\s+news)?\s*(?P<first>first)?\b",
    re.I,
)
_BARE_LIKE_RE = re.compile(
    r"\b(i|we)\b\s+(?:really\s+)?\b(like|prefer|love|enjoy|follow)\b"
    r"\s+(?P<cat>.+?)(?:\s+news)?\s*(?P<first>first)?\b",
    re.I,
)
_NUM_STORIES_RE = re.compile(
    r"\b(?:read|only|just|play|tell me|give me)\b[^.]*?\b(?P<a>\w+)\b\s+stories\b"
    r"|\b(?P<b>\w+)\b\s+stories\b",
    re.I,
)


def _normalize_category(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    key = raw.strip().lower().strip(" .!,")
    for det in ("the ", "a ", "an ", "some "):
        if key.startswith(det):
            key = key[len(det):]
    if key in CATEGORY_QUERIES:
        return key
    for canon, aliases in CATEGORY_ALIASES.items():
        if key in aliases:
            return canon
    return None


def _word_to_int(word: Optional[str]) -> Optional[int]:
    if not word:
        return None
    w = word.strip().lower()
    if w.isdigit():
        try:
            return int(w)
        except ValueError:
            return None
    return WORD_NUMBERS.get(w)


class PreferenceRecognizer:
    """Deterministic voice-command -> PreferenceIntent mapper (no LLM)."""

    def recognize(self, text: str) -> Optional[PreferenceIntent]:
        if not text:
            return None
        low = text.lower()

        # Forget / reset.
        if "forget" in low and "preferenc" in low:
            return PreferenceIntent(FORGET_PREFS)
        if "reset" in low and "preferenc" in low:
            return PreferenceIntent(FORGET_PREFS)

        # Recap request.
        if ("me" in low and ("remember" in low or "know" in low)
                and ("what" in low or "tell me" in low)):
            return PreferenceIntent(RECALL_PREFS)

        # AI summaries on/off.
        if "summar" in low and ("on" in low or "off" in low):
            return PreferenceIntent(SET_SUMMARIES, on=("on" in low))

        # Number of stories to read.
        m = _NUM_STORIES_RE.search(low)
        if m:
            n = _word_to_int(m.group("a") or m.group("b"))
            if n is not None and 1 <= n <= 20:
                return PreferenceIntent(SET_NUM_STORIES, value=n)

        # Remember / like a category (explicit or bare "I like X").
        cat, first = self._match_category(low)
        if cat is not None:
            return PreferenceIntent(REMEMBER_CATEGORY, category=cat, first=first)

        return None

    @staticmethod
    def _match_category(low: str):
        for rx in (_REMEMBER_RE, _BARE_LIKE_RE):
            m = rx.search(low)
            if m:
                first = m.group("first") is not None
                cat = _normalize_category(m.group("cat"))
                if cat is not None:
                    return cat, first
        return None, False
