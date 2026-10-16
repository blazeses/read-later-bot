import io
import json
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message
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
        "👋 <b>Добро пожаловать в персональный менеджер закладок «Прочитать позже»!</b>\n\n"
        "Отправляйте мне:\n"
        "🔗 <b>Ссылки на статьи</b> — сохраню чистый текст, сделаю саммари и теги.\n"
        "📷 <b>Фото и посты из каналов</b> — сохраню картинку с описанием.\n"
        "🐦 <b>Посты из X (Twitter)</b> — вытяну текст твита и автора.\n"
        "▶️ <b>YouTube-ссылки</b> — сохраню описание и субтитры.\n"
        "📄 <b>Файлы (PDF, DOCX, TXT, MD)</b> — извлеку текст для поиска.\n"
        "💬 <b>Текстовые заметки / цитаты</b> — сохраню в базу знаний.\n\n"
        "🤖 <b>Все анализируется DeepSeek</b> — мгновенно, без ограничений по квоте.\n\n"
        "Пользуйтесь кнопками внизу экрана или введите /help для справки."
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(F.text == "➕ Добавить статью")
async def btn_add_article(message: Message):
    text = (
        "📥 <b>Как сохранить материал в базу знаний:</b>\n\n"
        "1. <b>Ссылка</b>: отправьте любую ссылку (статья, Хабр, VC.ru, YouTube, Twitter/X).\n"
        "2. <b>Фото или пост</b>: перешлите пост из канала или отправьте фото с описанием/ссылкой.\n"
        "3. <b>Файл</b>: отправьте PDF, DOCX, TXT или Markdown документ.\n"
        "4. <b>Заметка</b>: просто отправьте любой текст или цитату.\n\n"
        "🤖 <i>DeepSeek автоматически извлечет текст, сделает краткое саммари и подберет теги!</i>"
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(F.text == "🔍 Поиск")
async def btn_search_prompt(message: Message):
    text = (
        "🔍 <b>Поиск по сохраненным материалам:</b>\n\n"
        "Напишите команду <code>/search &lt;запрос&gt;</code>, например:\n"
        "• <code>/search микросервисы и docker</code>\n"
        "• <code>/search python async за последний месяц</code>\n"
        "• <code>/search искусственный интеллект</code>\n\n"
        "<i>Поиск работает по смыслу, заголовкам, саммари, тексту и тегам!</i>"
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(F.text == "❓ Помощь")
@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "📚 <b>Справка по боту:</b>\n\n"
        "🔍 <code>/search &lt;запрос&gt;</code> — поиск по смыслу и ключевым словам\n"
        "🕒 <code>/recent</code> — просмотр последних сохраненных материалов\n"
        "📖 <code>/unread</code> — список непрочитанных статей\n"
        "🎲 <code>/random</code> — случайный непрочитанный материал\n"
        "🏷 <code>/tags</code> — список ваших тегов\n"
        "📊 <code>/stats</code> — статистика вашей базы знаний\n"
        "☕️ <code>/digest</code> — еженедельный дайджест (вкл/выкл)\n"
        "💾 <code>/export</code> — выгрузка базы в JSON\n\n"
        "💡 <i>Листайте карточки стрелками прямо в сообщении!</i>"
    )
    await message.answer(text, reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(F.text == "🕒 Недавние")
async def btn_recent(message: Message, session: AsyncSession):
    from app.bot.handlers.manage import cmd_recent
    await cmd_recent(message, session)


@router.message(F.text == "📖 Непрочитанные")
async def btn_unread(message: Message, session: AsyncSession):
    from app.bot.handlers.manage import cmd_unread
    await cmd_unread(message, session)


@router.message(F.text == "🏷 Теги")
async def btn_tags(message: Message, session: AsyncSession):
    from app.bot.handlers.manage import cmd_tags
    await cmd_tags(message, session)


@router.message(F.text == "🎲 Случайная")
async def btn_random(message: Message, session: AsyncSession):
    from app.bot.handlers.manage import cmd_random
    await cmd_random(message, session)


@router.message(F.text == "📊 Статистика")
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


@router.message(Command("digest"))
async def cmd_toggle_digest(message: Message, session: AsyncSession):
    user_repo = UserRepository(session)
    new_state = await user_repo.toggle_digest(message.from_user.id)
    status_str = "включен ✅ (рассылка каждый понедельник)" if new_state else "выключен ⏸"
    await message.answer(f"☕️ Еженедельный дайджест <b>{status_str}</b>.", reply_markup=get_main_reply_keyboard(), parse_mode="HTML")


@router.message(Command("export"))
async def cmd_export(message: Message, session: AsyncSession):
    repo = BookmarkRepository(session)
    bookmarks, total = await repo.list_bookmarks(user_id=message.from_user.id, limit=5000)

    if not bookmarks:
        await message.answer("У вас пока нет сохраненных закладок для экспорта.", reply_markup=get_main_reply_keyboard())
        return

    export_data = []
    for b in bookmarks:
        export_data.append(
            {
                "id": str(b.id),
                "title": b.title,
                "url": b.url,
                "domain": b.domain,
                "author": b.author,
                "summary": b.summary,
                "reading_time_min": b.reading_time_min,
                "is_read": b.is_read,
                "image_url": b.image_url,
                "tags": [t.name for t in b.tags],
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
        )

    json_str = json.dumps(export_data, ensure_ascii=False, indent=2)
    bytes_io = io.BytesIO(json_str.encode("utf-8"))
    file_doc = BufferedInputFile(bytes_io.getvalue(), filename="read_later_export.json")

    await message.answer_document(
        document=file_doc,
        caption=f"📦 Экспорт завершен! Всего выгружено: {len(export_data)} закладок.",
        reply_markup=get_main_reply_keyboard(),
    )
