import html
import json
import re
from typing import Optional, Tuple
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from app.core.logger import log
from app.db.models import Bookmark, ContentType


def get_content_type_badge(c_type: ContentType) -> str:
    badges = {
        ContentType.ARTICLE: "🌐 Статья",
        ContentType.YOUTUBE: "▶️ YouTube",
        ContentType.DOCUMENT: "📄 Документ",
        ContentType.TWEET: "🐦 Пост в X",
        ContentType.NOTE: "💬 Заметка",
    }
    return badges.get(c_type, "📌 Материал")


def strip_html_tags(text: str) -> str:
    """Удаляет все HTML-теги для надежного фоллбэка в plain text."""
    clean = re.sub(r"<[^>]+>", "", text)
    return html.unescape(clean)


def check_is_long_note(bookmark: Bookmark) -> bool:
    has_media = bool(bookmark.telegram_photo_id or getattr(bookmark, "telegram_video_id", None))
    limit = 750 if has_media else 3500
    text_len = len(bookmark.full_text or "")
    comm_len = len(bookmark.user_comment or "")
    return text_len > limit or comm_len > limit


async def _try_edit_caption(msg, caption_html: str, caption_plain: str, reply_markup) -> bool:
    """Меняет только подпись. True — если сообщение обновлено (или уже было таким)."""
    try:
        await msg.edit_caption(caption=caption_html, reply_markup=reply_markup, parse_mode="HTML")
        return True
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return True
    try:
        await msg.edit_caption(caption=caption_plain, reply_markup=reply_markup, parse_mode=None)
        return True
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return True
        log.debug(f"edit_caption failed: {e}")
    return False


async def _try_edit_media(msg, media_html, media_plain, reply_markup) -> bool:
    """Заменяет медиа вместе с подписью. True — если получилось."""
    for media in (media_html, media_plain):
        try:
            await msg.edit_media(media=media, reply_markup=reply_markup)
            return True
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return True
            log.debug(f"edit_media failed: {e}")
    return False


