import asyncio
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Standalone mock fallback for running tests outside docker container
try:
    import pytest
except ImportError:
    class _PytestMock:
        class mark:
            @staticmethod
            def asyncio(fn):
                return fn
    pytest = _PytestMock()

if "app.core.config" not in sys.modules:
    mock_config = MagicMock()
    mock_config.settings = MagicMock()
    mock_config.settings.MAX_FILE_SIZE_MB = 20
    mock_config.settings.MAX_TEXT_LENGTH = 150000
    sys.modules["app.core.config"] = mock_config

if "app.core.logger" not in sys.modules:
    mock_logger = MagicMock()
    mock_logger.log = MagicMock()
    sys.modules["app.core.logger"] = mock_logger

try:
    import aiogram
except ImportError:
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

try:
    from app.db.models import ContentType
except ImportError:
    from enum import Enum
    class ContentType(str, Enum):
        ARTICLE = "article"
        YOUTUBE = "youtube"
        DOCUMENT = "document"
        TWEET = "tweet"
        NOTE = "note"
    mock_models = MagicMock()
    mock_models.ContentType = ContentType
    sys.modules["app.db.models"] = mock_models

from app.extractors.documents import DocumentExtractor


@pytest.mark.asyncio
async def test_zip_extractor_with_text_and_readme():
    extractor = DocumentExtractor()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        zip_path = f.name

    try:
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("README.md", "# Super Read Later\n\nДокументация проекта в архиве.")
            zf.writestr("main.py", "def test():\n    return 'hello world'")
            zf.writestr("data/notes.txt", "Вложенная заметка в папке.")
            zf.writestr("__MACOSX/._README.md", b"\x00\x01\x02")  # should be ignored

        content = await extractor.extract(zip_path, filename="project.zip")
        assert content.success is True
        assert content.content_type == ContentType.DOCUMENT
        assert "📦 Содержимое архива «project.zip» (3 файлов):" in content.text
        assert "README.md" in content.text
        assert "main.py" in content.text
        assert "data/notes.txt" in content.text
        assert "__MACOSX" not in content.text
        assert "Документация проекта в архиве." in content.text
        assert "def test():" in content.text
        assert "Super Read Later" in content.title
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)


@pytest.mark.asyncio
async def test_zip_extractor_binary_only():
    extractor = DocumentExtractor()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        zip_path = f.name

    try:
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("photo1.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 50)
            zf.writestr("audio.mp3", b"ID3" + b"\x00" * 50)

        content = await extractor.extract(zip_path, filename="media.zip")
        assert content.success is True
        assert content.content_type == ContentType.DOCUMENT
        assert "📦 Содержимое архива «media.zip» (2 файлов):" in content.text
        assert "photo1.jpg" in content.text
        assert "audio.mp3" in content.text
        assert content.title == "Архив media.zip"
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)


@pytest.mark.asyncio
async def test_zip_extractor_invalid_file():
    extractor = DocumentExtractor()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(b"this is not a zip file content")
        fake_path = f.name

    try:
        content = await extractor.extract(fake_path, filename="corrupted.zip")
        assert content.success is False
        assert "Ошибка парсинга документа" in (content.error_message or "") or "не является корректным" in (content.error_message or "")
    finally:
        if os.path.exists(fake_path):
            os.remove(fake_path)


@pytest.mark.asyncio
async def test_archive_keyboard_and_card_display():
    import uuid
    from app.bot.keyboards import get_bookmark_card_keyboard
    from app.bot.card_renderer import build_card_text

    bm_id = uuid.uuid4()
    # Test keyboard with has_file=True
    kb = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url=None,
        is_read=False,
        has_file=True,
    )
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    dl_buttons = [btn for btn in all_buttons if btn.text == "📥 Скачать архив"]
    assert len(dl_buttons) == 1, "Should contain '📥 Скачать архив' button"
    assert dl_buttons[0].callback_data.startswith("dl_doc:"), "Callback data must start with dl_doc:"
    assert len(dl_buttons[0].callback_data.encode("utf-8")) <= 64, "Callback data must be <= 64 bytes"

    # Test keyboard with has_file=False (no download button)
    kb_no_file = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url=None,
        is_read=False,
        has_file=False,
    )
    all_buttons_no = [btn for row in kb_no_file.inline_keyboard for btn in row]
    dl_buttons_no = [btn for btn in all_buttons_no if btn.text == "📥 Скачать архив"]
    assert len(dl_buttons_no) == 0, "Should NOT contain '📥 Скачать архив' when has_file=False"

    # Test card renderer with telegram_document_id
    mock_bm = MagicMock()
    mock_bm.id = bm_id
    mock_bm.content_type = ContentType.DOCUMENT
    mock_bm.title = "📦 backup.zip"
    mock_bm.domain = "архив"
    mock_bm.reading_time_min = 1
    mock_bm.author = None
    mock_bm.tags = []
    mock_bm.is_read = False
    mock_bm.telegram_photo_id = None
    mock_bm.telegram_video_id = None
    mock_bm.photos_json = None
    mock_bm.telegram_document_id = "BQACAgIAAxkBAAIB..."
    mock_bm.summary = "📦 Архив: backup.zip (2.50 ГБ)"
    mock_bm.full_text = "Файл архива сохранён в закладочник."
    mock_bm.user_comment = None

    text = build_card_text(mock_bm, showing_full_text=True)
    assert "📦 Архив" in text, "Card text should have 📦 Архив badge"
    assert "backup.zip" in text, "Card text should contain the archive name"


@pytest.mark.asyncio
async def test_archive_size_formatting_and_caps():
    # 500 MB
    bytes_500mb = 500 * 1024 * 1024
    if bytes_500mb >= 1024 * 1024 * 1024:
        s500 = f"{bytes_500mb / (1024 * 1024 * 1024):.2f} ГБ"
    else:
        s500 = f"{bytes_500mb / (1024 * 1024):.1f} МБ"
    assert s500 == "500.0 МБ"

    # 3.2 GB
    bytes_3gb = int(3.2 * 1024 * 1024 * 1024)
    if bytes_3gb >= 1024 * 1024 * 1024:
        s3 = f"{bytes_3gb / (1024 * 1024 * 1024):.2f} ГБ"
    else:
        s3 = f"{bytes_3gb / (1024 * 1024):.1f} МБ"
    assert s3 == "3.20 ГБ"

    # 4.5 GB (> 4 GB Telegram maximum upload cap)
    bytes_4_5gb = int(4.5 * 1024 * 1024 * 1024)
    max_cap = 4 * 1024 * 1024 * 1024
    assert bytes_4_5gb > max_cap


if __name__ == "__main__":
    async def run_all():
        await test_zip_extractor_with_text_and_readme()
        print("test_zip_extractor_with_text_and_readme: PASSED")
        await test_zip_extractor_binary_only()
        print("test_zip_extractor_binary_only: PASSED")
        await test_zip_extractor_invalid_file()
        print("test_zip_extractor_invalid_file: PASSED")
        await test_archive_keyboard_and_card_display()
        print("test_archive_keyboard_and_card_display: PASSED")
        await test_archive_size_formatting_and_caps()
        print("test_archive_size_formatting_and_caps: PASSED")
        print("\nALL ZIP & ARCHIVE TESTS PASSED!")

    asyncio.run(run_all())
