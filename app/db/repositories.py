import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from sqlalchemy import (
    and_,
    delete,
    desc,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Bookmark,
    ContentType,
    EmbeddingCache,
    Tag,
    User,
    bookmark_tags,
)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, telegram_id: int) -> User:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_id=telegram_id, digest_enabled=True)
            self.session.add(user)
            await self.session.flush()
        return user

    async def toggle_digest(self, telegram_id: int, enabled: Optional[bool] = None) -> bool:
        user = await self.get_or_create(telegram_id)
        if enabled is None:
            user.digest_enabled = not user.digest_enabled
        else:
            user.digest_enabled = enabled
        await self.session.flush()
        return user.digest_enabled

    async def get_users_with_digest(self) -> List[User]:
        stmt = select(User).where(User.digest_enabled.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class TagRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_tags(self, telegram_id: int) -> List[Tag]:
        stmt = select(Tag).where(Tag.user_id == telegram_id).order_by(Tag.name.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_or_create_tags(self, telegram_id: int, tag_names: List[str]) -> List[Tag]:
        cleaned_names = sorted(list({name.strip().lower() for name in tag_names if name.strip()}))
        if not cleaned_names:
            return []

        stmt = select(Tag).where(
            and_(Tag.user_id == telegram_id, Tag.name.in_(cleaned_names))
        )
        result = await self.session.execute(stmt)
        existing = {tag.name: tag for tag in result.scalars().all()}

        tags: List[Tag] = []
        for name in cleaned_names:
            if name in existing:
                tags.append(existing[name])
            else:
                new_tag = Tag(user_id=telegram_id, name=name)
                self.session.add(new_tag)
                tags.append(new_tag)

        await self.session.flush()
        return tags

    async def get_popular_tags(self, telegram_id: int, limit: int = 20) -> List[Tuple[str, int]]:
        stmt = (
            select(Tag.name, func.count(bookmark_tags.c.bookmark_id).label("count"))
            .join(bookmark_tags, Tag.id == bookmark_tags.c.tag_id)
            .where(Tag.user_id == telegram_id)
            .group_by(Tag.name)
            .order_by(desc("count"), Tag.name.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.all())


class BookmarkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, bookmark_id: uuid.UUID, user_id: int) -> Optional[Bookmark]:
        stmt = (
            select(Bookmark)
            .options(selectinload(Bookmark.tags))
            .where(and_(Bookmark.id == bookmark_id, Bookmark.user_id == user_id))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_normalized_url(self, normalized_url: str, user_id: int) -> Optional[Bookmark]:
        stmt = (
            select(Bookmark)
            .options(selectinload(Bookmark.tags))
            .where(
                and_(
                    Bookmark.user_id == user_id,
                    Bookmark.normalized_url == normalized_url,
                )
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: int,
        content_type: ContentType,
        url: Optional[str] = None,
        normalized_url: Optional[str] = None,
        title: Optional[str] = None,
        author: Optional[str] = None,
        domain: Optional[str] = None,
        full_text: Optional[str] = None,
        summary: Optional[str] = None,
        reading_time_min: int = 1,
        user_comment: Optional[str] = None,
        tags: Optional[List[Tag]] = None,
        image_url: Optional[str] = None,
        telegram_photo_id: Optional[str] = None,
        photos_json: Optional[str] = None,
    ) -> Bookmark:
        bookmark = Bookmark(
            id=uuid.uuid4(),
            user_id=user_id,
            content_type=content_type,
            url=url,
            normalized_url=normalized_url,
            title=title,
            author=author,
            domain=domain,
            full_text=full_text,
            summary=summary,
            reading_time_min=reading_time_min,
            user_comment=user_comment,
            tags=tags or [],
            image_url=image_url,
            telegram_photo_id=telegram_photo_id,
            photos_json=photos_json,
        )

        # Compute tsvector in Postgres if dialect supports it
        try:
            text_for_search = f"{title or ''} {summary or ''} {(full_text or '')[:5000]}"
            bookmark.tsv_content = func.to_tsvector("russian", text_for_search)
        except Exception:
            pass

        self.session.add(bookmark)
        await self.session.flush()
        return bookmark

    async def update_metadata(
        self,
        bookmark_id: uuid.UUID,
        user_id: int,
        title: Optional[str] = None,
        author: Optional[str] = None,
        summary: Optional[str] = None,
        full_text: Optional[str] = None,
        reading_time_min: Optional[int] = None,
        tags: Optional[List[Tag]] = None,
        image_url: Optional[str] = None,
        telegram_photo_id: Optional[str] = None,
        photos_json: Optional[str] = None,
    ) -> Optional[Bookmark]:
        bookmark = await self.get_by_id(bookmark_id, user_id)
        if not bookmark:
            return None

        if title is not None:
            bookmark.title = title
        if author is not None:
            bookmark.author = author
        if summary is not None:
            bookmark.summary = summary
        if full_text is not None:
            bookmark.full_text = full_text
            try:
                text_for_search = f"{bookmark.title or ''} {bookmark.summary or ''} {full_text[:5000]}"
                bookmark.tsv_content = func.to_tsvector("russian", text_for_search)
            except Exception:
                pass
        if reading_time_min is not None:
            bookmark.reading_time_min = reading_time_min
        if tags is not None:
            bookmark.tags = tags
        if image_url is not None:
            bookmark.image_url = image_url
        if telegram_photo_id is not None:
            bookmark.telegram_photo_id = telegram_photo_id
        if photos_json is not None:
            bookmark.photos_json = photos_json

        await self.session.flush()
        return bookmark

    async def mark_as_read(self, bookmark_id: uuid.UUID, user_id: int, is_read: bool = True) -> Optional[Bookmark]:
        bookmark = await self.get_by_id(bookmark_id, user_id)
        if bookmark:
            bookmark.is_read = is_read
            await self.session.flush()
        return bookmark

    async def delete(self, bookmark_id: uuid.UUID, user_id: int) -> bool:
        stmt = delete(Bookmark).where(and_(Bookmark.id == bookmark_id, Bookmark.user_id == user_id))
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def list_bookmarks(
        self,
        user_id: int,
        is_read: Optional[bool] = None,
        tag_name: Optional[str] = None,
        content_type: Optional[ContentType] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 5,
        offset: int = 0,
    ) -> Tuple[List[Bookmark], int]:
        filters = [Bookmark.user_id == user_id]

        if is_read is not None:
            filters.append(Bookmark.is_read == is_read)
        if content_type is not None:
            filters.append(Bookmark.content_type == content_type)
        if date_from is not None:
            filters.append(Bookmark.created_at >= date_from)
        if date_to is not None:
            filters.append(Bookmark.created_at <= date_to)

        base_stmt = select(Bookmark).options(selectinload(Bookmark.tags))
        count_stmt = select(func.count(func.distinct(Bookmark.id)))

        if tag_name:
            tag_filter = and_(
                Tag.name == tag_name.strip().lower(),
                Tag.user_id == user_id
            )
            base_stmt = base_stmt.join(Bookmark.tags).where(tag_filter)
            count_stmt = count_stmt.join(Bookmark.tags).where(tag_filter)

        base_stmt = base_stmt.where(and_(*filters)).order_by(Bookmark.created_at.desc()).limit(limit).offset(offset)
        count_stmt = count_stmt.where(and_(*filters))

        items_res = await self.session.execute(base_stmt)
        count_res = await self.session.execute(count_stmt)

        items = list(items_res.scalars().all())
        total = count_res.scalar() or 0
        return items, total

    async def get_random_unread(self, user_id: int) -> Optional[Bookmark]:
        stmt = (
            select(Bookmark)
            .options(selectinload(Bookmark.tags))
            .where(and_(Bookmark.user_id == user_id, Bookmark.is_read.is_(False)))
            .order_by(func.random())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_stats(self, user_id: int) -> Dict[str, int]:
        total_stmt = select(func.count(Bookmark.id)).where(Bookmark.user_id == user_id)
        unread_stmt = select(func.count(Bookmark.id)).where(
            and_(Bookmark.user_id == user_id, Bookmark.is_read.is_(False))
        )
        read_stmt = select(func.count(Bookmark.id)).where(
            and_(Bookmark.user_id == user_id, Bookmark.is_read.is_(True))
        )
        tags_stmt = select(func.count(Tag.id)).where(Tag.user_id == user_id)

        total = (await self.session.execute(total_stmt)).scalar() or 0
        unread = (await self.session.execute(unread_stmt)).scalar() or 0
        read = (await self.session.execute(read_stmt)).scalar() or 0
        tags_count = (await self.session.execute(tags_stmt)).scalar() or 0

        return {
            "total": total,
            "unread": unread,
            "read": read,
            "tags_count": tags_count,
        }

    async def fulltext_search(
        self,
        user_id: int,
        query: str,
        limit: int = 20,
    ) -> List[Tuple[Bookmark, float]]:
        """
        Fulltext search using PostgreSQL tsvector / ts_rank_cd,
        or substring/ILike fallback on other dialects (e.g. SQLite).
        """
        dialect = self.session.bind.dialect.name if self.session.bind else "postgresql"
        if dialect == "postgresql":
            # Postgres tsquery search
            ts_query = func.plainto_tsquery("russian", query)
            rank = func.ts_rank_cd(Bookmark.tsv_content, ts_query).label("rank")
            stmt = (
                select(Bookmark, rank)
                .options(selectinload(Bookmark.tags))
                .where(
                    and_(
                        Bookmark.user_id == user_id,
                        Bookmark.tsv_content.op("@@")(ts_query),
                    )
                )
                .order_by(desc("rank"))
                .limit(limit)
            )
            res = await self.session.execute(stmt)
            pg_matches = [(row[0], float(row[1])) for row in res.all()]
            if pg_matches:
                return pg_matches

        # Fallback ILIKE search (for SQLite, or when tsquery found 0 matches)
        words = [w.strip() for w in query.split() if len(w.strip()) >= 2]
        if not words:
            words = [query.strip()] if query.strip() else []

        ilike_filters = [
            or_(
                Bookmark.title.ilike(f"%{w}%"),
                Bookmark.summary.ilike(f"%{w}%"),
                Bookmark.full_text.ilike(f"%{w}%"),
                Bookmark.domain.ilike(f"%{w}%"),
            )
            for w in words
        ]
        stmt = (
            select(Bookmark)
            .options(selectinload(Bookmark.tags))
            .where(and_(Bookmark.user_id == user_id, or_(*ilike_filters) if ilike_filters else text("1=1")))
            .order_by(Bookmark.created_at.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        bookmarks = list(res.scalars().all())
        return [(b, 1.0 / (idx + 1)) for idx, b in enumerate(bookmarks)]


class EmbeddingCacheRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_batch(self, hashes: List[str], model_name: str) -> Dict[str, List[float]]:
        if not hashes:
            return {}
        stmt = select(EmbeddingCache).where(
            and_(
                EmbeddingCache.chunk_hash.in_(hashes),
                EmbeddingCache.model_name == model_name,
            )
        )
        res = await self.session.execute(stmt)
        return {item.chunk_hash: item.embedding["vec"] for item in res.scalars().all()}

    async def save_batch(self, cache_entries: Dict[str, List[float]], model_name: str) -> None:
        if not cache_entries:
            return
        for chunk_hash, vec in cache_entries.items():
            entry = EmbeddingCache(
                chunk_hash=chunk_hash,
                model_name=model_name,
                embedding={"vec": vec},
            )
            self.session.add(entry)
        await self.session.flush()
