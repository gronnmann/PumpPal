# PumpPal

A personal AI workout coach that lives in Telegram. When you finish a workout in [Hevy](https://hevyapp.com), PumpPal gets notified via webhook, asks for any notes, then analyses your session, adjusts your routine for next time, and sends you a detailed coaching message — all grounded in a swappable knowledge base of training methodology.

---

## How it works

```
Hevy app
  │  POST /webhook/hevy
  ▼
PumpPal server
  ├── Sends Telegram: "Hey! I saw your workout Upper B (5 ex, 15 sets, 43 min).
  │                   Anything to add before I start analysing?"
  │
  ▼  (you reply with notes or "go")
PydanticAI agent
  ├── Reads coach_log.md        (past sessions, prior decisions)
  ├── Fetches full workout      (Hevy API)
  ├── Fetches recent history    (Hevy API)
  ├── Fetches current routines  (Hevy API)
  ├── Reads KB index + relevant files (kb_dir/)
  ├── Applies double-progression logic
  ├── Updates the routine       (Hevy API)
  ├── Writes session entry      (coach_log.md)
  └── Sends coaching message    (Telegram)

  ▼  (you ask follow-up questions)
Multi-turn chat (history scoped to this workout only)
```

On the next workout the chat history resets; the `coach_log.md` journal carries all long-term memory.

---

## Stack

| Component | Library |
|---|---|
| AI agent | [PydanticAI](https://ai.pydantic.dev) |
| LLM | OpenRouter (`anthropic/claude-sonnet-4-5` by default) |
| Telegram bot | [python-telegram-bot](https://python-telegram-bot.org) v20+ (polling) |
| Webhook server | [FastAPI](https://fastapi.tiangolo.com) + uvicorn |
| Hevy API client | httpx (async) |
| Config | pydantic-settings |
| Logging | loguru |
| Package manager | [uv](https://docs.astral.sh/uv/) |

---

## Setup

### 1. Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A [Hevy](https://hevyapp.com) account with an API key (Settings → Developer)
- A Telegram bot token from [@BotFather](https://t.me/botfather)
- An [OpenRouter](https://openrouter.ai) API key
- A public URL for the Hevy webhook (see [Local development](#local-development))

### 2. Install

```bash
git clone <repo>
cd PumpPal
uv sync
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# Telegram
TELEGRAM_BOT_TOKEN=7xxx:AAA...      # from @BotFather
TELEGRAM_CHAT_ID=123456789          # your personal chat ID — find it via @userinfobot

# Hevy
HEVY_API_KEY=a70d9df0-...           # Settings → Developer → API key
HEVY_WEBHOOK_SECRET=choose-a-secret # you pick this; paste it into Hevy's "Authorization header" field

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=anthropic/claude-sonnet-4.6   # optional, this is the default

# Optional
COACH_LOG_PATH=coach_log.md         # where the session journal is stored
KB_DIR=my_kb                        # path to your knowledge base folder
```

### 4. Register the Hevy webhook

In Hevy → Settings → Developer → Webhooks:

- **URL**: `https://your-domain.com/webhook/hevy`
- **Authorization header**: the value you put in `HEVY_WEBHOOK_SECRET`

### 5. Run

```bash
uv run pumppal
```

The server starts on `http://0.0.0.0:8080` and Telegram polling begins automatically.

---

## Local development

You need a public HTTPS URL to receive Hevy webhooks. [ngrok](https://ngrok.com) is the easiest option:

```bash
ngrok http 8080
```

Paste the `https://xxxx.ngrok-free.app` URL into Hevy's webhook config as `https://xxxx.ngrok-free.app/webhook/hevy`.

---

## Telegram commands

| Command | What it does |
|---|---|
| `/start` | Welcome message and command list |
| `/analyze` | Manually trigger analysis of your latest Hevy workout |
| `/status` | Show current conversation phase |
| `/clear` | Reset the current session (clears chat history, returns to IDLE) |

The bot **only responds to your configured `TELEGRAM_CHAT_ID`**. Messages from any other user are silently dropped and logged as a warning.

---

## Coach log (`coach_log.md`)

Every analysed workout gets a dated entry appended to `coach_log.md`. This is the agent's long-term memory — it reads the full log at the start of every analysis to recall past decisions, spot trends, and track injuries or issues.

Example entry:

```markdown
---

## 2026-04-16 -- f1085cdb-...

**Duration:** 43 min | **Sets:** 15 | **Exercises:** 5
**User notes:** Felt strong on bench, left shoulder a bit tight

**Performance:**
- DB Bench 30kg: 3x10 -- bumped to 32.5kg next session
- Shoulder Press 42.5kg: 3x10,9,8 -- still rebuilding post-cut
- Pull-ups: 5/4/3 -- enforce 2-min rest

**Routine changes:**
- Upper B: DB Bench -> 32.5kg
- Upper B: Pull-up rest_seconds -> 120

**Coach observations:**
- Left shoulder tightness noted -- monitor next upper session
- 2nd session back from cut; strength rebounding well
```

`coach_log.md` is gitignored by default (personal training data).

---

## Knowledge base

PumpPal is knowledge-base agnostic. Point `KB_DIR` at any folder of markdown files and the agent will use it. Bring your own training methodology.

### Structure

A KB folder must contain an `index.md` at its root. The agent always reads this file first to orient itself before fetching any other files.

```
my_kb/
├── index.md    ← required — describes the KB and how to use it
├── topic_a.md
├── topic_b.md
└── ...
```

`index.md` should cover:
- What this KB is and its core framework
- How the agent should reason with it (e.g. priority order, decision rules)
- Any equipment constraints or user-specific context
- A table mapping filenames to their contents so the agent knows what to fetch

### Swapping the KB

To use a different methodology:

1. Create a new folder with your markdown files and an `index.md`.
2. Set `KB_DIR=/path/to/your/kb` in `.env`.
3. Restart PumpPal — no code changes needed.

### Agent tools

| Tool | What it does |
|---|---|
| `read_kb_index` | Reads `index.md` — always called first for KB context |
| `list_kb_files` | Lists all `.md` files in the KB (excluding `index.md`) |
| `read_kb_file` | Reads a specific file by filename |

---

## Development

```bash
uv sync --extra dev   # installs ruff + mypy

uv run mypy src       # type checking (strict)
uv run ruff check src # linting
uv run ruff format src # formatting
```

---

## Project structure

```
PumpPal/
├── src/pumppal/
│   ├── main.py           # entrypoint — uvicorn + PTB polling via anyio
│   ├── settings.py       # pydantic-settings (reads .env)
│   ├── state.py          # in-memory conversation state machine
│   ├── agent.py          # PydanticAI agent + all tools
│   ├── bot.py            # Telegram handlers
│   ├── webhook.py        # FastAPI POST /webhook/hevy
│   ├── logging_setup.py  # loguru + stdlib intercept
│   └── hevy/
│       ├── client.py     # async Hevy API client
│       └── models.py     # Pydantic v2 models for Hevy responses
├── kb/                   # knowledge base folder (bring your own)
│   ├── index.md          # required — KB index read first by the agent
│   └── *.md              # your methodology files
├── coach_log.md          # auto-generated session journal (gitignored)
└── pyproject.toml
```
