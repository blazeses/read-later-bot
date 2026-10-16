import html
import json
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.card_renderer import send_card_message, update_card_message
from app.bot.keyboards import get_bookmark_card_keyboard, get_main_reply_keyboard
from app.core.config import settings
from app.core.logger import log
from app.db.models import Bookmark, ContentType
from app.db.repositories import BookmarkRepository
from app.providers.registry import get_embedding_provider, get_vector_store
from app.services.search import HybridSearchService

import hashlib

router = Router()

_search_tokens: dict[str, str] = {}


def get_search_token(query: str) -> str:
    """Store active search query and return an 8-char token to keep callback_data <= 64 bytes."""
    token = hashlib.md5(query.encode("utf-8")).hexdigest()[:8]
    _search_tokens[token] = query
    if len(_search_tokens) > 500:
        _search_tokens.pop(next(iter(_search_tokens)))
    return token


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
    return 1 if (b.telegram_photo_id or b.image_url or getattr(b, "telegram_video_id", None)) else 0


async def execute_search(message: Message, query: str, session: AsyncSession):
    clean_q = query.strip()
    if not clean_q:
        await message.answer("Поисковый запрос не может быть пустым.")
        return

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

    status_msg = await message.answer(f"🔎 <i>Ищу «{html.escape(clean_q)}» по смыслу и ключевым словам...</i>", parse_mode="HTML")

    try:
        search_service = get_search_service()
        results, total_count = await search_service.search(
            user_id=message.from_user.id,
            query=clean_q,
            session=session,
            limit=1,
            offset=0,
        )

        if not results:
            kb_empty = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🕒 Показать 5 недавних", callback_data="rl:1")],
                    [InlineKeyboardButton(text="📖 Только непрочитанные", callback_data="ul:1")],
                    [InlineKeyboardButton(text="🏷 Популярные теги", callback_data="open_tags_menu")],
                ]
            )
            await status_msg.edit_text(
                f"🔍 По запросу <i>«{html.escape(clean_q)}»</i> ничего не найдено.\n\n"
                "💡 <b>Советы для поиска:</b>\n"
                "• Напишите только ключевое слово (например: <code>табрис</code>, <code>docker</code>, <code>заметка</code>)\n"
                "• Опишите суть статьи своими словами — DeepSeek поймет контекст\n"
                "• Или выберите материалы из разделов ниже:",
                reply_markup=kb_empty,
                parse_mode="HTML",
            )
            return

        first = results[0]
        b = first.bookmark
        token = get_search_token(clean_q)
        kb = get_bookmark_card_keyboard(
            b.id,
            url=b.url,
            is_read=b.is_read,
            nav_prefix="sp",
            current_page=1,
            total_pages=total_count,
            extra_data=token,
            photo_count=get_photo_count(b),
            is_note=(b.content_type == ContentType.NOTE),
        )

        await send_card_message(message, message.chat.id, b, kb, page_info=(1, total_count))
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        log.exception(f"Error in execute_search for query '{query}': {e}")
        err_hint = f" ({html.escape(str(e)[:80])})" if str(e) else ""
        try:
            await status_msg.edit_text(
                f"⚠️ <b>Не удалось завершить поиск «{html.escape(clean_q)}»{err_hint}:</b>\n\n"
                "Попробуйте уточнить запрос или выберите материалы из разделов ниже:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="🕒 Показать недавние", callback_data="rl:1")],
                        [InlineKeyboardButton(text="📖 Непрочитанные", callback_data="ul:1")],
                    ]
                ),
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(
                f"⚠️ Ошибка поиска{err_hint}. Вы можете открыть «Недавние» через меню внизу.",
                parse_mode="HTML",
            )


@router.callback_query(F.data == "open_tags_menu")
async def cb_open_tags_menu(callback: CallbackQuery, session: AsyncSession):
    from app.bot.handlers.manage import cmd_tags
    await callback.answer()
    await cmd_tags(callback.message, session)


@router.message(Command("cancel"))
async def cmd_cancel_search(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Поиск отменен.", reply_markup=get_main_reply_keyboard())


@router.message(F.text.in_({"🔍 Поиск", "Поиск"}))
async def btn_search(message: Message, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    text = (
        "🔍 <b>Умный поиск по вашей базе знаний:</b>\n\n"
        "Напишите тему, ключевые слова, автора, сайт или тег:\n"
        "• <i>«статьи про маркетинг и продажи»</i>\n"
        "• <i>«архитектура микросервисов и docker»</i>\n"
        "• <i>«#python асинхронность»</i>\n"
        "• <i>«видео про космос с youtube»</i>\n\n"
        "<i>Отправьте /cancel для отмены поиска</i>"
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
            "Напишите тему, автора, ключевые слова или тег (или /cancel для отмены):",
            parse_mode="HTML",
        )


@router.message(StateFilter(SearchState.waiting_for_query))
async def process_search_query_state(message: Message, state: FSMContext, session: AsyncSession):
    text = (message.text or "").strip()
    if text.startswith("/") or text in {
        "➕ Загрузить заметку", "Загрузить заметку",
        "➕ Добавить заметку", "Добавить заметку",
        "➕ Добавить статью", "Добавить статью",
        "📥 Мои закладки", "Мои закладки",
        "🔍 Поиск", "Поиск",
        "🕒 Недавние", "Недавние", "недавние",
        "📖 Непрочитанные", "Непрочитанные",
        "🏷 Теги", "Теги",
        "🎲 Случайная", "Случайная",
        "📊 Дайджест", "Дайджест",
        "📊 Статистика", "Статистика",
        "⚙️ Настройки", "Настройки",
        "❓ Помощь", "Помощь",
        "🔽 Скрыть меню", "Скрыть меню",
    }:
        await state.clear()
        return

    await state.clear()
    await execute_search(message, text, session)


@router.callback_query(F.data.startswith("sp:") | F.data.startswith("search_page:"))
async def cb_search_page(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":", 2)
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    raw_val = parts[2] if len(parts) > 2 else ""
    query = _search_tokens.get(raw_val, raw_val)

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
    token = get_search_token(query) if query else ""
    kb = get_bookmark_card_keyboard(
        b.id,
        url=b.url,
        is_read=b.is_read,
        nav_prefix="sp",
        current_page=page,
        total_pages=total_count,
        extra_data=token,
        photo_count=get_photo_count(b),
        is_note=(b.content_type == ContentType.NOTE),
    )
    await update_card_message(callback, b, kb, page_info=(page, total_count))
    await callback.answer()
