import html
import json
import math
import os
import uuid
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.card_renderer import check_is_long_note, send_card_message, update_card_message
from app.bot.keyboards import (
    get_bookmark_card_keyboard,
    get_bookmarks_list_keyboard,
    get_page_picker_keyboard,
    get_tags_keyboard,
)
from app.core.config import settings
from app.core.logger import log
from app.db.models import Bookmark, ContentType
from app.db.repositories import BookmarkRepository, TagRepository
from app.extractors.url_utils import normalize_emoji_digits

router = Router()

PAGE_SIZE = 5
_user_random_history: dict[int, list[uuid.UUID]] = {}


class TagEditState(StatesGroup):
    waiting_for_tags = State()


class NoteEditState(StatesGroup):
    waiting_for_text = State()


def get_photo_count(b: Bookmark) -> int:
    if b.photos_json:
        try:
            photos = json.loads(b.photos_json)
            if isinstance(photos, list):
                return len(photos)
        except Exception:
            pass
    return 1 if (b.telegram_photo_id or getattr(b, "telegram_video_id", None)) else 0


def render_bookmarks_list(
    bookmarks: list[Bookmark],
    current_page: int,
    total_count: int,
    limit: int = PAGE_SIZE,
    is_unread_only: bool = False,
    is_pinned_only: bool = False,
    is_favorites_only: bool = False,
) -> str:
    total_pages = max(1, math.ceil(total_count / limit))
    if is_favorites_only:
        title_header = "⭐ <b>Избранные заметки и материалы</b>"
    elif is_pinned_only:
        title_header = "📌 <b>Закреплённые материалы</b>"
    elif is_unread_only:
        title_header = "📖 <b>Непрочитанные материалы</b>"
    else:
        title_header = "🕒 <b>Недавние материалы</b>"
    lines = [f"{title_header} (всего: {total_count}, стр. {current_page}/{total_pages}):\n"]

    for idx, b in enumerate(bookmarks, start=1):
        status_icon = "✅" if b.is_read else "📖"
        safe_title = html.escape((b.title or "Без названия").strip())
        domain_str = html.escape(b.domain or "заметка")
        tags_str = " ".join([f"#{html.escape(t.name)}" for t in b.tags[:3]]) if b.tags else ""
        tags_part = f" | {tags_str}" if tags_str else ""
        pin_badge = "📌 <b>[Закреплено]</b> " if getattr(b, "is_pinned", False) else ""

        photos_note = ""
        if b.photos_json:
            try:
                p_list = json.loads(b.photos_json)
                if isinstance(p_list, list) and len(p_list) > 1:
                    photos_note = f" | 📷 {len(p_list)} фото"
            except Exception:
                pass

        lines.append(
            f"<b>[{idx}]</b> {pin_badge}<b>{safe_title}</b>\n"
            f"    {status_icon} 🌐 {domain_str} | ⏱ ~{b.reading_time_min} мин.{photos_note}{tags_part}\n"
        )

    lines.append("👇 <b>Нажмите на кнопку с номером статьи ниже, чтобы открыть её:</b>")
    return "\n".join(lines)


# --- Recent Articles with Direct Selection ---

@router.message(Command("recent"))
@router.message(Command("bookmarks"))
@router.message(F.text.in_({"📥 Мои закладки", "Мои закладки", "🕒 Недавние", "Недавние", "недавние"}))
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

    if callback.message.photo or callback.message.video or callback.message.animation:
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

        is_fav = any(t.name.lower() == "избранное" for t in bookmark.tags)
        has_file = bool(getattr(bookmark, "telegram_document_id", None))
        kb = get_bookmark_card_keyboard(
            bookmark.id,
            url=bookmark.url,
            is_read=bookmark.is_read,
            showing_full_text=True,
            nav_prefix=nav_prefix,
            current_page=current_page,
            total_pages=1,
            photo_count=get_photo_count(bookmark),
            is_note=(bookmark.content_type == ContentType.NOTE or bool(bookmark.user_comment and bookmark.user_comment.strip())),
            is_fav=is_fav,
            has_file=has_file,
            is_pinned=getattr(bookmark, "is_pinned", False),
            is_long_note=check_is_long_note(bookmark),
        )
        await update_card_message(callback, bookmark, kb, showing_full_text=True)
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

    if callback.message.photo or callback.message.video or callback.message.animation:
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


