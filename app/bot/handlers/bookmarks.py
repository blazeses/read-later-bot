import asyncio
import json
import os
import tempfile
import uuid
from aiogram import F, Router
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.card_renderer import update_card_message
from app.bot.keyboards import get_bookmark_card_keyboard
from app.core.config import settings
from app.core.logger import log
from app.db.models import ContentType
from app.db.repositories import BookmarkRepository
from app.extractors.twitter import TWITTER_URL_REGEX
from app.extractors.url_utils import extract_urls
from app.extractors.youtube import YOUTUBE_REGEX
from app.providers.registry import get_vector_store
from app.services.ingest import IngestTask, IngestionQueueService

router = Router()

# Global reference to ingestion service (injected in main)
ingest_service: IngestionQueueService | None = None


def set_ingest_service(service: IngestionQueueService):
    global ingest_service
    ingest_service = service


# Album aggregation cache for multi-photo media groups
_media_groups: dict[str, list[Message]] = {}

# Menu button labels to ignore in generic text ingest
MENU_BUTTON_TEXTS = {
    "➕ Добавить статью",
    "🔍 Поиск",
    "🕒 Недавние",
    "📖 Непрочитанные",
    "🏷 Теги",
    "🎲 Случайная",
    "📊 Статистика",
    "❓ Помощь",
}


@router.message(F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTON_TEXTS))
async def handle_text_or_urls(message: Message):
    if not ingest_service:
        await message.answer("Сервис обработки временно недоступен.")
        return

    text = message.text.strip()
    urls = extract_urls(text)

    # 1. If URLs are present in message
    if urls:
        user_comment = text
        for u in urls:
            user_comment = user_comment.replace(u, "").strip()
        user_comment = user_comment if user_comment else None

        for url in urls:
            if TWITTER_URL_REGEX.search(url):
                c_type = ContentType.TWEET
            elif YOUTUBE_REGEX.search(url):
                c_type = ContentType.YOUTUBE
            else:
                c_type = ContentType.ARTICLE

            status_msg = await message.answer(f"⏳ <b>Сохраняю ссылку:</b> <i>{url}</i>...", parse_mode="HTML")

            task = IngestTask(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                status_message_id=status_msg.message_id,
                source=url,
                content_type=c_type,
                user_comment=user_comment,
            )
            await ingest_service.enqueue(task)

    # 2. Plain text without URLs -> save as Note/Quote
    else:
        status_msg = await message.answer("⏳ <b>Сохраняю заметку...</b>", parse_mode="HTML")
        task = IngestTask(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            status_message_id=status_msg.message_id,
            source=text,
            content_type=ContentType.NOTE,
            user_comment=None,
        )
        await ingest_service.enqueue(task)


@router.message(F.photo)
async def handle_photo_upload(message: Message):
    if not ingest_service:
        await message.answer("Сервис обработки недоступен.")
        return

    # Check if this photo is part of an album (media group)
    if message.media_group_id:
        mg_id = message.media_group_id
        if mg_id in _media_groups:
            # It's another photo from the same post/album arriving right behind the first one
            _media_groups[mg_id].append(message)
            return

        # First photo of the media group: start collector
        _media_groups[mg_id] = [message]
        # Wait 0.8s for Telegram to deliver all photos of the album
        await asyncio.sleep(0.8)
        messages = _media_groups.pop(mg_id, [message])
    else:
        messages = [message]

    # Collect all photo file IDs
    photo_ids = [m.photo[-1].file_id for m in messages if m.photo]
    primary_photo_id = photo_ids[0] if photo_ids else None

    # Find caption (Telegram attaches caption to only one photo in the album)
    caption = ""
    for m in messages:
        if m.caption and m.caption.strip():
            caption = m.caption.strip()
            break

    # Determine author/source if forwarded
    author = None
    for m in messages:
        if m.forward_from_chat and m.forward_from_chat.title:
            author = m.forward_from_chat.title
            break
        elif m.forward_sender_name:
            author = m.forward_sender_name
            break
        elif m.forward_from and m.forward_from.full_name:
            author = m.forward_from.full_name
            break

    urls = extract_urls(caption)
    count_label = f" ({len(photo_ids)} фото)" if len(photo_ids) > 1 else ""

    if urls:
        url = urls[0]
        user_comment = caption.replace(url, "").strip() or None
        status_msg = await messages[0].answer(
            f"⏳ <b>Сохраняю статью{count_label}:</b> <i>{url}</i>...",
            parse_mode="HTML",
        )
        task = IngestTask(
            user_id=messages[0].from_user.id,
            chat_id=messages[0].chat.id,
            status_message_id=status_msg.message_id,
            source=url,
            content_type=ContentType.ARTICLE,
            user_comment=user_comment,
            telegram_photo_id=primary_photo_id,
            photo_ids=photo_ids,
            author=author,
        )
        await ingest_service.enqueue(task)
    else:
        note_text = caption if caption else "Фото-заметка из Telegram"
        status_msg = await messages[0].answer(
            f"⏳ <b>Сохраняю заметку{count_label}...</b>",
            parse_mode="HTML",
        )
        task = IngestTask(
            user_id=messages[0].from_user.id,
            chat_id=messages[0].chat.id,
            status_message_id=status_msg.message_id,
            source=note_text,
            content_type=ContentType.NOTE,
            telegram_photo_id=primary_photo_id,
            photo_ids=photo_ids,
            author=author,
        )
        await ingest_service.enqueue(task)


