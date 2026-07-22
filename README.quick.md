# JARVIS — Quick Start

Voice-activated AI news assistant. Say "JARVIS" to wake it.

## Requirements

- Python ≥ 3.14, ~500 MB RAM, microphone
- **Optional**: API keys for [OpenRouter](https://openrouter.ai/keys) (AI summaries), X/Twitter, Exa, Reddit CLI

## Setup

```bash
git clone <repo> && cd news-agent
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install yt-dlp                               # YouTube source (optional)
cp .env.example .env                             # Fill in your keys
```

Models (Vosk, Kokoro) download automatically on first run.

## Run

```bash
python main.py
```

Say **"JARVIS"** to start a session. Say **"stop"** or **"go to sleep"** to end one.

## Voice Commands

| Say | Result |
|-----|--------|
| "good morning" | Full briefing |
| "tech news" / "ai news" / "world news" | Category news |
| "tell me more" | Deep-dive current story |
| "next" / "previous" | Navigate stories |
| "first story" / "third one" | Jump to story |
| "repeat" | Repeat last response |
| "remember I like AI news" | Save preference |
| "read only 3 stories" | Change count |
| "turn AI summaries off" | Toggle enrichment |
| "what do you know about me?" | Recall preferences |
| "forget my preferences" | Reset all |
| "stop" / "go to sleep" | End session |

## News Sources

- **RSS** — works out of the box (BBC, CNN, Reuters)
- **YouTube** — needs `pip install yt-dlp`
- **Exa** — needs `npx mcporter config add exa https://mcp.exa.ai/mcp`
- **X (Twitter)** — set `TWITTER_AUTH_TOKEN` + `TWITTER_CT0` in `.env`
- **Reddit** — needs `rdt login` (browser auth)

## Troubleshooting

- **Mic not found**: Check `python -c "import sounddevice; print(sounddevice.query_devices())"`
- **Wake word not detected**: Speak clearly, reduce background noise
- **Windows Unicode**: Already handled — app forces UTF-8 output
- **Model download fails**: Run once while online; fully offline afterwards
- **Ctrl+C** exits gracefully (metrics printed on shutdown)

---

*Full documentation: [README.md](README.md)*
