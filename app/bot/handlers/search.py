import html
import json
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.card_renderer import send_card_message, update_card_message
from app.bot.keyboards import get_bookmark_card_keyboard, get_main_reply_keyboard
from app.core.config import settings
from app.core.logger import log
from app.db.repositories import BookmarkRepository
from app.providers.registry import get_embedding_provider, get_vector_store
from app.services.search import HybridSearchService

router = Router()


class SearchState(StatesGroup):
    waiting_for_query = State()


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


async def execute_search(message: Message, query: str, session: AsyncSession):
    clean_q = query.strip()
    if not clean_q:
        await message.answer("Поисковый запрос не может быть пустым.")
        return

    # Check if user has any bookmarks at all
    repo = BookmarkRepository(session)
    user_bookmarks, total_bm = await repo.list_bookmarks(user_id=message.from_user.id, limit=1)
    if total_bm == 0:
        await message.answer(
            "📭 <b>У вас пока нет сохраненных материалов для поиска.</b>\n\n"
            "Сначала отправьте мне ссылку, текст или фото, чтобы сохранить первую статью!",
            reply_markup=get_main_reply_keyboard(),
            parse_mode="HTML",
        )
        return

    status_msg = await message.answer(f"🔎 <i>Ищу «{html.escape(clean_q)}»...</i>", parse_mode="HTML")

    search_service = get_search_service()
    results, total_count = await search_service.search(
        user_id=message.from_user.id,
        query=clean_q,
        session=session,
        limit=1,
        offset=0,
    )

    if not results:
        await status_msg.edit_text(
            f"🔍 По запросу <i>«{html.escape(clean_q)}»</i> ничего не найдено.\n\n"
            "💡 <b>Советы:</b>\n"
            "• Попробуйте ввести одно ключевое слово (например «питон», «docker», «новости»)\n"
            "• Или опишите суть статьи своими словами (DeepSeek поймет смысл)\n"
            "• Посмотрите список всех ваших материалов через «🕒 Недавние»",
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
        extra_data=clean_q[:30],
        photo_count=get_photo_count(b),
    )

    try:
        await status_msg.delete()
    except Exception:
        pass

    await send_card_message(message, message.chat.id, b, kb, page_info=(1, total_count))


@router.message(Command("cancel"))
async def cmd_cancel_search(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Поиск отменен.", reply_markup=get_main_reply_keyboard())


@router.message(F.text.in_({"🔍 Поиск", "Поиск"}))
async def btn_search(message: Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    text = (
        "🔍 <b>Поиск по сохраненным материалам:</b>\n\n"
        "Отправьте мне поисковый запрос (тему, ключевое слово, сайт или тег):\n"
        "• <i>микросервисы и docker</i>\n"
        "• <i>искусственный интеллект</i>\n"
        "• <i>#python</i>\n\n"
        "<i>Для отмены отправьте /cancel</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext, session: AsyncSession):
    parts = message.text.split(maxsplit=1)
    if len(parts) >= 2 and parts[1].strip():
        await state.clear()
        await execute_search(message, parts[1].strip(), session)
    else:
        await state.set_state(SearchState.waiting_for_query)
        await message.answer(
            "🔍 <b>Введите поисковый запрос:</b>\n"
            "Напишите тему, название, автора или тег (или /cancel для отмены):",
            parse_mode="HTML",
        )


@router.message(StateFilter(SearchState.waiting_for_query))
async def process_search_query_state(message: Message, state: FSMContext, session: AsyncSession):
    text = (message.text or "").strip()
    if text.startswith("/") or text in {
        "➕ Добавить статью", "Добавить статью",
        "🔍 Поиск", "Поиск",
        "🕒 Недавние", "Недавние", "недавние",
        "📖 Непрочитанные", "Непрочитанные",
        "🏷 Теги", "Теги",
        "🎲 Случайная", "Случайная",
        "📊 Статистика", "Статистика",
        "❓ Помощь", "Помощь",
        "🔽 Скрыть меню", "Скрыть меню",
    }:
        await state.clear()
        return

    await state.clear()
    await execute_search(message, text, session)


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
