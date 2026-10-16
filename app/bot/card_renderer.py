import html
import json
import re
from typing import Optional, Tuple
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message
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


def build_card_text(
    bookmark: Bookmark,
    showing_full_text: bool = False,
    page_info: Optional[Tuple[int, int]] = None,
    for_caption: bool = False,
) -> str:
    header = ""
    if page_info and page_info[1] > 1:
        curr, total = page_info
        header = f"📄 <b>Материал {curr} из {total}</b>\n\n"

    type_badge = get_content_type_badge(bookmark.content_type)
    tags_str = " ".join([f"#{html.escape(t.name)}" for t in bookmark.tags]) if bookmark.tags else ""
    status_icon = "✅ Прочитано" if bookmark.is_read else "📖 Не прочитано"

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

    comment_block = ""
    if bookmark.user_comment and bookmark.user_comment.strip():
        comment_clean = html.escape(bookmark.user_comment.strip())
        comment_block = f"💭 <b>Комментарий:</b>\n<i>{comment_clean}</i>\n\n"

    # ДЛЯ ЗАМЕТОК: оригинальный текст пользователя 1:1, без перевода, сокращений и правок
    if bookmark.content_type == ContentType.NOTE:
        note_body = (bookmark.full_text or bookmark.summary or "").strip()
        title_raw = (bookmark.title or "").strip()
        # Если заголовок совпадает с текстом или его началом, не дублируем его
        show_title = bool(title_raw and not note_body.startswith(title_raw.rstrip(".")))
        title_block = f"📌 <b>{html.escape(title_raw)}</b>\n\n" if show_title else ""

        limit = 750 if for_caption else 3500
        if len(note_body) > limit:
            note_body = note_body[:limit] + "..."

        content_block = f"💬 <b>Заметка:</b>\n{html.escape(note_body)}"

        res = (
            f"{header}"
            f"{meta_line}\n"
            f"{title_block}"
            f"Статус: {status_icon} | {tags_str}\n\n"
            f"{comment_block}"
            f"{content_block}"
        )
    else:
        title_clean = html.escape((bookmark.title or "Без названия").strip())
        if for_caption and len(title_clean) > 160:
            title_clean = title_clean[:160] + "..."

        if showing_full_text:
            body = (bookmark.full_text or "").strip()
            if not body:
                content_block = "📄 <b>Полный текст:</b>\n<i>Текст недоступен или пуст.</i>"
            else:
                limit = 600 if for_caption else 2800
                if len(body) > limit:
                    body = body[:limit] + "...\n\n<i>(текст сокращен)</i>"
                content_block = f"📄 <b>Полный текст:</b>\n{html.escape(body)}"
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
            f"Статус: {status_icon} | {tags_str}\n\n"
            f"{comment_block}"
            f"{content_block}"
        )

    if for_caption and len(res) > 1024:
        res = res[:1015] + "..."
    elif not for_caption and len(res) > 4096:
        res = res[:4080] + "..."

    return res


async def send_card_message(
    bot_or_message,
    chat_id: int,
    bookmark: Bookmark,
    reply_markup: InlineKeyboardMarkup,
    showing_full_text: bool = False,
    page_info: Optional[Tuple[int, int]] = None,
) -> Message:
    bot = bot_or_message if hasattr(bot_or_message, "send_message") else bot_or_message.bot
    video_source = getattr(bookmark, "telegram_video_id", None)
    photo_source = bookmark.telegram_photo_id or bookmark.image_url

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
    showing_full_text: bool = False,
    page_info: Optional[Tuple[int, int]] = None,
):
    video_source = getattr(bookmark, "telegram_video_id", None)
    photo_source = bookmark.telegram_photo_id or bookmark.image_url
    msg = callback.message
    has_photo = bool(msg.photo)
    has_video = bool(msg.video)

    # 1. Если видео
    if video_source:
        caption_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=True
        )
        if has_video:
            try:
                await msg.edit_caption(caption=caption_html, reply_markup=reply_markup, parse_mode="HTML")
                return
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return
                try:
                    await msg.edit_caption(
                        caption=strip_html_tags(caption_html)[:1024],
                        reply_markup=reply_markup,
                        parse_mode=None,
                    )
                    return
                except Exception:
                    pass

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

    # 2. Если фото
    elif photo_source:
        caption_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=True
        )
        if has_photo:
            try:
                await msg.edit_caption(caption=caption_html, reply_markup=reply_markup, parse_mode="HTML")
                return
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return
                try:
                    await msg.edit_caption(
                        caption=strip_html_tags(caption_html)[:1024],
                        reply_markup=reply_markup,
                        parse_mode=None,
                    )
                    return
                except Exception:
                    pass

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

    # 3. Текстовое сообщение
    full_text_html = build_card_text(
        bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=False
    )
    if not (has_photo or has_video):
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
