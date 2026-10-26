import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Fallback types for running directly on local python without Docker
class InlineKeyboardButton:
    def __init__(self, text="", callback_data=None, url=None):
        self.text = text
        self.callback_data = callback_data
        self.url = url

class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard=None):
        self.inline_keyboard = inline_keyboard or []

class InlineKeyboardBuilder:
    def __init__(self):
        self._rows = []

    def row(self, *buttons):
        self._rows.append(list(buttons))
        return self

    def as_markup(self):
        return InlineKeyboardMarkup(inline_keyboard=self._rows)

mock_aiogram = MagicMock()
mock_types = MagicMock()
mock_types.InlineKeyboardButton = InlineKeyboardButton
mock_types.InlineKeyboardMarkup = InlineKeyboardMarkup
mock_utils_kb = MagicMock()
mock_utils_kb.InlineKeyboardBuilder = InlineKeyboardBuilder

sys.modules["aiogram"] = mock_aiogram
sys.modules["aiogram.types"] = mock_types
sys.modules["aiogram.utils"] = MagicMock()
sys.modules["aiogram.utils.keyboard"] = mock_utils_kb
class State:
    def __init__(self, *args, **kwargs):
        pass

class StatesGroup:
    pass

mock_fsm_state = MagicMock()
mock_fsm_state.State = State
mock_fsm_state.StatesGroup = StatesGroup
sys.modules["aiogram.fsm.state"] = mock_fsm_state
sys.modules["aiogram.fsm.context"] = MagicMock()
sys.modules["aiogram.filters"] = MagicMock()
sys.modules["aiogram.fsm"] = MagicMock()
sys.modules["loguru"] = MagicMock()
sys.modules["sqlalchemy"] = MagicMock()
sys.modules["sqlalchemy.ext"] = MagicMock()
sys.modules["sqlalchemy.ext.asyncio"] = MagicMock()
sys.modules["sqlalchemy.orm"] = MagicMock()

from enum import Enum
class ContentType(str, Enum):
    ARTICLE = "article"
    YOUTUBE = "youtube"
    DOCUMENT = "document"
    TWEET = "tweet"
    NOTE = "note"

class Bookmark:
    def __init__(self, id, user_id, title, full_text, content_type, reading_time_min, is_read, is_pinned=False, telegram_photo_id=None, telegram_video_id=None, telegram_document_id=None, image_url=None, photos_json=None, url=None, domain=None, author=None, summary=None, user_comment=None):
        self.id = id
        self.user_id = user_id
        self.title = title
        self.full_text = full_text
        self.content_type = content_type
        self.reading_time_min = reading_time_min
        self.is_read = is_read
        self.is_pinned = is_pinned
        self.telegram_photo_id = telegram_photo_id
        self.telegram_video_id = telegram_video_id
        self.telegram_document_id = telegram_document_id
        self.image_url = image_url
        self.photos_json = photos_json
        self.url = url
        self.domain = domain
        self.author = author
        self.summary = summary
        self.user_comment = user_comment
        self.tags = []

mock_models = MagicMock()
mock_models.ContentType = ContentType
mock_models.Bookmark = Bookmark
sys.modules["app.db.models"] = mock_models

if "app.core.logger" not in sys.modules:
    mock_logger = MagicMock()
    mock_logger.log = MagicMock()
    sys.modules["app.core.logger"] = mock_logger

if "app.core.config" not in sys.modules:
    mock_config = MagicMock()
    mock_config.settings = MagicMock()
    mock_config.settings.MAX_FILE_SIZE_MB = 20
    sys.modules["app.core.config"] = mock_config

mock_repo = MagicMock()
sys.modules["app.db.repositories"] = mock_repo

from app.bot.card_renderer import build_card_text
from app.bot.keyboards import get_bookmark_card_keyboard
from app.bot.handlers.manage import render_bookmarks_list


async def test_pin_buttons_in_card_keyboard():
    bm_id = uuid.uuid4()

    # 1. Unpinned bookmark: must show "📌 Закрепить"
    kb_unpinned = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        is_pinned=False,
        is_note=True,
    )
    all_btns_1 = [btn for row in kb_unpinned.inline_keyboard for btn in row]
    pin_btns_1 = [btn for btn in all_btns_1 if btn.text == "📌 Закрепить"]
    assert len(pin_btns_1) == 1, "Should have '📌 Закрепить' button when is_pinned=False"
    assert pin_btns_1[0].callback_data.startswith("pin:"), "Callback data must start with pin:"
    assert len(pin_btns_1[0].callback_data.encode("utf-8")) <= 64, "Callback data must be <= 64 bytes"

    # 2. Pinned bookmark: must show "📍 Открепить"
    kb_pinned = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        is_pinned=True,
        is_note=True,
    )
    all_btns_2 = [btn for row in kb_pinned.inline_keyboard for btn in row]
    unpin_btns_2 = [btn for btn in all_btns_2 if btn.text == "📍 Открепить"]
    assert len(unpin_btns_2) == 1, "Should have '📍 Открепить' button when is_pinned=True"
    assert unpin_btns_2[0].callback_data.startswith("pin:"), "Callback data must start with pin:"
    assert len(unpin_btns_2[0].callback_data.encode("utf-8")) <= 64, "Callback data must be <= 64 bytes"


