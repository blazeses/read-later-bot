import html
import json
import math
import uuid
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.card_renderer import send_card_message, update_card_message
from app.bot.keyboards import (
    get_bookmark_card_keyboard,
    get_bookmarks_list_keyboard,
    get_tags_keyboard,
)
from app.core.logger import log
from app.db.models import Bookmark
from app.db.repositories import BookmarkRepository, TagRepository

router = Router()

PAGE_SIZE = 5
_user_random_history: dict[int, list[uuid.UUID]] = {}


class TagEditState(StatesGroup):
    waiting_for_tags = State()


def get_photo_count(b: Bookmark) -> int:
    if b.photos_json:
        try:
            photos = json.loads(b.photos_json)
            if isinstance(photos, list):
                return len(photos)
        except Exception:
            pass
    return 1 if b.telegram_photo_id else 0


def render_bookmarks_list(
    bookmarks: list[Bookmark],
    current_page: int,
    total_count: int,
    limit: int = PAGE_SIZE,
    is_unread_only: bool = False,
) -> str:
    total_pages = max(1, math.ceil(total_count / limit))
    title_header = "📖 <b>Непрочитанные материалы</b>" if is_unread_only else "🕒 <b>Недавние материалы</b>"
    lines = [f"{title_header} (всего: {total_count}, стр. {current_page}/{total_pages}):\n"]

    for idx, b in enumerate(bookmarks, start=1):
        status_icon = "✅" if b.is_read else "📖"
        safe_title = html.escape((b.title or "Без названия").strip())
        domain_str = html.escape(b.domain or "заметка")
        tags_str = " ".join([f"#{html.escape(t.name)}" for t in b.tags[:3]]) if b.tags else ""
        tags_part = f" | {tags_str}" if tags_str else ""

        photos_note = ""
        if b.photos_json:
            try:
                p_list = json.loads(b.photos_json)
                if isinstance(p_list, list) and len(p_list) > 1:
                    photos_note = f" | 📷 {len(p_list)} фото"
            except Exception:
                pass

        lines.append(
            f"<b>[{idx}]</b> 📌 <b>{safe_title}</b>\n"
            f"    {status_icon} 🌐 {domain_str} | ⏱ ~{b.reading_time_min} мин.{photos_note}{tags_part}\n"
        )

    lines.append("👇 <b>Нажмите на кнопку с номером статьи ниже, чтобы открыть её:</b>")
    return "\n".join(lines)


# --- Recent Articles with Direct Selection ---

@router.message(Command("recent"))
@router.message(F.text.in_({"🕒 Недавние", "Недавние", "недавние"}))
async def cmd_recent(message: Message, session: AsyncSession):
    try:
        repo = BookmarkRepository(session)
        bookmarks, total = await repo.list_bookmarks(user_id=message.from_user.id, limit=PAGE_SIZE, offset=0)

        if not bookmarks:
            await message.answer(
                "📭 <b>У вас пока нет сохраненных закладок.</b>\n\n"
                "Отправьте мне ссылку, текст, фото или файл, чтобы сохранить первый материал!",
                parse_mode="HTML",
            )
            return

        total_pages = max(1, math.ceil(total / PAGE_SIZE))
        text = render_bookmarks_list(bookmarks, current_page=1, total_count=total, limit=PAGE_SIZE, is_unread_only=False)
        kb = get_bookmarks_list_keyboard(bookmarks, current_page=1, total_pages=total_pages, prefix="rl")
        await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        log.exception(f"Error in cmd_recent: {e}")
        await message.answer(f"⚠️ Не удалось загрузить недавние закладки. Ошибка: {str(e)[:150]}")


