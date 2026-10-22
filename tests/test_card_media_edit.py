import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pytest
except ImportError:
    class _PytestMock:
        class mark:
            @staticmethod
            def asyncio(fn):
                return fn
    pytest = _PytestMock()

try:
    from aiogram.types import InputMediaPhoto, InputMediaVideo
    from app.db.models import Bookmark, ContentType
except ImportError:
    class InputMediaPhoto:
        def __init__(self, media=None, caption=None, parse_mode=None):
            self.media = media
            self.caption = caption
            self.parse_mode = parse_mode

    class InputMediaVideo:
        def __init__(self, media=None, caption=None, parse_mode=None):
            self.media = media
            self.caption = caption
            self.parse_mode = parse_mode

    mock_types = MagicMock()
    mock_types.InputMediaPhoto = InputMediaPhoto
    mock_types.InputMediaVideo = InputMediaVideo
    sys.modules['aiogram'] = MagicMock()
    sys.modules['aiogram.types'] = mock_types

    from enum import Enum
    class ContentType(str, Enum):
        ARTICLE = "article"
        YOUTUBE = "youtube"
        DOCUMENT = "document"
        TWEET = "tweet"
        NOTE = "note"

    class Bookmark:
        def __init__(self, id, user_id, title, full_text, content_type, reading_time_min, is_read, telegram_photo_id=None, telegram_video_id=None, image_url=None, photos_json=None, url=None, domain=None, author=None, summary=None, user_comment=None):
            self.id = id
            self.user_id = user_id
            self.title = title
            self.full_text = full_text
            self.content_type = content_type
            self.reading_time_min = reading_time_min
            self.is_read = is_read
            self.telegram_photo_id = telegram_photo_id
            self.telegram_video_id = telegram_video_id
            self.image_url = image_url
            self.photos_json = photos_json
            self.url = url
            self.domain = domain
            self.author = author
            self.summary = summary
            self.user_comment = user_comment
            self.tags = []

    mock_models = MagicMock()
    mock_models.Bookmark = Bookmark
    mock_models.ContentType = ContentType
    sys.modules['app.db.models'] = mock_models

if 'loguru' not in sys.modules:
    sys.modules['loguru'] = MagicMock()

from app.bot.card_renderer import update_card_message


def make_bookmark(photo_id: str | None) -> Bookmark:
    b = Bookmark(
        id=uuid.uuid4(),
        user_id=1,
        title=f"Заметка {photo_id}",
        full_text="текст",
        content_type=ContentType.NOTE,
        reading_time_min=1,
        is_read=False,
        telegram_photo_id=photo_id,
    )
    b.tags = []
    return b


@pytest.mark.asyncio
async def test_photo_changes_when_switching_cards():
    old_msg = MagicMock()
    old_msg.photo = [MagicMock(file_id="OLD_PHOTO")]
    old_msg.video = None
    old_msg.media_group_id = None
    old_msg.edit_caption = AsyncMock()
    old_msg.edit_media = AsyncMock()
    old_msg.edit_text = AsyncMock()

    callback = MagicMock()
    callback.message = old_msg

    await update_card_message(callback, make_bookmark("NEW_PHOTO"), MagicMock())

    # Главное: медиа должно меняться, а не только подпись
    old_msg.edit_media.assert_awaited_once()
    media = old_msg.edit_media.await_args.kwargs["media"]
    assert media.media == "NEW_PHOTO"
    old_msg.edit_caption.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_photo_uses_caption_only():
    old_msg = MagicMock()
    old_msg.photo = [MagicMock(file_id="SAME")]
    old_msg.video = None
    old_msg.media_group_id = None
    old_msg.edit_caption = AsyncMock()
    old_msg.edit_media = AsyncMock()

    callback = MagicMock()
    callback.message = old_msg

    await update_card_message(callback, make_bookmark("SAME"), MagicMock())

    old_msg.edit_caption.assert_awaited_once()
    old_msg.edit_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_changes_when_switching_cards():
    old_msg = MagicMock()
    old_msg.photo = None
    old_msg.video = MagicMock(file_id="OLD_VIDEO")
    old_msg.media_group_id = None
    old_msg.edit_caption = AsyncMock()
    old_msg.edit_media = AsyncMock()
    old_msg.edit_text = AsyncMock()

    callback = MagicMock()
    callback.message = old_msg

    b = make_bookmark(None)
    b.telegram_video_id = "NEW_VIDEO"

    await update_card_message(callback, b, MagicMock())

    old_msg.edit_media.assert_awaited_once()
    media = old_msg.edit_media.await_args.kwargs["media"]
    assert media.media == "NEW_VIDEO"
    old_msg.edit_caption.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_to_photo_displays_photo():
    old_msg = MagicMock()
    old_msg.photo = None
    old_msg.video = None
    old_msg.media_group_id = None
    old_msg.delete = AsyncMock()
    old_msg.answer_photo = AsyncMock()

    callback = MagicMock()
    callback.message = old_msg

    await update_card_message(callback, make_bookmark("ATTACHED_PHOTO"), MagicMock())

    old_msg.delete.assert_awaited_once()
    old_msg.answer_photo.assert_awaited_once()
    assert old_msg.answer_photo.await_args.kwargs["photo"] == "ATTACHED_PHOTO"