def build_card_text(
    bookmark: Bookmark,
    showing_full_text: bool = True,
    page_info: Optional[Tuple[int, int]] = None,
    for_caption: bool = False,
) -> str:
    header = ""
    if page_info and page_info[1] > 1:
        curr, total = page_info
        header = f"📄 <b>Материал {curr} из {total}</b>\n\n"

    type_badge = get_content_type_badge(bookmark.content_type)
    if getattr(bookmark, "telegram_document_id", None):
        type_badge = "📦 Архив"
    tags_str = " ".join([f"#{html.escape(t.name)}" for t in bookmark.tags]) if bookmark.tags else ""
    tags_part = f" | {tags_str}" if tags_str else ""
    status_icon = "✅ Прочитано" if bookmark.is_read else "📖 Не прочитано"
    pin_badge = "📌 Закреплено | " if getattr(bookmark, "is_pinned", False) else ""
    status_line = f"Статус: {pin_badge}{status_icon}{tags_part}"

    author_clean = html.escape(bookmark.author.strip()) if bookmark.author else ""
    author_str = f" | 👤 {author_clean}" if author_clean else ""
    domain_clean = html.escape(bookmark.domain.strip()) if bookmark.domain else "заметка"
    meta_line = f"{type_badge} | ⏱ ~{bookmark.reading_time_min} мин | 🌐 {domain_clean}{author_str}"

    if getattr(bookmark, "telegram_video_id", None):
        meta_line += " | 🎥 Видео"
    elif bookmark.photos_json:
        try:
            photos = json.loads(bookmark.photos_json)
            if isinstance(photos, list) and len(photos) > 1:
                meta_line += f" | 📷 {len(photos)} фото"
        except Exception:
            pass

    # ДЛЯ ЗАМЕТОК: оригинальный текст пользователя 1:1, без перевода, сокращений и правок
    if bookmark.content_type == ContentType.NOTE:
        from app.extractors.url_utils import normalize_emoji_digits
        note_body = (bookmark.full_text or bookmark.summary or bookmark.user_comment or "").strip()
        note_body = normalize_emoji_digits(note_body)
        title_raw = (bookmark.title or "").strip()
        title_raw = normalize_emoji_digits(title_raw)
        # Если заголовок совпадает с текстом или его началом, не дублируем его
        show_title = bool(title_raw and not note_body.startswith(title_raw.rstrip(".")))
        title_block = f"📌 <b>{html.escape(title_raw)}</b>\n\n" if show_title else ""

        limit = 750 if for_caption else 3500
        if len(note_body) > limit:
            note_body = note_body[:limit] + "...\n\n<i>(заметка длинная, нажмите «📖 Читать полностью» ниже)</i>"

        content_block = f"💬 <b>Заметка:</b>\n{html.escape(note_body)}"

        res = (
            f"{header}"
            f"{meta_line}\n"
            f"{title_block}"
            f"{status_line}\n\n"
            f"{content_block}"
        )
    else:
        title_clean = html.escape((bookmark.title or "Без названия").strip())
        if for_caption and len(title_clean) > 160:
            title_clean = title_clean[:160] + "..."

        # Если у материала есть комментарий пользователя / сохраненный текст поста:
        # показываем именно заметку пользователя, а не простыню спарсенного ИИ текста!
        if bookmark.user_comment and bookmark.user_comment.strip():
            comment_body = bookmark.user_comment.strip()
            limit = 650 if for_caption else 3300
            if len(comment_body) > limit:
                comment_body = comment_body[:limit] + "...\n\n<i>(заметка длинная, нажмите «📖 Читать полностью» ниже)</i>"

            if showing_full_text:
                content_block = f"💬 <b>Заметка:</b>\n{html.escape(comment_body)}"
            else:
                summary = (bookmark.summary or "").strip()
                if not summary:
                    summary = (bookmark.full_text or "").strip()
                if len(summary) > (650 if for_caption else 2500):
                    summary = summary[:(650 if for_caption else 2500)] + "..."
                content_block = f"💡 <b>Краткая суть статьи:</b>\n{html.escape(summary)}" if summary else f"💬 <b>Заметка:</b>\n{html.escape(comment_body)}"

            res = (
                f"{header}"
                f"{meta_line}\n"
                f"📌 <b>{title_clean}</b>\n\n"
                f"{status_line}\n\n"
                f"{content_block}"
            )
        else:
            # Обычная статья по ссылке без комментария
            if showing_full_text:
                body = (bookmark.full_text or bookmark.summary or "").strip()
                if not body:
                    content_block = "📄 <b>Содержание:</b>\n<i>Содержимое пусто или недоступно.</i>"
                else:
                    limit = 650 if for_caption else 3300
                    if len(body) > limit:
                        body = body[:limit] + "...\n\n<i>(показана основная часть)</i>"
                    content_block = f"📄 <b>Содержание:</b>\n{html.escape(body)}"
            else:
                summary = (bookmark.summary or "").strip()
                if not summary:
                    content_block = "💡 <b>Краткая суть:</b>\n<i>Краткая выжимка отсутствует.</i>"
                else:
                    limit = 650 if for_caption else 2500
                    if len(summary) > limit:
                        summary = summary[:limit] + "..."
                    content_block = f"💡 <b>Краткая суть:</b>\n{html.escape(summary)}"

            res = (
                f"{header}"
                f"{meta_line}\n"
                f"📌 <b>{title_clean}</b>\n\n"
                f"{status_line}\n\n"
                f"{content_block}"
            )

    return res


def get_primary_photo_source(bookmark: Bookmark) -> Optional[str]:
    """Возвращает ID или URL главного изображения закладки (с учетом photos_json)."""
    if bookmark.telegram_photo_id:
        return bookmark.telegram_photo_id
    if bookmark.photos_json:
        try:
            p_list = json.loads(bookmark.photos_json)
            if isinstance(p_list, list) and p_list:
                return p_list[0]
        except Exception:
            pass
    return bookmark.image_url