@router.callback_query(F.data.startswith("rl:") | F.data.startswith("recent_list:"))
async def cb_recent_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    offset = (page - 1) * PAGE_SIZE
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=callback.from_user.id, limit=PAGE_SIZE, offset=offset)

    if not bookmarks:
        await callback.answer("Закладок нет.", show_alert=True)
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    text = render_bookmarks_list(bookmarks, current_page=page, total_count=total, limit=PAGE_SIZE, is_unread_only=False)
    kb = get_bookmarks_list_keyboard(bookmarks, current_page=page, total_pages=total_pages, prefix="rl")

    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    else:
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith("op:") | F.data.startswith("open_bm:"))
async def cb_open_bookmark(callback: CallbackQuery, session: AsyncSession):
    try:
        # format: op:{id}:{prefix}:{page}
        parts = callback.data.split(":")
        bm_id = uuid.UUID(parts[1])
        nav_prefix = parts[2] if len(parts) > 2 else "rl"
        current_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

        repo = BookmarkRepository(session)
        bookmark = await repo.get_by_id(bm_id, callback.from_user.id)
        if not bookmark:
            await callback.answer("Закладка не найдена.", show_alert=True)
            return

        kb = get_bookmark_card_keyboard(
            bookmark.id,
            url=bookmark.url,
            is_read=bookmark.is_read,
            nav_prefix=nav_prefix,
            current_page=current_page,
            total_pages=1,
            photo_count=get_photo_count(bookmark),
        )
        await update_card_message(callback, bookmark, kb)
        await callback.answer()
    except Exception as e:
        log.exception(f"Error in cb_open_bookmark: {e}")
        await callback.answer(f"Не удалось открыть статью: {str(e)[:100]}", show_alert=True)


# --- Unread Articles with Direct Selection ---

@router.message(Command("unread"))
@router.message(F.text.in_({"📖 Непрочитанные", "Непрочитанные", "непрочитанные"}))
async def cmd_unread(message: Message, session: AsyncSession):
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=message.from_user.id, is_read=False, limit=PAGE_SIZE, offset=0)

    if not bookmarks:
        await message.answer("🎉 Отлично! У вас нет непрочитанных материалов.")
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    text = render_bookmarks_list(bookmarks, current_page=1, total_count=total, limit=PAGE_SIZE, is_unread_only=True)
    kb = get_bookmarks_list_keyboard(bookmarks, current_page=1, total_pages=total_pages, prefix="ul")
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data.startswith("ul:") | F.data.startswith("unread_list:"))
async def cb_unread_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    offset = (page - 1) * PAGE_SIZE
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=callback.from_user.id, is_read=False, limit=PAGE_SIZE, offset=offset)

    if not bookmarks:
        await callback.answer("Непрочитанных закладок нет.", show_alert=True)
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    text = render_bookmarks_list(bookmarks, current_page=page, total_count=total, limit=PAGE_SIZE, is_unread_only=True)
    kb = get_bookmarks_list_keyboard(bookmarks, current_page=page, total_pages=total_pages, prefix="unread_list")

    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    else:
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


# --- Smart Random Without Consecutive Repeats ---

