import asyncio
import io
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.agent import NewsAgent

# Load .env (e.g. TWITTER_AUTH_TOKEN / TWITTER_CT0 for X auth, OPENROUTER_API_KEY
# for the summarizer) BEFORE anything prints, so subprocess CLIs and the OpenAI
# client inherit credentials.
load_dotenv(Path(__file__).parent / ".env")


def _install_utf8_streams() -> None:
    """Make ``sys.stdout``/``sys.stderr`` UTF-8 and crash-proof.

    - ``encoding="utf-8"``: correct rendering on a real console, valid bytes to
      a pipe/file.
    - ``errors="replace"``: any character that can't be represented is
      substituted instead of raising ``UnicodeEncodeError``.
    - ``line_buffering=True``: every newline flushes, so logs appear
      immediately and are not lost if the process later crashes.

    Falls back to re-wrapping the underlying binary buffer if
    ``reconfigure()`` is unavailable on the stream.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            continue
        except Exception:
            pass
        # Fallback: wrap the underlying binary buffer ourselves.
        try:
            raw = stream.buffer
        except Exception:
            try:
                raw = stream.detach()
            except Exception:
                continue
        try:
            setattr(
                sys,
                name,
                io.TextIOWrapper(raw, encoding="utf-8", errors="replace", line_buffering=True),
            )
        except Exception:
            pass


# Windows consoles default to cp1252; fetched posts are emoji-heavy and would
# raise UnicodeEncodeError on print(). Force UTF-8 with error-tolerant decoding
# so Unicode can NEVER crash the application (console, pipe, or file output).
_install_utf8_streams()


async def main():
    agent = NewsAgent()
    await agent.start()


if __name__ == "__main__":
    asyncio.run(main())
