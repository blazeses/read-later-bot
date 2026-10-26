import csv
import io
import json
import zipfile
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import get_main_reply_keyboard
from app.db.repositories import BookmarkRepository, UserRepository

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    user_repo = UserRepository(session)
    await user_repo.get_or_create(user_id)

    text = (
        "🚀 <b>Добро пожаловать в ReadLater!</b>\n\n"
        "Твой личный органайзер и умная копилка: сохраняй важные мысли, посты, статьи и файлы в одном месте, чтобы никогда их не потерять.\n\n"
        "💡 <b>Что можно делать прямо сейчас:</b>\n"
        "<b>1.</b> <b>Просто напиши или перешли сообщение</b> — любую мысль, заметку, список дел или пост из другого канала. Бот сохранит его 1:1 в твои закладки!\n"
        "<b>2.</b> <b>Или отправь ссылку</b> — на статью, видео YouTube или пост в Twitter/X. ИИ мгновенно уберёт рекламу и выделит главное.\n"
        "<b>3.</b> <b>Сохраняй файлы и архивы</b> — PDF, DOCX и ZIP/RAR (до 2 ГБ бесплатно, до 4 ГБ с Premium) со скачиванием обратно в 1 клик.\n\n"
        "📌 <b>Удобство и порядок:</b>\n"
        "• Закрепляй главные заметки кнопкой <b>«📌 Закрепить»</b> — они всегда будут на самом верху списка.\n"
        "• Быстрый поиск по смыслу через <code>/search</code> и удобное меню внизу.\n\n"
        "👇 <b>Попробуй прямо сейчас: отправь сюда любой текст/заметку или нажми кнопку ниже:</b>"
    )

    demo_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Попробовать на примере", callback_data="show_demo_bookmark")],
            [InlineKeyboardButton(text="📚 Подробнее о возможностях", callback_data="show_onboarding_help")],
        ]
    )
    await message.answer(text, reply_markup=demo_kb, parse_mode="HTML")
    await message.answer("👇 Главное меню доступно на кнопках внизу экрана:", reply_markup=get_main_reply_keyboard())


@router.callback_query(F.data == "show_demo_bookmark")
async def cb_show_demo_bookmark(callback: CallbackQuery):
    demo_text = (
        "🌐 <b>Статья</b> | ⏱ ~4 мин | 👤 Martin Fowler | 🌐 martinfowler.com\n"
        "📌 <b>Архитектура современных AI-агентов и паттерн Memory Bank</b>\n\n"
        "Статус: 📖 Не прочитано | #ai #архитектура #deepseek\n\n"
        "📄 <b>Содержание:</b>\n"
        "Традиционные LLM ограничены размером контекста, поэтому память агента выносится во внешний векторный индекс. "
        "Паттерн Memory Bank разделяет кратковременную оперативную память диалога и долговременную базу знаний. "
        "Для точного поиска используется гибридная модель: полнотекстовый поиск BM25 + семантический эмбеддинг через RRF."
    )
    demo_card_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ В избранное", callback_data="demo_fav"),
                InlineKeyboardButton(text="🏷 Сменить тег", callback_data="demo_tag"),
            ],
            [
                InlineKeyboardButton(text="💡 Саммари", callback_data="demo_fulltext"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data="demo_del"),
            ],
            [
                InlineKeyboardButton(text="⏰ Напомнить завтра", callback_data="demo_remind"),
                InlineKeyboardButton(text="🌐 Оригинал", url="https://martinfowler.com"),
            ],
        ]
    )
    await callback.message.answer(demo_text, reply_markup=demo_card_kb, parse_mode="HTML")
    await callback.answer("Пример карточки сгенерирован!")


@router.callback_query(F.data == "show_onboarding_help")
async def cb_show_onboarding_help(callback: CallbackQuery):
    help_text = (
        "📚 <b>Возможности бота «ReadLater»:</b>\n\n"
        "• <b>Любые статьи и блоги</b> — вырезает баннеры, оставляет только чистый текст.\n"
        "• <b>Видео YouTube</b> — скачивает субтитры и делает выжимку содержания.\n"
        "• <b>Посты X (Twitter)</b> — сохраняет текст твита и прикрепленные фото.\n"
        "• <b>Документы и архивы (ZIP, PDF, DOCX)</b> — архивы до 2 ГБ (до 4 ГБ с Premium) сохраняются мгновенно со скачиванием обратно в 1 клик.\n"
        "• <b>Еженедельный дайджест</b> — по понедельникам напоминает о 3 отложенных материалах (/digest).\n"
        "• <b>Инлайн-режим</b> — пишите <code>@bot_name запрос</code> прямо в любом чате, чтобы поделиться закладкой!\n"
        "• <b>Экспорт</b> — выгружайте базу в Obsidian Markdown, CSV или JSON через команду /export."
    )
    await callback.message.answer(help_text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("demo_"))