@router.message(Command("random"))
@router.message(F.text.in_({"🎲 Случайная", "Случайная", "случайная"}))
async def cmd_random(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    history = _user_random_history.get(user_id, [])

    repo = BookmarkRepository(session)
    bookmark = await repo.get_random_unread(user_id, exclude_ids=history)

    if not bookmark:
        await message.answer("🎉 Непрочитанных закладок нет!")
        return

    history.append(bookmark.id)
    if len(history) > 20:
        history.pop(0)
    _user_random_history[user_id] = history

    kb = get_bookmark_card_keyboard(
        bookmark.id,
        bookmark.url,
        bookmark.is_read,
        nav_prefix="random",
        photo_count=get_photo_count(bookmark),
    )
    await send_card_message(message, message.chat.id, bookmark, kb)


@router.callback_query(F.data == "next_random")
async def cb_next_random(callback: CallbackQuery, session: AsyncSession):
    user_id = callback.from_user.id
    history = _user_random_history.get(user_id, [])

    repo = BookmarkRepository(session)
    bookmark = await repo.get_random_unread(user_id, exclude_ids=history)

    if not bookmark:
        await callback.answer("🎉 Непрочитанных закладок больше нет!", show_alert=True)
        return

    history.append(bookmark.id)
    if len(history) > 20:
        history.pop(0)
    _user_random_history[user_id] = history

    kb = get_bookmark_card_keyboard(
        bookmark.id,
        bookmark.url,
        bookmark.is_read,
        nav_prefix="random",
        photo_count=get_photo_count(bookmark),
    )
    await update_card_message(callback, bookmark, kb)
    await callback.answer()


# --- Tags Filter and Management ---

@router.message(Command("tags"))
@router.message(F.text.in_({"🏷 Теги", "Теги", "теги"}))
async def cmd_tags(message: Message, session: AsyncSession):
    tag_repo = TagRepository(session)
    popular = await tag_repo.get_popular_tags(message.from_user.id, limit=30)

    if not popular:
        await message.answer("У вас пока нет сохраненных тегов.")
        return

    kb = get_tags_keyboard(popular)
    await message.answer("🏷 <b>Ваши теги:</b>\nНажмите на тег, чтобы отфильтровать материалы:", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("filter_tag:"))
async def cb_filter_tag(callback: CallbackQuery, session: AsyncSession):
    # format: filter_tag:{name}:{page}
    parts = callback.data.split(":")
    tag_name = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    limit = 1
    offset = (page - 1) * limit

    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(
        user_id=callback.from_user.id,
        tag_name=tag_name,
        limit=limit,
        offset=offset,
    )

    if not bookmarks:
        await callback.answer(f"По тегу #{tag_name} ничего не найдено.", show_alert=True)
        return

    b = bookmarks[0]
    kb = get_bookmark_card_keyboard(
        b.id,
        url=b.url,
        is_read=b.is_read,
        nav_prefix="filter_tag",
        current_page=page,
        total_pages=total,
        extra_data=tag_name,
        photo_count=get_photo_count(b),
    )

    if "filter_tag:" in (callback.message.reply_markup.inline_keyboard[0][0].callback_data if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard and callback.message.reply_markup.inline_keyboard[0] else ""):
        await update_card_message(callback, b, kb, page_info=(page, total))
    else:
        await send_card_message(callback.message, callback.message.chat.id, b, kb, page_info=(page, total))

    await callback.answer()


@router.callback_query(F.data.startswith("edit_tags:"))
async def cb_edit_tags(callback: CallbackQuery, state: FSMContext):
    bookmark_id_str = callback.data.split(":")[1]
    await state.update_data(bookmark_id=bookmark_id_str)
    await state.set_state(TagEditState.waiting_for_tags)

    await callback.message.answer(
        "✏️ Отправьте новые теги через пробел или запятую (например: <code>python async архитектура</code>):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(TagEditState.waiting_for_tags))
async def process_new_tags(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    bookmark_id_str = data.get("bookmark_id")
    await state.clear()

    if not bookmark_id_str:
        await message.answer("Сессия редактирования устарела.")
        return

    bookmark_id = uuid.UUID(bookmark_id_str)
    raw_tags = message.text.replace(",", " ").split()
    cleaned = [t.strip().lstrip("#").lower() for t in raw_tags if t.strip()]

    tag_repo = TagRepository(session)
    bookmark_repo = BookmarkRepository(session)

    new_tags = await tag_repo.get_or_create_tags(message.from_user.id, cleaned)
    updated = await bookmark_repo.update_metadata(bookmark_id, message.from_user.id, tags=new_tags)
    await session.commit()

    if updated:
        tags_str = " ".join([f"#{t.name}" for t in new_tags])
        await message.answer(f"✅ Теги обновлены: {tags_str}")
    else:
        await message.answer("Не удалось обновить теги для этой закладки.")
