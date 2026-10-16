import asyncio
from dataclasses import dataclass
import html
import json
from typing import Dict, Optional, Set, Tuple
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import log
from app.db.models import ContentType
from app.db.repositories import (
    BookmarkRepository,
    TagRepository,
    UserRepository,
)
from app.db.session import async_session_factory
from app.extractors.base import ExtractedContent
from app.extractors.registry import extractor_registry
from app.extractors.url_utils import normalize_url
from app.providers.registry import (
    get_embedding_provider,
    get_llm_provider,
    get_vector_store,
)
from app.services.chunker import chunker
from app.services.embedding_service import EmbeddingService


@dataclass
class IngestTask:
    user_id: int
    chat_id: int
    status_message_id: int
    source: str
    content_type: ContentType
    user_comment: Optional[str] = None
    filename: Optional[str] = None
    telegram_photo_id: Optional[str] = None
    photo_ids: Optional[list[str]] = None
    author: Optional[str] = None


class IngestionQueueService:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.queue: asyncio.Queue[IngestTask] = asyncio.Queue()
        self.workers: list[asyncio.Task] = []
        self._running = False
        self._active_url_lock = asyncio.Lock()
        self._active_urls: Set[Tuple[int, str]] = set()
        self.failed_tasks: Dict[int, IngestTask] = {}

    def start(self, num_workers: int = settings.CONCURRENT_WORKERS):
        if self._running:
            return
        self._running = True
        for i in range(num_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self.workers.append(task)
        log.info(f"Запущено {num_workers} воркеров очереди инжеста.")

    async def stop(self):
        self._running = False
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def enqueue(self, task: IngestTask):
        await self.queue.put(task)

    async def _safe_edit_status(self, chat_id: int, message_id: int, text: str):
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass

    async def _worker_loop(self, worker_id: int):
        while self._running:
            try:
                task = await self.queue.get()
                await self._process_task(task)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(f"Необработанная ошибка в воркере {worker_id}: {e}")

    async def _process_task(self, task: IngestTask):
        norm_url = None
        if task.content_type in (ContentType.ARTICLE, ContentType.TWEET, ContentType.YOUTUBE):
            norm_url = normalize_url(task.source)
            # Защита от Race condition: блокировка одновременной обработки одинаковых URL одним юзером
            async with self._active_url_lock:
                key = (task.user_id, norm_url)
                if key in self._active_urls:
                    await self._safe_edit_status(
                        task.chat_id,
                        task.status_message_id,
                        "ℹ️ Этот материал уже обрабатывается и сохраняется...",
                    )
                    return
                self._active_urls.add(key)

        try:
            await self._run_ingest_pipeline(task, norm_url)
        finally:
            if norm_url:
                async with self._active_url_lock:
                    self._active_urls.discard((task.user_id, norm_url))

    async def _run_ingest_pipeline(self, task: IngestTask, norm_url: Optional[str]):
        async with async_session_factory() as session:
            try:
                user_repo = UserRepository(session)
                tag_repo = TagRepository(session)
                bookmark_repo = BookmarkRepository(session)

                await user_repo.get_or_create(task.user_id)

                # 1. Дедупликация в БД
                if norm_url:
                    existing = await bookmark_repo.get_by_normalized_url(norm_url, task.user_id)
                    if existing:
                        from app.bot.card_renderer import send_card_message
                        from app.bot.keyboards import get_bookmark_card_keyboard
                        try:
                            await self.bot.delete_message(chat_id=task.chat_id, message_id=task.status_message_id)
                        except Exception:
                            pass
                        photo_count = 1 if (existing.telegram_photo_id or existing.image_url) else 0
                        kb = get_bookmark_card_keyboard(existing.id, existing.url, existing.is_read, photo_count=photo_count)
                        await send_card_message(self.bot, task.chat_id, existing, kb)
                        return

                # Progress State 1: Скачивание
                await self._safe_edit_status(
                    task.chat_id, task.status_message_id,
                    "⏳ <b>[1/3] Скачиваю контент и очищаю от рекламы...</b>"
                )

                # 2. Экстракция
                extracted: ExtractedContent
                if task.content_type == ContentType.DOCUMENT:
                    extracted = await extractor_registry.extract_file(task.source, filename=task.filename)
                elif task.content_type == ContentType.NOTE:
                    extracted = extractor_registry.create_note_content(
                        task.source,
                        task.user_comment,
                        telegram_photo_id=task.telegram_photo_id,
                        author=task.author,
                    )
                else:
                    extracted = await extractor_registry.extract_url(task.source)
                    if task.telegram_photo_id:
                        extracted.telegram_photo_id = task.telegram_photo_id

                # Если парсер упал и текста нет: предлагаем сохранить как заметку
                if not extracted.success and not (extracted.text and len(extracted.text.strip()) > 10):
                    self.failed_tasks[task.status_message_id] = task
                    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                    kb_fail = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="📝 Сохранить как заметку", callback_data=f"save_raw_note:{task.status_message_id}")],
                            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data=f"retry_ingest:{task.status_message_id}")],
                        ]
                    )
                    err_detail = html.escape(extracted.error_message or "Сайт защищен или недоступен")
                    try:
                        await self.bot.edit_message_text(
                            chat_id=task.chat_id,
                            message_id=task.status_message_id,
                            text=(
                                f"⚠️ <b>Не удалось автоматически разобрать страницу:</b>\n"
                                f"<i>{err_detail}</i>\n\n"
                                "Вы можете сохранить ссылку как обычную текстовую заметку или попробовать снова:"
                            ),
                            reply_markup=kb_fail,
                            parse_mode="HTML",
                        )
                    except Exception:
                        await self.bot.edit_message_text(
                            chat_id=task.chat_id,
                            message_id=task.status_message_id,
                            text=(
                                f"⚠️ Не удалось автоматически разобрать страницу:\n"
                                f"{extracted.error_message or 'Сайт защищен или недоступен'}\n\n"
                                "Вы можете сохранить ссылку как обычную текстовую заметку или попробовать снова:"
                            ),
                            reply_markup=kb_fail,
                        )
                    return

                # Progress State 2: ИИ-анализ
                await self._safe_edit_status(
                    task.chat_id, task.status_message_id,
                    "🧠 <b>[2/3] DeepSeek анализирует материал и формирует саммари...</b>"
                )

                # 3. LLM Обогащение
                existing_tags_objs = await tag_repo.get_user_tags(task.user_id)
                existing_tag_names = [t.name for t in existing_tags_objs]

                llm = get_llm_provider()
                enrichment = await llm.enrich_bookmark(
                    title=extracted.title or "Без названия",
                    text=extracted.text or task.source,
                    existing_tags=existing_tag_names,
                )

                # Progress State 3: Сохранение
                await self._safe_edit_status(
                    task.chat_id, task.status_message_id,
                    "✨ <b>[3/3] Подбираю теги и формирую карточку...</b>"
                )

                # 4. Теги и Закладка в PostgreSQL
                tags = await tag_repo.get_or_create_tags(task.user_id, enrichment.tags)
                photos_json = json.dumps(task.photo_ids) if task.photo_ids else None

                bookmark = await bookmark_repo.create(
                    user_id=task.user_id,
                    content_type=extracted.content_type,
                    url=extracted.url,
                    normalized_url=norm_url,
                    title=extracted.title,
                    author=extracted.author or task.author,
                    domain=extracted.domain,
                    full_text=extracted.text or task.source,
                    summary=enrichment.summary,
                    reading_time_min=enrichment.reading_time_min,
                    user_comment=task.user_comment,
                    tags=tags,
                    image_url=extracted.image_url,
                    telegram_photo_id=extracted.telegram_photo_id or task.telegram_photo_id,
                    photos_json=photos_json,
                )
                await session.commit()

                # 5. Векторизация в Qdrant (если включен векторный провайдер)
                if settings.EMBEDDING_PROVIDER not in ("none", "disabled", "deepseek", "deepseek_search") and extracted.text.strip():
                    try:
                        text_chunks = chunker.chunk_text(extracted.text)
                        embedding_service = EmbeddingService(get_embedding_provider())
                        vector_chunks = await embedding_service.get_or_create_embeddings(text_chunks, session)
                        await session.commit()

                        vector_store = get_vector_store()
                        await vector_store.upsert_chunks(
                            user_id=task.user_id,
                            bookmark_id=bookmark.id,
                            chunks=vector_chunks,
                        )
                    except Exception as emb_e:
                        log.warning(f"Vector store upsert skipped: {emb_e}")

                # 6. Отправка итоговой карточки
                from app.bot.card_renderer import send_card_message
                from app.bot.keyboards import get_bookmark_card_keyboard

                photo_count = len(task.photo_ids) if task.photo_ids else (1 if (bookmark.telegram_photo_id or bookmark.image_url) else 0)
                kb = get_bookmark_card_keyboard(
                    bookmark.id,
                    url=bookmark.url,
                    is_read=bookmark.is_read,
                    photo_count=photo_count,
                )

                try:
                    await self.bot.delete_message(chat_id=task.chat_id, message_id=task.status_message_id)
                except Exception:
                    pass

                await send_card_message(self.bot, task.chat_id, bookmark, kb)
                self.failed_tasks.pop(task.status_message_id, None)

            except Exception as e:
                # Обязательный rollback сессии asyncpg при любом сбое
                await session.rollback()
                log.exception(f"Ошибка сохранения закладки для user {task.user_id}: {e}")
                
                self.failed_tasks[task.status_message_id] = task
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                retry_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data=f"retry_ingest:{task.status_message_id}")],
                        [InlineKeyboardButton(text="📝 Сохранить как заметку", callback_data=f"save_raw_note:{task.status_message_id}")],
                    ]
                )
                safe_err = html.escape(str(e)[:150])
                try:
                    await self.bot.edit_message_text(
                        chat_id=task.chat_id,
                        message_id=task.status_message_id,
                        text=f"❌ <b>Не удалось обработать материал:</b>\n{safe_err}\n\nПопробуйте повторить запрос или сохранить ссылку как заметку.",
                        reply_markup=retry_kb,
                        parse_mode="HTML",
                    )
                except Exception:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=task.chat_id,
                            message_id=task.status_message_id,
                            text=f"❌ Не удалось обработать материал:\n{str(e)[:150]}\n\nПопробуйте повторить запрос или сохранить ссылку как заметку.",
                            reply_markup=retry_kb,
                        )
                    except Exception:
                        pass
