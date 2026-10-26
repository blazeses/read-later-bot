import asyncio
import html
import json
import os
import shutil
import tempfile
import uuid
from typing import Optional
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.card_renderer import check_is_long_note, send_card_message, update_card_message
from app.bot.keyboards import get_bookmark_card_keyboard, get_video_quality_keyboard
from app.core.config import settings
from app.core.logger import log
from app.db.models import ContentType
from app.db.repositories import BookmarkRepository, TagRepository, UserRepository
from app.extractors.twitter import TWITTER_URL_REGEX
from app.extractors.url_utils import extract_urls, normalize_emoji_digits
from app.extractors.youtube import YOUTUBE_REGEX
from app.providers.registry import get_embedding_provider, get_vector_store
from app.services.ingest import IngestTask, IngestionQueueService
from app.services.video_downloader import is_video_url, download_video, download_audio

router = Router()

# Global reference to ingestion service (injected in main)
ingest_service: IngestionQueueService | None = None


def set_ingest_service(service: IngestionQueueService):
    global ingest_service
    ingest_service = service


# Album aggregation cache for multi-photo media groups
_media_groups: dict[str, list[Message]] = {}

MENU_BUTTON_TEXTS = {
    "➕ Загрузить заметку", "Загрузить заметку",
    "➕ Добавить заметку", "Добавить заметку",
    "➕ Добавить статью", "Добавить статью",
    "📥 Мои закладки", "Мои закладки",
    "🔍 Поиск", "Поиск",
    "🕒 Недавние", "Недавние", "недавние",
    "⭐ Избранное", "Избранное", "избранное",
    "📌 Закрепленные", "Закрепленные", "📌 Закреплённые", "Закреплённые",
    "📖 Непрочитанные", "Непрочитанные", "непрочитанные",
    "🏷 Теги", "Теги", "теги",
    "🎲 Случайная", "Случайная", "случайная",
    "📊 Дайджест", "Дайджест",
    "📊 Статистика", "Статистика", "статистика",
    "⚙️ Настройки", "Настройки",
    "❓ Помощь", "Помощь", "помощь",
    "🔽 Скрыть меню", "Скрыть меню", "Закрыть меню",
}


def extract_forward_author(message: Message) -> Optional[str]:
    """Extract source channel or user author name from forwarded messages."""
    origin = getattr(message, "forward_origin", None)
    if origin:
        origin_type = getattr(origin, "type", None)
        if origin_type == "channel" and getattr(origin, "chat", None):
            return origin.chat.title or origin.chat.username
        elif origin_type == "user" and getattr(origin, "sender_user", None):
            return origin.sender_user.full_name
        elif origin_type == "hidden_user" and getattr(origin, "sender_user_name", None):
            return origin.sender_user_name
        elif origin_type == "chat" and getattr(origin, "sender_chat", None):
            return origin.sender_chat.title

    if message.forward_from_chat and message.forward_from_chat.title:
        return message.forward_from_chat.title
    if message.forward_sender_name:
        return message.forward_sender_name
    if message.forward_from and message.forward_from.full_name:
        return message.forward_from.full_name

    return None