async def test_pin_visual_in_card_text():
    mock_bm = MagicMock()
    mock_bm.id = uuid.uuid4()
    mock_bm.content_type = ContentType.NOTE
    mock_bm.title = "Купить сервер"
    mock_bm.user_comment = "Заметка: оплатить сервер до 25 числа"
    mock_bm.full_text = mock_bm.user_comment
    mock_bm.summary = None
    mock_bm.domain = "заметка"
    mock_bm.reading_time_min = 1
    mock_bm.author = None
    mock_bm.tags = []
    mock_bm.is_read = False
    mock_bm.is_pinned = True
    mock_bm.telegram_photo_id = None
    mock_bm.telegram_video_id = None
    mock_bm.telegram_document_id = None
    mock_bm.photos_json = None

    text = build_card_text(mock_bm, showing_full_text=True)
    assert "📌 Закреплено" in text, "Card text must contain '📌 Закреплено'"
    assert "Купить сервер" in text or "Заметка: оплатить сервер" in text

    # When unpinned
    mock_bm.is_pinned = False
    text_unpinned = build_card_text(mock_bm, showing_full_text=True)
    assert "📌 Закреплено" not in text_unpinned, "Unpinned card text must not contain '📌 Закреплено'"


async def test_pinned_display_in_list():
    now = datetime.now()

    bm1 = MagicMock()
    bm1.id = uuid.uuid4()
    bm1.title = "Важная закреплённая заметка"
    bm1.domain = "заметка"
    bm1.reading_time_min = 1
    bm1.is_read = False
    bm1.is_pinned = True
    bm1.tags = []
    bm1.photos_json = None
    bm1.telegram_photo_id = None
    bm1.telegram_video_id = None
    bm1.created_at = now - timedelta(days=2)

    bm2 = MagicMock()
    bm2.id = uuid.uuid4()
    bm2.title = "Новая обычная статья"
    bm2.domain = "habr.com"
    bm2.reading_time_min = 3
    bm2.is_read = False
    bm2.is_pinned = False
    bm2.tags = []
    bm2.photos_json = None
    bm2.telegram_photo_id = None
    bm2.telegram_video_id = None
    bm2.created_at = now

    list_text = render_bookmarks_list([bm1, bm2], current_page=1, total_count=2)
    assert "📌 <b>[Закреплено]</b>" in list_text, "List text must contain '📌 <b>[Закреплено]</b>' for pinned items"
    assert "Важная закреплённая заметка" in list_text
    assert "Новая обычная статья" in list_text


async def test_pinned_sorting_invariance():
    now = datetime.now()

    # Create bookmarks: some old and pinned, some new and unpinned
    pinned_note_1 = MagicMock(id=1, title="Pin 1", is_pinned=True, created_at=now - timedelta(days=5))
    pinned_note_2 = MagicMock(id=2, title="Pin 2", is_pinned=True, created_at=now - timedelta(days=3))
    new_article_1 = MagicMock(id=3, title="New 1", is_pinned=False, created_at=now - timedelta(hours=2))
    new_article_2 = MagicMock(id=4, title="New 2", is_pinned=False, created_at=now - timedelta(minutes=10))

    # Python simulation of SQL: ORDER BY is_pinned DESC, created_at DESC
    all_items = [new_article_1, pinned_note_1, new_article_2, pinned_note_2]
    sorted_items = sorted(all_items, key=lambda b: (not b.is_pinned, -b.created_at.timestamp()))

    # Pinned items must be 1st and 2nd!
    assert sorted_items[0].is_pinned is True
    assert sorted_items[1].is_pinned is True
    # Among pinned, newest created_at is first (Pin 2 before Pin 1)
    assert sorted_items[0].title == "Pin 2"
    assert sorted_items[1].title == "Pin 1"

    # Even if 10 more new items are added, pinned stay at the top:
    for i in range(10):
        all_items.append(MagicMock(id=10 + i, title=f"New {i}", is_pinned=False, created_at=now + timedelta(minutes=i)))

    sorted_after_additions = sorted(all_items, key=lambda b: (not b.is_pinned, -b.created_at.timestamp()))
    assert sorted_after_additions[0].title == "Pin 2"
    assert sorted_after_additions[1].title == "Pin 1"


if __name__ == "__main__":
    async def run_all():
        await test_pin_buttons_in_card_keyboard()
        print("test_pin_buttons_in_card_keyboard: PASSED")
        await test_pin_visual_in_card_text()
        print("test_pin_visual_in_card_text: PASSED")
        await test_pinned_display_in_list()
        print("test_pinned_display_in_list: PASSED")
        await test_pinned_sorting_invariance()
        print("test_pinned_sorting_invariance: PASSED")
        print("\nALL PINNED NOTES TESTS PASSED!")

    asyncio.run(run_all())
