import asyncio
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

MODE_TOP_NEWS = "top_news"
MODE_OVERALL_SUMMARY = "overall_summary"
MODE_BOTH = "both"
VALID_MODES = {MODE_TOP_NEWS, MODE_OVERALL_SUMMARY, MODE_BOTH}


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@dataclass
class Settings:
    api_id: int
    api_hash: str
    user_session_string: str
    sender_bot_token: str
    target_chat_ids: List[int]
    mode_changer_id: Optional[int]
    channel_usernames: List[str]
    openai_base_url: str
    openai_api_key: str
    openai_model: str
    report_language: str
    summary_min_items: int
    summary_max_items_in_report: int
    summary_category_count: int
    summary_item_word_limit: int
    summary_max_items: int
    summary_max_chars_per_item: int
    overall_chunk_size: int
    overall_max_chars_per_item: int
    mode_both_delay_seconds: int
    backfill_per_channel_limit: int
    summary_send_time_utc: str
    retention_days: int
    llm_timeout_seconds: int
    llm_retry_base_seconds: int
    llm_retry_max_seconds: int
    llm_retry_max_attempts: int
    telegram_send_retry_base_seconds: int
    telegram_send_retry_max_seconds: int
    telegram_send_retry_max_attempts: int
    data_dir: Path

    @staticmethod
    def load() -> "Settings":
        channels_raw = env_required("CHANNEL_USERNAMES")
        channel_usernames = [c.strip() for c in channels_raw.split(",") if c.strip()]
        if not channel_usernames:
            raise RuntimeError("CHANNEL_USERNAMES must include at least one username")

        data_dir = Path(os.getenv("DATA_DIR", "/news_data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        target_chat_ids_raw = os.getenv("TARGET_CHAT_IDS", "").strip()
        if not target_chat_ids_raw:
            target_chat_ids_raw = env_required("TARGET_CHAT_ID")
        target_chat_ids = parse_int_list(target_chat_ids_raw, "TARGET_CHAT_IDS/TARGET_CHAT_ID")

        settings = Settings(
            api_id=int(env_required("TELEGRAM_API_ID")),
            api_hash=env_required("TELEGRAM_API_HASH"),
            user_session_string=env_required("TELEGRAM_USER_SESSION_STRING"),
            sender_bot_token=env_required("TELEGRAM_BOT_TOKEN"),
            target_chat_ids=target_chat_ids,
            mode_changer_id=parse_optional_int(os.getenv("MODE_CHANGER_ID", "").strip(), "MODE_CHANGER_ID"),
            channel_usernames=channel_usernames,
            openai_base_url=env_required("OPENAI_BASE_URL").rstrip("/"),
            openai_api_key=env_required("OPENAI_API_KEY"),
            openai_model=env_required("OPENAI_MODEL"),
            report_language=os.getenv("REPORT_LANGUAGE", "English").strip() or "English",
            summary_min_items=int(os.getenv("SUMMARY_MIN_ITEMS", "5")),
            summary_max_items_in_report=int(os.getenv("SUMMARY_MAX_ITEMS_IN_REPORT", "10")),
            summary_category_count=int(os.getenv("SUMMARY_CATEGORY_COUNT", "3")),
            summary_item_word_limit=int(os.getenv("SUMMARY_ITEM_WORD_LIMIT", "35")),
            summary_max_items=int(os.getenv("SUMMARY_MAX_ITEMS", "80")),
            summary_max_chars_per_item=int(os.getenv("SUMMARY_MAX_CHARS_PER_ITEM", "700")),
            overall_chunk_size=int(os.getenv("OVERALL_CHUNK_SIZE", "120")),
            overall_max_chars_per_item=int(os.getenv("OVERALL_MAX_CHARS_PER_ITEM", "500")),
            mode_both_delay_seconds=int(os.getenv("MODE_BOTH_DELAY_SECONDS", "60")),
            backfill_per_channel_limit=int(os.getenv("BACKFILL_PER_CHANNEL_LIMIT", "500")),
            summary_send_time_utc=os.getenv("SUMMARY_SEND_TIME_UTC", "00:10"),
            retention_days=int(os.getenv("RETENTION_DAYS", "1")),
            llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            llm_retry_base_seconds=int(os.getenv("LLM_RETRY_BASE_SECONDS", "2")),
            llm_retry_max_seconds=int(os.getenv("LLM_RETRY_MAX_SECONDS", "120")),
            llm_retry_max_attempts=int(os.getenv("LLM_RETRY_MAX_ATTEMPTS", "60")),
            telegram_send_retry_base_seconds=int(os.getenv("TELEGRAM_SEND_RETRY_BASE_SECONDS", "2")),
            telegram_send_retry_max_seconds=int(os.getenv("TELEGRAM_SEND_RETRY_MAX_SECONDS", "60")),
            telegram_send_retry_max_attempts=int(os.getenv("TELEGRAM_SEND_RETRY_MAX_ATTEMPTS", "20")),
            data_dir=data_dir,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.target_chat_ids:
            raise RuntimeError("TARGET_CHAT_IDS/TARGET_CHAT_ID must include at least one chat id")
        if self.summary_min_items < 1:
            raise RuntimeError("SUMMARY_MIN_ITEMS must be >= 1")
        if self.summary_max_items_in_report < 1:
            raise RuntimeError("SUMMARY_MAX_ITEMS_IN_REPORT must be >= 1")
        if self.summary_min_items > self.summary_max_items_in_report:
            raise RuntimeError("SUMMARY_MIN_ITEMS cannot be greater than SUMMARY_MAX_ITEMS_IN_REPORT")
        if self.summary_category_count < 1:
            raise RuntimeError("SUMMARY_CATEGORY_COUNT must be >= 1")
        if self.summary_item_word_limit < 1:
            raise RuntimeError("SUMMARY_ITEM_WORD_LIMIT must be >= 1")
        if self.summary_max_items < 1:
            raise RuntimeError("SUMMARY_MAX_ITEMS must be >= 1")
        if self.summary_max_chars_per_item < 1:
            raise RuntimeError("SUMMARY_MAX_CHARS_PER_ITEM must be >= 1")
        if self.overall_chunk_size < 1:
            raise RuntimeError("OVERALL_CHUNK_SIZE must be >= 1")
        if self.overall_max_chars_per_item < 1:
            raise RuntimeError("OVERALL_MAX_CHARS_PER_ITEM must be >= 1")
        if self.mode_both_delay_seconds < 0:
            raise RuntimeError("MODE_BOTH_DELAY_SECONDS must be >= 0")
        if self.backfill_per_channel_limit < 1:
            raise RuntimeError("BACKFILL_PER_CHANNEL_LIMIT must be >= 1")
        if self.retention_days < 1:
            raise RuntimeError("RETENTION_DAYS must be >= 1")
        if self.llm_timeout_seconds < 1:
            raise RuntimeError("LLM_TIMEOUT_SECONDS must be >= 1")
        if self.llm_retry_base_seconds < 1 or self.llm_retry_max_seconds < 1:
            raise RuntimeError("LLM retry seconds must be >= 1")
        if self.llm_retry_max_attempts < 1:
            raise RuntimeError("LLM_RETRY_MAX_ATTEMPTS must be >= 1")
        if self.telegram_send_retry_base_seconds < 1 or self.telegram_send_retry_max_seconds < 1:
            raise RuntimeError("TELEGRAM_SEND_RETRY_*_SECONDS must be >= 1")
        if self.telegram_send_retry_max_attempts < 1:
            raise RuntimeError("TELEGRAM_SEND_RETRY_MAX_ATTEMPTS must be >= 1")


class NewsStorage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_username TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    date_utc TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE(chat_id, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_date_utc
                ON messages(date_utc)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_message(
        self,
        channel_username: str,
        chat_id: int,
        message_id: int,
        dt_utc: datetime,
        text: str,
    ) -> None:
        if not text.strip():
            return

        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO messages (
                    channel_username, chat_id, message_id, date_utc, text, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_username,
                    chat_id,
                    message_id,
                    dt_utc.isoformat(),
                    text.strip(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def get_messages_for_day(self, day_utc: date, max_items: int) -> List[sqlite3.Row]:
        start = datetime(day_utc.year, day_utc.month, day_utc.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT channel_username, chat_id, message_id, date_utc, text
                FROM messages
                WHERE date_utc >= ? AND date_utc < ?
                ORDER BY date_utc ASC
                LIMIT ?
                """,
                (start.isoformat(), end.isoformat(), max_items),
            ).fetchall()
            return rows

    def get_messages_for_day_all(self, day_utc: date) -> List[sqlite3.Row]:
        start = datetime(day_utc.year, day_utc.month, day_utc.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT channel_username, chat_id, message_id, date_utc, text
                FROM messages
                WHERE date_utc >= ? AND date_utc < ?
                ORDER BY date_utc ASC
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            return rows

    def get_mode(self) -> str:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM bot_state WHERE key = 'mode'").fetchone()
            if not row:
                return MODE_TOP_NEWS
            value = str(row["value"]).strip().lower()
            return value if value in VALID_MODES else MODE_TOP_NEWS

    def set_mode(self, mode: str) -> None:
        mode = mode.strip().lower()
        if mode not in VALID_MODES:
            raise RuntimeError(f"Invalid mode: {mode}")
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO bot_state (key, value) VALUES ('mode', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (mode,),
            )
            conn.commit()

    def cleanup_old(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "DELETE FROM messages WHERE date_utc < ?",
                (cutoff.isoformat(),),
            )
            conn.commit()
            return cur.rowcount


def message_to_text(msg: Message) -> str:
    parts = []
    if msg.text:
        parts.append(msg.text)
    if msg.caption:
        parts.append(msg.caption)
    if msg.poll and msg.poll.question:
        parts.append(f"Poll: {msg.poll.question}")
    if msg.media:
        parts.append(f"[media={msg.media.value}]")
    return "\n".join(parts).strip()


class Summarizer:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _post_with_retry(self, payload: dict) -> dict:
        url = f"{self.settings.openai_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        attempt = 0
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=float(self.settings.llm_timeout_seconds)) as client:
                    resp = await client.post(url, headers=headers, json=payload)

                if resp.status_code == 429 or 500 <= resp.status_code <= 599:
                    raise httpx.HTTPStatusError(
                        f"Retryable status code: {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )

                resp.raise_for_status()
                return resp.json()
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
                status_code = None
                retry_after = None
                if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
                    status_code = e.response.status_code
                    if 400 <= status_code < 500 and status_code != 429:
                        raise
                    retry_after = e.response.headers.get("Retry-After")

                delay = min(
                    self.settings.llm_retry_max_seconds,
                    self.settings.llm_retry_base_seconds * (2 ** (attempt - 1)),
                )
                if retry_after and retry_after.isdigit():
                    delay = max(delay, int(retry_after))
                logging.warning(
                    "LLM call failed (attempt=%s, status=%s, error=%s). Retrying in %ss",
                    attempt,
                    status_code,
                    str(e),
                    delay,
                )
                if attempt >= self.settings.llm_retry_max_attempts:
                    raise RuntimeError(
                        f"LLM call failed after {attempt} attempts; last_status={status_code}"
                    ) from e
                await asyncio.sleep(delay)

    @staticmethod
    def _extract_content(data: dict) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("LLM response missing choices")

        message = choices[0].get("message", {})
        content = message.get("content", "")

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "".join(parts)

        raise RuntimeError("LLM response content format unsupported")

    def _build_top_news_prompt(self, rows: List[sqlite3.Row], day_utc: date) -> str:
        items = []
        for idx, row in enumerate(rows, start=1):
            text = re.sub(r"\s+", " ", row["text"]).strip()
            text = text[: self.settings.summary_max_chars_per_item]
            items.append(
                f"[{idx}] channel=@{row['channel_username']} "
                f"msg_id={row['message_id']} time={row['date_utc']} text={text}"
            )

        joined = "\n".join(items)

        return (
            "You are a news analyst.\n"
            "Given Telegram channel posts for one UTC day, rank by importance and create a concise summary.\n"
            "Output valid JSON only with this shape:\n"
            "{\n"
            "  \"headline\": \"string\",\n"
            "  \"quick_take\": \"string\",\n"
            "  \"categories\": [\n"
            "    {\n"
            "      \"name\": \"string\",\n"
            "      \"items\": [\n"
            "        {\"rank\": 1, \"source\": \"@channel\", \"why_important\": \"string\", \"summary\": \"string\"}\n"
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}\n"
            f"Select between {self.settings.summary_min_items} and {self.settings.summary_max_items_in_report} total items.\n"
            f"Use 2 to 4 categories (target: {self.settings.summary_category_count}).\n"
            f"Maximum {self.settings.summary_item_word_limit} words for each item summary.\n"
            "Each why_important must be one short sentence.\n"
            f"Write headline, quick_take, categories, and all item texts in {self.settings.report_language}.\n"
            "Focus on globally/materially important developments, avoid duplicates, and ignore low-signal chatter.\n"
            f"Day (UTC): {day_utc.isoformat()}\n"
            "Posts:\n"
            f"{joined}"
        )

    async def summarize_top_news(self, rows: List[sqlite3.Row], day_utc: date) -> str:
        if not rows:
            return (
                f"Daily News Summary for {day_utc.isoformat()} (UTC)\n\n"
                "No messages collected from configured channels for yesterday."
            )

        prompt = self._build_top_news_prompt(rows, day_utc)
        payload = {
            "model": self.settings.openai_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return strict JSON."},
                {"role": "user", "content": prompt},
            ],
        }
        data = await self._post_with_retry(payload)

        content = self._extract_content(data)
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S).strip()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logging.warning("LLM returned non-JSON output; sending raw text")
            return f"Daily News Summary for {day_utc.isoformat()} (UTC)\n\n{content}"

        headline = parsed.get("headline", f"Top News {day_utc.isoformat()}")
        quick_take = parsed.get("quick_take", "")
        categories = parsed.get("categories", [])

        lines = [f"Daily News Summary for {day_utc.isoformat()} (UTC)", "", f"{headline}"]
        if quick_take:
            lines.extend(["", f"Quick take: {quick_take}"])

        item_counter = 0
        lines.append("")
        for cat in categories:
            name = str(cat.get("name", "General")).strip() or "General"
            items = cat.get("items", [])
            if not isinstance(items, list) or not items:
                continue
            lines.append(f"[{name}]")
            for h in items:
                if item_counter >= self.settings.summary_max_items_in_report:
                    break
                source = h.get("source", "unknown")
                why = h.get("why_important", "")
                summary = h.get("summary", "")
                item_counter += 1
                lines.append(f"{item_counter}. {source}: {summary}")
                if why:
                    lines.append(f"   Why: {why}")
            lines.append("")
            if item_counter >= self.settings.summary_max_items_in_report:
                break

        return "\n".join(lines).strip()

    async def summarize_overall(self, rows: List[sqlite3.Row], day_utc: date) -> str:
        if not rows:
            return (
                f"Overall News Summary for {day_utc.isoformat()} (UTC)\n\n"
                "No messages collected from configured channels for yesterday."
            )

        chunk_summaries: List[str] = []
        chunk_size = self.settings.overall_chunk_size
        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            chunk_items = []
            for i, row in enumerate(chunk, start=1):
                text = re.sub(r"\s+", " ", row["text"]).strip()
                text = text[: self.settings.overall_max_chars_per_item]
                chunk_items.append(
                    f"[{i}] channel=@{row['channel_username']} time={row['date_utc']} text={text}"
                )
            chunk_prompt = (
                "Summarize this subset of one-day Telegram news posts.\n"
                f"Write in {self.settings.report_language}.\n"
                "Return concise plain text with:\n"
                "1) 3-6 key developments\n"
                "2) short market tone sentence\n"
                "3) major risks/watch items\n\n"
                "Posts:\n"
                + "\n".join(chunk_items)
            )
            payload = {
                "model": self.settings.openai_model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": "Be concise and factual."},
                    {"role": "user", "content": chunk_prompt},
                ],
            }
            data = await self._post_with_retry(payload)
            chunk_text = self._extract_content(data).strip()
            if chunk_text:
                chunk_summaries.append(chunk_text[:2000])

        final_prompt = (
            "You are preparing a morning digest.\n"
            f"Language: {self.settings.report_language}\n"
            f"Day (UTC): {day_utc.isoformat()}\n"
            "Given partial summaries from the full day, produce a concise overall summary.\n"
            "Format as plain text:\n"
            "- One headline line\n"
            "- One quick-take paragraph\n"
            "- 6-10 bullets of the most important developments\n"
            "- One short 'What to watch today' section (3 bullets)\n"
            "Avoid repetition.\n\n"
            "Partial summaries:\n"
            + "\n\n---\n\n".join(chunk_summaries)
        )
        final_payload = {
            "model": self.settings.openai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "Return concise plain text only."},
                {"role": "user", "content": final_prompt},
            ],
        }
        final_data = await self._post_with_retry(final_payload)
        final_text = self._extract_content(final_data).strip()
        return f"Overall News Summary for {day_utc.isoformat()} (UTC)\n\n{final_text}"


class NewsBot:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ingest_client = Client(
            name=str(settings.data_dir / "user_ingest"),
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            session_string=settings.user_session_string,
            workdir=str(settings.data_dir),
        )
        self.sender_client = Client(
            name=str(settings.data_dir / "bot_sender"),
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            bot_token=settings.sender_bot_token,
            workdir=str(settings.data_dir),
        )
        self.storage = NewsStorage(settings.data_dir / "news.sqlite3")
        self.summarizer = Summarizer(settings)
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self._channel_chat_ids = set()

    async def bootstrap_channels(self) -> None:
        for username in self.settings.channel_usernames:
            ch = username if username.startswith("@") else f"@{username}"
            try:
                chat = await self.ingest_client.get_chat(ch)
                self._channel_chat_ids.add(chat.id)
                logging.info("Tracking channel %s (id=%s)", ch, chat.id)
            except Exception:
                logging.exception("Failed to access channel %s via user session", ch)

    def _is_tracked(self, msg: Message) -> bool:
        return bool(msg.chat and msg.chat.id in self._channel_chat_ids)

    async def backfill_recent(self, days: int = 1) -> None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        per_channel_limit = self.settings.backfill_per_channel_limit
        for username in self.settings.channel_usernames:
            ch = username if username.startswith("@") else f"@{username}"
            count = 0
            try:
                async for msg in self.ingest_client.get_chat_history(ch, limit=per_channel_limit):
                    msg_time = msg.date.replace(tzinfo=timezone.utc)
                    if msg_time < since:
                        break
                    text = message_to_text(msg)
                    uname = msg.chat.username or ch.lstrip("@")
                    self.storage.save_message(uname, msg.chat.id, msg.id, msg_time, text)
                    count += 1
                    await asyncio.sleep(0)
                logging.info("Backfilled %s messages for %s", count, ch)
            except FloodWait as e:
                logging.warning("FloodWait during backfill for %s, sleeping %s sec", ch, e.value)
                await asyncio.sleep(e.value)
            except Exception:
                logging.exception("Failed backfill for %s", ch)

    async def on_new_message(self, _client: Client, msg: Message) -> None:
        if not self._is_tracked(msg):
            return
        text = message_to_text(msg)
        msg_time = msg.date.replace(tzinfo=timezone.utc)
        uname = msg.chat.username or str(msg.chat.id)
        self.storage.save_message(uname, msg.chat.id, msg.id, msg_time, text)

    async def send_daily_summary(self) -> None:
        day_utc = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        mode = self.storage.get_mode()
        send_error: Exception | None = None
        logging.info("Running daily summary for %s in mode=%s", day_utc, mode)

        # Backfill before summarising to catch any messages the live listener may
        # have missed (e.g. reconnects, restarts).  days=2 ensures we cover all of
        # yesterday from 00:00 UTC regardless of when the job fires.
        await self.backfill_recent(days=2)

        try:
            if mode in (MODE_TOP_NEWS, MODE_BOTH):
                top_rows = self.storage.get_messages_for_day(day_utc, self.settings.summary_max_items)
                try:
                    top_summary = await self.summarizer.summarize_top_news(top_rows, day_utc)
                except Exception:
                    logging.exception("Failed to create top_news summary for %s; using fallback", day_utc)
                    top_summary = self._fallback_summary(top_rows, day_utc, MODE_TOP_NEWS)
                top_failures = await self._send_report(top_summary, day_utc)
                if len(top_failures) == len(self.settings.target_chat_ids):
                    raise RuntimeError("top_news delivery failed for all recipients")

            if mode == MODE_BOTH and self.settings.mode_both_delay_seconds > 0:
                logging.info(
                    "Mode both: waiting %s seconds before overall_summary",
                    self.settings.mode_both_delay_seconds,
                )
                await asyncio.sleep(self.settings.mode_both_delay_seconds)

            if mode in (MODE_OVERALL_SUMMARY, MODE_BOTH):
                all_rows = self.storage.get_messages_for_day_all(day_utc)
                try:
                    overall_summary = await self.summarizer.summarize_overall(all_rows, day_utc)
                except Exception:
                    logging.exception("Failed to create overall_summary for %s; using fallback", day_utc)
                    overall_summary = self._fallback_summary(all_rows, day_utc, MODE_OVERALL_SUMMARY)
                overall_failures = await self._send_report(overall_summary, day_utc)
                if len(overall_failures) == len(self.settings.target_chat_ids):
                    raise RuntimeError("overall_summary delivery failed for all recipients")
        except Exception as e:
            send_error = e
            logging.exception("Daily summary run encountered send/generation error for %s", day_utc)

        deleted = self.storage.cleanup_old(self.settings.retention_days)
        logging.info("Summary run for %s finished. Cleanup removed %s old rows", day_utc, deleted)
        if send_error is not None:
            raise send_error

    async def _send_report(self, text: str, day_utc: date) -> List[int]:
        failed_chat_ids: List[int] = []
        for chat_id in self.settings.target_chat_ids:
            for chunk in split_telegram_text(text):
                try:
                    await self._send_chunk_with_retry(chat_id, chunk)
                except Exception:
                    logging.exception("Failed sending summary chunk for %s to chat_id=%s", day_utc, chat_id)
                    failed_chat_ids.append(chat_id)
                    break
        if failed_chat_ids:
            logging.warning("Report delivery partial failures for %s: %s", day_utc, failed_chat_ids)
        return failed_chat_ids

    async def _send_chunk_with_retry(self, chat_id: int, text: str) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                await self.sender_client.send_message(chat_id=chat_id, text=text)
                return
            except FloodWait as e:
                logging.warning("Telegram FloodWait while sending to %s. Sleeping %s sec", chat_id, e.value)
                await asyncio.sleep(e.value)
            except RPCError:
                # Most RPC errors are permanent (e.g., chat not found / bot blocked).
                raise
            except Exception as e:
                delay = min(
                    self.settings.telegram_send_retry_max_seconds,
                    self.settings.telegram_send_retry_base_seconds * (2 ** (attempt - 1)),
                )
                logging.warning(
                    "Telegram send failed (chat_id=%s, attempt=%s, error=%s). Retrying in %ss",
                    chat_id,
                    attempt,
                    str(e),
                    delay,
                )
                if attempt >= self.settings.telegram_send_retry_max_attempts:
                    raise RuntimeError(
                        f"Telegram send failed after {attempt} attempts"
                    ) from e
                await asyncio.sleep(delay)

    def _fallback_summary(self, rows: List[sqlite3.Row], day_utc: date, mode: str) -> str:
        prefix = "Overall News Summary" if mode == MODE_OVERALL_SUMMARY else "Daily News Summary"
        lines = [f"{prefix} for {day_utc.isoformat()} (UTC)", ""]
        if not rows:
            lines.append("No messages collected from configured channels for yesterday.")
            return "\n".join(lines)

        lines.append("LLM summary unavailable. Fallback: latest important-looking posts.")
        lines.append("")
        for idx, row in enumerate(rows[-self.settings.summary_min_items :], start=1):
            text = re.sub(r"\s+", " ", row["text"]).strip()
            text = text[:220]
            lines.append(f"{idx}. @{row['channel_username']}: {text}")
        return "\n".join(lines)

    async def run(self) -> None:
        @self.ingest_client.on_message()
        async def _handler(client: Client, msg: Message) -> None:
            await self.on_new_message(client, msg)

        @self.sender_client.on_message(filters.command("mode"))
        async def _mode_handler(_client: Client, msg: Message) -> None:
            await self.on_mode_command(msg)

        await self.ingest_client.start()
        await self.sender_client.start()

        await self.bootstrap_channels()
        await self.backfill_recent(days=1)

        hh, mm = parse_hh_mm(self.settings.summary_send_time_utc)
        self.scheduler.add_job(self.send_daily_summary, "cron", hour=hh, minute=mm)
        self.scheduler.start()
        current_mode = self.storage.get_mode()

        logging.info(
            "Bot started. Daily summary schedule: %s UTC. Tracking %s channels. Recipients=%s. Current mode=%s",
            self.settings.summary_send_time_utc,
            len(self._channel_chat_ids),
            len(self.settings.target_chat_ids),
            current_mode,
        )

        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        finally:
            await self.ingest_client.stop()
            await self.sender_client.stop()
            self.scheduler.shutdown(wait=False)

    async def on_mode_command(self, msg: Message) -> None:
        if not msg.text:
            return

        if self.settings.mode_changer_id is None:
            await msg.reply_text("MODE_CHANGER_ID is not configured. Mode change command is disabled.")
            return

        actor_id = msg.from_user.id if msg.from_user else None
        if actor_id != self.settings.mode_changer_id:
            await msg.reply_text("Unauthorized user for mode changes.")
            return

        tokens = msg.text.strip().split()
        current_mode = self.storage.get_mode()
        if len(tokens) == 1:
            await msg.reply_text(
                f"Current mode: {current_mode}\n"
                "Usage: /mode top_news | /mode overall_summary | /mode both"
            )
            return

        requested = tokens[1].strip().lower()
        if requested not in VALID_MODES:
            await msg.reply_text(
                "Invalid mode.\n"
                "Valid modes: top_news, overall_summary, both"
            )
            return

        self.storage.set_mode(requested)
        await msg.reply_text(
            f"Mode updated to: {requested}\n"
            "This will be applied on the next scheduled summary run."
        )


def parse_int_list(raw: str, name: str) -> List[int]:
    values = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as e:
            raise RuntimeError(f"{name} contains non-integer value: {item}") from e
    if not values:
        raise RuntimeError(f"{name} must include at least one integer")
    return values


def parse_optional_int(raw: str, name: str) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"{name} contains non-integer value: {raw}") from e


def parse_hh_mm(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{2}):(\d{2})", value.strip())
    if not m:
        raise RuntimeError("SUMMARY_SEND_TIME_UTC must be HH:MM")
    hh = int(m.group(1))
    mm = int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise RuntimeError("SUMMARY_SEND_TIME_UTC out of range")
    return hh, mm


def split_telegram_text(text: str, chunk_size: int = 3900) -> List[str]:
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            nl = text.rfind("\n", start, end)
            if nl > start + 400:
                end = nl
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


async def main() -> None:
    setup_logging()
    settings = Settings.load()
    bot = NewsBot(settings)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
