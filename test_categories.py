import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Bookmark, ContentType, User
from app.db.repositories import BookmarkRepository
from app.services.category_detector import detect_category
from app.bot.card_renderer import build_card_text


@pytest_asyncio.fixture
async def async_session():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await test_engine.dispose()


def test_category_detector_heuristics():
    # IT detection
    cat_it = detect_category(
        title="Быстрый гайд по FastAPI и PostgreSQL",
        text="Разработка асинхронных бэкенд сервисов на Python",
        url="https://github.com/fastapi/fastapi",
    )
    assert cat_it == "💼 Работа & IT"

    # Business & Finance
    cat_fin = detect_category(
        title="Как инвестировать в дивидендные акции в 2026",
        text="Анализ рынка, инфляция и доходность портфеля",
        url="https://tinkoff.ru/invest/stocks",
    )
    assert cat_fin == "📈 Бизнес & Финансы"

    # Media & Entertainment
    cat_media = detect_category(
        title="Новый трейлер фильма и обзор музыки",
        url="https://youtube.com/watch?v=12345",
    )
    assert cat_media == "🎬 Медиа & Развлечения"

    # LLM category normalization
    cat_norm = detect_category(
        title="Некоторый заголовок",
        llm_category="Наука & Образование",
    )
    assert cat_norm == "🔬 Наука & Образование"


@pytest.mark.asyncio
async def test_category_repository_and_card(async_session: AsyncSession):
    repo = BookmarkRepository(async_session)
    user = User(telegram_id=777888)
    async_session.add(user)
    await async_session.flush()

    bm1 = await repo.create(
        user_id=777888,
        content_type=ContentType.ARTICLE,
        title="Статья по Python",
        category="💼 Работа & IT",
    )
    bm2 = await repo.create(
        user_id=777888,
        content_type=ContentType.ARTICLE,
        title="Статья по Docker",
        category="💼 Работа & IT",
    )
    bm3 = await repo.create(
        user_id=777888,
        content_type=ContentType.ARTICLE,
        title="Инвестиции для начинающих",
        category="📈 Бизнес & Финансы",
    )
    await async_session.commit()

    # Verify counts
    counts = await repo.get_categories_with_counts(777888)
    assert len(counts) == 2
    count_dict = dict(counts)
    assert count_dict["💼 Работа & IT"] == 2
    assert count_dict["📈 Бизнес & Финансы"] == 1

    # Verify filtering by category
    it_bms, total_it = await repo.get_bookmarks_by_category(777888, "💼 Работа & IT")
    assert total_it == 2
    assert len(it_bms) == 2

    # Verify card text contains category badge
    card_text = build_card_text(bm1)
    assert "💼 Работа" in card_text
    assert "IT" in card_text


def test_category_button_callback_lengths_under_64_bytes():
    from app.bot.keyboards import get_categories_keyboard, get_bookmarks_list_keyboard, get_bookmark_card_keyboard
    from app.services.category_detector import CANONICAL_CATEGORIES, get_category_code
    import uuid

    dummy_counts = [(cat, 5) for cat in CANONICAL_CATEGORIES]
    cat_kb = get_categories_keyboard(dummy_counts)

    # 1. Check all buttons in category menu
    for row in cat_kb.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                cb_bytes = len(btn.callback_data.encode("utf-8"))
                assert cb_bytes <= 64, f"Button {btn.text} has callback_data {btn.callback_data} ({cb_bytes} bytes > 64)"

    # 2. Check bookmark list buttons inside each category
    class DummyBookmark:
        def __init__(self):
            self.id = uuid.uuid4()

    dummy_bookmarks = [DummyBookmark() for _ in range(5)]
    for cat in CANONICAL_CATEGORIES:
        code = get_category_code(cat)
        prefix = f"c_{code}"
        list_kb = get_bookmarks_list_keyboard(
            bookmarks=dummy_bookmarks,
            current_page=1,
            total_pages=5,
            prefix=prefix,
        )
        for row in list_kb.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    cb_bytes = len(btn.callback_data.encode("utf-8"))
                    assert cb_bytes <= 64, f"Category list button {btn.text} has callback_data {btn.callback_data} ({cb_bytes} bytes > 64)"

        # Check bookmark card buttons inside category
        bm_id = uuid.uuid4()
        card_kb = get_bookmark_card_keyboard(
            bookmark_id=bm_id,
            url="https://example.com",
            is_read=False,
            showing_full_text=False,
            nav_prefix=prefix,
            current_page=1,
            total_pages=5,
        )
        for row in card_kb.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    cb_bytes = len(btn.callback_data.encode("utf-8"))
                    assert cb_bytes <= 64, f"Category card button {btn.text} has callback_data {btn.callback_data} ({cb_bytes} bytes > 64)"


@pytest.mark.asyncio
async def test_category_code_lookup_and_variants(async_session: AsyncSession):
    repo = BookmarkRepository(async_session)
    user = User(telegram_id=999111)
    async_session.add(user)
    await async_session.flush()

    # Save with different variants
    await repo.create(
        user_id=999111,
        content_type=ContentType.ARTICLE,
        title="Python Guide",
        category="💼 Работа & IT",
    )
    await repo.create(
        user_id=999111,
        content_type=ContentType.ARTICLE,
        title="Linux Tips",
        category="it",
    )
    await repo.create(
        user_id=999111,
        content_type=ContentType.ARTICLE,
        title="Random Note",
        category="💡 Разное",
    )
    await async_session.commit()

    # Query with code "it"
    bms_it_code, count_it_code = await repo.get_bookmarks_by_category(999111, "it")
    assert count_it_code == 2

    # Query with canonical name
    bms_it_canon, count_it_canon = await repo.get_bookmarks_by_category(999111, "💼 Работа & IT")
    assert count_it_canon == 2

    # Query with code "other"
    bms_other, count_other = await repo.get_bookmarks_by_category(999111, "other")
    assert count_other == 1

    # Aggregated counts
    cats = await repo.get_categories_with_counts(999111)
    cat_dict = dict(cats)
    assert cat_dict["💼 Работа & IT"] == 2
    assert cat_dict["💡 Разное"] == 1


@pytest.mark.asyncio
async def test_render_categories_menu_with_legacy_and_none_bookmarks(async_session: AsyncSession):
    from app.bot.handlers.manage import render_categories_menu

    repo = BookmarkRepository(async_session)
    user = User(telegram_id=555444)
    async_session.add(user)
    await async_session.flush()

    # Create bookmarks with category=None and None in fields
    await repo.create(
        user_id=555444,
        content_type=ContentType.ARTICLE,
        title=None,
        full_text="Бэкенд сервис на FastAPI и Docker",
        category=None,
    )
    await repo.create(
        user_id=555444,
        content_type=ContentType.NOTE,
        title="Заметка про инвестиции",
        summary="Криптовалюты и акции",
        category=None,
    )
    await async_session.commit()

    text, kb = await render_categories_menu(555444, async_session)
    assert text is not None
    assert "Категории материалов" in text
    assert kb is not None
    assert len(kb.inline_keyboard) > 0
