import uuid
from typing import List, Optional, Tuple
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Bottom persistent Reply Keyboard designed with Zero Guesswork principles."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📥 Мои закладки"),
        KeyboardButton(text="🔍 Поиск"),
    )
    builder.row(
        KeyboardButton(text="📊 Дайджест"),
        KeyboardButton(text="⚙️ Настройки"),
    )
    builder.row(
        KeyboardButton(text="❓ Помощь"),
    )
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def make_safe_callback(prefix: str, *args) -> str:
    """Ensure callback_data never exceeds Telegram's 64-byte limit."""
    candidate = ":".join([prefix] + [str(a) for a in args if a is not None])
    if len(candidate.encode("utf-8")) <= 64:
        return candidate
    # Fallback to minimal essential parts (always <= 50 bytes)
    if args:
        minimal = f"{prefix}:{args[0]}"
        if len(args) > 1 and prefix in ("toggle_view", "op", "sp", "filter_tag"):
            minimal = f"{prefix}:{args[0]}:{args[1]}"
        if len(minimal.encode("utf-8")) <= 64:
            return minimal
    return candidate.encode("utf-8")[:64].decode("utf-8", errors="ignore")


def get_bookmark_card_keyboard(
    bookmark_id: uuid.UUID,
    url: Optional[str] = None,
    is_read: bool = False,
    showing_full_text: bool = False,
    nav_prefix: Optional[str] = None,
    current_page: int = 1,
    total_pages: int = 1,
    extra_data: str = "",
    photo_count: int = 1,
) -> InlineKeyboardMarkup:
    """
    Inline keyboard for bookmark cards with action buttons:
    [⭐ В избранное] [🏷 Изменить теги]
    [💡 Саммари / 📄 Полный текст] [🗑 Удалить]
    [⏰ Напомнить] [🌐 Открыть оригинал]
    """
    builder = InlineKeyboardBuilder()

    # 1. Navigation Row (if browsing list of cards)
    if total_pages > 1 and nav_prefix:
        extra = f":{extra_data}" if extra_data else ""
        nav_buttons: List[InlineKeyboardButton] = []
        if current_page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=make_safe_callback(nav_prefix, current_page - 1, extra_data),
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{current_page} / {total_pages}",
                callback_data="noop",
            )
        )
        if current_page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед »",
                    callback_data=make_safe_callback(nav_prefix, current_page + 1, extra_data),
                )
            )
        builder.row(*nav_buttons)

    # 2. Main actions: Favorite & Tag editing
    np_safe = nav_prefix if nav_prefix else "_"
    ex_safe = extra_data if extra_data else "_"
    fav_cb = make_safe_callback("toggle_fav", bookmark_id)
    tags_cb = make_safe_callback("edit_tags", bookmark_id)

    builder.row(
        InlineKeyboardButton(text="⭐ В избранное", callback_data=fav_cb),
        InlineKeyboardButton(text="🏷 Сменить тег", callback_data=tags_cb),
    )

    # 3. Read status and In-place text toggle (strictly <= 64 bytes)
    status_text = "📖 В непрочитанные" if is_read else "✅ Прочитано"
    status_cb = make_safe_callback("toggle_read", bookmark_id, np_safe, current_page, total_pages, ex_safe)

    view_text_mode = 0 if showing_full_text else 1
    view_label = "💡 Саммари" if showing_full_text else "📄 Полный текст"
    toggle_view_cb = make_safe_callback("toggle_view", bookmark_id, view_text_mode, np_safe, current_page, total_pages, ex_safe)

    builder.row(
        InlineKeyboardButton(text=status_text, callback_data=status_cb),
        InlineKeyboardButton(text=view_label, callback_data=toggle_view_cb),
    )

    # 4. Remind & Delete
    remind_cb = make_safe_callback("remind_menu", bookmark_id)
    delete_cb = make_safe_callback("delete", bookmark_id)
    builder.row(
        InlineKeyboardButton(text="⏰ Напомнить", callback_data=remind_cb),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=delete_cb),
    )

    # 5. External URL and album photos
    ext_row = []
    if url and url.startswith("http"):
        ext_row.append(InlineKeyboardButton(text="🌐 Оригинал", url=url))
    if photo_count > 1:
        ext_row.append(InlineKeyboardButton(text=f"🖼 Все фото ({photo_count})", callback_data=make_safe_callback("show_album", bookmark_id)))
    if ext_row:
        builder.row(*ext_row)

    # 6. Back to list or Next Random button
    if nav_prefix in ("rl", "recent_list", "ul", "unread_list"):
        builder.row(
            InlineKeyboardButton(
                text="« 📋 К списку",
                callback_data=make_safe_callback(nav_prefix, current_page),
            )
        )
    elif nav_prefix in ("rnd", "random"):
        builder.row(
            InlineKeyboardButton(
                text="🎲 Следующая случайная",
                callback_data="next_random",
            )
        )

    return builder.as_markup()


def get_bookmarks_list_keyboard(
    bookmarks: list,
    current_page: int,
    total_pages: int,
    prefix: str = "rl",
) -> InlineKeyboardMarkup:
    """
    Inline keyboard for interactive bookmark selection list:
    [1] [2] [3]... to open article card directly.
    """
    builder = InlineKeyboardBuilder()

    num_buttons = []
    for idx, b in enumerate(bookmarks, start=1):
        num_buttons.append(
            InlineKeyboardButton(
                text=f"[{idx}]",
                callback_data=f"op:{b.id}:{prefix}:{current_page}",
            )
        )
    if num_buttons:
        builder.row(*num_buttons)

    if total_pages > 1:
        nav_buttons = []
        if current_page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"{prefix}:{current_page - 1}",
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{current_page} / {total_pages}",
                callback_data="noop",
            )
        )
        if current_page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед »",
                    callback_data=f"{prefix}:{current_page + 1}",
                )
            )
        builder.row(*nav_buttons)

    if prefix in ("rl", "recent_list"):
        builder.row(
            InlineKeyboardButton(text="📖 Только непрочитанные", callback_data="ul:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )
    elif prefix in ("ul", "unread_list"):
        builder.row(
            InlineKeyboardButton(text="🕒 Все недавние", callback_data="rl:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )

    return builder.as_markup()


def get_tags_keyboard(tags_with_counts: List[Tuple[str, int]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for name, count in tags_with_counts:
        builder.button(
            text=f"#{name} ({count})",
            callback_data=f"filter_tag:{name}:1",
        )
    builder.adjust(2)
    return builder.as_markup()
