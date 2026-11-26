import ssl
from collections.abc import AsyncGenerator
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings
from app.core.logger import log


def parse_database_url_and_ssl(
    raw_url: str,
    force_ssl: Optional[bool] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Нормализует URL базы данных и настраивает SSL для asyncpg.
    - Автоматически преобразует postgres:// и postgresql:// в postgresql+asyncpg://
    - Удаляет параметры запроса sslmode и ssl, чтобы asyncpg не падал с unexpected keyword argument
    - Автоматически включает защищенный SSL контекст для облачных баз данных (Neon, Supabase, Render и др.)
      или когда указан sslmode=require / DB_SSL=True
    """
    url_str = (raw_url or "").strip()
    if url_str.startswith("postgres://"):
        url_str = "postgresql+asyncpg://" + url_str[len("postgres://"):]
    elif url_str.startswith("postgresql://") and not url_str.startswith("postgresql+asyncpg://"):
        url_str = "postgresql+asyncpg://" + url_str[len("postgresql://"):]
    elif url_str.startswith("sqlite://") and not url_str.startswith("sqlite+aiosqlite://"):
        url_str = "sqlite+aiosqlite://" + url_str[len("sqlite://"):]

    if url_str.startswith("sqlite"):
        return url_str, {"echo": False, "future": True}

    url = make_url(url_str)
    query = dict(url.query)

    # Извлекаем параметры SSL из query, чтобы asyncpg не ругался на неподдерживаемые kwargs
    sslmode = query.pop("sslmode", None)
    ssl_param = query.pop("ssl", None)

    needs_ssl = False
    if force_ssl is True:
        needs_ssl = True
    elif force_ssl is False:
        needs_ssl = False
    else:
        if sslmode in ("require", "prefer", "verify-ca", "verify-full"):
            needs_ssl = True
        elif ssl_param and str(ssl_param).lower() in ("require", "true", "1"):
            needs_ssl = True
        else:
            host = (url.host or "").lower()
            cloud_indicators = (
                "neon.tech", "supabase.co", "supabase.com",
                "railway.app", "render.com", "aivencloud.com",
                "cockroachlabs.cloud", "rds.amazonaws.com",
            )
            if any(ci in host for ci in cloud_indicators):
                needs_ssl = True

    # Реконструируем URL без sslmode в query строке с сохранением реального пароля
    clean_url = url._replace(query=query).render_as_string(hide_password=False)

    engine_kwargs: Dict[str, Any] = {
        "echo": False,
        "future": True,
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 10,
        "max_overflow": 20,
    }

    if needs_ssl:
        ctx = ssl.create_default_context()
        if sslmode == "verify-full":
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            # Для облачных провайдеров (Neon, Supabase) режим sslmode=require
            # обеспечивает шифрование трафика без сбоев проверки самоподписанных цепочек
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        engine_kwargs["connect_args"] = {"ssl": ctx}
        log.info("Database SSL enabled for asyncpg connection.")

    return clean_url, engine_kwargs


cleaned_url, engine_kwargs = parse_database_url_and_ssl(settings.DATABASE_URL, settings.DB_SSL)

engine = create_async_engine(
    cleaned_url,
    **engine_kwargs,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