@pytest.mark.asyncio
async def test_photo_from_photos_json_is_displayed():
    old_msg = MagicMock()
    old_msg.photo = None
    old_msg.video = None
    old_msg.media_group_id = None
    old_msg.delete = AsyncMock()
    old_msg.answer_photo = AsyncMock()

    callback = MagicMock()
    callback.message = old_msg

    b = make_bookmark(None)
    b.photos_json = '["ALBUM_PHOTO_1", "ALBUM_PHOTO_2"]'

    await update_card_message(callback, b, MagicMock())

    old_msg.delete.assert_awaited_once()
    old_msg.answer_photo.assert_awaited_once()
    assert old_msg.answer_photo.await_args.kwargs["photo"] == "ALBUM_PHOTO_1"


@pytest.mark.asyncio
async def test_photo_to_text_note_clears_photo():
    old_msg = MagicMock()
    old_msg.photo = [MagicMock(file_id="OLD_PHOTO")]
    old_msg.video = None
    old_msg.media_group_id = None
    old_msg.delete = AsyncMock()
    old_msg.answer = AsyncMock()

    callback = MagicMock()
    callback.message = old_msg

    # Текстовая заметка без фото
    await update_card_message(callback, make_bookmark(None), MagicMock())

    old_msg.delete.assert_awaited_once()
    old_msg.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_to_text_edits_in_place():
    old_msg = MagicMock()
    old_msg.photo = None
    old_msg.video = None
    old_msg.media_group_id = None
    old_msg.edit_text = AsyncMock()
    old_msg.delete = AsyncMock()
    old_msg.answer = AsyncMock()

    callback = MagicMock()
    callback.message = old_msg

    await update_card_message(callback, make_bookmark(None), MagicMock())

    old_msg.edit_text.assert_awaited_once()
    old_msg.delete.assert_not_awaited()
    old_msg.answer.assert_not_awaited()


if __name__ == "__main__":
    async def run_all():
        await test_photo_changes_when_switching_cards()
        print("test_photo_changes_when_switching_cards: PASSED")
        await test_same_photo_uses_caption_only()
        print("test_same_photo_uses_caption_only: PASSED")
        await test_video_changes_when_switching_cards()
        print("test_video_changes_when_switching_cards: PASSED")
        await test_text_to_photo_displays_photo()
        print("test_text_to_photo_displays_photo: PASSED")
        await test_photo_from_photos_json_is_displayed()
        print("test_photo_from_photos_json_is_displayed: PASSED")
        await test_photo_to_text_note_clears_photo()
        print("test_photo_to_text_note_clears_photo: PASSED")
        await test_text_to_text_edits_in_place()
        print("test_text_to_text_edits_in_place: PASSED")
        print("\nALL CARD MEDIA & DISPLAY TESTS PASSED!")

    asyncio.run(run_all())
