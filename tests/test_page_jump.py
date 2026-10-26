import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock aiogram if not installed
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
            self._buttons = []
            self._rows = []

        def row(self, *buttons):
            self._rows.append(list(buttons))

        def button(self, text, callback_data=None, url=None):
            self._buttons.append(InlineKeyboardButton(text=text, callback_data=callback_data, url=url))

        def adjust(self, repeat):
            chunk = []
            for b in self._buttons:
                chunk.append(b)
                if len(chunk) == repeat:
                    self._rows.append(chunk)
                    chunk = []
            if chunk:
                self._rows.append(chunk)
            self._buttons = []

        def as_markup(self):
            rows = list(self._rows)
            if self._buttons:
                rows.append(list(self._buttons))
            return InlineKeyboardMarkup(inline_keyboard=rows)

    sys.modules["aiogram"] = MagicMock()
    sys.modules["aiogram.types"] = MagicMock(
        InlineKeyboardButton=InlineKeyboardButton,
        InlineKeyboardMarkup=InlineKeyboardMarkup,
        KeyboardButton=MagicMock(),
        ReplyKeyboardMarkup=MagicMock(),
    )
    sys.modules["aiogram.utils.keyboard"] = MagicMock(
        InlineKeyboardBuilder=InlineKeyboardBuilder,
        ReplyKeyboardBuilder=MagicMock(),
    )

from app.bot.keyboards import (
    get_page_picker_keyboard,
    get_bookmarks_list_keyboard,
    get_bookmark_card_keyboard,
    make_safe_callback,
)


def test_page_picker_small():
    # 5 pages, current page is 2
    kb = get_page_picker_keyboard(total_pages=5, current_page=2, prefix="rl", is_card=False)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in all_btns]
    cbs = [b.callback_data for b in all_btns]

    # Should contain 1, • 2 •, 3, 4, 5 and « Отмена
    assert "1" in texts
    assert "• 2 •" in texts
    assert "3" in texts
    assert "4" in texts
    assert "5" in texts
    assert "« Отмена" in texts

    # Check callback formats
    cancel_btn = [b for b in all_btns if b.text == "« Отмена"][0]
    assert cancel_btn.callback_data == "rl:2"

    p2_btn = [b for b in all_btns if b.text == "• 2 •"][0]
    assert p2_btn.callback_data == "rl:2"

    p4_btn = [b for b in all_btns if b.text == "4"][0]
    assert p4_btn.callback_data == "rl:4"

    for cb in cbs:
        assert len(cb.encode("utf-8")) <= 64
    print("test_page_picker_small: PASSED")


def test_page_picker_card():
    # 10 pages for card search with token
    token = "a1b2c3d4"
    kb = get_page_picker_keyboard(total_pages=10, current_page=4, prefix="sp", extra_data=token, is_card=True)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in all_btns]
    cbs = [b.callback_data for b in all_btns]

    assert "• 4 •" in texts
    assert "« Отмена" in texts

    p1_btn = [b for b in all_btns if b.text == "1"][0]
    assert p1_btn.callback_data == f"sp:1:{token}"

    cancel_btn = [b for b in all_btns if b.text == "« Отмена"][0]
    assert cancel_btn.callback_data == f"sp:4:{token}"

    for cb in cbs:
        assert len(cb.encode("utf-8")) <= 64
    print("test_page_picker_card: PASSED")


def test_page_picker_large():
    # 80 pages, current page is 33
    kb = get_page_picker_keyboard(total_pages=80, current_page=33, prefix="filter_tag", extra_data="python", is_card=True)
    all_btns = [b for row in kb.inline_keyboard for b in row]
    texts = [b.text for b in all_btns]
    cbs = [b.callback_data for b in all_btns]

    # Should contain 1, 80, window around 33 (30..36), milestones (10, 20...)
    assert "1" in texts
    assert "80" in texts
    assert "• 33 •" in texts
    assert "« Отмена" in texts

    # Total buttons should be concise and well-paginated
    assert len(all_btns) <= 30

    for cb in cbs:
        assert len(cb.encode("utf-8")) <= 64
    print("test_page_picker_large: PASSED")


def test_bookmarks_list_page_jump_button():
    class MockBookmark:
        def __init__(self, id, title):
            self.id = id
            self.title = title
            self.is_pinned = False
            self.content_type = None

    bms = [MockBookmark(uuid.uuid4(), f"BM {i}") for i in range(3)]
    kb = get_bookmarks_list_keyboard(bms, current_page=3, total_pages=7, prefix="rl")
    all_btns = [b for row in kb.inline_keyboard for b in row]

    page_btn = [b for b in all_btns if "3 / 7" in b.text]
    assert len(page_btn) == 1
    assert page_btn[0].callback_data == "jump_p:rl:7:3"
    print("test_bookmarks_list_page_jump_button: PASSED")


def test_bookmark_card_page_jump_button():
    bm_id = uuid.uuid4()
    kb = get_bookmark_card_keyboard(
        bookmark_id=bm_id,
        url="https://test.com",
        is_read=False,
        showing_full_text=True,
        nav_prefix="sp",
        current_page=2,
        total_pages=9,
        extra_data="tok123",
    )
    all_btns = [b for row in kb.inline_keyboard for b in row]
    page_btn = [b for b in all_btns if "2 / 9" in b.text]
    assert len(page_btn) == 1
    assert page_btn[0].callback_data == "jump_c:sp:9:2:tok123"
    print("test_bookmark_card_page_jump_button: PASSED")


if __name__ == "__main__":
    test_page_picker_small()
    test_page_picker_card()
    test_page_picker_large()
    test_bookmarks_list_page_jump_button()
    test_bookmark_card_page_jump_button()
    print("\nALL PAGE JUMP TESTS PASSED SUCCESSFULLY!")