@router.message(F.document)
async def handle_document_upload(message: Message):
    if not ingest_service:
        await message.answer("Сервис обработки недоступен.")
        return

    doc = message.document
    filename = doc.file_name or "document"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in (".pdf", ".docx", ".txt", ".md", ".markdown"):
        await message.answer(
            f"❌ Неподдерживаемый формат файла: {ext}\n"
            "Поддерживаются: PDF, DOCX, TXT, MD."
        )
        return

    file_size_mb = (doc.file_size or 0) / (1024 * 1024)
    if file_size_mb > settings.MAX_FILE_SIZE_MB:
        await message.answer(f"❌ Файл слишком большой ({file_size_mb:.1f} МБ). Лимит: {settings.MAX_FILE_SIZE_MB} МБ.")
        return

    status_msg = await message.answer(f"⏳ <b>Загружаю и анализирую {filename}...</b>", parse_mode="HTML")

    temp_dir = tempfile.gettempdir()
    local_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{filename}")

    bot = message.bot
    file_info = await bot.get_file(doc.file_id)
    await bot.download_file(file_info.file_path, destination=local_path)

    task = IngestTask(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        status_message_id=status_msg.message_id,
        source=local_path,
        content_type=ContentType.DOCUMENT,
        filename=filename,
        user_comment=message.caption,
    )
    await ingest_service.enqueue(task)


# --- Callback Queries ---

@router.callback_query(F.data.startswith("show_album:"))
async def cb_show_album(callback: CallbackQuery, session: AsyncSession):
    bookmark_id_str = callback.data.split(":")[1]
    bookmark_id = uuid.UUID(bookmark_id_str)

    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark or not bookmark.photos_json:
        await callback.answer("Фотографии не найдены.", show_alert=True)
        return

    try:
        photos = json.loads(bookmark.photos_json)
        if not isinstance(photos, list) or not photos:
            await callback.answer("Фотографии не найдены.", show_alert=True)
            return

        media_group = [InputMediaPhoto(media=pid) for pid in photos[:10]]
        await callback.message.bot.send_media_group(
            chat_id=callback.message.chat.id,
            media=media_group,
        )
        await callback.answer(f"Отправлено {len(media_group)} фото")
    except Exception as e:
        log.warning(f"Failed to send album: {e}")
        await callback.answer("Ошибка при отправке альбома.", show_alert=True)


