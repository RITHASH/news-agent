from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Optional

import feedparser

from app.models import NewsArticle


class NewsFetcher:
    """Fetch news concurrently from every configured Agent Reach source.

    Each source maps to the upstream tool that Agent Reach routes to
    (see ~/.agents/skills/agent-reach). Sources whose tool is missing or
    unauthenticated degrade to [] and are reported in ``status`` so the
    combined run still succeeds.
    """

    def __init__(self, query: str = "technology news", max_per_source: int = 5):
        self.query = query
        self.max_per_source = max_per_source
        self.rss_feeds = [
            "https://feeds.bbci.co.uk/news/rss.xml",
            "https://rss.cnn.com/rss/edition.rss",
            "https://feeds.reuters.com/reuters/topNews",
        ]
        # source name -> "ok (N)" or "skipped: <reason>"
        self.status: dict[str, str] = {}

    async def fetch(self) -> List[NewsArticle]:
        results = await asyncio.gather(
            self._fetch_rss(),
            self._fetch_exa(),
            self._fetch_youtube(),
            self._fetch_x(),
            self._fetch_reddit(),
            self._fetch_linkedin(),
            return_exceptions=True,
        )
        articles: List[NewsArticle] = []
        for r in results:
            if isinstance(r, Exception):
                continue
            articles.extend(r)
        return articles

    async def fetch_stream(self):
        """Yield each source's articles as soon as that source completes.

        All six sources launch concurrently (identical to ``fetch``), but the
        caller receives results incrementally instead of blocking on the
        slowest source. This is what lets the pipeline begin speaking before
        the last fetch finishes. Failed sources are skipped; empty batches are
        not yielded.
        """
        tasks = [
            self._fetch_rss(),
            self._fetch_exa(),
            self._fetch_youtube(),
            self._fetch_x(),
            self._fetch_reddit(),
            self._fetch_linkedin(),
        ]
        for done in asyncio.as_completed(tasks):
            batch = await done
            if isinstance(batch, Exception) or not batch:
                continue
            yield batch

    # ------------------------------------------------------------------ #
    # subprocess helper
    # ------------------------------------------------------------------ #
    async def _run_cli(self, args: List[str], timeout: float = 60.0) -> str:
        exe = self._resolve(args[0])
        if not exe:
            raise FileNotFoundError(f"{args[0]} not found on PATH")
        rest = args[1:]
        if exe.lower().endswith((".cmd", ".bat")):
            # npm shims are .cmd; they need cmd.exe to launch.
            proc = await asyncio.create_subprocess_exec(
                "cmd.exe", "/c", exe, *rest,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                exe, *rest, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"timed out after {timeout}s: {' '.join(args)}")
        if proc.returncode != 0:
            raise RuntimeError(
                f"{args[0]} exited {proc.returncode}: {err.decode(errors='replace')[:300]}"
            )
        return out.decode(errors="replace")

    @staticmethod
    def _resolve(name: str) -> Optional[str]:
        exe = shutil.which(name)
        if exe:
            return exe
        # Fall back to the project venv's Scripts dir (covers the case where
        # the venv isn't "activated" but python is run from it).
        for cand in (
            Path(sys.prefix) / "Scripts" / name,
            Path(sys.prefix) / "Scripts" / f"{name}.exe",
        ):
            if cand.exists():
                return str(cand)
        return None

    # ------------------------------------------------------------------ #
    # field helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _norm_url(u: Optional[str]) -> Optional[str]:
        if not u:
            return None
        u = u.strip()
        if "://" not in u:
            u = "https://" + u.lstrip("/")
        return u

    @staticmethod
    def _parse_dt(value) -> Optional[datetime]:
        if value is None:
            return None
        s = str(value).strip()
        if not s or s.upper() in ("N/A", "NA", "NONE", "-"):
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
        try:
            return parsedate_to_datetime(s)
        except Exception:
            pass
        if len(s) == 8 and s.isdigit():
            try:
                return datetime.strptime(s, "%Y%m%d")
            except Exception:
                pass
        try:
            return datetime.utcfromtimestamp(float(s))
        except Exception:
            pass
        return None

    @staticmethod
    def _clean(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        v = value.strip()
        if v.upper() in ("N/A", "NA", "NONE", "-"):
            return None
        return v or None

    def _make_article(
        self, url, title, summary, source, author, published, category=None,
    ) -> Optional[NewsArticle]:
        u = self._norm_url(url)
        if not u:
            return None
        try:
            return NewsArticle(
                id=hashlib.sha256(u.encode()).hexdigest(),
                title=(title or "").strip() or "Untitled",
                summary=self._clean(summary),
                url=u,
                source=source,
                author=self._clean(author),
                published_at=self._parse_dt(published) or datetime.utcnow(),
                category=category,
            )
        except Exception:
            return None

    @staticmethod
    def _to_articles(
        fetcher: "NewsFetcher", items: List[dict], source: str,
    ) -> List[NewsArticle]:
        out: List[NewsArticle] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            a = fetcher._make_article(
                it.get("url"), it.get("title"), it.get("summary"),
                source, it.get("author"), it.get("published"), it.get("category"),
            )
            if a:
                out.append(a)
        return out

    # ------------------------------------------------------------------ #
    # RSS  (in-process feedparser, keyless)
    # ------------------------------------------------------------------ #
    async def _fetch_rss(self) -> List[NewsArticle]:
        articles: List[NewsArticle] = []
        try:
            for feed_url in self.rss_feeds:
                feed = await asyncio.to_thread(feedparser.parse, feed_url)
                for entry in feed.entries:
                    url = entry.get("link")
                    if not url:
                        continue
                    a = self._make_article(
                        url,
                        entry.get("title"),
                        entry.get("summary"),
                        feed.feed.get("title", "RSS"),
                        entry.get("author"),
                        entry.get("published"),
                    )
                    if a:
                        articles.append(a)
            self.status["rss"] = f"ok ({len(articles)})"
        except Exception as e:
            self.status["rss"] = f"error: {e}"
        return articles

    # ------------------------------------------------------------------ #
    # Exa  (mcporter -> mcp.exa.ai, keyless/free)
    # ------------------------------------------------------------------ #
    async def _fetch_exa(self) -> List[NewsArticle]:
        try:
            out = await self._run_cli(
                ["mcporter", "call",
                 f'exa.web_search_exa(query: "{self.query}", numResults: {self.max_per_source})',
                 "--output", "json"],
                timeout=90,
            )
            data = json.loads(out)
            items: List[dict] = []
            for block in data.get("content", []):
                items.extend(self._parse_exa_block(block.get("text", "")))
            articles = self._to_articles(self, items, "Exa")
            self.status["exa"] = f"ok ({len(articles)})"
            return articles
        except FileNotFoundError as e:
            self.status["exa"] = f"skipped: {e} (run: mcporter config add exa https://mcp.exa.ai/mcp)"
        except Exception as e:
            self.status["exa"] = f"skipped: {e}"
        return []

    @staticmethod
    def _parse_exa_block(text: str) -> List[dict]:
        items: List[dict] = []
        cur: dict = {}
        for line in text.splitlines():
            m = re.match(r"^(Title|URL|Published|Author):\s*(.*)$", line)
            if m:
                key = m.group(1).lower()
                val = m.group(2).strip()
                if key == "url":
                    if cur:
                        items.append(cur)
                    cur = {}
                cur[key] = val
        if cur:
            items.append(cur)
        return items

    # ------------------------------------------------------------------ #
    # YouTube  (yt-dlp, keyless)
    # ------------------------------------------------------------------ #
    async def _fetch_youtube(self) -> List[NewsArticle]:
        try:
            out = await self._run_cli(
                ["yt-dlp", "--dump-json", "--no-warnings",
                 f"ytsearch{self.max_per_source}:{self.query}"],
                timeout=90,
            )
            articles: List[NewsArticle] = []
            for line in out.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                url = d.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={d['id']}" if d.get("id") else None
                )
                a = self._make_article(
                    url, d.get("title"), d.get("description"),
                    "YouTube", d.get("uploader"),
                    d.get("upload_date") or d.get("timestamp"),
                )
                if a:
                    articles.append(a)
            self.status["youtube"] = f"ok ({len(articles)})"
            return articles
        except FileNotFoundError as e:
            self.status["youtube"] = f"skipped: {e} (run: pip install yt-dlp)"
        except Exception as e:
            self.status["youtube"] = f"skipped: {e}"
        return []

    # ------------------------------------------------------------------ #
    # X / Twitter  (twitter-cli, needs auth)
    # ------------------------------------------------------------------ #
    async def _fetch_x(self) -> List[NewsArticle]:
        try:
            out = await self._run_cli(
                ["twitter", "search", self.query, "-n", str(self.max_per_source), "--json"],
                timeout=60,
            )
            articles = self._to_articles(self, self._parse_twitter_json(out), "X")
            if articles:
                self.status["x"] = f"ok ({len(articles)})"
                return articles
            raise RuntimeError("no parseable tweets")
        except FileNotFoundError as e:
            self.status["x"] = f"skipped: twitter-cli not found ({e})"
        except Exception as e:
            self.status["x"] = (
                "skipped: X/Twitter needs auth - set TWITTER_AUTH_TOKEN + "
                "TWITTER_CT0 (Cookie-Editor export from x.com), or install the "
                "OpenCLI Chrome extension and open Chrome logged into X. "
                f"Last error: {str(e)[:160]}"
            )
        return []

    @staticmethod
    def _parse_twitter_json(out: str) -> List[dict]:
        try:
            data = json.loads(out)
        except Exception:
            return []
        if isinstance(data, dict):
            items = data.get("data") or data.get("tweets") or data.get("results") or []
        elif isinstance(data, list):
            items = data
        else:
            return []
        result: List[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            text = it.get("text") or it.get("full_text") or it.get("content")
            twid = it.get("id")
            author_obj = it.get("author") or it.get("user") or {}
            if isinstance(author_obj, dict):
                screen = author_obj.get("screenName") or author_obj.get("username") or author_obj.get("screen_name")
                name = author_obj.get("name")
            else:
                screen = name = None
            if twid and screen:
                url = f"https://x.com/{screen}/status/{twid}"
            else:
                url = it.get("url") or it.get("permalink")
            dt = it.get("createdAtISO") or it.get("createdAt") or it.get("timestamp")
            result.append({"url": url, "title": text, "summary": None,
                          "author": screen or name, "published": dt, "category": None})
        return result

    # ------------------------------------------------------------------ #
    # Reddit  (rdt-cli, needs auth)
    # ------------------------------------------------------------------ #
    async def _fetch_reddit(self) -> List[NewsArticle]:
        try:
            out = await self._run_cli(
                ["rdt", "search", self.query, "--limit", str(self.max_per_source), "--json"],
                timeout=60,
            )
            articles = self._to_articles(self, self._parse_reddit_json(out), "Reddit")
            if articles:
                self.status["reddit"] = f"ok ({len(articles)})"
                return articles
            raise RuntimeError("no parseable posts")
        except FileNotFoundError as e:
            self.status["reddit"] = f"skipped: rdt-cli not found ({e})"
        except Exception as e:
            self.status["reddit"] = (
                "skipped: Reddit needs auth - run `rdt login` (browser), or use "
                "OpenCLI with Chrome logged into reddit.com. "
                f"Last error: {str(e)[:160]}"
            )
        return []

    @staticmethod
    def _parse_reddit_json(out: str) -> List[dict]:
        try:
            data = json.loads(out)
        except Exception:
            return []
        # rdt wraps results as {"ok": true, "data": {"kind": "Listing",
        # "data": {"children": [{"kind": "t3", "data": {...}}]}}} — the
        # "children" list is nested several "data" levels deep, so walk down
        # until we find it.
        def _find_children(node, depth=0):
            if depth > 8 or not isinstance(node, dict):
                return None
            ch = node.get("children")
            if isinstance(ch, list):
                return ch
            return _find_children(node.get("data"), depth + 1)

        children: list = []
        if isinstance(data, list):
            children = data
        elif isinstance(data, dict):
            found = _find_children(data)
            if found is not None:
                children = found
            else:
                children = data.get("posts") or data.get("results") or []
        # Unwrap the {kind, data} post objects.
        posts: list = []
        for it in children:
            if not isinstance(it, dict):
                continue
            node = it.get("data") if "data" in it and isinstance(it.get("data"), dict) else it
            # Don't descend into a nested Listing.
            if isinstance(node, dict) and isinstance(node.get("children"), list):
                continue
            posts.append(node)
        result: List[dict] = []
        for p in posts:
            if not isinstance(p, dict):
                continue
            url = p.get("permalink")
            if isinstance(url, str) and url.startswith("/"):
                url = "https://reddit.com" + url
            url = url or p.get("url")
            dt = p.get("created_utc") or p.get("created") or p.get("createdAt")
            result.append({
                "url": url, "title": p.get("title"), "summary": p.get("selftext"),
                "author": p.get("author"), "published": dt, "category": p.get("subreddit"),
            })
        return result

    # ------------------------------------------------------------------ #
    # LinkedIn  (linkedin-scraper MCP via mcporter, needs session)
    # ------------------------------------------------------------------ #
    async def _fetch_linkedin(self) -> List[NewsArticle]:
        try:
            out = await self._run_cli(
                ["mcporter", "call",
                 f'linkedin-scraper.search_jobs(keyword: "{self.query}", limit: {self.max_per_source})',
                 "--output", "json"],
                timeout=60,
            )
            data = json.loads(out)
            items: List[dict] = []
            for block in data.get("content", []):
                items.extend(self._parse_linkedin_text(block.get("text", "")))
            articles = self._to_articles(self, items, "LinkedIn")
            if articles:
                self.status["linkedin"] = f"ok ({len(articles)})"
                return articles
            raise RuntimeError("no parseable LinkedIn results")
        except FileNotFoundError as e:
            self.status["linkedin"] = f"skipped: linkedin-scraper MCP not found ({e})"
        except Exception as e:
            self.status["linkedin"] = (
                "skipped: LinkedIn MCP not configured/authenticated - register it with "
                "`mcporter config add linkedin-scraper <server-url>` and provide a "
                f"LinkedIn session. Last error: {str(e)[:160]}"
            )
        return []

    @staticmethod
    def _parse_linkedin_text(text: str) -> List[dict]:
        try:
            j = json.loads(text)
            items = j if isinstance(j, list) else [j]
        except Exception:
            return []
        result: List[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            result.append({
                "url": it.get("url") or it.get("link") or it.get("jobUrl"),
                "title": it.get("title") or it.get("name") or it.get("headline"),
                "summary": it.get("description") or it.get("snippet"),
                "author": it.get("author") or it.get("company") or it.get("poster"),
                "published": it.get("published") or it.get("date") or it.get("postedAt"),
                "category": None,
            })
        return result
