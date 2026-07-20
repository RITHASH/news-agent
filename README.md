# 📰 News Agent — "Jarvis"

> A local-first, voice-controlled AI news assistant. Say **"Jarvis"**, ask for
> the latest technology / startup / world / sports news, and it reads you a
> spoken briefing built from live sources — **no cloud TTS, no cloud STT, and no
> keys required for the core pipeline.**

News Agent ("Jarvis") fetches news from many sources at once, deduplicates and
ranks it, and — optionally — uses an LLM to enrich the top stories with a
one-line summary, *why it matters*, and *possible impact*. It then speaks the
briefing aloud using a **local neural TTS** engine, listens for your follow-up
questions, and remembers your preferences across restarts. Everything that can
be local, **is** local: speech synthesis (Kokoro), speech recognition and the
wake word (Vosk).

---

## ✨ What it does

- **Wake-word activation** — says nothing and uses almost no CPU until it hears
  *"Jarvis"* locally with Vosk.
- **Spoken morning briefing** — a time-aware, fully scripted briefing read aloud
  at startup, built entirely from live data (no hardcoded facts).
- **Multi-source fetching** — RSS (BBC / CNN / Reuters) plus Exa, YouTube,
  X/Twitter, Reddit, and LinkedIn. Every source **degrades gracefully** if its
  CLI or auth is missing, so the run always completes.
- **Deterministic ranking** — de-duplication, keyword categorization, and a
  recency + source + signal scoring model. No LLM needed for the core pipeline.
- **Optional LLM enrichment** — OpenRouter chat-completions add structured
  analysis to the most important articles.
- **Local neural TTS** — Kokoro ONNX model for natural speech, with a
  headless text fallback.
