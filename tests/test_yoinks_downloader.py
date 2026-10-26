import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock loguru
if "loguru" not in sys.modules:
    mock_loguru = MagicMock()
    mock_loguru.logger = MagicMock()
    sys.modules["loguru"] = mock_loguru

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

    mock_types = MagicMock()
    mock_types.InlineKeyboardButton = InlineKeyboardButton
    mock_types.InlineKeyboardMarkup = InlineKeyboardMarkup
    mock_types.InlineKeyboardBuilder = InlineKeyboardBuilder
    sys.modules["aiogram"] = MagicMock()
    sys.modules["aiogram.types"] = mock_types
    mock_builder = MagicMock()
    mock_builder.InlineKeyboardBuilder = InlineKeyboardBuilder
    sys.modules["aiogram.utils"] = MagicMock()
    sys.modules["aiogram.utils.keyboard"] = mock_builder

from enum import Enum
class ContentType(str, Enum):
    ARTICLE = "article"
    TWEET = "tweet"
    YOUTUBE = "youtube"
    DOCUMENT = "document"
    NOTE = "note"

if "app.db.models" not in sys.modules:
    mock_models = MagicMock()
    mock_models.ContentType = ContentType
    sys.modules["app.db.models"] = mock_models

from app.bot.keyboards import get_bookmark_card_keyboard
from app.services.video_downloader import (
    _find_ffmpeg,
    format_duration,
    is_video_url,
)


def test_is_video_url():
    """Test URL detection for yoinks/yt-dlp supported video platforms."""
    # Positive
    assert is_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert is_video_url("https://youtu.be/dQw4w9WgXcQ") is True
    assert is_video_url("https://www.tiktok.com/@user/video/1234567890") is True
    assert is_video_url("https://vm.tiktok.com/ZM8example/") is True
    assert is_video_url("https://www.instagram.com/reel/C3abc123/") is True
    assert is_video_url("https://twitter.com/user/status/123456789") is True
    assert is_video_url("https://x.com/user/status/123456789") is True
    assert is_video_url("https://www.reddit.com/r/funny/comments/xyz123/clip/") is True
    assert is_video_url("https://v.redd.it/abc123xyz") is True
    assert is_video_url("https://vimeo.com/12345678") is True
    assert is_video_url("https://example.com/video.mp4") is True
    assert is_video_url("https://vk.com/video-12345_67890") is True
    assert is_video_url("https://rutube.ru/video/1234567890abcdef/") is True

    # Negative
    assert is_video_url("https://habr.com/ru/articles/12345/") is False
    assert is_video_url("https://github.com/pablostanley/yoinks") is False
    assert is_video_url("https://en.wikipedia.org/wiki/Python") is False
    assert is_video_url(None) is False
    assert is_video_url("") is False
    assert is_video_url("not a url") is False
    print("test_is_video_url passed!")


def test_format_duration():
    """Test duration formatting to MM:SS and HH:MM:SS."""
    assert format_duration(45) == "0:45"
    assert format_duration(125) == "2:05"
    assert format_duration(3665) == "1:01:05"
    assert format_duration(0) == "0:00"
    assert format_duration(None) == "0:00"
    assert format_duration(-10) == "0:00"
    print("test_format_duration passed!")


def test_ffmpeg_found():
    """Test that ffmpeg is discoverable on the system."""
    ffmpeg_bin = _find_ffmpeg()
    assert ffmpeg_bin is not None, "ffmpeg should be found via PATH or Windows registry"
    assert os.path.exists(ffmpeg_bin), f"ffmpeg path {ffmpeg_bin} does not exist"
    print(f"test_ffmpeg_found passed! (Found: {ffmpeg_bin})")


def test_bookmark_card_keyboard_yoinks_buttons():
    """Test that video bookmarks receive Yoinks 🎬 and 🎵 action buttons."""
    bm_id = uuid.uuid4()

    # 1. Video URL (YouTube)
    kb = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    btn_texts = [b.text for b in all_buttons]
    btn_cbs = [b.callback_data for b in all_buttons if b.callback_data]

    assert "🎬 Скачать видео" in btn_texts
    assert "🎵 Скачать аудио" in btn_texts

    # Check callback_data and length <= 64 bytes
    video_cb = next(b.callback_data for b in all_buttons if b.text == "🎬 Скачать видео")
    audio_cb = next(b.callback_data for b in all_buttons if b.text == "🎵 Скачать аудио")

    assert video_cb.startswith("yoink_v:")
    assert audio_cb.startswith("yoink_a:")
    assert len(video_cb.encode("utf-8")) <= 64
    assert len(audio_cb.encode("utf-8")) <= 64

    # 2. Non-video URL (Article)
    kb_article = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url="https://habr.com/ru/articles/12345/",
        is_video=False,
    )
    article_btn_texts = [btn.text for row in kb_article.inline_keyboard for btn in row]
    assert "🎬 Скачать видео" not in article_btn_texts
    assert "🎵 Скачать аудио" not in article_btn_texts

    # 3. Explicit is_video=True
    kb_custom = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url=None,
        is_video=True,
    )
    custom_btn_texts = [btn.text for row in kb_custom.inline_keyboard for btn in row]
    assert "🎬 Скачать видео" in custom_btn_texts
    assert "🎵 Скачать аудио" in custom_btn_texts

    print("test_bookmark_card_keyboard_yoinks_buttons passed!")


async def test_video_extractor():
    """Test VideoExtractor text creation with mock metadata."""
    from app.extractors.video import VideoExtractor

    extractor = VideoExtractor()
    mock_meta = {
        "title": "Интересный шортс",
        "uploader": "CoolAuthor",
        "duration": 42,
        "duration_str": "0:42",
        "extractor": "Youtube",
        "thumbnail": "https://img.youtube.com/thumb.jpg",
        "description": "Описание короткого ролика",
    }

    with patch("app.extractors.video.extract_video_metadata", new=AsyncMock(return_value=mock_meta)):
        content = await extractor.extract("https://youtube.com/shorts/xyz123")
        assert content.success is True
        assert content.title == "Интересный шортс"
        assert content.author == "CoolAuthor"
        assert "0:42" in content.text
        assert "CoolAuthor" in content.text
        assert content.image_url == "https://img.youtube.com/thumb.jpg"

    print("test_video_extractor passed!")


if __name__ == "__main__":
    test_is_video_url()
    test_format_duration()
    test_ffmpeg_found()
    test_bookmark_card_keyboard_yoinks_buttons()
    asyncio.run(test_video_extractor())
    print("\nALL YOINKS DOWNLOADER TESTS PASSED!")
