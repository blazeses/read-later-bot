import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Bookmark, ContentType, Reminder, User
from app.db.repositories import ReminderRepository
from app.services.reminder import ReminderScheduler


@pytest_asyncio.fixture
async def async_session():
    # In-memory SQLite for repository testing
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_reminder_repository_lifecycle(async_session: AsyncSession):
    repo = ReminderRepository(async_session)
    user_id = 999111
    bm_id = uuid.uuid4()

    # Create dummy user and bookmark
    user = User(telegram_id=user_id, digest_enabled=True)
    async_session.add(user)
    bm = Bookmark(id=bm_id, user_id=user_id, title="Тестовая статья", content_type=ContentType.ARTICLE)
    async_session.add(bm)
    await async_session.commit()

    # 1. Create reminder for 5 minutes ago (due now)
    remind_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    reminder = await repo.create_or_update(user_id, bm_id, remind_time)
    await async_session.commit()

    assert reminder is not None
    assert reminder.is_sent is False
    assert reminder.bookmark_id == bm_id

    # 2. Query due reminders
    now = datetime.now(timezone.utc)
    due = await repo.get_due_reminders(now)
    assert len(due) == 1
    assert due[0].id == reminder.id

    # 3. Mark as sent
    await repo.mark_as_sent(reminder.id)
    await async_session.commit()

    # 4. Verify it's no longer due
    due_after = await repo.get_due_reminders(now)
    assert len(due_after) == 0

    # 5. Delete test
    # Create another reminder and delete it
    rem2 = await repo.create_or_update(user_id, bm_id, datetime.now(timezone.utc) + timedelta(hours=2))
    await async_session.commit()
    deleted = await repo.delete_for_bookmark(user_id, bm_id)
    await async_session.commit()
    assert deleted is True

    active = await repo.get_active_for_bookmark(user_id, bm_id)
    assert active is None


@pytest.mark.asyncio
async def test_reminder_scheduler_delivery():
    bot_mock = MagicMock()
    bot_mock.send_message = AsyncMock()

    scheduler = ReminderScheduler(bot=bot_mock)

    bm = Bookmark(
        id=uuid.uuid4(),
        user_id=12345,
        title="Архитектура RAG",
        content_type=ContentType.ARTICLE,
        reading_time_min=5,
        summary="Краткая суть статьи о RAG",
        url="https://example.com/rag",
    )

    await scheduler._deliver_reminder(12345, bm)

    bot_mock.send_message.assert_awaited_once()
    call_kwargs = bot_mock.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 12345
    assert "Напоминание о материале!" in call_kwargs["text"]
    assert "Архитектура RAG" in call_kwargs["text"]
    assert call_kwargs["reply_markup"] is not None


def test_custom_reminder_parser_and_threshold():
    from app.services.date_parser import parse_user_reminder_time, MSK_TZ

    now_utc = datetime.now(timezone.utc)

    # 1. Below 15 minutes threshold must fail
    res, err = parse_user_reminder_time("10 мин")
    assert res is None
    assert "15 минут" in err

    res, err = parse_user_reminder_time("5 минут")
    assert res is None
    assert "15 минут" in err

    res, err = parse_user_reminder_time("через 14 минут")
    assert res is None
    assert "15 минут" in err

    # 2. Exactly or above 15 minutes must succeed
    res, err = parse_user_reminder_time("15 мин")
    assert err is None
    assert res is not None
    assert res >= now_utc + timedelta(minutes=14, seconds=50)

    res, err = parse_user_reminder_time("через 30 минут")
    assert err is None
    assert res is not None
    assert res >= now_utc + timedelta(minutes=29)

    res, err = parse_user_reminder_time("2 часа")
    assert err is None
    assert res is not None
    assert res >= now_utc + timedelta(hours=1, minutes=59)

    # 3. Tomorrow date
    res, err = parse_user_reminder_time("завтра в 15:00")
    assert err is None
    assert res is not None
    local_parsed = res.astimezone(MSK_TZ)
    assert local_parsed.hour == 15
    assert local_parsed.minute == 0

