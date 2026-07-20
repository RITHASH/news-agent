# News Agent

A local-first, voice-controlled AI news assistant. Say **"Jarvis"**, ask for the
latest technology / startup / world / sports news (or "tell me more about the
first story"), and it reads you a spoken briefing built from live sources —
no cloud TTS, no cloud STT, no keys for the core pipeline.

The news pipeline is **fully offline and deterministic**: it fetches from RSS
and several external sources, de-duplicates and ranks articles, and uses an
LLM (via OpenRouter) to enrich the top stories with a one-line summary, why it
matters, and possible impact. Speech synthesis (Kokoro), speech recognition
(Vosk), and the wake word are all run locally.

---

## Features

- **Wake-word activation** — listens for "Jarvis" locally with Vosk; no cloud,
  minimal CPU while idle.
- **Spoken morning briefing** — a time-aware, fully scripted briefing read aloud
  at startup, built entirely from live data (no hardcoded facts).
- **Multi-source fetching** — RSS (BBC/CNN/Reuters) plus Exa, YouTube, X/Twitter,
  Reddit, and LinkedIn. Every source degrades gracefully if its CLI/tool or auth
  is missing, so the run always completes.
- **Deterministic ranking** — de-duplication, keyword categorization, and a
  recency + source + signal scoring model. No LLM needed for the core pipeline.
- **LLM enrichment** — OpenRouter chat-completions add structured analysis to the
  most important articles (optional; the pipeline runs without a key).
- **Local neural TTS** — Kokoro ONNX model for natural speech, with a headless
  fallback.
- **Rule-based conversation router** — fast, predictable intent matching (no LLM
  in the hot path).
- **Conversation context & follow-ups** — within a session, JARVIS remembers the
  current category and the stories it just read, so you can say *"next"*,
  *"previous"*, *"first / second / third story"*, *"tell me more"*, *"repeat"*,
  or *"explain the first story"* without re-fetching.
- **Long-term user preferences** — tell JARVIS what you like and it remembers
  across restarts, persisted to a local JSON file (no cloud). Set preferred
  categories (*"remember I like AI news"*, *"put technology first"*), the number
  of stories (*"read only three stories"*), and whether to use AI summaries
  (*"turn AI summaries off"*); ask *"what do you remember about me?"*, or
  *"forget my preferences"* to reset.
- **Pluggable engines** — swap TTS / wake-word engines by dropping in one module
  and setting an env var.

---

## Architecture

```
main.py
  └─ NewsAgent (app/agent)            # orchestrator: wake-word <-> session loop
       ├─ WakeWordDetector (app/wakeword)   # Vosk "jarvis" listener
       ├─ SpeechRecognizer (app/voice/stt)  # Vosk STT (one utterance)
       ├─ ConversationManager (app/conversation)  # rule-based intent router
       ├─ Preferences (app/preferences)     # long-term user preferences (JSON)
       ├─ MorningBriefing (app/briefing)    # builds + speaks the briefing
       │    ├─ NewsFetcher (app/fetchers)   # concurrent multi-source fetch
       │    ├─ NewsProcessor (app/processors)  # dedupe / categorize / score
       │    └─ NewsSummarizer (app/agents)  # OpenRouter LLM enrichment
       └─ VoiceAgent (app/voice)            # speaks via a TTSEngine
            └─ KokoroTTS (app/voice/kokoro_tts)  # local neural TTS
```

**Lifecycle:** at startup the agent runs one morning briefing, then enters a
wake-word loop — `idle → (hear "jarvis") → session → (Stop / idle timeout) → idle`.
The microphone is idle while responses are spoken.

**Resilience:** each fetcher source and the summarizer report an `ok (N)` /
`skipped: <reason>` / `error: ...` status instead of throwing, so a missing CLI,
an unauthenticated source, or a throttled LLM never takes down the run.

---

## Tech Stack

| Concern            | Technology                                            |
|--------------------|-------------------------------------------------------|
| Language           | Python 3.14                                           |
| Data model         | Pydantic v2                                           |
| News parsing       | `feedparser`                                          |
| LLM enrichment     | `openai` client against OpenRouter (OpenAI-compatible)|
| Text-to-Speech     | `kokoro-onnx` (local neural TTS)                      |
| Speech / wake word | `vosk` + `sounddevice` (local, offline)               |
| External sources   | `mcporter` (Exa/LinkedIn MCP), `yt-dlp`, `twitter-cli`, `rdt-cli` |

---

## Installation

Requires **Python 3.14+**.

```bash
# 1. Clone
git clone https://github.com/<your-username>/news-agent.git
cd news-agent

# 2. Create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
python -m pip install -r requirements.txt
```

> The TTS/STT models (Kokoro ONNX ~300 MB, Vosk ~50 MB) download automatically
> into `~/.cache` on first use — no manual setup required.

---

## Environment Setup

Copy the example env file and fill in what you need:

```bash
cp .env.example .env
```

| Variable             | Required | Purpose                                                        |
|----------------------|----------|----------------------------------------------------------------|
| `OPENROUTER_API_KEY` | Optional | Enables LLM enrichment (one-line summary, why-it-matters, etc).|
| `MODEL_NAME`         | Optional | Override the OpenRouter model (default: `openai/gpt-oss-120b:free`). |
| `TWITTER_AUTH_TOKEN` | Optional | X/Twitter fetching (Cookie-Editor export from x.com).          |
| `TWITTER_CT0`        | Optional | X/Twitter `ct0` cookie, paired with the auth token.           |
| `TTS_ENGINE`         | Optional | TTS engine name (default: `kokoro`).                           |
| `WAKEWORD_ENGINE`    | Optional | Wake-word engine name (default: `vosk`).                       |

The pipeline works without any keys — you only lose LLM enrichment (and the X
source) if they're absent.

### Optional external CLIs

These unlock extra sources and are auto-detected; missing ones are skipped:

- **Exa / LinkedIn** — `npm i -g mcporter` (configured via `config/mcporter.json`)
- **YouTube** — `pip install yt-dlp`
- **X/Twitter** — a `twitter` CLI (e.g. the OpenCLI Chrome extension)
- **Reddit** — a `rdt` CLI (`rdt login` in a browser)

---

## Running the Project

```bash
# from the activated venv, in the project root
python main.py
```

You'll see a morning briefing printed and spoken, then:

```
[JARVIS] Listening for wake word...
```

Say **"Jarvis"**, then try:

- *"latest news"*, *"technology news"*, *"startup news"*, *"world news"*, *"sports news"*
- *"tell me more about the first story"* — or, after a briefing, just *"explain the first story"*
- *"next"*, *"previous"*, *"first story"*, *"third one"* — move through the stories already read
- *"tell me more"* — expand the story you're currently on
- *"repeat"* — hear the last thing JARVIS said again
- *"remember that I like AI news"*, *"read only three stories"*, *"turn AI summaries off"*
- *"what do you remember about me?"*, *"forget my preferences"*
- *"stop"* (ends the session, returns to wake-word listening)

---

## Configuration

Most behavior is configured through environment variables (see above) or the
`NewsAgent(...)` constructor in `main.py` (`query`, `top_n`, `max_per_source`,
`session_timeout`). Source lists and ranking weights live in
`app/fetchers/news_fetcher.py` and `app/processors/news_processor.py`.

---

## Folder Structure

```
news-agent/
├── main.py                  # Entry point: env load, UTF-8 streams, run NewsAgent
├── requirements.txt         # Pinned runtime dependencies
├── config/
│   └── mcporter.json        # MCP server config (Exa) for mcporter
├── app/
│   ├── agent/               # NewsAgent orchestrator
│   ├── agents/              # NewsSummarizer (LLM enrichment)
│   ├── briefing/            # MorningBriefing spoken script
│   ├── conversation/        # Rule-based intent router
│   ├── fetchers/            # NewsFetcher (multi-source, concurrent)
│   ├── models/              # NewsArticle data model
│   ├── processors/          # Dedupe / categorize / score
│   ├── preferences/         # Long-term user preferences (local JSON)
│   ├── voice/               # TTS engine, Kokoro, STT, VoiceAgent
│   ├── wakeword/            # Vosk wake-word detector
│   ├── api/                 # Reserved (future)
│   ├── config/              # Reserved (future)
│   ├── memory/              # Reserved (future)
│   └── utils/               # Reserved (future)
└── .env                     # Your secrets (git-ignored)
```

---

## Current Capabilities

- Local wake word ("Jarvis"), local STT, and local neural TTS.
- Concurrent fetch from 6 source types with graceful degradation.
- Deterministic dedup + categorization + importance scoring.
- Optional LLM enrichment of top stories via OpenRouter.
- Spoken morning briefing and on-demand category news.
- Session conversation context: next / previous / select-by-ordinal navigation,
  "tell me more" deep-dives, and "repeat" — all without re-fetching.
- Long-term user preferences: preferred categories, briefing order, stories-per-
  briefing, and AI-summary toggle, persisted locally and reloaded on start.
- Fully offline core pipeline (no keys required to run).

---

## Known Limitations

- **X / Reddit / LinkedIn** require external CLIs and authentication; they are
  skipped automatically when unavailable.
- **Microphone required** for wake word and voice queries; the briefing still
  prints to the console without audio.
- **First-run downloads** fetch Kokoro and Vosk models into `~/.cache`.
- **Windows-first** today (`os.startfile` audio fallback; UTF-8 stream hardening
  for `cp1252` consoles). Linux/macOS should work but are less tested.
- **Python 3.14** is the only tested runtime.

---

## Future Roadmap

- Implement the reserved packages: `app/api` (HTTP API), `app/memory`
  (persistent article store), `app/config` (settings loader), `app/utils`.
- Additional TTS and wake-word engines behind the existing factory interfaces.
- Richer "explain" follow-ups and a persistent article store.
- Cross-platform audio setup and a lightweight web UI.

---

## License

Released under the [MIT License](LICENSE).