@router.callback_query(F.data.startswith("toggle_read:"))
async def cb_toggle_read(callback: CallbackQuery, session: AsyncSession):
    # format: toggle_read:{id}:{nav_prefix}:{current_page}:{total_pages}:{extra_data}
    parts = callback.data.split(":")
    bookmark_id = uuid.UUID(parts[1])
    nav_prefix = parts[2] if len(parts) > 2 and parts[2] != "_" else None
    current_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    total_pages = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
    extra_data = parts[5] if len(parts) > 5 and parts[5] != "_" else ""

    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark:
        await callback.answer("Закладка не найдена.", show_alert=True)
        return

    new_status = not bookmark.is_read
    await repo.mark_as_read(bookmark_id, callback.from_user.id, is_read=new_status)
    await session.commit()
    bookmark.is_read = new_status

    photo_count = 1
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list):
                photo_count = len(p_list)
        except Exception:
            pass

    kb = get_bookmark_card_keyboard(
        bookmark.id,
        url=bookmark.url,
        is_read=new_status,
        showing_full_text=False,
        nav_prefix=nav_prefix,
        current_page=current_page,
        total_pages=total_pages,
        extra_data=extra_data,
        photo_count=photo_count,
    )
    page_info = (current_page, total_pages) if total_pages > 1 else None
    await update_card_message(callback, bookmark, kb, showing_full_text=False, page_info=page_info)

    status_text = "прочитано ✅" if new_status else "не прочитано 📖"
    await callback.answer(f"Отмечено как {status_text}")


@router.callback_query(F.data.startswith("toggle_view:"))
async def cb_toggle_view(callback: CallbackQuery, session: AsyncSession):
    # format: toggle_view:{id}:{mode}:{nav_prefix}:{current_page}:{total_pages}:{extra_data}
    parts = callback.data.split(":")
    bookmark_id = uuid.UUID(parts[1])
    mode = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    showing_full_text = (mode == 1)
    nav_prefix = parts[3] if len(parts) > 3 and parts[3] != "_" else None
    current_page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
    total_pages = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 1
    extra_data = parts[6] if len(parts) > 6 and parts[6] != "_" else ""

    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark:
        await callback.answer("Закладка не найдена.", show_alert=True)
        return

    photo_count = 1
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list):
                photo_count = len(p_list)
        except Exception:
            pass

    kb = get_bookmark_card_keyboard(
        bookmark.id,
        url=bookmark.url,
        is_read=bookmark.is_read,
        showing_full_text=showing_full_text,
        nav_prefix=nav_prefix,
        current_page=current_page,
        total_pages=total_pages,
        extra_data=extra_data,
        photo_count=photo_count,
    )
    page_info = (current_page, total_pages) if total_pages > 1 else None
    await update_card_message(callback, bookmark, kb, showing_full_text=showing_full_text, page_info=page_info)
    await callback.answer()


@router.callback_query(F.data.startswith("view_text:"))
async def cb_view_text_legacy(callback: CallbackQuery, session: AsyncSession):
    # Redirect legacy view_text calls to in-place full text toggle
    parts = callback.data.split(":")
    bookmark_id = uuid.UUID(parts[1])
    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark:
        await callback.answer("Закладка не найдена.", show_alert=True)
        return

    photo_count = 1
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list):
                photo_count = len(p_list)
        except Exception:
            pass

    kb = get_bookmark_card_keyboard(
        bookmark.id,
        url=bookmark.url,
        is_read=bookmark.is_read,
        showing_full_text=True,
        photo_count=photo_count,
    )
    await update_card_message(callback, bookmark, kb, showing_full_text=True)
    await callback.answer()


@router.callback_query(F.data.startswith("delete:"))
async def cb_delete_bookmark(callback: CallbackQuery, session: AsyncSession):
    bookmark_id_str = callback.data.split(":")[1]
    bookmark_id = uuid.UUID(bookmark_id_str)
    user_id = callback.from_user.id

    repo = BookmarkRepository(session)
    deleted = await repo.delete(bookmark_id, user_id)
    if deleted:
        await session.commit()
        if settings.EMBEDDING_PROVIDER not in ("none", "disabled", "deepseek", "deepseek_search"):
            try:
                vector_store = get_vector_store()
                await vector_store.delete_by_bookmark(user_id, bookmark_id)
            except Exception as e:
                log.warning(f"Error removing vectors from Qdrant: {e}")

        # In-place clean delete message
        msg = callback.message
        if msg.photo:
            try:
                await msg.delete()
            except Exception:
                pass
            await msg.answer("🗑 <b>Закладка успешно удалена.</b>", parse_mode="HTML")
        else:
            await msg.edit_text("🗑 <b>Закладка успешно удалена.</b>", parse_mode="HTML")
        await callback.answer("Удалено")
    else:
        await callback.answer("Закладка уже удалена или не найдена.", show_alert=True)
