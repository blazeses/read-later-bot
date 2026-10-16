import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import log
from app.db.models import Bookmark, ContentType
from app.db.repositories import BookmarkRepository
from app.providers.embeddings.base import BaseEmbeddingProvider
from app.providers.vectorstore.base import BaseVectorStore
from app.services.date_parser import parse_date_range_from_query


@dataclass
class SearchResultItem:
    bookmark: Bookmark
    score: float
    relevant_snippet: str


class HybridSearchService:
    def __init__(
        self,
        vector_store: BaseVectorStore,
        embedding_provider: BaseEmbeddingProvider,
        k: int = settings.RRF_K,
    ):
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.k = k

    async def search(
        self,
        user_id: int,
        query: str,
        session: AsyncSession,
        tag_name: Optional[str] = None,
        content_type: Optional[ContentType] = None,
        is_read: Optional[bool] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[List[SearchResultItem], int]:
        # 1. Parse natural date filters if not provided
        clean_query, parsed_from, parsed_to = parse_date_range_from_query(query)
        effective_from = date_from or parsed_from
        effective_to = date_to or parsed_to

        if not clean_query.strip():
            clean_query = query.strip()

        bookmark_repo = BookmarkRepository(session)

        # 2. Parallel execution: Vector search (if enabled) + Fulltext search
        async def run_vector_search():
            if (
                settings.EMBEDDING_PROVIDER in ("none", "disabled", "deepseek", "deepseek_search")
                or self.embedding_provider.model_name == "none"
            ):
                return []
            try:
                q_vec = await self.embedding_provider.embed_query(clean_query)
                return await self.vector_store.search(user_id=user_id, query_vector=q_vec, limit=40)
            except Exception as e:
                log.warning(f"Vector search failed: {e}")
                return []

        async def run_text_search():
            try:
                return await bookmark_repo.fulltext_search(user_id=user_id, query=clean_query, limit=40)
            except Exception as e:
                log.warning(f"Fulltext search failed: {e}")
                return []

        vector_hits, text_hits = await asyncio.gather(
            run_vector_search(),
            run_text_search(),
        )

        # 3. Reciprocal Rank Fusion (RRF)
        # Group vector hits by bookmark_id, keeping best rank & snippet
        doc_snippets: Dict[uuid.UUID, str] = {}
        rrf_scores: Dict[uuid.UUID, float] = {}

        # Vector rankings (already sorted by cosine score desc)
        seen_vector_docs = set()
        v_rank = 1
        for v_hit in vector_hits:
            b_id = v_hit.bookmark_id
            if b_id not in seen_vector_docs:
                seen_vector_docs.add(b_id)
                rrf_scores[b_id] = rrf_scores.get(b_id, 0.0) + (1.0 / (self.k + v_rank))
                doc_snippets[b_id] = v_hit.text[:300]
                v_rank += 1

        # Fulltext rankings (sorted by ts_rank_cd desc)
        for t_rank, (b_item, _) in enumerate(text_hits, start=1):
            b_id = b_item.id
            rrf_scores[b_id] = rrf_scores.get(b_id, 0.0) + (1.0 / (self.k + t_rank))
            if b_id not in doc_snippets:
                snippet = b_item.summary or (b_item.full_text[:300] if b_item.full_text else "")
                doc_snippets[b_id] = snippet

        if not rrf_scores:
            return [], 0

        # Sort candidate IDs by fused score
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        # 4. Fetch full Bookmark objects & apply post-filters
        matched_results: List[SearchResultItem] = []
        for b_id in sorted_ids:
            bookmark = await bookmark_repo.get_by_id(b_id, user_id=user_id)
            if not bookmark:
                continue

            # Apply filters
            if is_read is not None and bookmark.is_read != is_read:
                continue
            if content_type is not None and bookmark.content_type != content_type:
                continue
            if effective_from is not None and bookmark.created_at < effective_from:
                continue
            if effective_to is not None and bookmark.created_at > effective_to:
                continue
            if tag_name:
                tag_names = [t.name.lower() for t in bookmark.tags]
                if tag_name.strip().lower() not in tag_names:
                    continue

            snippet = doc_snippets.get(b_id, "")
            if not snippet and bookmark.summary:
                snippet = bookmark.summary

            matched_results.append(
                SearchResultItem(
                    bookmark=bookmark,
                    score=rrf_scores[b_id],
                    relevant_snippet=snippet,
                )
            )

        total_count = len(matched_results)
        paged_results = matched_results[offset : offset + limit]
        return paged_results, total_count