async def send_card_message(
    bot_or_message,
    chat_id: int,
    bookmark: Bookmark,
    reply_markup: InlineKeyboardMarkup,
    showing_full_text: bool = True,
    page_info: Optional[Tuple[int, int]] = None,
) -> Message:
    bot = bot_or_message if hasattr(bot_or_message, "send_message") else bot_or_message.bot
    video_source = getattr(bookmark, "telegram_video_id", None)
    photo_source = get_primary_photo_source(bookmark)

    # 1. Если это ВИДЕО
    if video_source:
        caption_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=True
        )
        try:
            return await bot.send_video(
                chat_id=chat_id,
                video=video_source,
                caption=caption_html,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as e:
            log.debug(f"send_video HTML failed: {e}. Trying plain text.")
            try:
                return await bot.send_video(
                    chat_id=chat_id,
                    video=video_source,
                    caption=strip_html_tags(caption_html)[:1024],
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            except Exception:
                pass

    # 2. Если это ФОТО
    if photo_source:
        caption_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=True
        )
        try:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=photo_source,
                caption=caption_html,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as e:
            log.debug(f"send_photo HTML failed: {e}. Trying plain text.")
            try:
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_source,
                    caption=strip_html_tags(caption_html)[:1024],
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            except Exception:
                pass

    # 3. Текстовое сообщение
    full_text_html = build_card_text(
        bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=False
    )
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=full_text_html,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        return await bot.send_message(
            chat_id=chat_id,
            text=strip_html_tags(full_text_html)[:4096],
            reply_markup=reply_markup,
            parse_mode=None,
            disable_web_page_preview=True,
        )


async def update_card_message(
    callback: CallbackQuery,
    bookmark: Bookmark,
    reply_markup: InlineKeyboardMarkup,
    showing_full_text: bool = True,
    page_info: Optional[Tuple[int, int]] = None,
):
    video_source = getattr(bookmark, "telegram_video_id", None)
    photo_source = get_primary_photo_source(bookmark)
    msg = callback.message
    has_photo = bool(msg.photo)
    has_video = bool(msg.video)

    # 1. Если у закладки ВИДЕО
    if video_source:
        caption_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=True
        )
        caption_plain = strip_html_tags(caption_html)[:1024]

        if has_video:
            # То же видео — обновляем только подпись
            if video_source == getattr(msg.video, "file_id", None):
                if await _try_edit_caption(msg, caption_html, caption_plain, reply_markup):
                    return
            # Другое видео — edit_media (меняется видео и подпись)
            elif await _try_edit_media(
                msg,
                InputMediaVideo(media=video_source, caption=caption_html, parse_mode="HTML"),
                InputMediaVideo(media=video_source, caption=caption_plain, parse_mode=None),
                reply_markup,
            ):
                return
        elif has_photo:
            # Фото -> видео через edit_media
            if await _try_edit_media(
                msg,
                InputMediaVideo(media=video_source, caption=caption_html, parse_mode="HTML"),
                InputMediaVideo(media=video_source, caption=caption_plain, parse_mode=None),
                reply_markup,
            ):
                return

        # Текст -> видео (или fallback)
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await msg.answer_video(
                video=video_source,
                caption=caption_html,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    # 2. Если у закладки ФОТО (прикрепленная фотография / фото-заметка)
    elif photo_source:
        caption_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=True
        )
        caption_plain = strip_html_tags(caption_html)[:1024]

        if has_photo:
            # Та же картинка (Прочитано / Саммари / В избранное) — только подпись
            if photo_source == msg.photo[-1].file_id:
                if await _try_edit_caption(msg, caption_html, caption_plain, reply_markup):
                    return
            # Другая картинка (листание фото -> фото) — edit_media (меняется и фото, и подпись)
            elif await _try_edit_media(
                msg,
                InputMediaPhoto(media=photo_source, caption=caption_html, parse_mode="HTML"),
                InputMediaPhoto(media=photo_source, caption=caption_plain, parse_mode=None),
                reply_markup,
            ):
                return
        elif has_video:
            # Видео -> фото через edit_media
            if await _try_edit_media(
                msg,
                InputMediaPhoto(media=photo_source, caption=caption_html, parse_mode="HTML"),
                InputMediaPhoto(media=photo_source, caption=caption_plain, parse_mode=None),
                reply_markup,
            ):
                return

        # Текст -> фото (открытие заметки с фото из списка или при листании):
        # Обязательно отображаем прикрепленное фото через answer_photo
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await msg.answer_photo(
                photo=photo_source,
                caption=caption_html,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    # 3. Текстовое сообщение (заметка или статья без фото/видео)
    full_text_html = build_card_text(
        bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=False
    )
    if not (has_photo or has_video):
        # Текст -> текст: обновляем прямо на месте через edit_text
        try:
            await msg.edit_text(
                text=full_text_html,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return
            try:
                await msg.edit_text(
                    text=strip_html_tags(full_text_html)[:4096],
                    reply_markup=reply_markup,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
                return
            except Exception:
                pass

    # Фото -> текст (переход на текстовую заметку без фото):
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        await msg.answer(
            text=full_text_html,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        await msg.answer(
            text=strip_html_tags(full_text_html)[:4096],
            reply_markup=reply_markup,
            parse_mode=None,
            disable_web_page_preview=True,
        )
