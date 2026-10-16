import json
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.card_renderer import send_card_message, update_card_message
from app.bot.keyboards import get_bookmark_card_keyboard
from app.core.config import settings
from app.providers.registry import get_embedding_provider, get_vector_store
from app.services.search import HybridSearchService

router = Router()


def get_search_service() -> HybridSearchService:
    return HybridSearchService(
        vector_store=get_vector_store(),
        embedding_provider=get_embedding_provider(),
        k=settings.RRF_K,
    )


def get_photo_count(b) -> int:
    if b.photos_json:
        try:
            photos = json.loads(b.photos_json)
            if isinstance(photos, list):
                return len(photos)
        except Exception:
            pass
    return 1 if b.telegram_photo_id else 0


@router.message(Command("search"))
async def cmd_search(message: Message, session: AsyncSession):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "🔎 <b>Как использовать поиск:</b>\n"
            "Напишите команду и ваш запрос, например:\n"
            "<code>/search микросервисы и docker</code>\n"
            "<code>/search python async за последний месяц</code>",
            parse_mode="HTML",
        )
        return

    query = parts[1].strip()
    status_msg = await message.answer(f"🔎 <i>Ищу «{query}»...</i>", parse_mode="HTML")

    search_service = get_search_service()
    results, total_count = await search_service.search(
        user_id=message.from_user.id,
        query=query,
        session=session,
        limit=1,
        offset=0,
    )

    if not results:
        await status_msg.edit_text(
            f"🔍 По запросу <i>«{query}»</i> ничего не найдено.\n"
            "Попробуйте изменить формулировку запроса.",
            parse_mode="HTML",
        )
        return

    first = results[0]
    b = first.bookmark
    kb = get_bookmark_card_keyboard(
        b.id,
        url=b.url,
        is_read=b.is_read,
        nav_prefix="search_page",
        current_page=1,
        total_pages=total_count,
        extra_data=query[:25],
        photo_count=get_photo_count(b),
    )

    try:
        await status_msg.delete()
    except Exception:
        pass

    await send_card_message(message, message.chat.id, b, kb, page_info=(1, total_count))


@router.callback_query(F.data.startswith("search_page:"))
async def cb_search_page(callback: CallbackQuery, session: AsyncSession):
    # format: search_page:{page}:{query}
    parts = callback.data.split(":", 2)
    page = int(parts[1]) if parts[1].isdigit() else 1
    query = parts[2] if len(parts) > 2 else ""

    offset = page - 1
    search_service = get_search_service()
    results, total_count = await search_service.search(
        user_id=callback.from_user.id,
        query=query,
        session=session,
        limit=1,
        offset=offset,
    )

    if not results:
        await callback.answer("Больше результатов нет.", show_alert=True)
        return

    b = results[0].bookmark
    kb = get_bookmark_card_keyboard(
        b.id,
        url=b.url,
        is_read=b.is_read,
        nav_prefix="search_page",
        current_page=page,
        total_pages=total_count,
        extra_data=query,
        photo_count=get_photo_count(b),
    )
    await update_card_message(callback, b, kb, page_info=(page, total_count))
    await callback.answer()
