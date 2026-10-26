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
    mock_aiogram = MagicMock()
    sys.modules["aiogram"] = mock_aiogram
    sys.modules["aiogram.types"] = mock_types
    sys.modules["aiogram.filters"] = MagicMock()
    sys.modules["aiogram.fsm"] = MagicMock()
    sys.modules["aiogram.fsm.context"] = MagicMock()
    sys.modules["aiogram.fsm.state"] = MagicMock()
    sys.modules["sqlalchemy"] = MagicMock()
    sys.modules["sqlalchemy.ext"] = MagicMock()
    sys.modules["sqlalchemy.ext.asyncio"] = MagicMock()
    sys.modules["sqlalchemy.orm"] = MagicMock()
    sys.modules["pydantic"] = MagicMock()
    sys.modules["pydantic_settings"] = MagicMock()
    sys.modules["httpx"] = MagicMock()
    sys.modules["bs4"] = MagicMock()
    sys.modules["aiofiles"] = MagicMock()
    mock_yta = MagicMock()
    sys.modules["youtube_transcript_api"] = mock_yta
    sys.modules["youtube_transcript_api._errors"] = MagicMock()
    sys.modules["openai"] = MagicMock()
    sys.modules["anthropic"] = MagicMock()
    sys.modules["google"] = MagicMock()
    sys.modules["google.generativeai"] = MagicMock()
    sys.modules["qdrant_client"] = MagicMock()
    sys.modules["trafilatura"] = MagicMock()
    sys.modules["pdfplumber"] = MagicMock()
    sys.modules["pypdf"] = MagicMock()
    sys.modules["readability"] = MagicMock()
    sys.modules["newspaper"] = MagicMock()
    sys.modules["app.providers.registry"] = MagicMock()
    sys.modules["app.core.config"] = MagicMock()
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

from app.bot.keyboards import get_bookmark_card_keyboard, get_video_quality_keyboard
from app.services.video_downloader import (
    _find_ffmpeg,
    format_duration,
    is_video_url,
    split_video_into_parts,
)


def test_is_video_url():
    """Test URL detection for yoinks/yt-dlp supported video platforms (YouTube downloading removed)."""
    # Positive (TikTok, Reels, Twitter, VK, Rutube, Reddit)
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

    # Negative (YouTube is handled as summary/bookmark, not video download)
    assert is_video_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False
    assert is_video_url("https://youtu.be/dQw4w9WgXcQ") is False
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
    """Test that video bookmarks receive Yoinks 🎬 and 🎵 action buttons, while YouTube does not."""
    bm_id = uuid.uuid4()

    # 1. Video URL (TikTok)
    kb = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url="https://www.tiktok.com/@user/video/1234567890",
    )
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    btn_texts = [b.text for b in all_buttons]

    assert "🎬 Скачать видео" in btn_texts
    assert "🎵 Скачать аудио" in btn_texts

    # Check callback_data and length <= 64 bytes
    video_cb = next(b.callback_data for b in all_buttons if b.text == "🎬 Скачать видео")
    audio_cb = next(b.callback_data for b in all_buttons if b.text == "🎵 Скачать аудио")

    assert video_cb.startswith("yoink_v:")
    assert audio_cb.startswith("yoink_a:")
    assert len(video_cb.encode("utf-8")) <= 64
    assert len(audio_cb.encode("utf-8")) <= 64

    # 2. YouTube URL (Video download buttons must NOT appear)
    kb_yt = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    yt_texts = [b.text for row in kb_yt.inline_keyboard for b in row]
    assert "🎬 Скачать видео" not in yt_texts
    assert "🎵 Скачать аудио" not in yt_texts

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


def test_video_quality_keyboard():
    """Test the video quality picker keyboard buttons and callbacks."""
    bm_id = uuid.uuid4()
    kb = get_video_quality_keyboard(bm_id)
    all_buttons = [btn for row in kb.inline_keyboard for btn in row]
    btn_texts = [b.text for b in all_buttons]
    btn_cbs = [b.callback_data for b in all_buttons if b.callback_data]

    assert "💎 4K (2160p)" in btn_texts
    assert "🎬 2K (1440p)" in btn_texts
    assert "🎬 1080p (Full HD)" in btn_texts
    assert "🎬 720p (HD)" in btn_texts
    assert "🎬 480p (SD)" in btn_texts
    assert "🎬 360p (Эконом)" in btn_texts
    assert "« Назад к карточке" in btn_texts

    for cb in btn_cbs:
        assert len(cb.encode("utf-8")) <= 64, f"Callback {cb} exceeds 64 bytes"
        assert cb.startswith("yq:") or cb.startswith("yoink_a:") or cb.startswith("back_card:")

    print("test_video_quality_keyboard passed!")


