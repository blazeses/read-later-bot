import enum
import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class SafeTSVector(TypeDecorator):
    """Platform-independent TSVector type: TSVECTOR on PostgreSQL, Text on SQLite/other."""
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import TSVECTOR
            return dialect.type_descriptor(TSVECTOR())
        return dialect.type_descriptor(Text())


class Base(DeclarativeBase):
    pass


class ContentType(str, enum.Enum):
    ARTICLE = "article"
    TWEET = "tweet"
    YOUTUBE = "youtube"
    DOCUMENT = "document"
    NOTE = "note"


bookmark_tags = Table(
    "bookmark_tags",
    Base.metadata,
    Column("bookmark_id", ForeignKey("bookmarks.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    bookmarks: Mapped[List["Bookmark"]] = relationship(
        "Bookmark", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    tags: Mapped[List["Tag"]] = relationship(
        "Tag", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="tags")
    bookmarks: Mapped[List["Bookmark"]] = relationship(
        "Bookmark", secondary=bookmark_tags, back_populates="tags", lazy="selectin"
    )


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), index=True, nullable=False)

    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_url: Mapped[Optional[str]] = mapped_column(Text, index=True, nullable=True)

    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type_enum"),
        default=ContentType.ARTICLE,
        nullable=False,
    )

    full_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reading_time_min: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    user_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_photo_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_video_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photos_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tsv_content = mapped_column(SafeTSVector, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="bookmarks")
    tags: Mapped[List["Tag"]] = relationship(
        "Tag", secondary=bookmark_tags, back_populates="bookmarks", lazy="selectin"
    )


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"

    chunk_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    embedding: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
