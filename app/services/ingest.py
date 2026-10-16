import asyncio
from dataclasses import dataclass
from typing import Optional
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

    def start(self, num_workers: int = settings.CONCURRENT_WORKERS):
        if self._running:
            return
        self._running = True
        for i in range(num_workers):
            task = asyncio.create_task(self._worker_loop(i))
            self.workers.append(task)
        log.info(f"Started {num_workers} background ingestion worker(s).")

    async def stop(self):
        self._running = False
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def enqueue(self, task: IngestTask):
        await self.queue.put(task)

    async def _worker_loop(self, worker_id: int):
        while self._running:
            try:
                task = await self.queue.get()
                await self._process_task(task)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(f"Unhandled error in ingestion worker {worker_id}: {e}")

    async def _process_task(self, task: IngestTask):
        async with async_session_factory() as session:
            try:
                user_repo = UserRepository(session)
                tag_repo = TagRepository(session)
                bookmark_repo = BookmarkRepository(session)

                # Ensure user exists
                await user_repo.get_or_create(task.user_id)

                # 1. URL Normalization and Deduplication check
                norm_url = None
                if task.content_type in (ContentType.ARTICLE, ContentType.TWEET, ContentType.YOUTUBE):
                    norm_url = normalize_url(task.source)
                    existing = await bookmark_repo.get_by_normalized_url(norm_url, task.user_id)
                    if existing:
                        from app.bot.card_renderer import send_card_message
                        from app.bot.keyboards import get_bookmark_card_keyboard
                        try:
                            await self.bot.delete_message(chat_id=task.chat_id, message_id=task.status_message_id)
                        except Exception:
                            pass
                        kb = get_bookmark_card_keyboard(existing.id, existing.url, existing.is_read)
                        await send_card_message(self.bot, task.chat_id, existing, kb)
                        return

                # 2. Extract content
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

                # 3. LLM Enrichment (Tags, Summary, Reading time) via DeepSeek/configured LLM
                existing_tags_objs = await tag_repo.get_user_tags(task.user_id)
                existing_tag_names = [t.name for t in existing_tags_objs]

                llm = get_llm_provider()
                enrichment = await llm.enrich_bookmark(
                    title=extracted.title or "Без названия",
                    text=extracted.text or task.source,
                    existing_tags=existing_tag_names,
                )

                # 4. Associate / Create Tags in DB
                tags = await tag_repo.get_or_create_tags(task.user_id, enrichment.tags)

                # 5. Create Bookmark in PostgreSQL
                import json
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

                # 6. Optional Chunking and Vector Embedding in Qdrant (only if external embeddings enabled)
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
                        log.warning(f"Embedding/Vector store upsert skipped: {emb_e}")

                # 7. Render presentation card (photo card or text) cleanly
                from app.bot.card_renderer import send_card_message
                from app.bot.keyboards import get_bookmark_card_keyboard

                photo_count = len(task.photo_ids) if task.photo_ids else (1 if bookmark.telegram_photo_id else 0)
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

            except Exception as e:
                log.exception(f"Error processing ingest task for user {task.user_id}: {e}")
                await self.bot.edit_message_text(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                    text=f"❌ Произошла ошибка при сохранении закладки: {str(e)[:150]}",
                )