def test_split_video_into_parts_small_file(tmp_path=None):
    """Test that small files under 48MB are not split."""
    import tempfile
    tmp_fd, path = tempfile.mkstemp(suffix=".mp4")
    os.write(tmp_fd, b"0" * 1024)
    os.close(tmp_fd)

    try:
        parts = split_video_into_parts(path, max_part_size_mb=48)
        assert parts == [path]
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    print("test_split_video_into_parts_small_file passed!")


def test_resolve_cookiefile():
    """Test cookiefile resolution from environment variable with auto-sanitization."""
    from app.services.video_downloader import _resolve_cookiefile, _sanitize_netscape_cookies, _resolve_proxy

    # Test auto-sanitization of broken copy-pasted cookies
    corrupted_sample = ".youtube.com TRUE / FALSE 1750000000 SID my_secret_cookie_val"
    sanitized = _sanitize_netscape_cookies(corrupted_sample)
    assert "# Netscape HTTP Cookie File" in sanitized
    assert "\t" in sanitized
    assert "my_secret_cookie_val" in sanitized

    # Test resolution from env var with escaped newlines
    os.environ["YOUTUBE_COOKIES"] = "\\n.youtube.com\\tTRUE\\t/\\tFALSE\\t0\\tTEST\\t12345\\n"
    try:
        resolved = _resolve_cookiefile()
        assert resolved is not None
        assert os.path.exists(resolved)
        with open(resolved, "r", encoding="utf-8") as rf:
            content = rf.read()
        assert "TEST\t12345" in content
    finally:
        os.environ.pop("YOUTUBE_COOKIES", None)

    # Test proxy resolution
    os.environ["YTDLP_PROXY"] = "http://user:pass@1.2.3.4:8080"
    try:
        assert _resolve_proxy() == "http://user:pass@1.2.3.4:8080"
    finally:
        os.environ.pop("YTDLP_PROXY", None)

    print("test_resolve_cookiefile passed!")


async def test_process_and_send_video_pipeline():
    """Verify that process_and_send_video automatically downloads, tracks status, and sends video."""
    import tempfile
    from unittest.mock import AsyncMock, patch

    mock_bot = AsyncMock()
    mock_status_msg = AsyncMock()
    status_edits = []

    async def fake_edit_text(text, **kwargs):
        status_edits.append(text)
    mock_status_msg.edit_text.side_effect = fake_edit_text

    # Create dummy video file
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"0" * 2048)
        dummy_file = f.name

    from app.bot.handlers.bookmarks import process_and_send_video

    with patch("app.bot.handlers.bookmarks.download_video", new=AsyncMock(return_value=([dummy_file], {"title": "Test YouTube Video", "duration": 120, "width": 1280, "height": 720}, None))):
        await process_and_send_video(
            bot=mock_bot,
            chat_id=12345,
            user_id=12345,
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            status_msg=mock_status_msg,
            quality="auto",
        )

    # Verify status transitions
    assert any("[1/3] Скачивание видео" in s for s in status_edits), "Missing download state"
    assert any("[2/3] Подготовка файла" in s for s in status_edits), "Missing preparation state"
    assert any("[3/3] Отправка видео в Telegram" in s for s in status_edits), "Missing sending state"
    assert any("Видео успешно отправлено" in s for s in status_edits), "Missing completed state"

    # Verify video was sent to user chat
    assert mock_bot.send_video.called, "bot.send_video was not called!"
    call_kwargs = mock_bot.send_video.call_args.kwargs
    assert call_kwargs["chat_id"] == 12345
    assert "Test YouTube Video" in call_kwargs["caption"]
    assert call_kwargs["supports_streaming"] is True

    print("test_process_and_send_video_pipeline passed!")


if __name__ == "__main__":
    test_is_video_url()
    test_format_duration()
    test_ffmpeg_found()
    test_bookmark_card_keyboard_yoinks_buttons()
    test_video_quality_keyboard()
    test_split_video_into_parts_small_file()
    asyncio.run(test_video_extractor())
    test_resolve_cookiefile()
    asyncio.run(test_process_and_send_video_pipeline())

    print("\nALL YOINKS DOWNLOADER TESTS PASSED!")
