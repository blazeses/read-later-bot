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
    """Bottom Reply Keyboard: collapsible (non-persistent) with note upload and hide button."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📥 Мои закладки"),
        KeyboardButton(text="⭐ Избранное"),
    )
    builder.row(
        KeyboardButton(text="🔍 Поиск"),
        KeyboardButton(text="➕ Загрузить заметку"),
    )
    builder.row(
        KeyboardButton(text="📊 Дайджест"),
        KeyboardButton(text="⚙️ Настройки"),
    )
    builder.row(
        KeyboardButton(text="❓ Помощь"),
        KeyboardButton(text="🔽 Скрыть меню"),
    )
    return builder.as_markup(resize_keyboard=True, is_persistent=False)


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
    showing_full_text: bool = True,
    nav_prefix: Optional[str] = None,
    current_page: int = 1,
    total_pages: int = 1,
    extra_data: str = "",
    photo_count: int = 1,
    is_note: bool = False,
    is_fav: bool = False,
    has_file: bool = False,
    is_pinned: bool = False,
    is_long_note: bool = False,
) -> InlineKeyboardMarkup:
    """
    Inline keyboard for bookmark cards with action buttons:
    [⭐ В избранное / 🌟 В избранном] [🏷 Изменить теги]
    [✅ Прочитано] [💡 Саммари / 📄 Содержание]
    [⏰ Напомнить] [🗑 Удалить]
    [🌐 Открыть оригинал]
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
                callback_data=make_safe_callback("jump_c", nav_prefix, total_pages, current_page, extra_data or "_"),
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

    # 2. Main actions: Favorite, Pin & Tag editing
    np_safe = nav_prefix if nav_prefix else "_"
    ex_safe = extra_data if extra_data else "_"
    fav_text = "🌟 В избранном" if is_fav else "⭐ В избранное"
    fav_cb = make_safe_callback("toggle_fav", bookmark_id, np_safe, current_page, total_pages, ex_safe)

    pin_text = "📍 Открепить" if is_pinned else "📌 Закрепить"
    pin_cb = make_safe_callback("pin", bookmark_id, np_safe, current_page, total_pages, ex_safe)

    tags_cb = make_safe_callback("edit_tags", bookmark_id)

    builder.row(
        InlineKeyboardButton(text=fav_text, callback_data=fav_cb),
        InlineKeyboardButton(text=pin_text, callback_data=pin_cb),
    )

    # 3. Read status and In-place text toggle (strictly <= 64 bytes)
    status_text = "📖 В непрочитанные" if is_read else "✅ Прочитано"
    status_cb = make_safe_callback("toggle_read", bookmark_id, np_safe, current_page, total_pages, ex_safe)
    remind_cb = make_safe_callback("remind_menu", bookmark_id)
    delete_cb = make_safe_callback("delete", bookmark_id)

    if is_note:
        edit_note_cb = make_safe_callback("edit_note", bookmark_id)
        builder.row(
            InlineKeyboardButton(text=status_text, callback_data=status_cb),
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=edit_note_cb),
        )
        if is_long_note:
            full_note_cb = make_safe_callback("full_note", bookmark_id)
            builder.row(
                InlineKeyboardButton(text="📖 Читать полностью", callback_data=full_note_cb),
            )
        builder.row(
            InlineKeyboardButton(text="🏷 Сменить тег", callback_data=tags_cb),
            InlineKeyboardButton(text="⏰ Напомнить", callback_data=remind_cb),
        )
        builder.row(
            InlineKeyboardButton(text="🗑 Удалить", callback_data=delete_cb),
        )
    else:
        view_text_mode = 0 if showing_full_text else 1
        view_label = "💡 Саммари" if showing_full_text else "📄 Содержание"
        toggle_view_cb = make_safe_callback("toggle_view", bookmark_id, view_text_mode, np_safe, current_page, total_pages, ex_safe)
        builder.row(
            InlineKeyboardButton(text=status_text, callback_data=status_cb),
            InlineKeyboardButton(text=view_label, callback_data=toggle_view_cb),
        )
        builder.row(
            InlineKeyboardButton(text="🏷 Сменить тег", callback_data=tags_cb),
            InlineKeyboardButton(text="⏰ Напомнить", callback_data=remind_cb),
        )
        builder.row(
            InlineKeyboardButton(text="🗑 Удалить", callback_data=delete_cb),
        )

    # 5. External URL, album photos, download file
    ext_row = []
    if has_file:
        ext_row.append(InlineKeyboardButton(text="📥 Скачать архив", callback_data=make_safe_callback("dl_doc", bookmark_id)))
    if url and url.startswith("http"):
        ext_row.append(InlineKeyboardButton(text="🌐 Оригинал", url=url))
    if photo_count > 1:
        ext_row.append(InlineKeyboardButton(text=f"📁 Альбом ({photo_count})", callback_data=make_safe_callback("show_album", bookmark_id)))
    if ext_row:
        builder.row(*ext_row)

    # 6. Back to list, tags, or Next Random button
    if nav_prefix in ("rl", "recent_list", "ul", "unread_list", "pl", "pinned_list", "fl", "fav_list"):
        builder.row(
            InlineKeyboardButton(
                text="« 📋 К списку",
                callback_data=make_safe_callback(nav_prefix, current_page),
            )
        )
    elif nav_prefix == "filter_tag":
        builder.row(
            InlineKeyboardButton(
                text="« 🏷 К списку тегов",
                callback_data="open_tags_menu",
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
                callback_data=f"jump_p:{prefix}:{total_pages}:{current_page}",
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

    if prefix in ("fl", "fav_list"):
        builder.row(
            InlineKeyboardButton(text="🕒 Все недавние", callback_data="rl:1"),
            InlineKeyboardButton(text="📌 Закрепленные", callback_data="pl:1"),
        )
        builder.row(
            InlineKeyboardButton(text="📖 Непрочитанные", callback_data="ul:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )
    elif prefix in ("rl", "recent_list"):
        builder.row(
            InlineKeyboardButton(text="⭐ Избранное", callback_data="fl:1"),
            InlineKeyboardButton(text="📌 Закрепленные", callback_data="pl:1"),
        )
        builder.row(
            InlineKeyboardButton(text="📖 Только непрочитанные", callback_data="ul:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )
    elif prefix in ("pl", "pinned_list"):
        builder.row(
            InlineKeyboardButton(text="⭐ Избранное", callback_data="fl:1"),
            InlineKeyboardButton(text="🕒 Все недавние", callback_data="rl:1"),
        )
        builder.row(
            InlineKeyboardButton(text="📖 Непрочитанные", callback_data="ul:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )
    elif prefix in ("ul", "unread_list"):
        builder.row(
            InlineKeyboardButton(text="⭐ Избранное", callback_data="fl:1"),
            InlineKeyboardButton(text="🕒 Все недавние", callback_data="rl:1"),
        )
        builder.row(
            InlineKeyboardButton(text="📌 Закрепленные", callback_data="pl:1"),
            InlineKeyboardButton(text="🎲 Случайная", callback_data="next_random"),
        )

    return builder.as_markup()


def get_tags_keyboard(tags_with_counts: List[Tuple[str, int]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for name, count in tags_with_counts:
        safe_name = name[:26]
        builder.button(
            text=f"#{name} ({count})",
            callback_data=f"filter_tag:{safe_name}:1",
        )
    builder.adjust(2)
    return builder.as_markup()


def get_page_picker_keyboard(
    total_pages: int,
    current_page: int,
    prefix: str,
    extra_data: str = "",
    is_card: bool = False,
) -> InlineKeyboardMarkup:
    """Inline keyboard for jumping directly to a selected page."""
    builder = InlineKeyboardBuilder()

    if total_pages <= 25:
        pages = list(range(1, total_pages + 1))
    else:
        step = 5 if total_pages <= 60 else 10
        page_set = {1, total_pages}
        for p in range(current_page - 3, current_page + 4):
            if 1 <= p <= total_pages:
                page_set.add(p)
        for p in range(step, total_pages, step):
            page_set.add(p)
        pages = sorted(page_set)

    for p in pages:
        text = f"• {p} •" if p == current_page else str(p)
        if is_card:
            if prefix == "filter_tag":
                cb = make_safe_callback("filter_tag", extra_data, p)
            else:
                cb = make_safe_callback(prefix, p, extra_data if extra_data != "_" else None)
        else:
            cb = f"{prefix}:{p}"
        builder.button(text=text, callback_data=cb)

    builder.adjust(5)

    if is_card:
        if prefix == "filter_tag":
            cancel_cb = make_safe_callback("filter_tag", extra_data, current_page)
        else:
            cancel_cb = make_safe_callback(prefix, current_page, extra_data if extra_data != "_" else None)
    else:
        cancel_cb = f"{prefix}:{current_page}"

    builder.row(InlineKeyboardButton(text="« Отмена", callback_data=cancel_cb))
    return builder.as_markup()
