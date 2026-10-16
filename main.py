import asyncio
import signal
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import base, bookmarks, manage, search
from app.bot.middlewares import DbSessionMiddleware, WhitelistMiddleware
from app.core.config import settings
from app.core.logger import log
from app.db.models import Base
from app.db.session import engine
from app.providers.registry import get_embedding_provider, get_vector_store
from sqlalchemy import text
from app.services.digest import DigestScheduler
from app.services.ingest import IngestionQueueService


async def init_databases():
    log.info("Checking database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE bookmarks ADD COLUMN IF NOT EXISTS image_url TEXT;"))
            await conn.execute(text("ALTER TABLE bookmarks ADD COLUMN IF NOT EXISTS telegram_photo_id TEXT;"))
            await conn.execute(text("ALTER TABLE bookmarks ALTER COLUMN telegram_photo_id TYPE TEXT;"))
            await conn.execute(text("ALTER TABLE bookmarks ADD COLUMN IF NOT EXISTS photos_json TEXT;"))
        except Exception as e:
            log.debug(f"Schema column check skipped or not needed: {e}")
    log.info("Database schema initialized.")

    if settings.EMBEDDING_PROVIDER not in ("none", "disabled", "deepseek", "deepseek_search"):
        try:
            log.info("Initializing vector store collection...")
            vector_store = get_vector_store()
            embedding_provider = get_embedding_provider()
            await vector_store.init_store(dimension=embedding_provider.dimension)
            log.info("Vector store initialized.")
        except Exception as vs_err:
            log.warning(f"Vector store initialization skipped: {vs_err}")
    else:
        log.info("Embedding provider set to 'none'. Running in high-speed DeepSeek + PostgreSQL search mode.")


async def main():
    log.info("Starting Read Later Telegram Bot...")

    # 1. Initialize DB and Vector Collections
    await init_databases()

    # 2. Setup Bot & Dispatcher
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # 3. Setup Middlewares
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())

    # 4. Setup Services
    ingest_service = IngestionQueueService(bot=bot)
    ingest_service.start(num_workers=settings.CONCURRENT_WORKERS)
    bookmarks.set_ingest_service(ingest_service)

    digest_scheduler = DigestScheduler(bot=bot)
    digest_scheduler.start()

    # 5. Register Routers (Order matters: catch-all bookmarks router is last)
    dp.include_router(base.router)
    dp.include_router(search.router)
    dp.include_router(manage.router)
    dp.include_router(bookmarks.router)

    # 6. Set Bot Command Menu in Telegram UI
    try:
        from aiogram.types import BotCommand
        await bot.set_my_commands([
            BotCommand(command="menu", description="📱 Открыть меню кнопок"),
            BotCommand(command="recent", description="🕒 Недавние материалы"),
            BotCommand(command="search", description="🔍 Поиск по базе знаний"),
            BotCommand(command="unread", description="📖 Непрочитанные статьи"),
            BotCommand(command="tags", description="🏷 Поиск по тегам"),
            BotCommand(command="random", description="🎲 Случайная статья"),
            BotCommand(command="stats", description="📊 Статистика базы"),
            BotCommand(command="help", description="❓ Справка по боту"),
            BotCommand(command="hide", description="⌨️ Скрыть клавиатуру"),
        ])
    except Exception as cmd_err:
        log.warning(f"Failed to set bot commands: {cmd_err}")

    log.info("Bot started successfully. Waiting for messages...")

    # Optional dummy HTTP server for cloud platforms (Hugging Face Spaces, Koyeb, Render)
    async def run_health_server():
        import os
        port = int(os.getenv("PORT", "7860"))
        try:
            async def handle_client(reader, writer):
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 14\r\n\r\nBot is running")
                await writer.drain()
                writer.close()
            server = await asyncio.start_server(handle_client, "0.0.0.0", port)
            log.info(f"Health-check HTTP server listening on port {port}")
            async with server:
                await server.serve_forever()
        except Exception as e:
            log.debug(f"Health server skipped or port in use: {e}")

    asyncio.create_task(run_health_server())

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        log.info("Shutting down bot and background services...")
        await ingest_service.stop()
        digest_scheduler.stop()
        await bot.session.close()
        await engine.dispose()
        log.info("Bot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot execution terminated by user.")