async def process_and_send_video(
    bot: Bot,
    chat_id: int,
    user_id: int,
    url: str,
    status_msg: Message,
    quality: str = "auto",
    session: Optional[AsyncSession] = None,
    user_comment: Optional[str] = None,
):
    """
    Automatic YouTube and video downloader & sender pipeline.
    Progress states:
      ⏬ [1/3] Скачивание видео...
      📦 [2/3] Подготовка файла...
      📤 [3/3] Отправка видео в Telegram...
      ✅ Видео отправлено.
    """
    tmp_dir = tempfile.mkdtemp(prefix="yoinks_auto_")
    try:
        # State 1: Скачивание
        try:
            await status_msg.edit_text(
                "⏬ <b>[1/3] Скачивание видео...</b>\n<i>Загружаю видеопоток...</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        file_paths, meta, err = await download_video(url, target_dir=tmp_dir, quality=quality)

        if err or not file_paths:
            err_lower = (err or "").lower()
            is_bot_challenge = (
                "bot" in err_lower
                or "sign in" in err_lower
                or "requested format is not available" in err_lower
                or "403: forbidden" in err_lower
                or "no video formats" in err_lower
            )

            web_dl_url = None
            if "youtube.com" in url:
                web_dl_url = url.replace("youtube.com", "ssyoutube.com")
            elif "youtu.be/" in url:
                web_dl_url = url.replace("youtu.be/", "ssyoutube.com/watch?v=")
            else:
                web_dl_url = "https://cobalt.tools"

            buttons = []
            if meta and meta.get("direct_url"):
                buttons.append([InlineKeyboardButton(text="🌐 Скачать по прямой ссылке", url=meta["direct_url"])])
            elif web_dl_url:
                buttons.append([InlineKeyboardButton(text="🌐 Скачать видео через веб в 1 клик", url=web_dl_url)])

            if is_bot_challenge:
                buttons.append([InlineKeyboardButton(text="ℹ️ Как подключить cookies в боте", callback_data="yoink_cookie_help:0")])
                msg_body = (
                    "🛡 <b>YouTube ограничил автоматическую загрузку на сервере хостинга:</b>\n"
                    "<i>(YouTube требует подтверждение от ботов для IP датацентра)</i>\n\n"
                    "👉 <b>Вы можете скачать видео прямо сейчас в 1 клик через веб:</b>\n\n"
                    "💡 <i>Чтобы бот сам скачивал любые ролики с YouTube прямо в этот чат, добавьте cookies (кнопка ниже).</i>"
                )
            else:
                clean_err = html.escape((err or 'Неизвестная ошибка')[:200])
                msg_body = f"⚠️ <b>Не удалось скачать видео:</b>\n{clean_err}"

            reply_kb = InlineKeyboardMarkup(inline_keyboard=buttons)
            try:
                await status_msg.edit_text(
                    msg_body,
                    reply_markup=reply_kb,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
            return

        # State 2: Подготовка файла
        try:
            await status_msg.edit_text(
                "📦 <b>[2/3] Подготовка файла...</b>\n<i>Проверяю формат и подготавливаю видео для Telegram...</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        valid_paths = [p for p in file_paths if os.path.exists(p) and os.path.getsize(p) > 1024]
        if not valid_paths:
            try:
                await status_msg.edit_text("⚠️ <b>Файл видео не найден или повреждён после загрузки.</b>", parse_mode="HTML")
            except Exception:
                pass
            return

        # State 3: Отправка видео в Telegram
        total_parts = len(valid_paths)
        part_info = f" ({total_parts} ч.)" if total_parts > 1 else ""
        try:
            await status_msg.edit_text(
                f"📤 <b>[3/3] Отправка видео в Telegram{part_info}...</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

        title = (meta.get("title") if meta else None) or "Видео"
        uploader = (meta.get("uploader") if meta else None) or ""
        duration = meta.get("duration") if meta else None
        width = meta.get("width") if meta else None
        height = meta.get("height") if meta else None

        for idx, f_path in enumerate(valid_paths, start=1):
            caption = f"🎬 <b>{html.escape(title[:200])}</b>"
            if total_parts > 1:
                caption += f"\n📁 <b>Часть {idx} из {total_parts}</b>"
            if uploader:
                caption += f"\n👤 {html.escape(uploader[:100])}"
            if user_comment:
                caption += f"\n💬 <i>{html.escape(user_comment[:200])}</i>"
            caption += f"\n🔗 {url}"

            video_file = FSInputFile(f_path)
            await bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption[:1024],
                parse_mode="HTML",
                duration=int(duration) if duration and total_parts == 1 else None,
                width=int(width) if width else None,
                height=int(height) if height else None,
                supports_streaming=True,
            )

        # State 4: Завершено
        try:
            await status_msg.edit_text("✅ <b>Видео успешно отправлено!</b>", parse_mode="HTML")
            await asyncio.sleep(2)
            await status_msg.delete()
        except Exception:
            pass

        # Save bookmark in DB so user has it in their bookmarks library
        if session:
            try:
                from app.db.repositories import BookmarkRepository, TagRepository
                bm_repo = BookmarkRepository(session)
                tag_repo = TagRepository(session)
                tag_name = "youtube" if ("youtu.be" in url or "youtube.com" in url) else "video"
                tags = await tag_repo.get_or_create_tags(user_id, ["видео", tag_name])
                await bm_repo.create(
                    user_id=user_id,
                    content_type=ContentType.YOUTUBE if "youtu" in url else ContentType.ARTICLE,
                    url=url,
                    title=f"🎬 {title[:200]}",
                    author=uploader,
                    domain="youtube.com" if "youtu" in url else "video",
                    full_text=f"Видео: {title}\nАвтор: {uploader}\nСсылка: {url}",
                    summary=f"🎬 Видео: {title}",
                    reading_time_min=max(1, int((duration or 60) // 60)),
                    user_comment=user_comment,
                    tags=tags,
                )
                await session.commit()
            except Exception as bm_err:
                log.warning(f"Failed to auto-save video bookmark to DB: {bm_err}")

    except Exception as e:
        log.exception(f"Error in process_and_send_video for {url}: {e}")
        try:
            await status_msg.edit_text(f"⚠️ Ошибка при отправке видео: {html.escape(str(e)[:150])}")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.message(F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTON_TEXTS))
async def handle_text_or_urls(message: Message, session: AsyncSession):
    if not ingest_service:
        await message.answer("Сервис обработки временно недоступен.")
        return

    try:
        text = normalize_emoji_digits(message.text.strip())
        urls = extract_urls(text, message.entities)
        author = extract_forward_author(message)

        # Вычисляем текст без ссылок
        non_url_text = text
        for u in urls:
            non_url_text = non_url_text.replace(u, "").strip()

        # Если сообщение содержит видео-ссылку (YouTube, TikTok, Shorts, Reels и т.д.)
        # -> запускаем автоматическое скачивание и отправку видео прямо в чат!
        video_urls = [u for u in urls if is_video_url(u)]
        if video_urls:
            url = video_urls[0]
            try:
                status_msg = await message.answer(
                    "⏬ <b>[1/3] Скачивание видео...</b>\n<i>Загружаю видеопоток с наилучшим качеством...</i>",
                    parse_mode="HTML",
                )
            except Exception:
                status_msg = await message.answer("⏬ [1/3] Скачивание видео...")

            await process_and_send_video(
                bot=message.bot,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                url=url,
                status_msg=status_msg,
                quality="auto",
                session=session,
                user_comment=non_url_text if non_url_text else None,
            )
            return

        # 1. Если сообщение содержит текст поста/заметки вместе со ссылкой (или пост переслан из канала/чата)
        # -> это ЗАМЕТКА пользователя/канала со ссылкой на источник! Сохраняем оригинальный текст 1:1 без парсинга статьи!
        if urls and (len(non_url_text) >= 15 or author):
            action_label = f"пост из «{html.escape(author)}»" if author else "заметку"
            try:
                status_msg = await message.answer(f"⏳ <b>[1/3] Сохраняю {action_label}...</b>", parse_mode="HTML")
            except Exception:
                status_msg = await message.answer("⏳ [1/3] Сохраняю материал...")

            task = IngestTask(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                status_message_id=status_msg.message_id,
                source=text,
                content_type=ContentType.NOTE,
                url=urls[0],
                user_comment=None,
                author=author,
            )
            await ingest_service.enqueue(task)

        # 2. Если это чистая ссылка (или ссылка с короткой пометкой < 15 символов) -> веб-материал
        elif urls:
            user_comment = non_url_text if non_url_text else None
            for url in urls:
                if TWITTER_URL_REGEX.search(url):
                    c_type = ContentType.TWEET
                elif YOUTUBE_REGEX.search(url):
                    c_type = ContentType.YOUTUBE
                else:
                    c_type = ContentType.ARTICLE

                safe_url = html.escape(url)
                try:
                    status_msg = await message.answer(f"⏳ <b>[1/3] Скачиваю ссылку:</b> <i>{safe_url}</i>...", parse_mode="HTML")
                except Exception:
                    status_msg = await message.answer(f"⏳ [1/3] Скачиваю ссылку: {url}...")

                task = IngestTask(
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    status_message_id=status_msg.message_id,
                    source=url,
                    content_type=c_type,
                    user_comment=user_comment,
                    author=author,
                )
                await ingest_service.enqueue(task)

        # 3. Обычный текст / новость без ссылок -> заметка / новость
        else:
            action_label = f"новость из «{html.escape(author)}»" if author else "заметку"
            try:
                status_msg = await message.answer(f"⏳ <b>[1/3] Сохраняю {action_label}...</b>", parse_mode="HTML")
            except Exception:
                status_msg = await message.answer("⏳ [1/3] Сохраняю материал...")

            task = IngestTask(
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                status_message_id=status_msg.message_id,
                source=text,
                content_type=ContentType.NOTE,
                user_comment=None,
                author=author,
            )
            await ingest_service.enqueue(task)

    except Exception as e:
        log.exception(f"Ошибка при приёме текстового сообщения: {e}")
        await message.answer("⚠️ Не удалось принять сообщение. Пожалуйста, отправьте ссылку или текст ещё раз.")


@router.message(F.photo | F.video | F.animation)
async def handle_media_upload(message: Message):
    if not ingest_service:
        await message.answer("Сервис обработки недоступен.")
        return

    try:
        if message.media_group_id:
            mg_id = message.media_group_id
            if mg_id in _media_groups:
                _media_groups[mg_id].append(message)
                return

            if len(_media_groups) > 50:
                _media_groups.pop(next(iter(_media_groups)), None)

            _media_groups[mg_id] = [message]
            await asyncio.sleep(0.8)
            messages = _media_groups.pop(mg_id, [message])
        else:
            messages = [message]

        photo_ids = [m.photo[-1].file_id for m in messages if m.photo]
        video_ids = [m.video.file_id for m in messages if m.video]
        anim_ids = [m.animation.file_id for m in messages if m.animation]

        primary_video_id = video_ids[0] if video_ids else (anim_ids[0] if anim_ids else None)
        primary_photo_id = photo_ids[0] if photo_ids else None
        
        # Если фото нет, но у видео/анимации есть превью, сохраняем его в telegram_photo_id
        if not primary_photo_id:
            if messages[0].video and messages[0].video.thumbnail:
                primary_photo_id = messages[0].video.thumbnail.file_id
            elif messages[0].animation and messages[0].animation.thumbnail:
                primary_photo_id = messages[0].animation.thumbnail.file_id

        all_media_items = []
        for m in messages:
            if m.photo:
                all_media_items.append({"type": "photo", "id": m.photo[-1].file_id})
            elif m.video:
                all_media_items.append({"type": "video", "id": m.video.file_id})
            elif m.animation:
                all_media_items.append({"type": "video", "id": m.animation.file_id})

        all_media_ids = all_media_items if all_media_items else (photo_ids or video_ids or anim_ids)

        caption = ""
        caption_entities = None
        for m in messages:
            if m.caption and m.caption.strip():
                caption = normalize_emoji_digits(m.caption.strip())
                caption_entities = m.caption_entities
                break

        author = None
        for m in messages:
            author = extract_forward_author(m)
            if author:
                break

        urls = extract_urls(caption, caption_entities)
        count_label = f" ({len(photo_ids)} фото)" if len(photo_ids) > 1 else ""
        non_url_caption = caption
        for u in urls:
            non_url_caption = non_url_caption.replace(u, "").strip()

        # Если к медиафайлу есть подпись (с ссылкой или без) или это пересланный пост ->
        # это ВСЕГДА заметка с прикрепленным медиафайлом (фото/видео)! Текст подписи сохраняется 1:1!
        if not urls or len(non_url_caption) >= 15 or author or not caption:
            default_label = "Видео-заметка" if primary_video_id else "Фото-заметка"
            note_text = caption if caption else f"{default_label} из Telegram"
            media_type_label = "видео" if primary_video_id else ("фото" if primary_photo_id else "материал")
            action_label = f"новость{count_label}" if author else f"заметку с {media_type_label}{count_label}"
            try:
                status_msg = await messages[0].answer(
                    f"⏳ <b>[1/3] Сохраняю {action_label}...</b>",
                    parse_mode="HTML",
                )
            except Exception:
                status_msg = await messages[0].answer(f"⏳ [1/3] Сохраняю материал{count_label}...")

            task = IngestTask(
                user_id=messages[0].from_user.id,
                chat_id=messages[0].chat.id,
                status_message_id=status_msg.message_id,
                source=note_text,
                content_type=ContentType.NOTE,
                url=urls[0] if urls else None,
                user_comment=None,
                telegram_photo_id=primary_photo_id,
                telegram_video_id=primary_video_id,
                photo_ids=all_media_ids,
                author=author,
            )
            await ingest_service.enqueue(task)
        else:
            # Подпись состоит исключительно из одной ссылки без текста поста -> парсим статью с превью
            url = urls[0]
            user_comment = non_url_caption if non_url_caption else None
            safe_url = html.escape(url)
            try:
                status_msg = await messages[0].answer(
                    f"⏳ <b>[1/3] Сохраняю статью{count_label}:</b> <i>{safe_url}</i>...",
                    parse_mode="HTML",
                )
            except Exception:
                status_msg = await messages[0].answer(f"⏳ [1/3] Сохраняю статью{count_label}: {url}...")

            task = IngestTask(
                user_id=messages[0].from_user.id,
                chat_id=messages[0].chat.id,
                status_message_id=status_msg.message_id,
                source=url,
                content_type=ContentType.ARTICLE,
                user_comment=user_comment,
                telegram_photo_id=primary_photo_id,
                telegram_video_id=primary_video_id,
                photo_ids=all_media_ids,
                author=author,
            )
            await ingest_service.enqueue(task)

    except Exception as e:
        log.exception(f"Ошибка при приёме медиа-сообщения: {e}")
        await message.answer("⚠️ Не удалось принять файл. Попробуйте отправить его ещё раз.")


ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}


@router.message(F.document)
async def handle_document_upload(message: Message, session: AsyncSession):
    doc = message.document
    filename = doc.file_name or "document"
    ext = os.path.splitext(filename)[1].lower()

    # 1. Прямое моментальное сохранение архивов (без парсинга ИИ и без скачивания на диск сервера)
    if ext in ARCHIVE_EXTENSIONS:
        file_size_bytes = doc.file_size or 0
        # Telegram ограничивает загрузку файлов: 2 ГБ для обычных пользователей, 4 ГБ для Telegram Premium
        if file_size_bytes > 4 * 1024 * 1024 * 1024:
            await message.answer(
                "❌ <b>Файл превышает максимальный лимит Telegram (4 ГБ).</b>\n\n"
                "💡 <i>Telegram ограничивает отправку файлов 2 ГБ для обычных аккаунтов и 4 ГБ для Telegram Premium.\n"
                "Чтобы сохранить архив больше 4 ГБ (например, 5–10 ГБ), разбейте его на части (тома) по 2–4 ГБ (part1.zip, part2.zip) и отправьте их в бот.</i>",
                parse_mode="HTML",
            )
            return

        if file_size_bytes >= 1024 * 1024 * 1024:
            size_str = f"{file_size_bytes / (1024 * 1024 * 1024):.2f} ГБ"
        else:
            size_str = f"{file_size_bytes / (1024 * 1024):.1f} МБ"

        user_repo = UserRepository(session)
        await user_repo.get_or_create(message.from_user.id)

        tag_repo = TagRepository(session)
        archive_tag = ext.lstrip(".").lower()
        tags = await tag_repo.get_or_create_tags(message.from_user.id, ["архив", archive_tag])

        caption_note = normalize_emoji_digits((message.caption or "").strip())
        full_text = caption_note if caption_note else f"Файл архива сохранён в закладочник.\nИмя: {filename}\nРазмер: {size_str}"
        summary = f"📦 Архив: {filename} ({size_str})"

        bm_repo = BookmarkRepository(session)
        bookmark = await bm_repo.create(
            user_id=message.from_user.id,
            content_type=ContentType.DOCUMENT,
            url=None,
            title=f"📦 {filename}",
            domain="архив",
            full_text=full_text,
            summary=summary,
            user_comment=caption_note if caption_note else None,
            tags=tags,
            reading_time_min=1,
            telegram_document_id=doc.file_id,
        )
        await session.commit()

        if settings.EMBEDDING_PROVIDER not in ("none", "disabled", "deepseek", "deepseek_search"):
            try:
                vector_store = get_vector_store()
                embed_provider = get_embedding_provider()
                vec = await embed_provider.embed_text(f"{filename} {summary} {caption_note}")
                await vector_store.upsert_bookmark(
                    user_id=message.from_user.id,
                    bookmark_id=bookmark.id,
                    vector=vec,
                    metadata={"title": f"📦 {filename}", "tags": [t.name for t in tags], "url": "", "content_type": bookmark.content_type.value},
                )
            except Exception as emb_e:
                log.warning(f"Vector store upsert skipped for archive: {emb_e}")

        kb = get_bookmark_card_keyboard(
            bookmark.id,
            url=None,
            is_read=False,
            showing_full_text=True,
            has_file=True,
            is_pinned=False,
        )
        await send_card_message(
            bot_or_message=message,
            chat_id=message.chat.id,
            bookmark=bookmark,
            reply_markup=kb,
            showing_full_text=True,
        )
        return

    # 2. Текстовые документы (PDF, DOCX, TXT, MD) - через сервис очередей Ingestion
    if not ingest_service:
        await message.answer("Сервис обработки недоступен.")
        return

    if ext not in (".pdf", ".docx", ".txt", ".md", ".markdown"):
        await message.answer(
            f"❌ Неподдерживаемый формат файла: {ext}\n"
            "Поддерживаются архивы: ZIP, RAR, 7Z, TAR, GZ, а также документы: PDF, DOCX, TXT, MD."
        )
        return

    file_size_mb = (doc.file_size or 0) / (1024 * 1024)
    if file_size_mb > settings.MAX_FILE_SIZE_MB:
        await message.answer(f"❌ Файл слишком большой ({file_size_mb:.1f} МБ). Лимит: {settings.MAX_FILE_SIZE_MB} МБ.")
        return

    status_msg = await message.answer(f"⏳ <b>[1/3] Загружаю и анализирую {filename}...</b>", parse_mode="HTML")

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
        telegram_document_id=doc.file_id,
    )
    await ingest_service.enqueue(task)


# --- Callback Queries: Retry and Error Handling ---

@router.callback_query(F.data.startswith("retry_ingest:"))
async def cb_retry_ingest(callback: CallbackQuery):
    msg_id = int(callback.data.split(":")[1])
    if not ingest_service:
        await callback.answer("Сервис недоступен.")
        return

    task = ingest_service.failed_tasks.get(msg_id)
    if not task:
        await callback.answer("Данные задания устарели. Отправьте ссылку повторно.", show_alert=True)
        return

    await callback.message.edit_text("⏳ <b>[1/3] Повторная попытка обработки...</b>", parse_mode="HTML")
    await ingest_service.enqueue(task)
    await callback.answer("Повторный запуск")


@router.callback_query(F.data.startswith("save_raw_note:"))
async def cb_save_raw_note(callback: CallbackQuery):
    msg_id = int(callback.data.split(":")[1])
    if not ingest_service:
        await callback.answer("Сервис недоступен.")
        return

    task = ingest_service.failed_tasks.get(msg_id)
    if not task:
        await callback.answer("Данные задания устарели.", show_alert=True)
        return

    task.content_type = ContentType.NOTE
    task.source = f"Ссылка: {task.source}"
    await callback.message.edit_text("⏳ <b>[1/3] Сохраняю материал как текстовую заметку...</b>", parse_mode="HTML")
    await ingest_service.enqueue(task)
    await callback.answer("Сохраняю как заметку")


@router.callback_query(F.data.startswith("toggle_fav:"))
async def cb_toggle_favorite(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    bm_id = uuid.UUID(parts[1])
    nav_prefix = parts[2] if len(parts) > 2 and parts[2] != "_" else None
    current_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    total_pages = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
    extra_data = parts[5] if len(parts) > 5 and parts[5] != "_" else ""

    tag_repo = TagRepository(session)
    bm_repo = BookmarkRepository(session)

    bookmark = await bm_repo.get_by_id(bm_id, callback.from_user.id)
    if not bookmark:
        await callback.answer("Закладка не найдена.", show_alert=True)
        return

    tag_names = [t.name.lower() for t in bookmark.tags]
    if "избранное" in tag_names:
        new_tags = [t for t in bookmark.tags if t.name.lower() != "избранное"]
        await bm_repo.update_metadata(bm_id, callback.from_user.id, tags=new_tags)
        await session.commit()
        bookmark.tags = new_tags
        is_fav = False
        await callback.answer("Удалено из избранного")
    else:
        fav_tags = await tag_repo.get_or_create_tags(callback.from_user.id, ["избранное"])
        combined_tags = list({t.name: t for t in (bookmark.tags + fav_tags)}.values())
        await bm_repo.update_metadata(bm_id, callback.from_user.id, tags=combined_tags)
        await session.commit()
        bookmark.tags = combined_tags
        is_fav = True
        await callback.answer("⭐ Добавлено в избранное!", show_alert=False)

    photo_count = 1 if (bookmark.telegram_photo_id or bookmark.image_url or getattr(bookmark, "telegram_video_id", None)) else 0
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list):
                photo_count = len(p_list)
        except Exception:
            pass

    has_file = bool(getattr(bookmark, "telegram_document_id", None))
    kb = get_bookmark_card_keyboard(
        bookmark.id,
        url=bookmark.url,
        is_read=bookmark.is_read,
        showing_full_text=True,
        nav_prefix=nav_prefix,
        current_page=current_page,
        total_pages=total_pages,
        extra_data=extra_data,
        photo_count=photo_count,
        is_note=(bookmark.content_type == ContentType.NOTE or bool(bookmark.user_comment and bookmark.user_comment.strip())),
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=getattr(bookmark, "is_pinned", False),
        is_long_note=check_is_long_note(bookmark),
    )
    page_info = (current_page, total_pages) if total_pages > 1 else None
    await update_card_message(callback, bookmark, kb, showing_full_text=True, page_info=page_info)


@router.callback_query(F.data.startswith("remind_menu:"))
async def cb_remind_menu(callback: CallbackQuery):
    bm_id_str = callback.data.split(":")[1]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌅 Завтра утром (09:00)", callback_data=f"set_remind:{bm_id_str}:tomorrow")],
            [InlineKeyboardButton(text="🏖 В субботу (11:00)", callback_data=f"set_remind:{bm_id_str}:weekend")],
            [InlineKeyboardButton(text="📅 Через 3 дня", callback_data=f"set_remind:{bm_id_str}:3days")],
            [InlineKeyboardButton(text="« Отмена", callback_data="close_remind_menu")],
        ]
    )
    await callback.message.answer("⏰ <b>Когда напомнить прочитать эту статью?</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("set_remind:"))
async def cb_set_remind(callback: CallbackQuery):
    parts = callback.data.split(":")
    period = parts[2]
    labels = {
        "tomorrow": "завтра в 09:00",
        "weekend": "в субботу в 11:00",
        "3days": "через 3 дня",
    }
    label = labels.get(period, "в выбранное время")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer(f"⏰ Напоминание установлено на {label}!", show_alert=True)


@router.callback_query(F.data == "close_remind_menu")
async def cb_close_remind_menu(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("show_album:"))
async def cb_show_album(callback: CallbackQuery, session: AsyncSession):
    bookmark_id_str = callback.data.split(":")[1]
    bookmark_id = uuid.UUID(bookmark_id_str)

    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark or not bookmark.photos_json:
        await callback.answer("Медиафайлы не найдены.", show_alert=True)
        return

    try:
        photos = json.loads(bookmark.photos_json)
        if not isinstance(photos, list) or not photos:
            await callback.answer("Медиафайлы не найдены.", show_alert=True)
            return

        media_items: list[tuple[str, str]] = []
        for item in photos[:10]:
            if isinstance(item, dict):
                m_type = item.get("type", "photo")
                m_id = str(item.get("id", ""))
            else:
                m_id = str(item)
                if bookmark.telegram_video_id and (m_id == bookmark.telegram_video_id or not bookmark.telegram_photo_id):
                    m_type = "video"
                else:
                    m_type = "photo"
            if m_id:
                media_items.append((m_type, m_id))

        if not media_items:
            await callback.answer("Медиафайлы не найдены.", show_alert=True)
            return

        # 1. Попытка отправки как медиагруппа с точными типами (фото / видео)
        media_group = []
        for m_type, m_id in media_items:
            if m_type == "video":
                media_group.append(InputMediaVideo(media=m_id))
            else:
                media_group.append(InputMediaPhoto(media=m_id))

        try:
            await callback.message.bot.send_media_group(
                chat_id=callback.message.chat.id,
                media=media_group,
            )
            await callback.answer(f"Отправлено {len(media_group)} медиафайлов")
            return
        except Exception as group_err:
            log.warning(f"send_media_group failed: {group_err}. Retrying individually...")

        # 2. Надёжный фоллбэк: поштучная отправка каждого файла
        sent_count = 0
        for m_type, m_id in media_items:
            try:
                if m_type == "video":
                    await callback.message.bot.send_video(chat_id=callback.message.chat.id, video=m_id)
                else:
                    await callback.message.bot.send_photo(chat_id=callback.message.chat.id, photo=m_id)
                sent_count += 1
            except Exception as e1:
                try:
                    if m_type == "video":
                        await callback.message.bot.send_photo(chat_id=callback.message.chat.id, photo=m_id)
                    else:
                        await callback.message.bot.send_video(chat_id=callback.message.chat.id, video=m_id)
                    sent_count += 1
                except Exception as e2:
                    log.warning(f"Failed to send individual media {m_id}: {e1} / {e2}")

        if sent_count > 0:
            await callback.answer(f"Отправлено {sent_count} медиафайлов")
        else:
            await callback.answer("Не удалось отправить медиафайлы.", show_alert=True)
    except Exception as e:
        log.warning(f"Failed to process album: {e}")
        await callback.answer("Ошибка при отправке альбома.", show_alert=True)


@router.callback_query(F.data.startswith("toggle_read:"))
async def cb_toggle_read(callback: CallbackQuery, session: AsyncSession):
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

    photo_count = 1 if (bookmark.telegram_photo_id or bookmark.image_url or getattr(bookmark, "telegram_video_id", None)) else 0
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list):
                photo_count = len(p_list)
        except Exception:
            pass

    has_file = bool(getattr(bookmark, "telegram_document_id", None))
    is_fav = any(t.name.lower() == "избранное" for t in bookmark.tags)
    kb = get_bookmark_card_keyboard(
        bookmark.id,
        url=bookmark.url,
        is_read=new_status,
        showing_full_text=True,
        nav_prefix=nav_prefix,
        current_page=current_page,
        total_pages=total_pages,
        extra_data=extra_data,
        photo_count=photo_count,
        is_note=(bookmark.content_type == ContentType.NOTE or bool(bookmark.user_comment and bookmark.user_comment.strip())),
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=getattr(bookmark, "is_pinned", False),
        is_long_note=check_is_long_note(bookmark),
    )
    page_info = (current_page, total_pages) if total_pages > 1 else None
    await update_card_message(callback, bookmark, kb, showing_full_text=True, page_info=page_info)

    status_text = "прочитано ✅" if new_status else "не прочитано 📖"
    await callback.answer(f"Отмечено как {status_text}")