# --- Pinned Articles with Direct Selection ---

@router.message(Command("pinned"))
@router.message(F.text.in_({"📌 Закрепленные", "Закрепленные", "📌 Закреплённые", "Закреплённые"}))
async def cmd_pinned(message: Message, session: AsyncSession):
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=message.from_user.id, is_pinned=True, limit=PAGE_SIZE, offset=0)

    if not bookmarks:
        await message.answer(
            "📌 <b>У вас пока нет закреплённых материалов.</b>\n\n"
            "Чтобы закрепить закладку или заметку, откройте её и нажмите кнопку <b>«📌 Закрепить»</b>. Она всегда будет отображаться в самом верху ваших закладок!",
            parse_mode="HTML",
        )
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    text = render_bookmarks_list(bookmarks, current_page=1, total_count=total, limit=PAGE_SIZE, is_pinned_only=True)
    kb = get_bookmarks_list_keyboard(bookmarks, current_page=1, total_pages=total_pages, prefix="pl")
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data.startswith("pl:") | F.data.startswith("pinned_list:"))
async def cb_pinned_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    offset = (page - 1) * PAGE_SIZE
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=callback.from_user.id, is_pinned=True, limit=PAGE_SIZE, offset=offset)

    if not bookmarks:
        await callback.answer("Закреплённых закладок нет.", show_alert=True)
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    text = render_bookmarks_list(bookmarks, current_page=page, total_count=total, limit=PAGE_SIZE, is_pinned_only=True)
    kb = get_bookmarks_list_keyboard(bookmarks, current_page=page, total_pages=total_pages, prefix="pl")

    if callback.message.photo or callback.message.video or callback.message.animation:
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


# --- Favorites Articles and Notes with Direct Selection ---

@router.message(Command("favorites"))
@router.message(Command("fav"))
@router.message(Command("избранное"))
@router.message(F.text.in_({"⭐ Избранное", "Избранное", "избранное"}))
async def cmd_favorites(message: Message, session: AsyncSession):
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(
        user_id=message.from_user.id,
        tag_name="избранное",
        limit=PAGE_SIZE,
        offset=0,
    )

    if not bookmarks:
        await message.answer(
            "⭐ <b>У вас пока нет избранных заметок и материалов.</b>\n\n"
            "Чтобы добавить заметку или статью в избранное, откройте её и нажмите кнопку <b>«⭐ В избранное»</b>.\n\n"
            "Вы также можете закреплять самые важные избранные заметки кнопкой <b>«📌 Закрепить»</b>, и они всегда будут на первом месте в этом меню!",
            parse_mode="HTML",
        )
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    text = render_bookmarks_list(bookmarks, current_page=1, total_count=total, limit=PAGE_SIZE, is_favorites_only=True)
    kb = get_bookmarks_list_keyboard(bookmarks, current_page=1, total_pages=total_pages, prefix="fl")
    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data.startswith("fl:") | F.data.startswith("fav_list:"))
