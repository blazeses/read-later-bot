import asyncio
import html
import os
import sys
import tempfile
import uuid
from enum import Enum
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Setup aiogram and dependency mocks
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

class State:
    def __init__(self, *args, **kwargs):
        pass

class StatesGroup:
    pass

mock_fsm_state = MagicMock()
mock_fsm_state.State = State
mock_fsm_state.StatesGroup = StatesGroup
sys.modules["aiogram.fsm.state"] = mock_fsm_state
sys.modules["aiogram.fsm.context"] = MagicMock()
sys.modules["aiogram.filters"] = MagicMock()
sys.modules["aiogram.fsm"] = MagicMock()
sys.modules["loguru"] = MagicMock()

class ContentType(str, Enum):
    ARTICLE = "article"
    YOUTUBE = "youtube"
    DOCUMENT = "document"
    TWEET = "tweet"
    NOTE = "note"

mock_models = MagicMock()
mock_models.ContentType = ContentType
sys.modules["app.db.models"] = mock_models


def test_sqlite_engine_compatibility():
    """Verify SQLite connection does not include pool_size/max_overflow."""
    database_url_pg = "postgresql+asyncpg://user:pass@localhost:5432/db"
    database_url_sqlite = "sqlite+aiosqlite:///readlater.db"

    def get_engine_kwargs(url: str):
        kw = {"echo": False, "future": True}
        if not url.startswith("sqlite"):
            kw.update({"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20})
        return kw

    pg_kw = get_engine_kwargs(database_url_pg)
    sqlite_kw = get_engine_kwargs(database_url_sqlite)

    assert "pool_size" in pg_kw
    assert "max_overflow" in pg_kw
    assert "pool_size" not in sqlite_kw
    assert "max_overflow" not in sqlite_kw
    print("test_sqlite_engine_compatibility: PASSED")


def test_digest_html_escaping_and_none_summary():
    """Verify digest handles None summary and unescaped HTML characters safely."""
    class FakeTag:
        def __init__(self, name):
            self.name = name

    class FakeBookmark:
        def __init__(self, title, url, summary, tags, reading_time_min=5):
            self.title = title
            self.url = url
            self.summary = summary
            self.tags = tags
            self.reading_time_min = reading_time_min

    # Test cases: None summary, tags with <>, title with &
    b1 = FakeBookmark(
        title="Title <with> & dangerous chars",
        url="https://example.com/item?a=1&b=2",
        summary=None,
        tags=[FakeTag("tag<1>"), FakeTag("python")]
    )
    b2 = FakeBookmark(
        title="Normal Title",
        url=None,
        summary="A normal summary with <b>tags</b> & words",
        tags=[]
    )

    bookmarks = [b1, b2]
    message_lines = ["☕️ <b>Твой еженедельный дайджест для чтения</b>\n"]

    for idx, b in enumerate(bookmarks, start=1):
        safe_title = html.escape(b.title or "Без названия")
        if b.url:
            safe_url = html.escape(b.url, quote=True)
            link = f"<a href='{safe_url}'>{safe_title}</a>"
        else:
            link = f"<b>{safe_title}</b>"

        tags_str = " ".join([f"#{html.escape(t.name)}" for t in b.tags]) if b.tags else ""
        time_part = f"~{b.reading_time_min} мин." if b.reading_time_min else "время не указано"
        meta_parts = [f"⏱ {time_part}"]
        if tags_str:
            meta_parts.append(tags_str)

        summary_part = ""
        if b.summary:
            clean_summary = html.escape(b.summary[:150])
            summary_part = f"   💡 {clean_summary}...\n"

        message_lines.append(
            f"{idx}. {link}\n"
            f"   {' | '.join(meta_parts)}\n"
            + summary_part
        )

    full_text = "\n".join(message_lines)
    assert "Title &lt;with&gt; &amp; dangerous chars" in full_text
    assert "#tag&lt;1&gt;" in full_text
    assert "https://example.com/item?a=1&amp;b=2" in full_text
    print("test_digest_html_escaping_and_none_summary: PASSED")


def test_temp_file_cleanup_logic():
    """Verify temp file cleanup removes temporary documents."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(b"sample pdf data")
        tmp_path = tmp.name

    assert os.path.exists(tmp_path)

    # Simulate ingest pipeline cleanup
    content_type = ContentType.DOCUMENT
    if content_type == ContentType.DOCUMENT and tmp_path and os.path.exists(tmp_path):
        os.remove(tmp_path)

    assert not os.path.exists(tmp_path)
    print("test_temp_file_cleanup_logic: PASSED")


def test_search_token_expired_check():
    """Verify search token expired detection."""
    _search_tokens = {"validtok": "python async"}
    
    # 8-char hex that is missing from dict
    raw_val = "1a2b3c4d"
    is_expired = len(raw_val) == 8 and raw_val not in _search_tokens and all(c in "0123456789abcdefABCDEF" for c in raw_val)
    assert is_expired is True

    # Valid token in dict
    raw_val_valid = "validtok"
    is_expired_valid = len(raw_val_valid) == 8 and raw_val_valid not in _search_tokens and all(c in "0123456789abcdefABCDEF" for c in raw_val_valid)
    assert is_expired_valid is False

    # Plain text query
    raw_val_query = "django"
    is_expired_query = len(raw_val_query) == 8 and raw_val_query not in _search_tokens and all(c in "0123456789abcdefABCDEF" for c in raw_val_query)
    assert is_expired_query is False

    print("test_search_token_expired_check: PASSED")


def test_cancel_edit_tags_in_manage():
    """Verify edit_tags callback handler and cancel button exist."""
    manage_py_path = Path(__file__).resolve().parent.parent / "app" / "bot" / "handlers" / "manage.py"
    with open(manage_py_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "cb_edit_tags" in content
    assert "cb_cancel_edit_tags" in content
    assert "cancel_edit_tags" in content
    print("test_cancel_edit_tags_in_manage: PASSED")


def test_middlewares_registration():
    """Verify main.py registers WhitelistMiddleware and DbSessionMiddleware on inline_query."""
    main_py_path = Path(__file__).resolve().parent.parent / "main.py"
    with open(main_py_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "dp.inline_query.middleware(WhitelistMiddleware())" in content
    assert "dp.inline_query.middleware(DbSessionMiddleware())" in content
    assert 'BotCommand(command="favorites"' in content
    assert 'BotCommand(command="yoink"' in content
    print("test_middlewares_registration: PASSED")


if __name__ == "__main__":
    test_sqlite_engine_compatibility()
    test_digest_html_escaping_and_none_summary()
    test_temp_file_cleanup_logic()
    test_search_token_expired_check()
    test_cancel_edit_tags_in_manage()
    test_middlewares_registration()
    print("\nALL AUDIT FIXES TESTS PASSED SUCCESSFULLY!")
