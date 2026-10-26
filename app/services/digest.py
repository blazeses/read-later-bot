import asyncio
import html
from datetime import datetime, timezone
from aiogram import Bot
from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logger import log
from app.db.models import Bookmark, User
from app.db.session import async_session_factory


class DigestScheduler:
    def __init__(self, bot: Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self):
        if not settings.DIGEST_ENABLED or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        log.info("Weekly digest scheduler started.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _scheduler_loop(self):
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                # Check if current weekday and hour match configuration
                if now.weekday() == settings.DIGEST_DAY_OF_WEEK and now.hour == settings.DIGEST_HOUR:
                    await self._send_digests()
                    # Sleep for 1 hour so it doesn't trigger repeatedly within the hour
                    await asyncio.sleep(3600)
                else:
                    # Check every 10 minutes
                    await asyncio.sleep(600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(f"Digest scheduler loop error: {e}")
                await asyncio.sleep(600)

    async def _send_digests(self):
        log.info("Starting weekly digest distribution...")
        async with async_session_factory() as session:
            stmt = select(User).where(User.digest_enabled.is_(True))
            res = await session.execute(stmt)
            users = list(res.scalars().all())

            for user in users:
                try:
                    # Select up to 3 unread bookmarks
                    b_stmt = (
                        select(Bookmark)
                        .options(selectinload(Bookmark.tags))
                        .where(
                            and_(
                                Bookmark.user_id == user.telegram_id,
                                Bookmark.is_read.is_(False),
                            )
                        )
                        .order_by(Bookmark.created_at.asc())
                        .limit(3)
                    )
                    b_res = await session.execute(b_stmt)
                    bookmarks = list(b_res.scalars().all())

                    if not bookmarks:
                        continue

                    message_lines = [
                        "☕️ <b>Твой еженедельный дайджест для чтения</b>\n",
                        "Вот интересные материалы из твоего списка, которые ждут тебя:\n",
                    ]

                    for idx, b in enumerate(bookmarks, start=1):
                        safe_title = html.escape(b.title or "Без названия")
                        if b.url:
                            safe_url = html.escape(b.url, quote=True)
                            link = f"<a href='{safe_url}'>{safe_title}</a>"
                        else:
                            link = f"<b>{safe_title}</b>"

                        tags_str = " ".join([f"#{html.escape(t.name)}" for t in b.tags]) if b.tags else ""
                        time_part = f"~{b.reading_time_min} мин." if b.reading_time_min else "время не указано"
                        meta_parts = [f"⏱ {time_part}"]
                        if tags_str:
                            meta_parts.append(tags_str)

                        summary_part = ""
                        if b.summary:
                            clean_summary = html.escape(b.summary[:150])
                            summary_part = f"   💡 {clean_summary}...\n"

                        message_lines.append(
                            f"{idx}. {link}\n"
                            f"   {' | '.join(meta_parts)}\n"
                            + summary_part
                        )

                    message_lines.append("\n<i>Управлять дайджестом можно в настройках (/help).</i>")
                    full_text = "\n".join(message_lines)

                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=full_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(0.1)  # Telegram rate limit compliance
                except Exception as e:
                    log.warning(f"Failed to send digest to user {user.telegram_id}: {e}")
