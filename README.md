# Telegram News Summarizer Bot (Dockerized)

A Dockerized Pyrogram bot that:
- reads Telegram channel posts from usernames in env vars
- stores raw messages in local SQLite storage
- keeps only the newest N days (default 7)
- once per day (UTC), summarizes **yesterday**
- ranks high-priority items via an OpenAI-compatible API
- sends summary to your Telegram chat via bot token

## Architecture (important)

- Ingestion client: Telegram **bot token** (`TELEGRAM_BOT_TOKEN`)
- Summarization model: OpenAI-compatible `/chat/completions`
- Storage: SQLite in `DATA_DIR/news.sqlite3`
- Session: Pyrogram session files in `DATA_DIR`

Because session + DB are in `DATA_DIR`, data survives restart only if `DATA_DIR` is mounted to persistent storage.

## Limitations you should know first

- Bot accounts can only read channels where the bot is actually allowed to read.
- For channels you do not control, bot ingestion is not guaranteed unless owners/admins add your bot.
- Daily summary is based on UTC day boundaries.

## Project Files

- `app/main.py`: bot logic
- `requirements.txt`: Python dependencies
- `Dockerfile`: image build
- `docker-compose.yml`: local runtime example
- `.env.example`: env template

## 1) Prerequisites

- Docker + Docker Compose
- Telegram API credentials (`api_id`, `api_hash`) from https://my.telegram.org
- Telegram bot token from @BotFather
- Target chat ID where summaries are sent
- OpenAI-compatible provider endpoint + API key + model name

## 2) Create Telegram Credentials

### 2.1 Telegram API ID / API Hash

1. Go to https://my.telegram.org
2. Log in with your phone number
3. Open `API development tools`
4. Create app (if needed)
5. Copy `api_id` and `api_hash`

### 2.2 Bot Token

1. Open Telegram
2. Chat with `@BotFather`
3. Use `/newbot`
4. Copy the token string into `TELEGRAM_BOT_TOKEN`

### 2.3 Target Chat ID

Use one of these quick methods:
- Add your bot to a private group, send one test message, then read updates via Telegram API.
- Or message your bot directly and use a helper bot/API call to inspect chat id.

`TARGET_CHAT_ID` examples:
- Personal chat: positive integer (example `123456789`)
- Group/supergroup: negative integer (example `-1001234567890`)

## 3) Configure Environment Variables

Copy `.env.example` to `.env` and fill every required field.

```bash
cp .env.example .env
```

### Required env vars

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_BOT_TOKEN`
- `TARGET_CHAT_ID`
- `CHANNEL_USERNAMES`
- `OPENAI_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

### Optional env vars (recommended defaults already set)

- `SUMMARY_SEND_TIME_UTC` default `00:10`
- `SUMMARY_TOP_K` default `12`
- `SUMMARY_MAX_ITEMS` default `80`
- `SUMMARY_MAX_CHARS_PER_ITEM` default `700`
- `RETENTION_DAYS` default `7`
- `DATA_DIR` default `/news_data`
- `LOG_LEVEL` default `INFO`

### Full env var reference

- `TELEGRAM_API_ID`: integer API id from my.telegram.org
- `TELEGRAM_API_HASH`: API hash from my.telegram.org
- `TELEGRAM_BOT_TOKEN`: BotFather token used for channel read + send
- `TARGET_CHAT_ID`: chat/group id where daily summary is delivered
- `CHANNEL_USERNAMES`: comma-separated usernames, example `cnn,bbcnews,reuters`
- `OPENAI_BASE_URL`: base URL ending in `/v1` for OpenAI-compatible API
- `OPENAI_API_KEY`: API key for the LLM provider
- `OPENAI_MODEL`: model id, example `gpt-4o-mini`
- `SUMMARY_SEND_TIME_UTC`: HH:MM UTC, runs daily summary for yesterday
- `SUMMARY_TOP_K`: number of ranked highlights in output
- `SUMMARY_MAX_ITEMS`: max stored messages passed into ranking each day
- `SUMMARY_MAX_CHARS_PER_ITEM`: truncate each message before prompt
- `RETENTION_DAYS`: keep latest N days, older rows auto-deleted
- `DATA_DIR`: absolute in-container folder for SQLite + session
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`

## 4) Persistent Storage and Mount Path

`DATA_DIR` must match your platform mount path.

Examples:
- If mount path is `/news_data`, set `DATA_DIR=/news_data`
- If mount path is `/var/lib/telegram-news`, set `DATA_DIR=/var/lib/telegram-news`

If mount path and `DATA_DIR` do not match, the bot appears to run but session/database may reset after restart.

## 5) Run Locally with Docker Compose

1. Ensure `.env` is configured
2. Start service:

```bash
docker compose up -d --build
```

3. View logs:

```bash
docker compose logs -f news-bot
```

4. Stop service:

```bash
docker compose down
```

Local compose currently mounts:
- host `./news_data` -> container `/news_data`

So set in `.env`:
- `DATA_DIR=/news_data`

## 6) Build and Push to GHCR

### 6.1 Login

```bash
echo $CR_PAT | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin
```

`CR_PAT` should have package write permission.

### 6.2 Build image

```bash
docker build -t ghcr.io/YOUR_GITHUB_USER/telegram-news-summarizer:latest .
```

### 6.3 Push image

```bash
docker push ghcr.io/YOUR_GITHUB_USER/telegram-news-summarizer:latest
```

### 6.4 Deploy image

Use this service template:

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

Then set in env:

```bash
DATA_DIR=/your/container/mount/path
```

## 7) Deploy on PaaS (generic checklist)

1. Create service from your GHCR image
2. Add all env vars from `.env.example`
3. Add a persistent disk/volume
4. Set mount path (example `/data`)
5. Set `DATA_DIR` to exactly that path (`/data`)
6. Deploy and check logs
7. Confirm first summary arrives at `TARGET_CHAT_ID` after schedule time

## 8) Runtime Behavior

- Startup:
  - creates/opens SQLite database
  - resolves channel usernames
  - backfills roughly last 1 day per channel
- Live:
  - stores new posts as they arrive
- Daily schedule:
  - loads yesterday (UTC) posts
  - asks LLM to rank and summarize top items
  - sends summary to target chat (auto-chunk if too long)
  - deletes rows older than `RETENTION_DAYS`

## 9) Troubleshooting

- No messages collected:
  - bot likely cannot read that channel
  - verify bot is added/allowed in channel
- 401/403 from LLM provider:
  - check `OPENAI_BASE_URL` and `OPENAI_API_KEY`
- No daily summary:
  - verify `SUMMARY_SEND_TIME_UTC` format (`HH:MM`)
  - check container timezone assumptions (scheduler uses UTC)
  - confirm `TARGET_CHAT_ID` is correct
- Data not persistent:
  - verify volume mount exists
  - verify `DATA_DIR` equals container mount path

## 10) Security Notes

- Never commit `.env`
- Rotate API keys/tokens if leaked
- Use least-privilege tokens where possible

## 11) Minimal Example `.env`

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=1234567890:AA...
TARGET_CHAT_ID=-1001234567890
CHANNEL_USERNAMES=cnn,bbcnews,reuters

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

SUMMARY_SEND_TIME_UTC=00:10
SUMMARY_TOP_K=12
SUMMARY_MAX_ITEMS=80
SUMMARY_MAX_CHARS_PER_ITEM=700
RETENTION_DAYS=7
DATA_DIR=/news_data
LOG_LEVEL=INFO
```
