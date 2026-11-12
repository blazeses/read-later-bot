import pytest
from unittest.mock import AsyncMock, MagicMock
from app.extractors.url_utils import (
    normalize_emoji_digits,
    heal_placeholder_number_emojis,
    resolve_telegram_custom_emojis,
)
from app.db.models import Bookmark, ContentType
from app.bot.card_renderer import build_card_text


def test_normalize_emoji_digits():
    raw = "1️⃣0️⃣ причин использовать 5️⃣ способов и 🔟 трюков ① ⑳ ❶ ０"
    res = normalize_emoji_digits(raw)
    assert "10 причин использовать 5 способов и 10 трюков 1 20 1 0" in res


def test_heal_placeholder_number_emojis_twenty_items():
    text = (
        "Забираем 🔴🔴 мощнейших ИИ-тулз для Claude Opus 5.5 — все лежат в открытом доступе совершенно БЕСПЛАТНО.\n\n"
        "С ними можно создавать анимации...\n\n"
        "Сама подборка:\n"
        "🔴 Remotion — создание видео через React;\n"
        "🔴 Remotion Skills — готовые навыки для ИИ-агентов по созданию видео;\n"
        "🔴 Anime.js — сложные анимации и управление таймалайнами;\n"
        "🔴 Motion — анимация интерфейсов и переходов;\n"
        "🔴 Manim — анимированные схемы, формулы и объяснения;\n"
        "🔴 Lottie — лёгкие векторные анимации;\n"
        "🔴 MCP for Blender — создание и редактирование 3D-сцен через Claude;\n"
        "🔴 Three.js — 3D-графика и движения камеры;\n"
        "🔴 Three GPU Pathtracer — реалистичное освещение и отражения;\n"
        "🔴 Jolt Physics — физика столкновений и движения;\n"
        "🔴 postprocessing — свечение, размытие и цветокоррекция;\n"
        "🔴 Three.quarks — частицы и визуальные эффекты;\n"
        "🔴 Tweakpane — настройка параметров сцены в реальном времени;\n"
        "🔴 ComfyUI — визуальные процессы ИИ-генерации;\n"
        "🔴 CosyVoice — озвучка на разных языках;\n"
        "🔴 librosa — анализ музыки и синхронизация анимации с битом;\n"
        "🔴 WhisperX — точные субтитры с таймкодами;\n"
        "🔴 OBS Studio — запись экрана;\n"
        "🔴 LosslessCut — быстрый монтаж без потери качества;\n"
        "🔴 FFmpeg — обработка, кодирование и экспорт видео.\n\n"
        "Сохраняем и используем."
    )

    healed = heal_placeholder_number_emojis(text)
    assert "Забираем 20 мощнейших ИИ-тулз" in healed
    assert "• Remotion — создание видео через React;" in healed
    assert "• FFmpeg — обработка, кодирование и экспорт видео." in healed
    assert "🔴" not in healed


def test_heal_placeholder_numbered_bullets():
    text = (
        "ТОП-🔴🔴 сервисов:\n"
        "🔴 1. Первый сервис\n"
        "🔴 2. Второй сервис\n"
        "🔴 3. Третий сервис\n"
    )
    healed = heal_placeholder_number_emojis(text)
    assert "ТОП-3 сервисов:" in healed
    assert "1. Первый сервис" in healed
    assert "2. Второй сервис" in healed
    assert "3. Третий сервис" in healed


@pytest.mark.asyncio
async def test_resolve_telegram_custom_emojis_with_bot():
    text = "Забираем 🔴🔴 мощнейших"
    # 🔴 in UTF-16: offset 9, len 2; offset 11, len 2
    ent1 = MagicMock(type="custom_emoji", offset=9, length=2, custom_emoji_id="emoji_id_2")
    ent2 = MagicMock(type="custom_emoji", offset=11, length=2, custom_emoji_id="emoji_id_0")

    sticker1 = MagicMock(custom_emoji_id="emoji_id_2", emoji="2️⃣", set_name=None)
    sticker2 = MagicMock(custom_emoji_id="emoji_id_0", emoji="0️⃣", set_name=None)

    bot = AsyncMock()
    bot.get_custom_emoji_stickers.return_value = [sticker1, sticker2]

    res = await resolve_telegram_custom_emojis(bot, text, [ent1, ent2])
    assert "Забираем 20 мощнейших" == res


def test_card_renderer_long_note_html_tag_unescaped():
    bm = Bookmark(
        id=1,
        user_id=123,
        content_type=ContentType.NOTE,
        title="Тестовая заметка",
        full_text="А" * 800,  # exceeds limit 750
        summary=None,
        user_comment=None,
        tags=[],
    )
    html_card = build_card_text(bm, for_caption=True)
    assert "<i>(заметка длинная, нажмите «📖 Читать полностью» ниже)</i>" in html_card
    assert "&lt;i&gt;" not in html_card
