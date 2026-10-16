from typing import Optional, Tuple
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto, Message
from app.core.logger import log
from app.db.models import Bookmark


def build_card_text(
    bookmark: Bookmark,
    showing_full_text: bool = False,
    page_info: Optional[Tuple[int, int]] = None,
) -> str:
    """Format bookmark display text with summary or full text."""
    header = ""
    if page_info and page_info[1] > 1:
        curr, total = page_info
        header = f"📄 <b>Материал {curr} из {total}</b>\n\n"

    tags_str = " ".join([f"#{t.name}" for t in bookmark.tags]) if bookmark.tags else ""
    status_icon = "✅ Прочитано" if bookmark.is_read else "📖 Не прочитано"
    author_str = f" | 👤 {bookmark.author}" if bookmark.author else ""
    meta_line = f"🌐 {bookmark.domain or 'заметка'}{author_str} | ⏱ ~{bookmark.reading_time_min} мин."

    if bookmark.photos_json:
        try:
            import json
            photos = json.loads(bookmark.photos_json)
            if isinstance(photos, list) and len(photos) > 1:
                meta_line += f" | 📷 {len(photos)} фото"
        except Exception:
            pass

    if showing_full_text:
        body = (bookmark.full_text or "").strip()
        if not body:
            body = "<i>Текст недоступен или пуст.</i>"
        elif len(body) > 2800:
            body = body[:2800] + "\n\n<i>... (текст сокращен)</i>"
        content_block = f"📄 <b>Полный текст:</b>\n{body}"
    else:
        summary = (bookmark.summary or "").strip()
        if not summary:
            summary = "<i>Краткое саммари отсутствует.</i>"
        content_block = f"💡 <b>Краткое саммари:</b>\n{summary}"

    return (
        f"{header}"
        f"📌 <b>{bookmark.title or 'Без названия'}</b>\n"
        f"{meta_line}\n"
        f"Статус: {status_icon} {tags_str}\n\n"
        f"{content_block}"
    )


async def send_card_message(
    bot_or_message,
    chat_id: int,
    bookmark: Bookmark,
    reply_markup: InlineKeyboardMarkup,
    showing_full_text: bool = False,
    page_info: Optional[Tuple[int, int]] = None,
) -> Message:
    """Send a bookmark card as photo or text."""
    text = build_card_text(bookmark, showing_full_text=showing_full_text, page_info=page_info)
    photo_source = bookmark.telegram_photo_id or bookmark.image_url

    # Helper to resolve bot instance
    bot = bot_or_message if hasattr(bot_or_message, "send_message") else bot_or_message.bot

    if photo_source:
        try:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=photo_source,
                caption=text[:1024],
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as e:
            log.warning(f"Failed to send card photo: {e}. Falling back to text message.")

    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
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
    Smoothly switches between text, captions, and media.
    """
    text = build_card_text(bookmark, showing_full_text=showing_full_text, page_info=page_info)
    photo_source = bookmark.telegram_photo_id or bookmark.image_url
    msg = callback.message
    has_photo = bool(msg.photo)

    if photo_source:
        # Destination requires a photo
        if has_photo:
            try:
                await msg.edit_media(
                    media=InputMediaPhoto(media=photo_source, caption=text[:1024], parse_mode="HTML"),
                    reply_markup=reply_markup,
                )
                return
            except Exception as e:
                log.debug(f"edit_media failed: {e}")
        # Otherwise replace message with photo card
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer_photo(
            photo=photo_source,
            caption=text[:1024],
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    else:
        # Destination is text only
        if not has_photo:
            try:
                await msg.edit_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                return
            except Exception as e:
                # If content did not change, ignore Telegram API 400 error
                if "message is not modified" in str(e).lower():
                    return
                log.debug(f"edit_text failed: {e}")
        # If old message was a photo, replace with text message
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
