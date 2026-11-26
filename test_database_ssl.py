import pytest
from app.db.session import parse_database_url_and_ssl
from sqlalchemy.ext.asyncio import create_async_engine


def test_neon_url_sslmode_require():
    raw_url = "postgresql://alex:secret123@ep-cool-fog-123.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    clean_url, kwargs = parse_database_url_and_ssl(raw_url)

    assert clean_url.startswith("postgresql+asyncpg://")
    assert "sslmode" not in clean_url
    assert "connect_args" in kwargs
    assert "ssl" in kwargs["connect_args"]
    assert kwargs["pool_recycle"] == 300
    assert kwargs["pool_pre_ping"] is True


def test_supabase_auto_ssl():
    raw_url = "postgres://postgres:secret@db.abcdefgh.supabase.co:5432/postgres"
    clean_url, kwargs = parse_database_url_and_ssl(raw_url)

    assert clean_url.startswith("postgresql+asyncpg://")
    assert "connect_args" in kwargs
    assert "ssl" in kwargs["connect_args"]


def test_localhost_no_ssl_by_default():
    raw_url = "postgresql+asyncpg://postgres:pass@localhost:5432/readlater"
    clean_url, kwargs = parse_database_url_and_ssl(raw_url)

    assert clean_url == raw_url
    assert "connect_args" not in kwargs


def test_force_ssl_override():
    raw_url = "postgresql+asyncpg://postgres:pass@localhost:5432/readlater"
    clean_url, kwargs = parse_database_url_and_ssl(raw_url, force_ssl=True)
    assert "connect_args" in kwargs
    assert "ssl" in kwargs["connect_args"]

    neon_url = "postgresql://alex:secret@ep-cool.neon.tech/neondb"
    clean_neon, kwargs_neon = parse_database_url_and_ssl(neon_url, force_ssl=False)
    assert "connect_args" not in kwargs_neon


def test_sqlite_url_handling():
    raw_url = "sqlite:///:memory:"
    clean_url, kwargs = parse_database_url_and_ssl(raw_url)

    assert clean_url == "sqlite+aiosqlite:///:memory:"
    assert "connect_args" not in kwargs


def test_engine_creation_compatibility():
    raw_url = "postgresql://alex:secret@ep-cool-fog-123.neon.tech/neondb?sslmode=require"
    clean_url, kwargs = parse_database_url_and_ssl(raw_url)
    engine = create_async_engine(clean_url, **kwargs)
    assert engine is not None
