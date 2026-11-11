import math
import uuid
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Bookmark, ContentType, User
from app.db.repositories import BookmarkRepository
from app.bot.keyboards import get_bookmark_card_keyboard, get_bookmarks_list_keyboard
from app.bot.handlers.bookmarks import cb_delete_bookmark


@pytest_asyncio.fixture
async def async_session():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await test_engine.dispose()


def test_delete_button_callback_format():
    bm_id = uuid.uuid4()
    kb = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        is_note=True,
        nav_prefix="rl",
        current_page=2,
    )
    all_btns = [btn for row in kb.inline_keyboard for btn in row]
    del_btns = [btn for btn in all_btns if "Удалить" in btn.text]
    assert len(del_btns) == 1
    cb_data = del_btns[0].callback_data
    assert cb_data.startswith(f"delete:{bm_id}:rl:2")


@pytest.mark.asyncio
async def test_cb_delete_bookmark_returns_panel(async_session: AsyncSession):
    repo = BookmarkRepository(async_session)
    user_id = 998877
    user = User(telegram_id=user_id)
    async_session.add(user)
    await async_session.flush()

    bm1 = await repo.create(user_id=user_id, content_type=ContentType.NOTE, title="Первая заметка", full_text="Текст 1")
    bm2 = await repo.create(user_id=user_id, content_type=ContentType.NOTE, title="Вторая заметка", full_text="Текст 2")
    await async_session.commit()

    callback = AsyncMock()
    callback.from_user.id = user_id
    callback.data = f"delete:{bm1.id}:rl:1"
    callback.message = AsyncMock()
    callback.message.photo = None
    callback.message.video = None
    callback.message.animation = None

    await cb_delete_bookmark(callback, async_session)

    # Bookmark 1 deleted
    deleted_check = await repo.get_by_id(bm1.id, user_id)
    assert deleted_check is None

    # Bookmark 2 remains
    remaining = await repo.get_by_id(bm2.id, user_id)
    assert remaining is not None

    # Message was edited with panel containing remaining notes
    assert callback.message.edit_text.called
    call_args = callback.message.edit_text.call_args
    panel_text = call_args[0][0] if call_args[0] else call_args[1].get("text", "")
    assert "Вторая заметка" in panel_text
    assert "🗑 <i>Закладка удалена.</i>" in panel_text

    # Reply markup has list keyboard with selection button [1]
    kb = call_args[1].get("reply_markup")
    assert kb is not None
    btns = [b for row in kb.inline_keyboard for b in row]
    assert any(b.text == "[1]" for b in btns)


@pytest.mark.asyncio
async def test_cb_delete_last_bookmark_shows_empty(async_session: AsyncSession):
    repo = BookmarkRepository(async_session)
    user_id = 998878
    user = User(telegram_id=user_id)
    async_session.add(user)
    await async_session.flush()

    bm = await repo.create(user_id=user_id, content_type=ContentType.NOTE, title="Единственная заметка", full_text="Текст")
    await async_session.commit()

    callback = AsyncMock()
    callback.from_user.id = user_id
    callback.data = f"delete:{bm.id}:rl:1"
    callback.message = AsyncMock()
    callback.message.photo = None
    callback.message.video = None
    callback.message.animation = None

    await cb_delete_bookmark(callback, async_session)

    assert callback.message.edit_text.called
    call_args = callback.message.edit_text.call_args
    text = call_args[0][0] if call_args[0] else call_args[1].get("text", "")
    assert "больше нет сохраненных закладок" in text
