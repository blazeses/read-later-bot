import asyncio
import sys
import uuid
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock aiogram if needed
try:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
except ImportError:
    class InlineKeyboardButton:
        def __init__(self, text, callback_data=None, url=None):
            self.text = text
            self.callback_data = callback_data
            self.url = url

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard=None):
            self.inline_keyboard = inline_keyboard or []

    class KeyboardButton:
        def __init__(self, text):
            self.text = text

    class ReplyKeyboardMarkup:
        def __init__(self, keyboard=None, resize_keyboard=True, is_persistent=False):
            self.keyboard = keyboard or []

    class InlineKeyboardBuilder:
        def __init__(self):
            self._rows = []

        def row(self, *buttons):
            self._rows.append(list(buttons))

        def button(self, text, callback_data=None, url=None):
            if not self._rows:
                self._rows.append([])
            self._rows[-1].append(InlineKeyboardButton(text=text, callback_data=callback_data, url=url))

        def as_markup(self):
            return InlineKeyboardMarkup(inline_keyboard=self._rows)

    class ReplyKeyboardBuilder:
        def __init__(self):
            self._rows = []

        def row(self, *buttons):
            self._rows.append(list(buttons))

        def as_markup(self, **kwargs):
            return ReplyKeyboardMarkup(keyboard=self._rows, **kwargs)

    sys.modules["aiogram"] = MagicMock()
    sys.modules["aiogram.types"] = MagicMock(
        InlineKeyboardButton=InlineKeyboardButton,
        InlineKeyboardMarkup=InlineKeyboardMarkup,
        KeyboardButton=KeyboardButton,
        ReplyKeyboardMarkup=ReplyKeyboardMarkup,
        BufferedInputFile=MagicMock(),
    )
    sys.modules["aiogram.utils"] = MagicMock()
    sys.modules["aiogram.utils.keyboard"] = MagicMock(
        InlineKeyboardBuilder=InlineKeyboardBuilder,
        ReplyKeyboardBuilder=ReplyKeyboardBuilder,
    )
    class State:
        def __init__(self, *args, **kwargs):
            pass
    class StatesGroup:
        pass
    mock_fsm_state = MagicMock()
    mock_fsm_state.State = State
    mock_fsm_state.StatesGroup = StatesGroup
    sys.modules["aiogram.fsm"] = MagicMock()
    sys.modules["aiogram.fsm.state"] = mock_fsm_state
    sys.modules["aiogram.fsm.context"] = MagicMock()
    sys.modules["aiogram.filters"] = MagicMock()
    sys.modules["sqlalchemy"] = MagicMock()
    sys.modules["sqlalchemy.ext"] = MagicMock()
    sys.modules["sqlalchemy.ext.asyncio"] = MagicMock()
    sys.modules["sqlalchemy.orm"] = MagicMock()


class ContentType(str, Enum):
    ARTICLE = "article"
    TWEET = "tweet"
    YOUTUBE = "youtube"
    DOCUMENT = "document"
    NOTE = "note"


class MockTag:
    def __init__(self, name):
        self.name = name


class MockBookmark:
    def __init__(
        self,
        id=None,
        user_id=1,
        content_type=ContentType.NOTE,
        title="Заметка",
        full_text="",
        summary="",
        user_comment="",
        tags=None,
        is_pinned=False,
        is_read=False,
        telegram_photo_id=None,
        telegram_video_id=None,
        telegram_document_id=None,
        photos_json=None,
        url=None,
        domain="note",
        author=None,
        reading_time_min=1,
    ):
        self.id = id or uuid.uuid4()
        self.user_id = user_id
        self.content_type = content_type
        self.title = title
        self.full_text = full_text
        self.summary = summary
        self.user_comment = user_comment
        self.tags = tags or []
        self.is_pinned = is_pinned
        self.is_read = is_read
        self.telegram_photo_id = telegram_photo_id
        self.telegram_video_id = telegram_video_id
        self.telegram_document_id = telegram_document_id
        self.photos_json = photos_json
        self.url = url
        self.domain = domain
        self.author = author
        self.reading_time_min = reading_time_min

mock_models = MagicMock()
mock_models.ContentType = ContentType
mock_models.Bookmark = MockBookmark
mock_models.Tag = MockTag
sys.modules["app.db.models"] = mock_models