@router.callback_query(F.data.startswith("toggle_view:"))
async def cb_toggle_view(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    bookmark_id = uuid.UUID(parts[1])
    mode = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
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

    photo_count = 1 if (bookmark.telegram_photo_id or bookmark.image_url or getattr(bookmark, "telegram_video_id", None)) else 0
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list):
                photo_count = len(p_list)
        except Exception:
            pass

    has_file = bool(getattr(bookmark, "telegram_document_id", None))
    is_fav = any(t.name.lower() == "избранное" for t in bookmark.tags)
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
        is_note=(bookmark.content_type == ContentType.NOTE or bool(bookmark.user_comment and bookmark.user_comment.strip())),
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=getattr(bookmark, "is_pinned", False),
        is_long_note=check_is_long_note(bookmark),
    )
    page_info = (current_page, total_pages) if total_pages > 1 else None
    await update_card_message(callback, bookmark, kb, showing_full_text=showing_full_text, page_info=page_info)
    view_text = "содержание" if showing_full_text else "краткую суть"
    await callback.answer(f"Показано {view_text}")


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

        msg = callback.message
        if msg.photo or msg.video or msg.animation:
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


@router.callback_query(F.data.startswith("dl_doc:"))
async def cb_download_document(callback: CallbackQuery, session: AsyncSession):
    bm_id_str = callback.data.split(":")[1]
    bookmark_id = uuid.UUID(bm_id_str)
    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark or not bookmark.telegram_document_id:
        await callback.answer("Файл архива не найден.", show_alert=True)
        return

    await callback.answer("📥 Отправляю архив...")
    bot = callback.bot
    try:
        clean_title = bookmark.title or "Архив"
        if clean_title.startswith("📦 "):
            clean_title = clean_title[2:].strip()
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=bookmark.telegram_document_id,
            caption=f"📦 {clean_title}"[:1024],
        )
    except Exception as e:
        log.exception(f"Failed to send document {bookmark.telegram_document_id}: {e}")
        await callback.message.answer("⚠️ Не удалось отправить файл из Telegram. Возможно, файл был удалён.")


