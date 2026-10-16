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
    """Reply keyboard displayed at the bottom of the chat (collapsible)."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="➕ Добавить статью"),
        KeyboardButton(text="🔍 Поиск"),
    )
    builder.row(
        KeyboardButton(text="🕒 Недавние"),
        KeyboardButton(text="📖 Непрочитанные"),
    )
    builder.row(
        KeyboardButton(text="🏷 Теги"),
        KeyboardButton(text="🎲 Случайная"),
    )
    builder.row(
        KeyboardButton(text="📊 Статистика"),
        KeyboardButton(text="❓ Помощь"),
    )
    builder.row(
        KeyboardButton(text="🔽 Скрыть меню"),
    )
    return builder.as_markup(resize_keyboard=True, is_persistent=False)


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
    Inline keyboard for bookmark cards with integrated pagination,
    in-place text/summary toggle, read status toggle, and delete button.
    """
    builder = InlineKeyboardBuilder()

    # 1. Navigation Row directly in the card (if multiple pages)
    if total_pages > 1 and nav_prefix:
        extra = f":{extra_data}" if extra_data else ""
        nav_buttons: List[InlineKeyboardButton] = []
        if current_page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="« Назад",
                    callback_data=f"{nav_prefix}:{current_page - 1}{extra}",
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
                    callback_data=f"{nav_prefix}:{current_page + 1}{extra}",
                )
            )
        builder.row(*nav_buttons)

    # 2. Link to original source & album photos button
    action_row = []
    if url and url.startswith("http"):
        action_row.append(InlineKeyboardButton(text="🌐 Открыть оригинал", url=url))
    if photo_count > 1:
        action_row.append(InlineKeyboardButton(text=f"🖼 Все фото ({photo_count})", callback_data=f"show_album:{bookmark_id}"))
    if action_row:
        builder.row(*action_row)

    # 3. Read status and Tag editing
    status_text = "📖 В непрочитанные" if is_read else "✅ Прочитано"
    np_safe = nav_prefix if nav_prefix else "_"
    ex_safe = extra_data if extra_data else "_"
    status_cb = f"toggle_read:{bookmark_id}:{np_safe}:{current_page}:{total_pages}:{ex_safe}"
    tags_cb = f"edit_tags:{bookmark_id}"

    builder.row(
        InlineKeyboardButton(text=status_text, callback_data=status_cb),
        InlineKeyboardButton(text="🏷 Изменить теги", callback_data=tags_cb),
    )

    # 4. In-place full text / summary toggle & Delete
    view_text_mode = 0 if showing_full_text else 1
    view_label = "💡 Саммари" if showing_full_text else "📄 Полный текст"
    toggle_view_cb = f"toggle_view:{bookmark_id}:{view_text_mode}:{np_safe}:{current_page}:{total_pages}:{ex_safe}"

    builder.row(
        InlineKeyboardButton(text=view_label, callback_data=toggle_view_cb),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{bookmark_id}"),
    )

    # 5. Back to list or Next Random button
    if nav_prefix in ("recent_list", "unread_list"):
        builder.row(
            InlineKeyboardButton(
                text="« 📋 Назад к списку",
                callback_data=f"{nav_prefix}:{current_page}",
            )
        )
    elif nav_prefix == "random":
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
    prefix: str = "recent_list",
) -> InlineKeyboardMarkup:
    """
    Inline keyboard for interactive bookmark selection list:
    Row of numbers [1] [2] [3]... to open each article card directly,
    plus pagination and quick filters.
    """
    builder = InlineKeyboardBuilder()

    # 1. Number selection buttons
    num_buttons = []
    for idx, b in enumerate(bookmarks, start=1):
        num_buttons.append(
            InlineKeyboardButton(
                text=f"[{idx}]",
                callback_data=f"open_bm:{b.id}:{prefix}:{current_page}",
            )
        )
    if num_buttons:
        builder.row(*num_buttons)

    # 2. Pagination row
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

    # 3. Quick mode switcher
    if prefix == "recent_list":
        builder.row(
            InlineKeyboardButton(text="📖 Только непрочитанные", callback_data="unread_list:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )
    elif prefix == "unread_list":
        builder.row(
            InlineKeyboardButton(text="🕒 Все недавние", callback_data="recent_list:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )

    return builder.as_markup()


def get_pagination_keyboard(
    prefix: str,
    current_page: int,
    total_pages: int,
    extra_data: str = "",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    buttons: List[InlineKeyboardButton] = []
    extra = f":{extra_data}" if extra_data else ""

    if current_page > 1:
        buttons.append(
            InlineKeyboardButton(
                text="« Назад",
                callback_data=f"{prefix}:{current_page - 1}{extra}",
            )
        )

    buttons.append(
        InlineKeyboardButton(
            text=f"{current_page} / {max(1, total_pages)}",
            callback_data="noop",
        )
    )

    if current_page < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="Вперед »",
                callback_data=f"{prefix}:{current_page + 1}{extra}",
            )
        )

    builder.row(*buttons)
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
