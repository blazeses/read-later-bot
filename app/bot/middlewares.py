from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from app.core.config import settings
from app.core.logger import log
from app.db.session import async_session_factory


class WhitelistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        whitelist = settings.whitelist_id_list
        if not whitelist:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if user and user.id not in whitelist:
            log.warning(f"Access denied for non-whitelisted user ID: {user.id}")
            if isinstance(event, Message):
                await event.answer("⛔️ Извините, бот работает в приватном режиме для ограниченного круга пользователей.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔️ Доступ ограничен.", show_alert=True)
            return None

        return await handler(event, data)


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with async_session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
