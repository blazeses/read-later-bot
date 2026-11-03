import asyncio
import html
from datetime import datetime, timezone, timedelta
from typing import Optional
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.logger import log
from app.db.models import Bookmark, ContentType
from app.db.repositories import ReminderRepository
from app.db.session import async_session_factory


class ReminderScheduler:
    """
    Background service that checks for scheduled reminders every 30 seconds
    and delivers them via Telegram proactive messages.
    
    Delivery to Telegram servers does NOT depend on whether the user is currently online.
    Telegram server queues the message and pushes it as soon as the user opens the app.
    """

    def __init__(self, bot: Bot):
        self.bot = bot
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._check_interval = 30  # seconds

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        log.info("Reminder scheduler started.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        log.info("Reminder scheduler stopped.")

    async def _scheduler_loop(self):
        while self._running:
            try:
                await self._check_and_send_due_reminders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(f"Error in reminder scheduler loop: {e}")

            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break

    async def _check_and_send_due_reminders(self):
        now = datetime.now(timezone.utc)
        async with async_session_factory() as session:
            repo = ReminderRepository(session)
            due_reminders = await repo.get_due_reminders(now)

            if not due_reminders:
                return

            log.info(f"Found {len(due_reminders)} due reminder(s) to send.")

            for reminder in due_reminders:
                bookmark: Optional[Bookmark] = reminder.bookmark
                if not bookmark:
                    await repo.mark_as_sent(reminder.id)
                    await session.commit()
                    continue

                try:
                    await self._deliver_reminder(reminder.user_id, bookmark)
                    await repo.mark_as_sent(reminder.id)
                    await session.commit()
                    log.info(f"Delivered reminder {reminder.id} to user {reminder.user_id}")
                except TelegramRetryAfter as retry_err:
                    log.warning(f"Telegram flood limit hit: sleeping {retry_err.retry_after}s")
                    await asyncio.sleep(retry_err.retry_after + 1)
                    # Retry once after wait
                    try:
                        await self._deliver_reminder(reminder.user_id, bookmark)
                        await repo.mark_as_sent(reminder.id)
                        await session.commit()
                    except Exception as retry_fail:
                        log.error(f"Retry deliver failed for {reminder.id}: {retry_fail}")
                except TelegramForbiddenError as fb_err:
                    log.warning(f"User {reminder.user_id} blocked bot: {fb_err}. Marking reminder sent.")
                    await repo.mark_as_sent(reminder.id)
                    await session.commit()
                except Exception as send_err:
                    err_msg = str(send_err).lower()
                    log.warning(f"Failed to deliver reminder {reminder.id} to {reminder.user_id}: {send_err}")
                    # If user blocked bot or chat deleted, mark as sent to avoid spamming the log
                    if any(x in err_msg for x in ("blocked", "deactivated", "chat not found", "user is deactivated")):
                        await repo.mark_as_sent(reminder.id)
                        await session.commit()

    async def _deliver_reminder(self, user_id: int, bookmark: Bookmark):
        title = html.escape(bookmark.title or "Без названия")
        type_name = "статью"
        if bookmark.content_type == ContentType.NOTE:
            type_name = "заметку"
        elif bookmark.content_type == ContentType.YOUTUBE:
            type_name = "видео"
        elif bookmark.content_type == ContentType.DOCUMENT:
            type_name = "документ"

        preview_text = ""
        if bookmark.summary:
            snippet = bookmark.summary.strip()
            if len(snippet) > 250:
                snippet = snippet[:250] + "..."
            preview_text = f"\n\n<i>{html.escape(snippet)}</i>"
        elif bookmark.full_text and bookmark.content_type == ContentType.NOTE:
            snippet = bookmark.full_text.strip()
            if len(snippet) > 250:
                snippet = snippet[:250] + "..."
            preview_text = f"\n\n<i>{html.escape(snippet)}</i>"

        text = (
            f"⏰ <b>Напоминание о материале!</b>\n\n"
            f"Вы просили напомнить прочитать {type_name}:\n"
            f"📌 <b>{title}</b> (~{bookmark.reading_time_min} мин){preview_text}"
        )

        bm_id_str = str(bookmark.id)
        keyboard_buttons = [
            [
                InlineKeyboardButton(text="📖 Открыть материал", callback_data=f"open_bm:{bm_id_str}"),
            ],
            [
                InlineKeyboardButton(text="⏱ +15 мин", callback_data=f"snooze:{bm_id_str}:15m"),
                InlineKeyboardButton(text="⏳ +1 ч", callback_data=f"snooze:{bm_id_str}:1h"),
                InlineKeyboardButton(text="🌅 Завтра", callback_data=f"snooze:{bm_id_str}:tomorrow"),
            ],
            [
                InlineKeyboardButton(text="🗑 Завершить", callback_data=f"snooze:{bm_id_str}:dismiss"),
            ],
        ]

        if bookmark.url and bookmark.url.startswith("http"):
            keyboard_buttons[-1].append(
                InlineKeyboardButton(text="🌐 Оригинал", url=bookmark.url)
            )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await self.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