- **Conversation context & follow-ups** — within a session, Jarvis remembers the
  stories it just read, so you can navigate without re-fetching (see
  [Voice Commands](#-voice-commands)).
- **Long-term user preferences** — tell Jarvis what you like and it remembers
  across restarts (see [Preferences](#-preferences)).
- **Pluggable engines** — swap TTS / wake-word engines by dropping in one module
  and setting an environment variable.

---

## 🏗️ Architecture

```
main.py
  └─ NewsAgent (app/agent)            # orchestrator: wake-word <-> session loop
       ├─ WakeWordDetector (app/wakeword)    # Vosk "jarvis" listener
       ├─ SpeechRecognizer (app/voice/stt)   # Vosk STT (one utterance)
       ├─ ConversationManager (app/conversation)  # rule-based intent router
       ├─ Preferences (app/preferences)      # long-term user preferences (JSON)
       ├─ MorningBriefing (app/briefing)     # builds + speaks the briefing
       │    ├─ NewsFetcher (app/fetchers)    # concurrent multi-source fetch
       │    ├─ NewsProcessor (app/processors)  # dedupe / categorize / score
       │    └─ NewsSummarizer (app/agents)   # OpenRouter LLM enrichment
       └─ VoiceAgent (app/voice)             # speaks via a TTSEngine
            └─ KokoroTTS (app/voice/kokoro_tts)  # local neural TTS
```

**Pipeline:** `Fetch (concurrent)` → `Process (dedupe · categorize · score · sort)`
→ `Summarize (LLM, top‑N, optional)` → `Speak (streamed)`.

**Lifecycle (a small state machine):**

```
SLEEPING  ──(hear "jarvis")──▶  LISTENING  ──(speak)──▶  SPEAKING
   ▲                              │   ▲                      │
   └────(Stop / Sleep / idle)─────┘   └────(done speaking)───┘
```

While **SLEEPING**, the mic only waits for the wake word and ignores everything
else. While **LISTENING**, it accepts and routes commands. While **SPEAKING**,
the mic stays idle until it finishes. Conversation history is **never** stored
cross-session — the next wake starts with a blank slate.

**Resilience:** each fetcher source and the summarizer report an `ok (N)` /
`skipped: <reason>` / `error: ...` status instead of throwing, so a missing CLI,
an unauthenticated source, or a throttled LLM never takes down the run.

---

## 🧰 Tech Stack

| Concern             | Technology                                            |
|---------------------|-------------------------------------------------------|
| Language            | Python 3.14                                           |
| Data model          | Pydantic v2                                           |
| News parsing        | `feedparser`                                          |
| LLM enrichment      | `openai` client against OpenRouter (OpenAI-compatible)|
| Text-to-Speech      | `kokoro-onnx` (local neural TTS)                      |
| Speech / wake word  | `vosk` + `sounddevice` (local, offline)               |
| External sources    | `mcporter` (Exa/LinkedIn MCP), `yt-dlp`, `twitter-cli`, `rdt-cli` |

---

## 🚀 Installation

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

## 🔑 Environment Setup

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

**The pipeline works without any keys** — you only lose LLM enrichment (and the
X source) if they're absent.

### Optional external CLIs (auto-detected; missing ones are skipped)

- **Exa / LinkedIn** — `npm i -g mcporter` (configured via `config/mcporter.json`)
- **YouTube** — `pip install yt-dlp`
- **X/Twitter** — a `twitter` CLI (e.g. the OpenCLI Chrome extension)
- **Reddit** — a `rdt` CLI (`rdt login` in a browser)

---

## ▶️ Running

```bash
# from the activated venv, in the project root
python main.py
```

You'll see a morning briefing printed and spoken, then:

```
[JARVIS] Sleeping - waiting for wake word 'JARVIS'...
```

Say **"Jarvis"**, then try the commands below.

---

## 🎙️ Voice Commands

Once awake, you can speak naturally. Commands are matched by a fast,
deterministic rule engine — **no LLM in the hot path**.

### News & briefings

| Say…                                              | Does…                                              |
|---------------------------------------------------|----------------------------------------------------|
| *"latest news"* / *"technology news"* / *"startup news"* / *"world news"* / *"sports news"* | Fetches and reads that category (cache-backed). |
| *"good morning"* / *"briefing"*                   | Replays the full morning briefing.                 |
| *"tell me more about the first story"*            | Deep-dive on a specific story by name.             |
| *"explain the first story"*                       | Same, after a briefing is already in context.      |

### Conversation follow-ups (no re-fetch)

| Say…                          | Does…                                            |
|-------------------------------|--------------------------------------------------|
| *"next"* / *"skip"* / *"continue"* | Read the next story in the list.             |
| *"previous"* / *"back"*       | Read the previous story.                          |
| *"first story"* / *"second one"* / *"third story"* | Jump straight to that story.           |
| *"tell me more"*              | Expand the story you're currently on.             |
| *"repeat"*                    | Hear the last thing Jarvis said again.            |

At the start or end of the list, Jarvis simply tells you there are no more
stories — it never wraps around silently.

### Preferences

| Say…                                                    | Does…                                                  |
|---------------------------------------------------------|--------------------------------------------------------|
| *"remember that I like AI news"*                        | Adds a preferred category.                             |
| *"remember that I prefer technology first"*            | Adds a category and moves it to the front of briefings.|
| *"read only three stories"*                             | Sets how many stories to read at a time.              |
| *"turn AI summaries off"* / *"turn AI summaries on"*   | Toggles LLM enrichment.                               |
| *"what do you remember about me?"*                     | Recaps your saved preferences.                         |
| *"forget my preferences"* / *"reset my preferences"*   | Clears everything back to defaults.                   |

### Session control

| Say…                          | Does…                                            |
|-------------------------------|--------------------------------------------------|
| *"stop"* / *"goodbye"* / *"exit"* | Ends the session, returns to wake-word listening. |
| *"go to sleep"* / *"sleep"*   | Pauses voice commands until the next wake word.   |

---

## 👤 Preferences

Preferences are stored locally in **`config/user_preferences.json`** (git-ignored,
never committed) and reloaded on every start. They cover only four knobs:

- **Preferred categories** — e.g. AI, technology, sports (ordered; preferred
  categories are read first in the morning briefing, then the rest).
- **Stories per briefing** — how many stories to read (default **5**).
- **AI summaries** — whether LLM enrichment is on (default **on**).

The file is written atomically (temp file + replace) and is **never** used to
store conversation history or anything sensitive. If the file is missing,
corrupt, or contains invalid values, Jarvis falls back to safe defaults and
keeps running.

---

## ⚙️ Configuration

Most behavior is configured through environment variables (see above) or the
`NewsAgent(...)` constructor in `main.py`:

```python
NewsAgent(
    query="technology news",   # default category for the morning briefing
    top_n=5,                   # stories to read (overridden by saved prefs)
    max_per_source=5,          # articles pulled from each source
    session_timeout=25.0,      # seconds of silence before auto-sleep
    cache_ttl=300.0,           # seconds before a cached fetch is refreshed
)
```

Source lists and ranking weights live in `app/fetchers/news_fetcher.py` and
`app/processors/news_processor.py`.

---

## 📁 Folder Structure

```
news-agent/
├── main.py                  # Entry point: env load, UTF-8 streams, run NewsAgent
├── requirements.txt         # Pinned runtime dependencies
├── config/
│   ├── mcporter.json        # MCP server config (Exa) for mcporter
│   └── user_preferences.json  # Your preferences (created at runtime, git-ignored)
├── app/
│   ├── agent/               # NewsAgent orchestrator
│   ├── agents/              # NewsSummarizer (LLM enrichment)
│   ├── briefing/            # MorningBriefing spoken script
│   ├── conversation/        # Rule-based intent router
│   ├── fetchers/            # NewsFetcher (multi-source, concurrent)
│   ├── models/              # NewsArticle data model
│   ├── preferences/         # Long-term user preferences (local JSON)
│   ├── processors/          # Dedupe / categorize / score
│   ├── voice/               # TTS engine, Kokoro, STT, VoiceAgent
│   ├── wakeword/            # Vosk wake-word detector
│   ├── api/                 # Reserved (future)
│   ├── config/              # Reserved (future)
│   ├── memory/              # Reserved (future)
│   └── utils/               # Reserved (future)
└── .env                     # Your secrets (git-ignored)
```

---

## ✅ Current Capabilities

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

## ⚠️ Known Limitations

- **X / Reddit / LinkedIn** require external CLIs and authentication; they are
  skipped automatically when unavailable.
- **Microphone required** for wake word and voice queries; the briefing still
  prints to the console without audio.
- **First-run downloads** fetch Kokoro and Vosk models into `~/.cache`.
- **Windows-first** today (`os.startfile` audio fallback; UTF-8 stream hardening
  for `cp1252` consoles). Linux/macOS should work but are less tested.
- **Python 3.14** is the only tested runtime.

---

## 🗺️ Future Roadmap

- Implement the reserved packages: `app/api` (HTTP API), `app/memory`
  (persistent article store), `app/config` (settings loader), `app/utils`.
- Additional TTS and wake-word engines behind the existing factory interfaces.
- Richer "explain" follow-ups and a persistent article store.
- Cross-platform audio setup and a lightweight web UI.

---

## 📜 License

Released under the [MIT License](LICENSE).