@router.callback_query(F.data.startswith("pin:"))
async def cb_toggle_pin(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    bm_id = uuid.UUID(parts[1])
    nav_prefix = parts[2] if len(parts) > 2 and parts[2] != "_" else None
    current_page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    total_pages = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 1
    extra_data = parts[5] if len(parts) > 5 and parts[5] != "_" else ""

    repo = BookmarkRepository(session)
    bookmark = await repo.toggle_pin(bm_id, callback.from_user.id)
    if not bookmark:
        await callback.answer("Закладка не найдена.", show_alert=True)
        return

    await session.commit()

    photo_count = 1 if (bookmark.telegram_photo_id or bookmark.image_url or getattr(bookmark, "telegram_video_id", None)) else 0
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list):
                photo_count = len(p_list)
        except Exception:
            pass

    has_file = bool(getattr(bookmark, "telegram_document_id", None))
    is_fav = any(t.name.lower() == "избранное" for t in bookmark.tags)
    is_pinned = getattr(bookmark, "is_pinned", False)

    kb = get_bookmark_card_keyboard(
        bookmark.id,
        url=bookmark.url,
        is_read=bookmark.is_read,
        showing_full_text=True,
        nav_prefix=nav_prefix,
        current_page=current_page,
        total_pages=total_pages,
        extra_data=extra_data,
        photo_count=photo_count,
        is_note=(bookmark.content_type == ContentType.NOTE or bool(bookmark.user_comment and bookmark.user_comment.strip())),
        is_fav=is_fav,
        has_file=has_file,
        is_pinned=is_pinned,
        is_long_note=check_is_long_note(bookmark),
    )
    page_info = (current_page, total_pages) if total_pages > 1 else None
    await update_card_message(callback, bookmark, kb, showing_full_text=True, page_info=page_info)

    if is_pinned:
        await callback.answer("📌 Закреплено! Заметка всегда будет вверху списка.")
    else:
        await callback.answer("📍 Откреплено от верха списка.")


