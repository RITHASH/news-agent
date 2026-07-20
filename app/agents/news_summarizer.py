from __future__ import annotations

import asyncio
import json
import os
import re
from typing import List, Optional

from openai import AsyncOpenAI, RateLimitError

from app.models import NewsArticle

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "openai/gpt-oss-120b:free"

_SYSTEM_PROMPT = (
    "You are a concise news analyst. For the given article you produce three "
    "short, factual fields and nothing else. Do not speculate wildly; ground "
    "your answer in the title and summary provided. Respond with STRICT JSON "
    "only, no markdown, using exactly these keys:\n"
    '{"one_line_summary": "...", "why_it_matters": "...", "possible_impact": "..."}\n'
    "Each value must be a single sentence. one_line_summary: neutral factual "
    "recap. why_it_matters: the significance for readers. possible_impact: a "
    "plausible near-term consequence."
)


class NewsSummarizer:
    """Enrich the most important articles with LLM-generated analysis.

    Uses the OpenRouter chat-completions API (OpenAI-compatible) concurrently,
    bounded by a semaphore. Any per-article failure degrades to leaving that
    article's LLM fields as ``None`` so the pipeline always completes.
    """

    def __init__(
        self,
        top_n: int = 10,
        min_importance: int = 55,
        concurrency: int = 5,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_base: float = 2.0,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.top_n = top_n
        self.min_importance = min_importance
        self.concurrency = concurrency
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base = retry_base
        self.model = model or os.environ.get("MODEL_NAME") or _DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        # name -> "ok (N)" / "skipped: <reason>" / "error: ..."
        self.status: str = ""

    async def summarize(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        if not articles:
            self.status = "ok (0)"
            return articles
        if not self.api_key:
            self.status = "skipped: OPENROUTER_API_KEY not set in .env"
            return articles

        # Only enrich the top-N most important; the rest pass through untouched.
        important = [a for a in articles if a.importance_score >= self.min_importance]
        targets = sorted(important, key=lambda a: a.importance_score, reverse=True)[
            : self.top_n
        ]
        if not targets:
            self.status = "ok (0)"
            return articles

        client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=_OPENROUTER_BASE_URL,
            timeout=self.timeout,
        )
        sem = asyncio.Semaphore(self.concurrency)

        async def _run(article: NewsArticle) -> bool:
            async with sem:
                return await self._summarize_one(client, article)

        try:
            results = await asyncio.gather(
                *(_run(a) for a in targets), return_exceptions=True
            )
        finally:
            await client.close()

        ok = sum(1 for r in results if r is True)
        failed = len(results) - ok
        self.status = f"ok ({ok}/{len(targets)})" + (
            f", {failed} failed" if failed else ""
        )
        return articles

    async def _summarize_one(
        self, client: AsyncOpenAI, article: NewsArticle
    ) -> bool:
        user_prompt = self._build_prompt(article)
        content = None
        for attempt in range(self.max_retries):
            try:
                resp = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=300,
                )
            except RateLimitError:
                # Free-tier upstreams are periodically throttled; back off and
                # retry, but don't burn the whole run on a stuck model.
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_base * (2 ** attempt))
                    continue
                return False
            except Exception:
                return False
            content = (
                resp.choices[0].message.content or ""
            ).strip() if resp.choices else ""
            break

        data = self._parse_json(content or "")
        if not data:
            return False

        article.one_line_summary = self._clean(data.get("one_line_summary"))
        article.why_it_matters = self._clean(data.get("why_it_matters"))
        article.possible_impact = self._clean(data.get("possible_impact"))
        return any(
            (article.one_line_summary, article.why_it_matters, article.possible_impact)
        )

    @staticmethod
    def _build_prompt(article: NewsArticle) -> str:
        parts = [f"Title: {article.title}"]
        if article.source:
            parts.append(f"Source: {article.source}")
        if article.category:
            parts.append(f"Category: {article.category}")
        if article.summary:
            parts.append(f"Summary: {article.summary[:1000]}")
        parts.append("\nReturn the strict JSON described in the system prompt.")
        return "\n".join(parts)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        if not text:
            return None
        # Strip ```json fences if the model added them despite instructions.
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        # Fallback: grab the first {...} block.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
        return None

    @staticmethod
    def _clean(value) -> Optional[str]:
        if not value or not isinstance(value, str):
            return None
        v = " ".join(value.split()).strip()
        return v or None
