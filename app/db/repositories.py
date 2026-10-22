import difflib
import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from app.core.logger import log
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


def extract_search_terms(query: str) -> List[str]:
    """
    Extract meaningful search terms, typo variants, and word stems for Russian and English.
    Handles grammatical inflections, typos (e.g. 'тарбрис' -> 'табрис'), and multi-word queries.
    """
    import re
    cleaned = re.sub(r"[^\w\s-]", " ", query.lower()).strip()
    raw_words = [w.strip() for w in cleaned.split() if len(w.strip()) >= 2]
    if not raw_words:
        return [query.strip().lower()] if query.strip() else []

    terms = set()
    ru_suffixes = (
        "оваться", "иваться", "ываться", "ельного", "ельному",
        "овать", "ивать", "ывать", "ение", "ения", "ению", "ениями",
        "тель", "ость", "ости", "остью", "остях", "ями", "ами",
        "ого", "его", "ому", "ему", "ыми", "ими", "ный", "ная", "ное",
        "ные", "ных", "ным", "ном", "ную",
        "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие",
        "ам", "ям", "ом", "ем", "ой", "ей", "ах", "ях", "ов", "ев",
        "ть", "ти", "ла", "ли", "ло", "ал", "ил", "ел",
        "ую", "юю", "а", "е", "и", "о", "у", "ы", "э", "ю", "я", "ь"
    )
    en_suffixes = ("ation", "tions", "tion", "ing", "ies", "ed", "es", "s")

    for w in raw_words:
        terms.add(w)

        # 1. 1-character deletion variants for typo tolerance (e.g. 'тарбрис' -> 'табрис')
        if len(w) >= 4:
            for i in range(len(w)):
                typo_term = w[:i] + w[i + 1:]
                if len(typo_term) >= 3:
                    terms.add(typo_term)

        # 2. Suffix stripping for Russian words
        if len(w) >= 4:
            stem_found = False
            for suf in ru_suffixes:
                if w.endswith(suf) and len(w) - len(suf) >= 3:
                    terms.add(w[: len(w) - len(suf)])
                    stem_found = True
                    break
            if not stem_found:
                for suf in en_suffixes:
                    if w.endswith(suf) and len(w) - len(suf) >= 3:
                        terms.add(w[: len(w) - len(suf)])
                        break

    return [t for t in terms if len(t) >= 2]


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
        telegram_video_id: Optional[str] = None,
        telegram_document_id: Optional[str] = None,
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
            telegram_video_id=telegram_video_id,
            telegram_document_id=telegram_document_id,
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

    async def get_random_unread(
        self,
        user_id: int,
        exclude_ids: Optional[List[uuid.UUID]] = None,
    ) -> Optional[Bookmark]:
        filters = [Bookmark.user_id == user_id, Bookmark.is_read.is_(False)]
        if exclude_ids:
            filters.append(Bookmark.id.not_in(exclude_ids))

        stmt = (
            select(Bookmark)
            .options(selectinload(Bookmark.tags))
            .where(and_(*filters))
            .order_by(func.random())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        bm = res.scalar_one_or_none()

        # If all unread were excluded, reset exclusion and pick from all unread
        if not bm and exclude_ids:
            stmt_all = (
                select(Bookmark)
                .options(selectinload(Bookmark.tags))
                .where(and_(Bookmark.user_id == user_id, Bookmark.is_read.is_(False)))
                .order_by(func.random())
                .limit(1)
            )
            res_all = await self.session.execute(stmt_all)
            bm = res_all.scalar_one_or_none()

        return bm

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
        Smart multi-field search with root stemming, typo tolerance (Levenshtein/difflib),
        tag matching, and PostgreSQL tsvector search with guaranteed fallback.
        """
        import difflib
        import re
        from app.core.logger import log

        clean_q = query.strip()
        if not clean_q:
            return []

        results_map: Dict[uuid.UUID, Tuple[Bookmark, float]] = {}

        # 1. Direct SQL keyword search (fast, reliable, covers title, user_comment, summary, domain, author, tags)
        terms = extract_search_terms(clean_q)
        if not terms:
            terms = [clean_q.lower()]

        try:
            async with self.session.begin_nested():
                term_conditions = []
                for t in terms:
                    term_pattern = f"%{t}%"
                    term_conditions.append(
                        or_(
                            Bookmark.title.ilike(term_pattern),
                            Bookmark.user_comment.ilike(term_pattern),
                            Bookmark.summary.ilike(term_pattern),
                            Bookmark.full_text.ilike(term_pattern),
                            Bookmark.domain.ilike(term_pattern),
                            Bookmark.author.ilike(term_pattern),
                            Bookmark.tags.any(Tag.name.ilike(term_pattern)),
                        )
                    )

                stmt = (
                    select(Bookmark)
                    .options(selectinload(Bookmark.tags))
                    .where(and_(Bookmark.user_id == user_id, or_(*term_conditions)))
                    .order_by(Bookmark.created_at.desc())
                    .limit(limit * 2)
                )
                res = await self.session.execute(stmt)
                for bm in res.scalars().all():
                    score = 10.0
                    t_lower = (bm.title or "").lower()
                    c_lower = (bm.user_comment or "").lower()
                    s_lower = (bm.summary or "").lower()
                    f_lower = (bm.full_text or "").lower()
                    tags_lower = [tag.name.lower() for tag in bm.tags]

                    q_lower = clean_q.lower()
                    if q_lower in t_lower or q_lower in c_lower or any(q_lower in tag for tag in tags_lower):
                        score += 30.0
                    if q_lower in s_lower or q_lower in f_lower:
                        score += 15.0

                    for t in terms:
                        if t in t_lower or t in c_lower:
                            score += 8.0
                        if any(t in tag for tag in tags_lower):
                            score += 8.0
                        if t in s_lower or t in f_lower:
                            score += 4.0
                        score += 1.0

                    results_map[bm.id] = (bm, score)
        except Exception as term_err:
            log.warning(f"Direct term search failed: {term_err}")

        # 2. PostgreSQL tsvector search (bonus ranking if available)
        try:
            async with self.session.begin_nested():
                ts_query = func.plainto_tsquery("russian", clean_q)
                combined_text = func.concat(
                    func.coalesce(Bookmark.title, ""),
                    " ",
                    func.coalesce(Bookmark.summary, ""),
                    " ",
                    func.coalesce(Bookmark.full_text, ""),
                    " ",
                    func.coalesce(Bookmark.user_comment, ""),
                    " ",
                    func.coalesce(Bookmark.domain, ""),
                )
                dyn_tsv = func.to_tsvector("russian", combined_text)
                rank = func.ts_rank_cd(dyn_tsv, ts_query).label("rank")
                stmt = (
                    select(Bookmark, rank)
                    .options(selectinload(Bookmark.tags))
                    .where(and_(Bookmark.user_id == user_id, dyn_tsv.op("@@")(ts_query)))
                    .order_by(desc("rank"))
                    .limit(limit)
                )
                res = await self.session.execute(stmt)
                for row in res.all():
                    bm = row[0]
                    r_score = float(row[1]) + 20.0
                    if bm.id in results_map:
                        old_bm, old_score = results_map[bm.id]
                        results_map[bm.id] = (bm, old_score + r_score)
                    else:
                        results_map[bm.id] = (bm, r_score)
        except Exception as pg_err:
            log.debug(f"PostgreSQL plainto_tsquery skipped: {pg_err}")

        # 3. Fuzzy typo matching across user's bookmarks (handles 'тарбрис' <-> 'табрис', >0.72 similarity)
        q_words = [w for w in re.split(r"\W+", clean_q.lower()) if len(w) >= 2]
        if q_words:
            try:
                async with self.session.begin_nested():
                    stmt_all = (
                        select(Bookmark)
                        .options(selectinload(Bookmark.tags))
                        .where(Bookmark.user_id == user_id)
                        .order_by(Bookmark.created_at.desc())
                        .limit(60)
                    )
                    res_all = await self.session.execute(stmt_all)
                    for bm in res_all.scalars().all():
                        text_parts = [
                            bm.title or "",
                            bm.user_comment or "",
                            bm.summary or "",
                            (bm.full_text or "")[:400],
                            bm.domain or "",
                            " ".join(t.name for t in bm.tags),
                        ]
                        combined_words = [w for w in re.split(r"\W+", " ".join(text_parts).lower()) if len(w) >= 2]
                        best_ratio = 0.0
                        for qw in q_words:
                            for bw in combined_words:
                                ratio = difflib.SequenceMatcher(None, qw, bw).ratio()
                                if ratio > best_ratio:
                                    best_ratio = ratio

                        if best_ratio >= 0.72:
                            fuzzy_score = 15.0 * best_ratio
                            if bm.id in results_map:
                                old_bm, old_score = results_map[bm.id]
                                results_map[bm.id] = (bm, max(old_score, fuzzy_score + 10.0))
                            else:
                                results_map[bm.id] = (bm, fuzzy_score)
            except Exception as fuzzy_err:
                log.warning(f"Fuzzy bookmark matching skipped: {fuzzy_err}")

        sorted_results = sorted(results_map.values(), key=lambda x: x[1], reverse=True)
        return sorted_results[:limit]


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