async def cb_favorites_list(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    offset = (page - 1) * PAGE_SIZE
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(
        user_id=callback.from_user.id,
        tag_name="избранное",
        limit=PAGE_SIZE,
        offset=offset,
    )

    if not bookmarks:
        await callback.answer("Избранных материалов нет.", show_alert=True)
        return

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    text = render_bookmarks_list(bookmarks, current_page=page, total_count=total, limit=PAGE_SIZE, is_favorites_only=True)
    kb = get_bookmarks_list_keyboard(bookmarks, current_page=page, total_pages=total_pages, prefix="fl")

    if callback.message.photo or callback.message.video or callback.message.animation:
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

    is_fav = any(t.name.lower() == "избранное" for t in bookmark.tags)
    has_file = bool(getattr(bookmark, "telegram_document_id", None))
    kb = get_bookmark_card_keyboard(
        bookmark.id,
        bookmark.url,
        bookmark.is_read,
        showing_full_text=True,
        nav_prefix="random",
        photo_count=get_photo_count(bookmark),
        is_note=(bookmark.content_type == ContentType.NOTE or bool(bookmark.user_comment and bookmark.user_comment.strip())),
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=getattr(bookmark, "is_pinned", False),
        is_long_note=check_is_long_note(bookmark),
    )
    await send_card_message(message, message.chat.id, bookmark, kb, showing_full_text=True)


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

    is_fav = any(t.name.lower() == "избранное" for t in bookmark.tags)
    has_file = bool(getattr(bookmark, "telegram_document_id", None))
    kb = get_bookmark_card_keyboard(
        bookmark.id,
        bookmark.url,
        bookmark.is_read,
        showing_full_text=True,
        nav_prefix="random",
        photo_count=get_photo_count(bookmark),
        is_note=(bookmark.content_type == ContentType.NOTE or bool(bookmark.user_comment and bookmark.user_comment.strip())),
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=getattr(bookmark, "is_pinned", False),
        is_long_note=check_is_long_note(bookmark),
    )
    await update_card_message(callback, bookmark, kb, showing_full_text=True)
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


@router.callback_query(F.data.startswith("jump_p:"))
async def cb_jump_page_list(callback: CallbackQuery):
    try:
        parts = callback.data.split(":")
        prefix = parts[1]
        total_pages = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        current_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

        kb = get_page_picker_keyboard(
            total_pages=total_pages,
            current_page=current_page,
            prefix=prefix,
            is_card=False,
        )
        text = f"📄 <b>Выберите страницу (всего {total_pages}):</b>"
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        log.warning(f"Error in cb_jump_page_list: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("jump_c:"))
async def cb_jump_page_card(callback: CallbackQuery):
    try:
        parts = callback.data.split(":")
        prefix = parts[1]
        total_pages = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
        current_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        extra_data = parts[4] if len(parts) > 4 else ""

        kb = get_page_picker_keyboard(
            total_pages=total_pages,
            current_page=current_page,
            prefix=prefix,
            extra_data=extra_data,
            is_card=True,
        )
        caption_text = f"📄 <b>Выберите материал (всего {total_pages}):</b>"
        if callback.message.photo or callback.message.video or callback.message.animation:
            await callback.message.edit_caption(caption=caption_text, reply_markup=kb, parse_mode="HTML")
        else:
            await callback.message.edit_text(text=caption_text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        log.warning(f"Error in cb_jump_page_card: {e}")
    await callback.answer()


@router.callback_query(F.data.startswith("filter_tag:"))
async def cb_filter_tag(callback: CallbackQuery, session: AsyncSession):
    # format: filter_tag:{name}:{page} or filter_tag:{page}:{name}
    parts = callback.data.split(":")
    if len(parts) >= 3 and parts[1].isdigit():
        page = int(parts[1])
        tag_name = parts[2]
    else:
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
    is_fav = any(t.name.lower() == "избранное" for t in b.tags)
    has_file = bool(getattr(b, "telegram_document_id", None))
    kb = get_bookmark_card_keyboard(
        b.id,
        url=b.url,
        is_read=b.is_read,
        showing_full_text=True,
        nav_prefix="filter_tag",
        current_page=page,
        total_pages=total,
        extra_data=tag_name,
        photo_count=get_photo_count(b),
        is_note=(b.content_type == ContentType.NOTE or bool(b.user_comment and b.user_comment.strip())),
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=getattr(b, "is_pinned", False),
        is_long_note=check_is_long_note(b),
    )

    first_cb = callback.message.reply_markup.inline_keyboard[0][0].callback_data if (callback.message.reply_markup and callback.message.reply_markup.inline_keyboard and callback.message.reply_markup.inline_keyboard[0]) else ""
    if "filter_tag:" in first_cb or "jump_c:" in first_cb:
        await update_card_message(callback, b, kb, showing_full_text=True, page_info=(page, total))
    else:
        await send_card_message(callback.message, callback.message.chat.id, b, kb, showing_full_text=True, page_info=(page, total))

    await callback.answer()


@router.callback_query(F.data.startswith("edit_tags:"))
async def cb_edit_tags(callback: CallbackQuery, state: FSMContext):
    bookmark_id_str = callback.data.split(":")[1]
    await state.update_data(bookmark_id=bookmark_id_str)
    await state.set_state(TagEditState.waiting_for_tags)

    await callback.message.answer(
        "✏️ Отправьте новые теги через пробел или запятую (например: <code>python async архитектура</code>):\n<i>(или отправьте /cancel для отмены)</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(StateFilter(TagEditState.waiting_for_tags))
async def process_new_tags(message: Message, state: FSMContext, session: AsyncSession):
    text = (message.text or "").strip()
    from app.bot.handlers.bookmarks import MENU_BUTTON_TEXTS
    if text == "/cancel" or text.startswith("/") or text in MENU_BUTTON_TEXTS:
        await state.clear()
        await message.answer("Редактирование тегов отменено.")
        return

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


# --- Note Editing & Full View ---

@router.callback_query(F.data.startswith("edit_note:"))
async def cb_edit_note(callback: CallbackQuery, state: FSMContext):
    bookmark_id_str = callback.data.split(":")[1]
    await state.update_data(bookmark_id=bookmark_id_str)
    await state.set_state(NoteEditState.waiting_for_text)

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_edit_note")]]
    )

    await callback.message.answer(
        "✏️ <b>Редактирование заметки:</b>\n\n"
        "Отправьте новый текст заметки следующим сообщением.\n"
        "<i>(Вы можете отправить текст любой длины или прикрепить текстовый файл .txt / .md, либо нажать «Отмена»)</i>",
        reply_markup=cancel_kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_edit_note")
async def cb_cancel_edit_note(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Редактирование заметки отменено.")
    except Exception:
        await callback.message.answer("❌ Редактирование заметки отменено.")
    await callback.answer()


@router.message(StateFilter(NoteEditState.waiting_for_text))
async def process_new_note_text(message: Message, state: FSMContext, session: AsyncSession):
    from app.bot.handlers.bookmarks import MENU_BUTTON_TEXTS
    raw_text = (message.text or message.caption or "").strip()

    if raw_text in ("/cancel", "/start") or raw_text in MENU_BUTTON_TEXTS:
        await state.clear()
        await message.answer("Редактирование заметки отменено.")
        return

    text = raw_text
    # If the user uploaded a text document (.txt, .md, .text) as the new text
    if message.document:
        doc = message.document
        ext = os.path.splitext(doc.file_name or "")[1].lower()
        if ext in (".txt", ".md", ".text", ".markdown"):
            try:
                import tempfile
                file_info = await message.bot.get_file(doc.file_id)
                temp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}_{doc.file_name}")
                await message.bot.download_file(file_info.file_path, destination=temp_path)
                with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                    file_text = f.read().strip()
                if file_text:
                    text = file_text
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            except Exception as e:
                log.warning(f"Error reading doc in note edit: {e}")

    if not text:
        await message.answer("⚠️ Текст заметки не может быть пустым. Отправьте текст или /cancel для отмены.")
        return

    data = await state.get_data()
    bookmark_id_str = data.get("bookmark_id")
    await state.clear()

    if not bookmark_id_str:
        await message.answer("Сессия редактирования устарела.")
        return

    bookmark_id = uuid.UUID(bookmark_id_str)
    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, message.from_user.id)
    if not bookmark:
        await message.answer("Заметка не найдена.")
        return

    clean_text = normalize_emoji_digits(text)
    lines = [line.strip() for line in clean_text.split("\n") if line.strip()]
    default_label = "Видео-заметка" if getattr(bookmark, "telegram_video_id", None) else ("Фото-заметка" if bookmark.telegram_photo_id else "Заметка")
    new_title = lines[0][:80] if lines else default_label
    if len(new_title) == 80:
        new_title += "..."

    bookmark.full_text = clean_text
    bookmark.summary = clean_text
    bookmark.title = new_title
    if bookmark.user_comment:
        bookmark.user_comment = clean_text

    try:
        text_for_search = f"{new_title} {clean_text[:5000]}"
        bookmark.tsv_content = func.to_tsvector("russian", text_for_search)
    except Exception:
        pass

    await session.commit()

    if settings.EMBEDDING_PROVIDER not in ("none", "disabled", "deepseek", "deepseek_search"):
        try:
            from app.providers.registry import get_embedding_provider, get_vector_store
            vector_store = get_vector_store()
            embed_provider = get_embedding_provider()
            vec = await embed_provider.embed_text(f"{new_title} {clean_text[:5000]}")
            await vector_store.upsert_bookmark(
                user_id=message.from_user.id,
                bookmark_id=bookmark.id,
                vector=vec,
                metadata={"title": new_title, "tags": [t.name for t in bookmark.tags], "url": "", "content_type": bookmark.content_type.value},
            )
        except Exception as emb_e:
            log.warning(f"Vector store update skipped: {emb_e}")

    is_fav = any(t.name.lower() == "избранное" for t in bookmark.tags)
    has_file = bool(getattr(bookmark, "telegram_document_id", None))
    kb = get_bookmark_card_keyboard(
        bookmark.id,
        url=bookmark.url,
        is_read=bookmark.is_read,
        showing_full_text=True,
        nav_prefix="rl",
        current_page=1,
        total_pages=1,
        photo_count=get_photo_count(bookmark),
        is_note=True,
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=getattr(bookmark, "is_pinned", False),
        is_long_note=check_is_long_note(bookmark),
    )
    await message.answer("✅ <b>Заметка успешно обновлена!</b>", parse_mode="HTML")
    await send_card_message(message, message.chat.id, bookmark, kb, showing_full_text=True)


@router.callback_query(F.data.startswith("full_note:"))
async def cb_view_full_note(callback: CallbackQuery, session: AsyncSession):
    bookmark_id_str = callback.data.split(":")[1]
    bookmark_id = uuid.UUID(bookmark_id_str)
    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark:
        await callback.answer("Заметка не найдена.", show_alert=True)
        return

    full_text = (bookmark.full_text or bookmark.summary or bookmark.user_comment or "").strip()
    if not full_text:
        await callback.answer("Текст заметки пуст.", show_alert=True)
        return

    if len(full_text) <= 3900:
        await callback.message.answer(
            f"📖 <b>Полный текст заметки:</b>\n\n{html.escape(full_text)}",
            parse_mode="HTML",
        )
    else:
        file_bytes = full_text.encode("utf-8")
        raw_title = bookmark.title or "note"
        clean_name = "".join(c for c in raw_title if c.isalnum() or c in (" ", "_", "-")).strip()[:30]
        filename = f"{clean_name or 'note'}.txt"
        input_file = BufferedInputFile(file_bytes, filename=filename)

        preview_text = full_text[:1500] + "\n\n<i>... [весь текст прикреплен файлом ниже]</i>"
        await callback.message.answer(
            f"📖 <b>Полный текст заметки ({len(full_text):,} симв.):</b>\n\n{html.escape(preview_text)}",
            parse_mode="HTML",
        )
        await callback.message.answer_document(
            document=input_file,
            caption=f"📄 Полная заметка: {filename} ({len(file_bytes) / 1024:.1f} КБ)",
        )
    await callback.answer()
