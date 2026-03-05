# Telegram News Summarizer (GHCR :latest)

One report per day.

- Reads public channel posts using `TELEGRAM_USER_SESSION_STRING` (2nd Telegram account).
- Ranks only highest-priority news with LLM.
- Sends concise morning report via bot token to your main account/chat.
- Keeps local DB for 1 day only.

## Auto GHCR publish on push

This repo now includes [`.github/workflows/ghcr.yml`](.github/workflows/ghcr.yml).

Behavior:
- On every push to `main`, GitHub Actions builds and pushes:
  - `ghcr.io/foxy1402/telegram-news-summarizer:latest`
  - `ghcr.io/foxy1402/telegram-news-summarizer:sha-<shortsha>`
- Multi-arch is published: `linux/amd64` and `linux/arm64`.

You do not need to run `docker push` manually after that.

If you saw only `:sha-...` in deploy logs, that usually means your service is pinned to a digest/tag from a previous deploy. Set image explicitly to `:latest` and redeploy.

## Deploy using latest image

Use this image in your platform/service:
- `ghcr.io/foxy1402/telegram-news-summarizer:latest`

Minimal compose example:

```yaml
services:
  news-bot:
    image: ghcr.io/foxy1402/telegram-news-summarizer:latest
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./news_data:/news_data
```

## Generate TELEGRAM_USER_SESSION_STRING (Python 3.14-safe)

Run this command in terminal:

```bash
python -c "import asyncio; asyncio.set_event_loop(asyncio.new_event_loop()); from pyrogram import Client; api_id=int(input('TELEGRAM_API_ID: ').strip()); api_hash=input('TELEGRAM_API_HASH: ').strip(); app=Client('session_maker', api_id=api_id, api_hash=api_hash); app.start(); print('\nTELEGRAM_USER_SESSION_STRING=' + app.export_session_string()); app.stop()"
```

After it prints your session string, remove local temporary session files if created:
- `session_maker.session`
- `session_maker.session-journal`

## Required env vars

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_USER_SESSION_STRING=
TELEGRAM_BOT_TOKEN=
TARGET_CHAT_ID=
CHANNEL_USERNAMES=cnn,bbcnews,reuters
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
REPORT_LANGUAGE=English
SUMMARY_SEND_TIME_UTC=00:10
DATA_DIR=/news_data
```

## Summary size controls

```env
SUMMARY_MIN_ITEMS=5
SUMMARY_MAX_ITEMS_IN_REPORT=10
SUMMARY_CATEGORY_COUNT=3
SUMMARY_ITEM_WORD_LIMIT=35
SUMMARY_MAX_ITEMS=80
SUMMARY_MAX_CHARS_PER_ITEM=700
RETENTION_DAYS=1
```

## Mixed-language channels

Yes, channels can be mixed language (English, Vietnamese, etc.).
Use:
- `REPORT_LANGUAGE=English` for English output
- `REPORT_LANGUAGE=Vietnamese` for Vietnamese output

The bot will ingest mixed-language source posts and ask LLM to output the final report in your chosen report language.

## Where you read the report

You read it on your main account in:
- DM with your bot (if `TARGET_CHAT_ID` is your user chat id), or
- your chosen group (if `TARGET_CHAT_ID` is that group id).