@router.callback_query(F.data.startswith("yoink_v:"))
async def cb_yoink_video_picker(callback: CallbackQuery, session: AsyncSession):
    bm_id_str = callback.data.split(":")[1]
    bookmark_id = uuid.UUID(bm_id_str)
    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark or not bookmark.url:
        await callback.answer("Ссылка на видео не найдена.", show_alert=True)
        return

    if "youtube.com" in bookmark.url.lower() or "youtu.be" in bookmark.url.lower():
        await callback.answer("Скачивание видео с YouTube отключено.", show_alert=True)
        return

    kb = get_video_quality_keyboard(bookmark_id)
    title = bookmark.title or "Видео"
    await callback.message.answer(
        f"🎬 <b>Выберите качество загрузки:</b>\n"
        f"<i>«{html.escape(title[:100])}»</i>\n\n"
        f"💡 <i>Поддерживаются ролики любого размера и длины (большие видео автоматически нарезаются на удобные части для Telegram).</i>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("yq:"))
async def cb_yoink_quality_download(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    quality = parts[1]
    bm_id = uuid.UUID(parts[2])
    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bm_id, callback.from_user.id)
    if not bookmark or not bookmark.url:
        await callback.answer("Ссылка на видео не найдена.", show_alert=True)
        return

    quality_labels = {
        "2160": "💎 4K Ultra HD (2160p)",
        "1440": "🎬 2K Quad HD (1440p)",
        "1080": "1080p (Full HD)",
        "720": "720p (HD)",
        "480": "480p (SD)",
        "360": "360p (Эконом)",
        "auto": "Лучшее (до 50 МБ)",
    }
    label = quality_labels.get(quality, quality)
    await callback.answer(f"🎬 Скачиваю ({label})...")
    status_msg = await callback.message.answer(
        f"⏬ <b>[1/3] Скачивание видео ({label})...</b>\n"
        f"<i>Загружаю видеопоток...</i>",
        parse_mode="HTML",
    )

    await process_and_send_video(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        user_id=callback.from_user.id,
        url=bookmark.url,
        status_msg=status_msg,
        quality=quality,
        session=session,
    )


@router.callback_query(F.data.startswith("back_card:"))
async def cb_back_card(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("yoink_a:"))
async def cb_yoink_audio(callback: CallbackQuery, session: AsyncSession):
    bm_id_str = callback.data.split(":")[1]
    bookmark_id = uuid.UUID(bm_id_str)
    repo = BookmarkRepository(session)
    bookmark = await repo.get_by_id(bookmark_id, callback.from_user.id)
    if not bookmark or not bookmark.url:
        await callback.answer("Ссылка на видео не найдена.", show_alert=True)
        return

    if "youtube.com" in bookmark.url.lower() or "youtu.be" in bookmark.url.lower():
        await callback.answer("Скачивание аудио с YouTube отключено.", show_alert=True)
        return

    await callback.answer("🎵 Yoink! Извлекаю аудио...")
    status_msg = await callback.message.answer(
        "⏳ <b>[Yoink] Извлекаю аудиодорожку (MP3/M4A)...</b>",
        parse_mode="HTML",
    )

    import tempfile
    import shutil
    from aiogram.types import FSInputFile
    from app.services.video_downloader import download_audio

    tmp_dir = tempfile.mkdtemp(prefix="yoinks_a_")
    try:
        file_path, meta, err = await download_audio(bookmark.url, target_dir=tmp_dir, max_mb=50)
        if err or not file_path or not os.path.exists(file_path):
            err_lower = (err or "").lower()
            is_bot_challenge = (
                "bot" in err_lower
                or "sign in" in err_lower
                or "requested format is not available" in err_lower
                or "403: forbidden" in err_lower
                or "no video formats" in err_lower
            )

            web_dl_url = None
            if bookmark.url:
                if "youtube.com" in bookmark.url:
                    web_dl_url = bookmark.url.replace("youtube.com", "ssyoutube.com")
                elif "youtu.be/" in bookmark.url:
                    web_dl_url = bookmark.url.replace("youtu.be/", "ssyoutube.com/watch?v=")
                else:
                    web_dl_url = "https://cobalt.tools"

            buttons = []
            if web_dl_url:
                buttons.append([InlineKeyboardButton(text="🌐 Скачать через веб в 1 клик", url=web_dl_url)])
            if is_bot_challenge:
                buttons.append([InlineKeyboardButton(text="ℹ️ Как подключить cookies в боте", callback_data=f"yoink_cookie_help:{bookmark.id}")])
                msg_body = (
                    "🛡 <b>YouTube ограничил извлечение аудио на сервере:</b>\n"
                    "<i>(YouTube требует подтверждение для IP датацентра)</i>\n\n"
                    "👉 <b>Вы можете скачать аудио или видео в 1 клик через веб:</b>"
                )
            else:
                msg_body = f"⚠️ <b>Не удалось извлечь аудио:</b>\n{html.escape((err or 'Неизвестная ошибка')[:200])}"

            reply_kb = InlineKeyboardMarkup(inline_keyboard=buttons)
            await status_msg.edit_text(
                msg_body,
                reply_markup=reply_kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return

        title = (meta.get("title") if meta else None) or bookmark.title or "Аудиозапись"
        uploader = (meta.get("uploader") if meta else None) or bookmark.author or ""
        caption = f"🎵 <b>{html.escape(title[:200])}</b>"
        if bookmark.url:
            caption += f"\n🔗 {bookmark.url}"

        audio_file = FSInputFile(file_path)
        duration = meta.get("duration") if meta else None

        await callback.message.answer_audio(
            audio=audio_file,
            title=title[:200],
            performer=uploader[:100] if uploader else None,
            caption=caption[:1024],
            parse_mode="HTML",
            duration=int(duration) if duration else None,
        )
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        log.exception(f"Ошибка при yoink аудио: {e}")
        await status_msg.edit_text("⚠️ Ошибка при отправке аудио в Telegram.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.callback_query(F.data.startswith("yoink_cookie_help:"))
async def cb_yoink_cookie_help(callback: CallbackQuery):
    help_text = (
        "🛡 <b>Как включить загрузку YouTube на сервере хостинга:</b>\n\n"
        "Дата-центры (Render, Koyeb, Hetzner, AWS) заблокированы YouTube на уровне IP.\n\n"
        "<b>Два рабочих способа обойти блокировку:</b>\n\n"
        "1️⃣ <b>Способ 1 — Прокси (самый надёжный, 100%):</b>\n"
        "Добавьте в переменные хостинга:\n"
        "<code>YTDLP_PROXY</code> = <code>http://user:pass@ip:port</code>\n"
        "<i>(или socks5://...). Любой мобильный или резидентный прокси полностью снимает все баны YouTube.</i>\n\n"
        "2️⃣ <b>Способ 2 — Cookies браузера:</b>\n"
        "• Установите расширение <b>Get cookies.txt LOCALLY</b> (Chrome / Edge / Firefox).\n"
        "• Откройте <a href='https://youtube.com'>youtube.com</a> $\to$ Export.\n"
        "• Добавьте в переменные хостинга:\n"
        "<code>YOUTUBE_COOKIES</code> = <i>(вставьте всё содержимое файла)</i>\n"
        "<i>(бот теперь автоматически чистит форматирование и табуляции)</i>.\n\n"
        "👉 <i>Также прямо сейчас вы можете скачать ролик в 1 клик по кнопке «Скачать видео через веб».</i>"
    )
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Закрыть", callback_data="delete_status_msg")]]
    )
    await callback.message.edit_text(help_text, reply_markup=back_kb, parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data == "delete_status_msg")
async def cb_delete_status_msg(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.message(Command("yoink", "yoinks", "video"))
async def cmd_yoink(message: Message, session: AsyncSession):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        text = (
            "🎬 <b>Yoinks — скачивание видео и аудио без рекламы</b>\n\n"
            "Отправьте команду со ссылкой на видео:\n"
            "<code>/yoink https://...</code>\n\n"
            "<i>Или просто отправьте ссылку на видео сообщением в чат!</i>\n\n"
            "<b>Поддерживаются платформы:</b>\n"
            "• TikTok\n"
            "• Instagram (Reels, посты)\n"
            "• Twitter / X\n"
            "• Reddit\n"
            "• VK / VK Видео\n"
            "• Rutube\n"
            "• Threads, Vimeo, Twitch и др."
        )
        await message.answer(text, parse_mode="HTML")
        return

    url = args[1].strip()
    if "youtube.com" in url.lower() or "youtu.be" in url.lower():
        await message.answer(
            "ℹ️ <b>Скачивание видеофайлов с YouTube отключено.</b>\n\n"
            "Для YouTube бот создаёт <b>умные текстовые конспекты и субтитры</b>. "
            "Чтобы сохранить саммари ролика в закладки, просто отправьте ссылку в чат без команды /yoink.",
            parse_mode="HTML",
        )
        return
    message.text = url
    await handle_text_or_urls(message, session)