async def cb_demo_interactions(callback: CallbackQuery):
    actions = {
        "demo_fav": "⭐ Материал добавлен в избранное!",
        "demo_tag": "🏷 В реальной карточке здесь открывается редактирование тегов.",
        "demo_fulltext": "📄 Здесь отображается полный извлеченный текст статьи.",
        "demo_del": "🗑 Демо-карточка удалена.",
        "demo_remind": "⏰ Напоминание установлено на завтра (09:00)!",
    }
    action_text = actions.get(callback.data, "Действие выполнено.")
    if callback.data == "demo_del":
        await callback.message.delete()
    else:
        await callback.answer(action_text, show_alert=True)


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    text = (
        "📋 <b>Главное меню:</b>\n"
        "Выберите действие ниже на кнопках клавиатуры.\n"
        "<i>(Панель можно свернуть кнопкой «🔽 Скрыть меню» или стрелкой клавиатуры)</i>"
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(Command("add"))
@router.message(F.text.in_({
    "➕ Загрузить заметку", "Загрузить заметку",
    "➕ Добавить заметку", "Добавить заметку",
    "➕ Добавить статью", "Добавить статью",
}))
async def btn_upload_note(message: Message):
    text = (
        "✍️ <b>Добавление новой заметки или материала:</b>\n\n"
        "Вы можете отправить прямо сюда в чат:\n"
        "• <b>Текст, мысль или цитату</b> — бот создаст заметку и подберёт теги\n"
        "• <b>Фотографию с подписью</b> — сохранит изображение и текст\n"
        "• <b>Ссылку на статью, YouTube или X</b> — скачает контент и сделает выжимку\n"
        "• <b>Документ (PDF, DOCX)</b> — проиндексирует файл для поиска\n\n"
        "👇 <i>Отправьте ссылку, текст или файл следующим сообщением!</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("hide"))
@router.message(F.text.in_({"🔽 Скрыть меню", "Скрыть меню", "Закрыть меню"}))
async def cmd_hide_keyboard(message: Message):
    text = (
        "⌨️ <b>Нижняя панель скрыта.</b>\n\n"
        "• Чтобы вернуть кнопки меню, отправьте команду /menu\n"
        "• Также вы всегда можете использовать кнопку <b>[/]</b> в строке ввода."
    )
    await message.answer(text, reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")


@router.message(F.text.in_({"📥 Мои закладки", "Мои закладки"}))
@router.message(Command("bookmarks"))
async def btn_my_bookmarks(message: Message, session: AsyncSession):
    from app.bot.handlers.manage import cmd_recent
    await cmd_recent(message, session)


@router.message(F.text.in_({"⚙️ Настройки", "Настройки"}))
@router.message(Command("settings"))
async def btn_settings(message: Message, session: AsyncSession):
    user_repo = UserRepository(session)
    user = await user_repo.get_or_create(message.from_user.id)
    d_status = "включен ✅" if user.digest_enabled else "выключен ⏸"

    text = (
        "⚙️ <b>Настройки вашего профиля:</b>\n\n"
        f"• ☕️ <b>Еженедельный дайджест:</b> <b>{d_status}</b>\n"
        "  <i>Каждый понедельник бот присылает подборку из 3 непрочитанных материалов со ссылками, тегами и временем на чтение, помогая разбирать сохранённое.</i>\n\n"
        "• 🔍 <b>Инлайн-режим:</b> активен (вводите <code>@имя_бота</code> в любом диалоге для быстрой отправки закладок)\n\n"
        "Вы можете переключить статус дайджеста кнопкой ниже или выгрузить данные:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="☕️ Переключить дайджест", callback_data="toggle_digest_btn")],
            [InlineKeyboardButton(text="💾 Экспорт всей базы", callback_data="show_export_menu")],
        ]
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "toggle_digest_btn")
async def cb_toggle_digest_btn(callback: CallbackQuery, session: AsyncSession):
    user_repo = UserRepository(session)
    new_state = await user_repo.toggle_digest(callback.from_user.id)
    await session.commit()
    d_status = "включен ✅" if new_state else "выключен ⏸"
    desc = (
        "Каждый понедельник бот пришлёт 3 непрочитанных материала"
        if new_state
        else "Рассылка напоминаний приостановлена"
    )
    await callback.answer(f"Дайджест {d_status}!")
    try:
        await callback.message.edit_text(
            f"⚙️ <b>Настройки обновлены:</b>\n\n"
            f"• ☕️ <b>Еженедельный дайджест:</b> <b>{d_status}</b>\n"
            f"  <i>{desc}.</i>\n\n"
            "• 🔍 <b>Инлайн-режим:</b> активен (вводите <code>@имя_бота</code> в любом чате)\n\n"
            "Вы можете переключить статус дайджеста кнопкой ниже или выгрузить данные:",
            reply_markup=callback.message.reply_markup,
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data == "show_export_menu")
async def cb_show_export_menu(callback: CallbackQuery):
    await callback.answer()
    await cmd_export_menu(callback.message)


@router.message(F.text.in_({"📊 Дайджест", "Дайджест"}))
@router.message(Command("digest"))
async def cmd_toggle_digest(message: Message, session: AsyncSession):
    user_repo = UserRepository(session)
    new_state = await user_repo.toggle_digest(message.from_user.id)
    await session.commit()
    if new_state:
        msg = (
            "☕️ <b>Еженедельный дайджест включен ✅</b>\n\n"
            "<b>Что это такое?</b>\n"
            "Каждый понедельник бот находит в вашей базе до <b>3 самых давних непрочитанных материалов</b> "
            "и присылает удобную подборку со ссылками, тегами и временем на чтение.\n\n"
            "Это помогает не забывать об отложенных статьях и заметках.\n\n"
            "<i>Если непрочитанных материалов нет, бот не будет вас беспокоить. Отключить дайджест можно в любой момент командой /digest или в /settings.</i>"
        )
    else:
        msg = (
            "☕️ <b>Еженедельный дайджест выключен ⏸</b>\n\n"
            "Бот больше не будет присылать напоминания по понедельникам. Включить обратно можно в любой момент этой же командой /digest или через /settings."
        )
    await message.answer(msg, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(F.text.in_({"❓ Помощь", "Помощь"}))
@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📚 <b>Справка по командам:</b>\n\n"
        "📥 <code>/bookmarks</code> — открыть список сохраненных материалов\n"
        "🎬 <code>/yoink &lt;ссылка&gt;</code> — скачать видео или аудио (YouTube, TikTok, Insta, X и др.)\n"
        "🔍 <code>/search &lt;запрос&gt;</code> — умный семантический поиск\n"
        "🎲 <code>/random</code> — случайный непрочитанный материал\n"
        "🏷 <code>/tags</code> — просмотр и фильтрация по тегам\n"
        "☕️ <code>/digest</code> — еженедельный дайджест 3 непрочитанных закладок\n"
        "📊 <code>/stats</code> — статистика вашей базы знаний\n"
        "💾 <code>/export</code> — выгрузка в Markdown (Obsidian), CSV или JSON\n"
        "📱 <code>/menu</code> — показать кнопки главного меню\n"
        "⌨️ <code>/hide</code> — скрыть кнопки клавиатуры\n\n"
        "💡 <i>Просто отправьте боту ссылку, фото или файл в любой момент!</i>"
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession):
    repo = BookmarkRepository(session)
    stats = await repo.get_stats(message.from_user.id)

    text = (
        "📊 <b>Ваша статистика базы знаний:</b>\n\n"
        f"📚 Всего закладок: <b>{stats['total']}</b>\n"
        f"⏳ Непрочитано: <b>{stats['unread']}</b>\n"
        f"✅ Прочитано: <b>{stats['read']}</b>\n"
        f"🏷 Уникальных тегов: <b>{stats['tags_count']}</b>"
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


# --- Мультиформатный экспорт: Markdown ZIP, CSV, JSON ---

@router.message(Command("export"))
async def cmd_export_menu(message: Message):
    text = (
        "💾 <b>Экспорт вашей базы знаний:</b>\n\n"
        "Выберите удобный формат выгрузки:\n"
        "• <b>Markdown (ZIP)</b> — архив заметок для Obsidian, Notion и Logseq.\n"
        "• <b>CSV Таблица</b> — для Excel, Google Таблиц и Notion Import.\n"
        "• <b>JSON</b> — полный снимок базы для резервного копирования."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Markdown Архив (.zip)", callback_data="export_fmt:md")],
            [InlineKeyboardButton(text="📊 Таблица CSV (.csv)", callback_data="export_fmt:csv")],
            [InlineKeyboardButton(text="📦 Исходный JSON (.json)", callback_data="export_fmt:json")],
        ]
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("export_fmt:"))
async def cb_perform_export(callback: CallbackQuery, session: AsyncSession):
    fmt = callback.data.split(":")[1]
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=callback.from_user.id, limit=5000)

    if not bookmarks:
        await callback.answer("У вас пока нет сохраненных закладок.", show_alert=True)
        return

    await callback.answer("Подготавливаю файл экспорта...")

    if fmt == "md":
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for b in bookmarks:
                tags_str = ", ".join([f'"{t.name}"' for t in b.tags])
                md_content = (
                    f"---\n"
                    f"title: \"{b.title or 'Без названия'}\"\n"
                    f"url: \"{b.url or ''}\"\n"
                    f"author: \"{b.author or ''}\"\n"
                    f"domain: \"{b.domain or ''}\"\n"
                    f"tags: [{tags_str}]\n"
                    f"created_at: \"{b.created_at.isoformat() if b.created_at else ''}\"\n"
                    f"read: {str(b.is_read).lower()}\n"
                    f"---\n\n"
                    f"# {b.title or 'Без названия'}\n\n"
                    f"**Оригинал:** {b.url or 'Заметка'}\n\n"
                    f"## Краткая суть\n{b.summary or 'Нет описания'}\n\n"
                    f"## Полный текст\n{b.full_text or ''}\n"
                )
                safe_title = "".join(c for c in (b.title or "note")[:40] if c.isalnum() or c in " _-").strip()
                filename = f"{safe_title}_{str(b.id)[:8]}.md"
                zip_file.writestr(filename, md_content.encode("utf-8"))

        file_doc = BufferedInputFile(zip_buffer.getvalue(), filename="read_later_obsidian.zip")
        await callback.message.answer_document(
            document=file_doc,
            caption=f"📦 Экспорт в <b>Markdown (ZIP)</b> готов! Файлов: {len(bookmarks)}.",
            parse_mode="HTML",
        )

    elif fmt == "csv":
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["ID", "Title", "URL", "Author", "Domain", "Tags", "Reading Time Min", "Read", "Summary", "Created At"])
        for b in bookmarks:
            tags_str = ", ".join([t.name for t in b.tags])
            writer.writerow([
                str(b.id),
                b.title or "",
                b.url or "",
                b.author or "",
                b.domain or "",
                tags_str,
                b.reading_time_min,
                b.is_read,
                b.summary or "",
                b.created_at.isoformat() if b.created_at else "",
            ])
        file_doc = BufferedInputFile(csv_buffer.getvalue().encode("utf-8-sig"), filename="read_later_bookmarks.csv")
        await callback.message.answer_document(
            document=file_doc,
            caption=f"📊 Экспорт в <b>CSV</b> готов! Строк: {len(bookmarks)}.",
            parse_mode="HTML",
        )

    elif fmt == "json":
        export_data = [
            {
                "id": str(b.id),
                "title": b.title,
                "url": b.url,
                "domain": b.domain,
                "author": b.author,
                "summary": b.summary,
                "reading_time_min": b.reading_time_min,
                "is_read": b.is_read,
                "tags": [t.name for t in b.tags],
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in bookmarks
        ]
        json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
        file_doc = BufferedInputFile(json_bytes, filename="read_later_dump.json")
        await callback.message.answer_document(
            document=file_doc,
            caption=f"📦 Экспорт в <b>JSON</b> готов! Всего объектов: {len(bookmarks)}.",
            parse_mode="HTML",
        )
