import asyncio
import sys
import uuid
from enum import Enum
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock aiogram if needed
try:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    class InlineKeyboardButton:
        def __init__(self, text, callback_data=None, url=None):
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

        def button(self, text, callback_data=None, url=None):
            if not self._rows:
                self._rows.append([])
            self._rows[-1].append(InlineKeyboardButton(text=text, callback_data=callback_data, url=url))

        def as_markup(self):
            return InlineKeyboardMarkup(inline_keyboard=self._rows)

    sys.modules["aiogram"] = MagicMock()
    sys.modules["aiogram.types"] = MagicMock(
        InlineKeyboardButton=InlineKeyboardButton,
        InlineKeyboardMarkup=InlineKeyboardMarkup,
        KeyboardButton=MagicMock(),
        ReplyKeyboardMarkup=MagicMock(),
        BufferedInputFile=MagicMock(),
    )
    sys.modules["aiogram.utils.keyboard"] = MagicMock(
        InlineKeyboardBuilder=InlineKeyboardBuilder,
        ReplyKeyboardBuilder=MagicMock(),
    )


class ContentType(str, Enum):
    ARTICLE = "article"
    TWEET = "tweet"
    YOUTUBE = "youtube"
    DOCUMENT = "document"
    NOTE = "note"


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

# Mock app.db.models in sys.modules
mock_models = MagicMock()
mock_models.ContentType = ContentType
mock_models.Bookmark = MockBookmark
sys.modules["app.db.models"] = mock_models

if 'loguru' not in sys.modules:
    sys.modules['loguru'] = MagicMock()

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

from app.bot.keyboards import get_bookmark_card_keyboard
from app.bot.card_renderer import check_is_long_note, build_card_text
from app.extractors.documents import DocumentExtractor


def test_note_keyboard_edit_and_full_buttons():
    bm_id = uuid.uuid4()

    # 1. Normal note (short)
    kb_short = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        is_note=True,
        is_long_note=False,
    )
    all_btns_short = [b for row in kb_short.inline_keyboard for b in row]
    texts_short = [b.text for b in all_btns_short]
    assert any("Редактировать" in t for t in texts_short)
    assert not any("Читать полностью" in t for t in texts_short)

    edit_btn = [b for b in all_btns_short if "Редактировать" in b.text][0]
    assert edit_btn.callback_data == f"edit_note:{bm_id}"
    assert len(edit_btn.callback_data.encode("utf-8")) <= 64

    # 2. Long note
    kb_long = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        is_note=True,
        is_long_note=True,
    )
    all_btns_long = [b for row in kb_long.inline_keyboard for b in row]
    texts_long = [b.text for b in all_btns_long]
    assert any("Редактировать" in t for t in texts_long)
    assert any("Читать полностью" in t for t in texts_long)

    full_btn = [b for b in all_btns_long if "Читать полностью" in b.text][0]
    assert full_btn.callback_data == f"full_note:{bm_id}"
    assert len(full_btn.callback_data.encode("utf-8")) <= 64

    print("test_note_keyboard_edit_and_full_buttons: PASSED")


def test_check_is_long_note():
    # 1. Short text note
    bm_short = MockBookmark(
        content_type=ContentType.NOTE,
        full_text="Короткая заметка",
    )
    assert not check_is_long_note(bm_short)

    # 2. Long text note (> 3500 chars)
    bm_long = MockBookmark(
        content_type=ContentType.NOTE,
        full_text="А" * 4000,
    )
    assert check_is_long_note(bm_long)

    # 3. Media note with > 750 chars
    bm_media_long = MockBookmark(
        content_type=ContentType.NOTE,
        telegram_photo_id="photo123",
        full_text="Б" * 900,
    )
    assert check_is_long_note(bm_media_long)

    print("test_check_is_long_note: PASSED")


def test_card_text_long_note_hint():
    bm_long = MockBookmark(
        content_type=ContentType.NOTE,
        title="Большая заметка",
        full_text="Очень важный текст " * 300,
    )
    card_html = build_card_text(bm_long, showing_full_text=True, for_caption=False)
    assert "заметка длинная, нажмите «📖 Читать полностью» ниже" in card_html
    assert len(card_html) <= 4096

    print("test_card_text_long_note_hint: PASSED")


def test_document_extractor_no_truncation_for_txt():
    extractor = DocumentExtractor()
    import tempfile
    import os

    # Create a 200,000 char text file (larger than old MAX_TEXT_LENGTH 150_000)
    big_content = "Строка заметки 1234567890.\n" * 8000  # ~216 KB
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as f:
        f.write(big_content)
        temp_path = f.name

    try:
        res = asyncio.run(extractor.extract(temp_path, filename="big_note.txt"))
        assert res.success is True
        assert res.content_type == ContentType.NOTE
        assert res.domain == "note"
        # Must NOT be truncated!
        assert len(res.text) == len(big_content.strip())
        assert "[текст документа обрезан]" not in res.text
    finally:
        os.remove(temp_path)

    print("test_document_extractor_no_truncation_for_txt: PASSED")


if __name__ == "__main__":
    test_note_keyboard_edit_and_full_buttons()
    test_check_is_long_note()
    test_card_text_long_note_hint()
    test_document_extractor_no_truncation_for_txt()
    print("\nALL NOTE EDIT & SIZE TESTS PASSED SUCCESSFULLY!")
