# Telegram News Summarizer Bot (Dockerized)

This project now uses a **userbot session** to read channel news, and a **bot token** only for sending your daily summary.

## Important Design Choice

- Reading news channels: done by `TELEGRAM_USER_SESSION_STRING` (normal Telegram user account session)
- Sending summary report: done by `TELEGRAM_BOT_TOKEN` to `TARGET_CHAT_ID`

Why: bot tokens often cannot read channels you do not own. A user session behaves like a normal Telegram account and can read public `@username` channels.

## Which Telegram account should be used?

Use a **second Telegram account** (not your main daily account) for userbot ingestion.

Recommended setup:
- Account A (second account): used only to create `TELEGRAM_USER_SESSION_STRING` and read public channels
- Account B (your main account): receives summary report via bot chat or group

## Where will I read the summary report?

Read it in your **main account** via the bot delivery target:
- If `TARGET_CHAT_ID` is your private chat ID, you read it in your DM with the bot.
- If `TARGET_CHAT_ID` is a group ID, you read it in that group.

So delivery is from **bot token chat**, not from the second account directly.

## Retention Policy

News DB retention is now **1 day**.

- `RETENTION_DAYS=1`
- After daily summary is sent, older rows are deleted automatically.

## 1) Prerequisites

- Docker + Docker Compose
- Telegram `api_id` + `api_hash` from https://my.telegram.org
- One second Telegram account for ingestion session
- One bot token from @BotFather for summary delivery
- OpenAI-compatible API (`/chat/completions`)

## 2) Create Telegram Credentials

### 2.1 API ID and API Hash

1. Open https://my.telegram.org
2. Log in
3. Open `API development tools`
4. Create app if needed
5. Save `api_id` and `api_hash`

These are used for both user session and bot sender client.

### 2.2 Create bot token (for report delivery only)

1. Open Telegram and chat with `@BotFather`
2. Run `/newbot`
3. Copy token into `TELEGRAM_BOT_TOKEN`
4. In your main account, open the bot chat and press `Start`

### 2.3 Create userbot session string (second account)

You need a session string for `TELEGRAM_USER_SESSION_STRING`.

Use this one-time local script (run outside container):

```python
from pyrogram import Client

api_id = 123456
api_hash = "your_api_hash"

with Client("session_maker", api_id=api_id, api_hash=api_hash) as app:
    print(app.export_session_string())
```

Flow:
- It asks phone number of your second account
- Enter Telegram OTP code
- If enabled, enter cloud password
- It prints a long session string

Copy that string into `.env` as `TELEGRAM_USER_SESSION_STRING`.

## 3) Configure Environment Variables

Copy template:

```bash
cp .env.example .env
```

Set required vars:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_USER_SESSION_STRING`
- `TELEGRAM_BOT_TOKEN`
- `TARGET_CHAT_ID`
- `CHANNEL_USERNAMES`
- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Optional tuning:

- `SUMMARY_SEND_TIME_UTC` default `00:10`
- `SUMMARY_MIN_ITEMS` default `5`
- `SUMMARY_MAX_ITEMS_IN_REPORT` default `10`
- `SUMMARY_CATEGORY_COUNT` default `3`
- `SUMMARY_ITEM_WORD_LIMIT` default `35`
- `SUMMARY_MAX_ITEMS` default `80`
- `SUMMARY_MAX_CHARS_PER_ITEM` default `700`
- `RETENTION_DAYS` default `1`
- `DATA_DIR` default `/news_data`

## 4) Persistent Storage and Mount Path

`DATA_DIR` must exactly match your container mount path.

Examples:
- Mount path is `/news_data` -> set `DATA_DIR=/news_data`
- Mount path is `/data` -> set `DATA_DIR=/data`
- Mount path is `/var/lib/telegram-news` -> set `DATA_DIR=/var/lib/telegram-news`

If this is mismatched, SQLite/session files will not persist between restarts.

## 5) Local Run with Docker Compose

1. Fill `.env`
2. Start:

```bash
docker compose up -d --build
```

3. Logs:

```bash
docker compose logs -f news-bot
```

4. Stop:

```bash
docker compose down
```

## 6) Deploy from GHCR

### Build and push

```bash
echo $CR_PAT | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
docker build -t ghcr.io/YOUR_GITHUB_USER/telegram-news-summarizer:latest .
docker push ghcr.io/YOUR_GITHUB_USER/telegram-news-summarizer:latest
```

### Deploy service

```yaml
services:
  news-bot:
    image: ghcr.io/YOUR_GITHUB_USER/telegram-news-summarizer:latest
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - /your/persistent/host/path:/your/container/mount/path
```

Then set:

```env
DATA_DIR=/your/container/mount/path
```

## 7) Runtime Behavior

- Startup:
  - connect user session and sender bot
  - resolve channels from `CHANNEL_USERNAMES`
  - backfill about 1 day of recent posts
- Live:
  - store new channel posts continuously
- Daily schedule:
  - summarize yesterday UTC posts
  - rank highest-priority items only
  - compress into 2-4 categories (target 3)
  - keep final report to about 5-10 items total
  - send summary to `TARGET_CHAT_ID`
  - cleanup data older than 1 day

## 8) Troubleshooting

- No channel data:
  - verify `TELEGRAM_USER_SESSION_STRING` is valid
  - verify channel usernames are correct public channels
- Bot cannot send report:
  - ensure your main account clicked `Start` in bot chat
  - verify `TARGET_CHAT_ID`
- No persistence:
  - verify mount exists and `DATA_DIR` matches mount path

## 9) Security

- Never commit `.env`
- Treat `TELEGRAM_USER_SESSION_STRING` as highly sensitive
- Rotate API keys and bot token if leaked

## 10) Minimal `.env` Example

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_USER_SESSION_STRING=1BQANOTAREALSTRING...
TELEGRAM_BOT_TOKEN=1234567890:AA...
TARGET_CHAT_ID=123456789
CHANNEL_USERNAMES=cnn,bbcnews,reuters

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

SUMMARY_SEND_TIME_UTC=00:10
SUMMARY_MIN_ITEMS=5
SUMMARY_MAX_ITEMS_IN_REPORT=10
SUMMARY_CATEGORY_COUNT=3
SUMMARY_ITEM_WORD_LIMIT=35
SUMMARY_MAX_ITEMS=80
SUMMARY_MAX_CHARS_PER_ITEM=700
RETENTION_DAYS=1
DATA_DIR=/news_data
LOG_LEVEL=INFO
```
