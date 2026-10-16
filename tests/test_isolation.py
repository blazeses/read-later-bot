import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, ContentType
from app.db.repositories import BookmarkRepository, TagRepository, UserRepository


@pytest_asyncio.fixture
async def async_test_session():
    # Use SQLite in-memory for testing repository multi-tenant isolation
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_user_data_isolation(async_test_session: AsyncSession):
    session = async_test_session
    user_repo = UserRepository(session)
    bookmark_repo = BookmarkRepository(session)
    tag_repo = TagRepository(session)

    user1_id = 11111
    user2_id = 22222

    await user_repo.get_or_create(user1_id)
    await user_repo.get_or_create(user2_id)

    # User 1 creates tags and bookmark
    u1_tags = await tag_repo.get_or_create_tags(user1_id, ["python", "rag"])
    b1 = await bookmark_repo.create(
        user_id=user1_id,
        content_type=ContentType.ARTICLE,
        url="https://example.com/u1-secret",
        normalized_url="https://example.com/u1-secret",
        title="Секретная статья пользователя 1",
        full_text="Конфиденциальная информация пользователя 1",
        summary="Саммари статьи 1",
        tags=u1_tags,
    )
    await session.commit()

    # User 2 creates bookmark
    u2_tags = await tag_repo.get_or_create_tags(user2_id, ["recipes"])
    b2 = await bookmark_repo.create(
        user_id=user2_id,
        content_type=ContentType.ARTICLE,
        url="https://example.com/u2-recipes",
        normalized_url="https://example.com/u2-recipes",
        title="Рецепты пользователя 2",
        full_text="Рецепт вкусного пирога",
        summary="Пирог",
        tags=u2_tags,
    )
    await session.commit()

    # 1. User 2 cannot get User 1's bookmark by ID
    assert await bookmark_repo.get_by_id(b1.id, user_id=user2_id) is None

    # 2. User 2 cannot get User 1's bookmark by normalized URL
    assert await bookmark_repo.get_by_normalized_url("https://example.com/u1-secret", user_id=user2_id) is None

    # 3. User 2's list_bookmarks returns only b2
    u2_items, u2_total = await bookmark_repo.list_bookmarks(user_id=user2_id)
    assert u2_total == 1
    assert u2_items[0].id == b2.id
    assert u2_items[0].title == "Рецепты пользователя 2"

    # 4. User 2 cannot delete User 1's bookmark
    deleted = await bookmark_repo.delete(b1.id, user_id=user2_id)
    assert deleted is False
    assert await bookmark_repo.get_by_id(b1.id, user_id=user1_id) is not None

    # 5. Tag isolation
    u1_stored_tags = await tag_repo.get_user_tags(user1_id)
    u2_stored_tags = await tag_repo.get_user_tags(user2_id)
    assert {t.name for t in u1_stored_tags} == {"python", "rag"}
    assert {t.name for t in u2_stored_tags} == {"recipes"}
