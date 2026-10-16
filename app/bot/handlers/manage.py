import json
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
    get_tags_keyboard,
)
from app.db.repositories import BookmarkRepository, TagRepository

router = Router()


class TagEditState(StatesGroup):
    waiting_for_tags = State()


def get_photo_count(b) -> int:
    if b.photos_json:
        try:
            photos = json.loads(b.photos_json)
            if isinstance(photos, list):
                return len(photos)
        except Exception:
            pass
    return 1 if b.telegram_photo_id else 0


import html


@router.message(Command("recent"))
@router.message(F.text.in_({"🕒 Недавние", "Недавние", "недавние"}))
async def cmd_recent(message: Message, session: AsyncSession):
    try:
        repo = BookmarkRepository(session)
        bookmarks, total = await repo.list_bookmarks(user_id=message.from_user.id, limit=1, offset=0)

        if not bookmarks:
            await message.answer(
                "📭 <b>У вас пока нет сохраненных закладок.</b>\n\n"
                "Отправьте мне ссылку, текст, фото или файл, чтобы сохранить первый материал!",
                parse_mode="HTML",
            )
            return

        b = bookmarks[0]
        kb = get_bookmark_card_keyboard(
            b.id,
            url=b.url,
            is_read=b.is_read,
            nav_prefix="recent_page",
            current_page=1,
            total_pages=total,
            photo_count=get_photo_count(b),
        )
        await send_card_message(message, message.chat.id, b, kb, page_info=(1, total))
    except Exception as e:
        from app.core.logger import log
        log.exception(f"Error in cmd_recent: {e}")
        await message.answer(f"⚠️ Не удалось загрузить недавние закладки. Ошибка: {str(e)[:150]}")


@router.callback_query(F.data.startswith("recent_page:"))
async def cb_recent_page(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=callback.from_user.id, limit=1, offset=page - 1)

    if not bookmarks:
        await callback.answer("Закладка не найдена.", show_alert=True)
        return

    b = bookmarks[0]
    kb = get_bookmark_card_keyboard(
        b.id,
        url=b.url,
        is_read=b.is_read,
        nav_prefix="recent_page",
        current_page=page,
        total_pages=total,
        photo_count=get_photo_count(b),
    )
    await update_card_message(callback, b, kb, page_info=(page, total))
    await callback.answer()


@router.message(Command("unread"))
@router.message(F.text.in_({"📖 Непрочитанные", "Непрочитанные", "непрочитанные"}))
async def cmd_unread(message: Message, session: AsyncSession):
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=message.from_user.id, is_read=False, limit=10)

    if not bookmarks:
        await message.answer("🎉 Отлично! У вас нет непрочитанных материалов.")
        return

    lines = [f"📖 <b>Список непрочитанных материалов (всего {total}):</b>\n"]
    for idx, b in enumerate(bookmarks, start=1):
        safe_title = html.escape((b.title or "Без названия").strip())
        link = f"<a href='{b.url}'>{safe_title}</a>" if b.url else f"<b>{safe_title}</b>"
        lines.append(f"{idx}. {link} (~{b.reading_time_min} мин.)")

    lines.append("\n<i>Используйте «🎲 Случайная» или /random, чтобы открыть случайную статью из непрочитанных.</i>")
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("random"))
@router.message(F.text.in_({"🎲 Случайная", "Случайная", "случайная"}))
async def cmd_random(message: Message, session: AsyncSession):
    repo = BookmarkRepository(session)
    bookmark = await repo.get_random_unread(message.from_user.id)

    if not bookmark:
        await message.answer("🎉 Непрочитанных закладок нет!")
        return

    kb = get_bookmark_card_keyboard(
        bookmark.id,
        bookmark.url,
        bookmark.is_read,
        photo_count=get_photo_count(bookmark),
    )
    await send_card_message(message, message.chat.id, bookmark, kb)


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

    # If first open from tags list, send as new card; if paging, update in-place
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
