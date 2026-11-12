import pytest
import pytest_asyncio
import uuid
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Bookmark, ContentType, User, UserCategory
from app.db.repositories import BookmarkRepository, UserCategoryRepository
from app.bot.keyboards import (
    get_category_picker_keyboard,
    get_new_category_options_keyboard,
    get_bookmark_card_keyboard,
)


@pytest_asyncio.fixture
async def async_session():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_user_category_repository_crud(async_session: AsyncSession):
    repo = UserCategoryRepository(async_session)
    user = User(telegram_id=123456)
    async_session.add(user)
    await async_session.flush()

    # 1. Create custom category
    cat1 = await repo.create(123456, "Рецепты десертов")
    assert cat1.name == "Рецепты десертов"
    assert cat1.code.startswith("u_")

    # 2. Idempotent creation
    cat1_dup = await repo.create(123456, "рецепты десертов")
    assert cat1_dup.id == cat1.id

    # 3. List categories
    cat2 = await repo.create(123456, "💼 Работа & IT")
    cats = await repo.get_user_categories(123456)
    assert len(cats) == 2

    # 4. Delete category
    deleted = await repo.delete(123456, cat1.id)
    assert deleted is True
    cats_after = await repo.get_user_categories(123456)
    assert len(cats_after) == 1
    assert cats_after[0].id == cat2.id


@pytest.mark.asyncio
async def test_bookmark_update_category(async_session: AsyncSession):
    bm_repo = BookmarkRepository(async_session)
    user = User(telegram_id=789012)
    async_session.add(user)
    await async_session.flush()

    bm = await bm_repo.create(
        user_id=789012,
        content_type=ContentType.ARTICLE,
        title="Тестовая статья",
        category="💡 Разное",
    )
    assert bm.category == "💡 Разное"

    updated = await bm_repo.update_category(bm.id, 789012, "Учёба и курсы")
    assert updated is not None
    assert updated.category == "Учёба и курсы"


def test_category_picker_keyboards_callback_byte_length():
    bm_id = uuid.uuid4()
    dummy_cats = [
        UserCategory(id=uuid.uuid4(), user_id=1, name="Рецепты", code="u_12345678"),
        UserCategory(id=uuid.uuid4(), user_id=1, name="Университет и лекции", code="u_87654321"),
    ]

    # 1. Category picker keyboard
    picker_kb = get_category_picker_keyboard(
        bookmark_id=bm_id,
        user_categories=dummy_cats,
        detected_category="💼 Работа & IT",
    )
    for row in picker_kb.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                cb_bytes = len(btn.callback_data.encode("utf-8"))
                assert cb_bytes <= 64, f"Picker button {btn.text} callback_data {btn.callback_data} exceeds 64 bytes"

    # 2. Options keyboard (5 presets + custom)
    options_kb = get_new_category_options_keyboard(bookmark_id=bm_id)
    assert len(options_kb.inline_keyboard) >= 7  # 5 presets + 1 custom + 1 back
    for row in options_kb.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                cb_bytes = len(btn.callback_data.encode("utf-8"))
                assert cb_bytes <= 64, f"Option button {btn.text} callback_data {btn.callback_data} exceeds 64 bytes"

    # 3. Card keyboard has '📁 Рубрика' button
    card_kb = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url="https://example.com",
        is_read=False,
        showing_full_text=False,
    )
    has_cat_button = any(
        any("Рубрика" in btn.text for btn in row)
        for row in card_kb.inline_keyboard
    )
    assert has_cat_button is True
