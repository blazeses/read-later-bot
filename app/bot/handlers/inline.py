import hashlib
from aiogram import F, Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import BookmarkRepository
from app.services.search import HybridSearchService
from app.providers.registry import get_embedding_provider, get_vector_store
from app.core.config import settings

router = Router()


def get_search_service() -> HybridSearchService:
    return HybridSearchService(
        vector_store=get_vector_store(),
        embedding_provider=get_embedding_provider(),
        k=settings.RRF_K,
    )


@router.inline_query()
async def inline_search_handler(inline_query: InlineQuery, session: AsyncSession):
    user_id = inline_query.from_user.id
    query = inline_query.query.strip()
    repo = BookmarkRepository(session)

    results = []
    if not query:
        # Если запрос пустой, показываем последние 10 сохраненных материалов
        bookmarks, _ = await repo.list_bookmarks(user_id=user_id, limit=10, offset=0)
    else:
        # Полнотекстовый / семантический поиск по запросу
        search_service = get_search_service()
        search_res, _ = await search_service.search(
            user_id=user_id,
            query=query,
            session=session,
            limit=10,
            offset=0,
        )
        bookmarks = [item.bookmark for item in search_res]

    for b in bookmarks:
        tags_str = " ".join([f"#{t.name}" for t in b.tags]) if b.tags else ""
        desc = (b.summary or b.full_text or "Без описания")[:100].replace("\n", " ")
        
        # Формируем текст, который будет отправлен в чат
        message_text = (
            f"📌 <b>{b.title or 'Без названия'}</b>\n"
            f"🌐 {b.domain or 'заметка'} | ⏱ ~{b.reading_time_min} мин\n"
            f"{tags_str}\n\n"
            f"💡 <b>Выжимка:</b>\n{b.summary or 'Краткая выжимка отсутствует.'}\n\n"
        )
        if b.url and b.url.startswith("http"):
            message_text += f"🔗 <a href='{b.url}'>Открыть оригинал</a>"

        result_id = hashlib.md5(f"{b.id}_{user_id}".encode()).hexdigest()
        results.append(
            InlineQueryResultArticle(
                id=result_id,
                title=b.title or "Без названия",
                description=f"⏱ ~{b.reading_time_min} мин | {desc}",
                thumbnail_url=b.image_url if b.image_url else None,
                input_message_content=InputTextMessageContent(
                    message_text=message_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                ),
            )
        )

    await inline_query.answer(
        results=results,
        cache_time=10,
        is_personal=True,
        switch_pm_text="📥 Открыть свои закладки" if not results else None,
        switch_pm_parameter="open_bookmarks" if not results else None,
    )