if 'pydantic' not in sys.modules:
    mock_pydantic = MagicMock()
    mock_pydantic.Field = lambda default=None, **kwargs: default
    mock_pydantic.field_validator = lambda *args, **kwargs: (lambda fn: fn)
    mock_pydantic.BaseModel = object
    sys.modules['pydantic'] = mock_pydantic

if 'pydantic_settings' not in sys.modules:
    mock_pydantic_settings = MagicMock()
    mock_pydantic_settings.BaseSettings = object
    mock_pydantic_settings.SettingsConfigDict = lambda **kwargs: None
    sys.modules['pydantic_settings'] = mock_pydantic_settings

if 'loguru' not in sys.modules:
    sys.modules['loguru'] = MagicMock()

from app.bot.keyboards import (
    get_bookmarks_list_keyboard,
    get_bookmark_card_keyboard,
    get_main_reply_keyboard,
)
from app.bot.handlers.manage import render_bookmarks_list


def test_main_reply_keyboard_has_favorites():
    reply_kb = get_main_reply_keyboard()
    all_texts = [b.text for row in reply_kb.keyboard for b in row]
    assert "⭐ Избранное" in all_texts
    print("test_main_reply_keyboard_has_favorites: PASSED")


def test_favorites_list_keyboard():
    bms = [MockBookmark(title=f"Заметка {i}") for i in range(3)]
    # List keyboard with prefix "fl"
    kb = get_bookmarks_list_keyboard(bms, current_page=1, total_pages=3, prefix="fl")
    all_btns = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in all_btns]
    cbs = [b.callback_data for b in all_btns if b.callback_data]

    assert "🕒 Все недавние" in texts
    assert "📌 Закрепленные" in texts
    assert "📖 Непрочитанные" in texts
    assert "🎲 Случайная" in texts

    # Verify callbacks
    assert any(b.callback_data == "rl:1" for b in all_btns)
    assert any(b.callback_data == "pl:1" for b in all_btns)
    assert any(b.callback_data == "ul:1" for b in all_btns)

    for cb in cbs:
        assert len(cb.encode("utf-8")) <= 64
    print("test_favorites_list_keyboard: PASSED")


def test_recent_list_has_favorites_button():
    bms = [MockBookmark(title=f"Заметка {i}") for i in range(2)]
    kb = get_bookmarks_list_keyboard(bms, current_page=1, total_pages=1, prefix="rl")
    all_btns = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in all_btns]

    assert "⭐ Избранное" in texts
    fav_btn = [b for b in all_btns if b.text == "⭐ Избранное"][0]
    assert fav_btn.callback_data == "fl:1"
    print("test_recent_list_has_favorites_button: PASSED")


def test_render_favorites_list_with_pinned_priority():
    bm_pinned = MockBookmark(
        title="Важная избранная заметка",
        tags=[MockTag("избранное")],
        is_pinned=True,
    )
    bm_regular = MockBookmark(
        title="Обычная избранная статья",
        tags=[MockTag("избранное")],
        is_pinned=False,
    )

    rendered = render_bookmarks_list(
        [bm_pinned, bm_regular],
        current_page=1,
        total_count=2,
        is_favorites_only=True,
    )

    assert "⭐ <b>Избранные заметки и материалы</b>" in rendered
    assert "📌 <b>[Закреплено]</b> <b>Важная избранная заметка</b>" in rendered
    assert "<b>[2]</b> <b>Обычная избранная статья</b>" in rendered
    print("test_render_favorites_list_with_pinned_priority: PASSED")


def test_card_keyboard_nav_prefix_fl():
    bm_id = uuid.uuid4()
    kb = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        is_note=True,
        nav_prefix="fl",
        current_page=2,
        total_pages=5,
    )
    all_btns = [b for row in kb.inline_keyboard for b in row]
    back_btn = [b for b in all_btns if "К списку" in b.text]
    assert len(back_btn) == 1
    assert back_btn[0].callback_data == "fl:2"
    print("test_card_keyboard_nav_prefix_fl: PASSED")


if __name__ == "__main__":
    test_main_reply_keyboard_has_favorites()
    test_favorites_list_keyboard()
    test_recent_list_has_favorites_button()
    test_render_favorites_list_with_pinned_priority()
    test_card_keyboard_nav_prefix_fl()
    print("\nALL FAVORITES MENU TESTS PASSED SUCCESSFULLY!")
