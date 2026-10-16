import html
import json
import re
from typing import Optional, Tuple
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message
from app.core.logger import log
from app.db.models import Bookmark


def strip_html_tags(text: str) -> str:
    """Strip all HTML tags to produce pure plain text."""
    clean = re.sub(r"<[^>]+>", "", text)
    return html.unescape(clean)


def build_card_text(
    bookmark: Bookmark,
    showing_full_text: bool = False,
    page_info: Optional[Tuple[int, int]] = None,
    for_caption: bool = False,
) -> str:
    """Format bookmark display text with summary or full text safely escaping HTML."""
    header = ""
    if page_info and page_info[1] > 1:
        curr, total = page_info
        header = f"📄 <b>Материал {curr} из {total}</b>\n\n"

    tags_str = " ".join([f"#{html.escape(t.name)}" for t in bookmark.tags]) if bookmark.tags else ""
    status_icon = "✅ Прочитано" if bookmark.is_read else "📖 Не прочитано"

    author_clean = html.escape(bookmark.author.strip()) if bookmark.author else ""
    author_str = f" | 👤 {author_clean}" if author_clean else ""
    domain_clean = html.escape(bookmark.domain.strip()) if bookmark.domain else "заметка"
    meta_line = f"🌐 {domain_clean}{author_str} | ⏱ ~{bookmark.reading_time_min} мин."

    if bookmark.photos_json:
        try:
            photos = json.loads(bookmark.photos_json)
            if isinstance(photos, list) and len(photos) > 1:
                meta_line += f" | 📷 {len(photos)} фото"
        except Exception:
            pass

    title_clean = html.escape((bookmark.title or "Без названия").strip())
    if for_caption and len(title_clean) > 180:
        title_clean = title_clean[:180] + "..."

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
            content_block = "💡 <b>Краткое саммари:</b>\n<i>Краткое саммари отсутствует.</i>"
        else:
            limit = 600 if for_caption else 2500
            if len(summary) > limit:
                summary = summary[:limit] + "..."
            content_block = f"💡 <b>Краткое саммари:</b>\n{html.escape(summary)}"

    res = (
        f"{header}"
        f"📌 <b>{title_clean}</b>\n"
        f"{meta_line}\n"
        f"Статус: {status_icon} {tags_str}\n\n"
        f"{content_block}"
    )

    if for_caption and len(res) > 1024:
        res = res[:1020]
    elif not for_caption and len(res) > 4096:
        res = res[:4090]

    return res


async def send_card_message(
    bot_or_message,
    chat_id: int,
    bookmark: Bookmark,
    reply_markup: InlineKeyboardMarkup,
    showing_full_text: bool = False,
    page_info: Optional[Tuple[int, int]] = None,
) -> Message:
    """Send a bookmark card as photo or text with foolproof HTML & plain-text fallbacks."""
    bot = bot_or_message if hasattr(bot_or_message, "send_message") else bot_or_message.bot
    photo_source = bookmark.telegram_photo_id or bookmark.image_url

    # 1. Try sending with photo if available
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
            log.warning(f"send_photo HTML failed: {e}. Retrying photo with plain text.")
            try:
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_source,
                    caption=strip_html_tags(caption_html)[:1024],
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
            except Exception as e2:
                log.warning(f"send_photo plain failed: {e2}. Falling back to text message.")

    # 2. Text message mode (photo missing or photo send failed)
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
    except Exception as e:
        log.warning(f"send_message HTML failed: {e}. Retrying send_message with plain text.")
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
    """
    Update the current message in-place without creating new spam messages.
    Smoothly switches between text, captions, and media with complete error recovery.
    """
    photo_source = bookmark.telegram_photo_id or bookmark.image_url
    msg = callback.message
    has_photo = bool(msg.photo)

    if photo_source:
        caption_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=True
        )
        if has_photo:
            try:
                await msg.edit_media(
                    media=InputMediaPhoto(media=photo_source, caption=caption_html, parse_mode="HTML"),
                    reply_markup=reply_markup,
                )
                return
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    return
                log.debug(f"edit_media HTML failed: {e}. Trying plain text.")
                try:
                    await msg.edit_media(
                        media=InputMediaPhoto(
                            media=photo_source,
                            caption=strip_html_tags(caption_html)[:1024],
                            parse_mode=None,
                        ),
                        reply_markup=reply_markup,
                    )
                    return
                except Exception as e2:
                    log.debug(f"edit_media plain failed: {e2}")

        # Replace message with photo card
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
        except Exception:
            await msg.answer_photo(
                photo=photo_source,
                caption=strip_html_tags(caption_html)[:1024],
                reply_markup=reply_markup,
                parse_mode=None,
            )
    else:
        full_text_html = build_card_text(
            bookmark, showing_full_text=showing_full_text, page_info=page_info, for_caption=False
        )
        if not has_photo:
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
                log.debug(f"edit_text HTML failed: {e}. Trying plain text.")
                try:
                    await msg.edit_text(
                        text=strip_html_tags(full_text_html)[:4096],
                        reply_markup=reply_markup,
                        parse_mode=None,
                        disable_web_page_preview=True,
                    )
                    return
                except Exception as e2:
                    log.debug(f"edit_text plain failed: {e2}")

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
