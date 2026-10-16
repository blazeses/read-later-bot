import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Dict, List, Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import log
from app.db.models import Bookmark, ContentType
from app.db.repositories import BookmarkRepository
from app.providers.embeddings.base import BaseEmbeddingProvider
from app.providers.registry import get_llm_provider
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

    async def _deepseek_semantic_fallback(
        self,
        user_id: int,
        query: str,
        bookmark_repo: BookmarkRepository,
    ) -> List[SearchResultItem]:
        """
        AI-powered semantic search fallback when keyword search yields 0 matches.
        Uses DeepSeek to match by meaning, context, synonyms, or conceptual requests.
        """
        try:
            candidates, _ = await bookmark_repo.list_bookmarks(user_id=user_id, limit=30, offset=0)
            if not candidates:
                return []

            candidate_lines = []
            for idx, b in enumerate(candidates, start=1):
                title = (b.title or "Без названия").strip()
                comment = (b.user_comment or "").strip()
                domain = b.domain or "заметка"
                tags = ", ".join([t.name for t in b.tags]) if b.tags else "нет"
                content_preview = (b.summary or b.full_text or "")[:180].strip().replace("\n", " ")
                candidate_lines.append(
                    f"{idx}. ID: {b.id} | Заголовок: {title} | Подпись/Заметка: {comment} | Домен: {domain} | Теги: {tags} | Описание: {content_preview}"
                )

            prompt = (
                f"Пользователь ищет материал в своих закладках по запросу: «{query}».\n\n"
                f"Список сохраненных закладок:\n" + "\n".join(candidate_lines) + "\n\n"
                "Выбери из списка ID материалов, которые соответствуют запросу пользователя по смыслу, теме или контексту "
                "(учитывай опечатки, пропущенные/лишние буквы, например 'тарбрис' и 'табрис', транслитерацию, синонимы или сходство звучания).\n"
                "Верни JSON со списком подходящих ID от наиболее релевантного к менее релевантному:\n"
                '{"matching_ids": ["uuid-1", "uuid-2"]}\n'
                "Если ничего из списка не соответствует запросу, верни: {\"matching_ids\": []}"
            )

            llm = get_llm_provider()
            client = getattr(llm, "client", None)
            if client:
                model = getattr(llm, "model", "deepseek-chat")
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a smart search assistant for bookmarks. Respond only in valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                raw_json = resp.choices[0].message.content or "{}"
                data = json.loads(raw_json)
                matching_ids = data.get("matching_ids", [])

                b_map = {str(b.id): b for b in candidates}
                results = []
                for mid in matching_ids:
                    mid_str = str(mid).strip()
                    if mid_str in b_map:
                        bm = b_map[mid_str]
                        snippet = bm.summary or bm.user_comment or (bm.full_text[:300] if bm.full_text else "")
                        results.append(
                            SearchResultItem(
                                bookmark=bm,
                                score=0.95,
                                relevant_snippet=snippet,
                            )
                        )
                if results:
                    log.info(f"DeepSeek semantic search found {len(results)} matches for query '{query}'")
                return results
        except Exception as e:
            log.warning(f"DeepSeek semantic fallback skipped: {e}")
        return []

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

        # 2. Parallel execution: Vector search (if enabled) + Stemmed Fulltext search
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
        doc_snippets: Dict[uuid.UUID, str] = {}
        rrf_scores: Dict[uuid.UUID, float] = {}

        seen_vector_docs = set()
        v_rank = 1
        for v_hit in vector_hits:
            b_id = v_hit.bookmark_id
            if b_id not in seen_vector_docs:
                seen_vector_docs.add(b_id)
                rrf_scores[b_id] = rrf_scores.get(b_id, 0.0) + (1.0 / (self.k + v_rank))
                doc_snippets[b_id] = v_hit.text[:300]
                v_rank += 1

        for t_rank, (b_item, t_score) in enumerate(text_hits, start=1):
            b_id = b_item.id
            rrf_scores[b_id] = rrf_scores.get(b_id, 0.0) + (t_score / (self.k + t_rank))
            if b_id not in doc_snippets:
                snippet = b_item.summary or b_item.user_comment or (b_item.full_text[:300] if b_item.full_text else "")
                doc_snippets[b_id] = snippet

        # 4. Fetch full Bookmark objects & apply post-filters
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        matched_results: List[SearchResultItem] = []

        for b_id in sorted_ids:
            bookmark = await bookmark_repo.get_by_id(b_id, user_id=user_id)
            if not bookmark:
                continue

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
            if not snippet:
                snippet = bookmark.summary or bookmark.user_comment or ""

            matched_results.append(
                SearchResultItem(
                    bookmark=bookmark,
                    score=rrf_scores[b_id],
                    relevant_snippet=snippet,
                )
            )

        # 4b. If date filter excluded everything, retry without date filter
        if not matched_results and (effective_from or effective_to):
            for b_id in sorted_ids:
                bookmark = await bookmark_repo.get_by_id(b_id, user_id=user_id)
                if not bookmark:
                    continue
                snippet = doc_snippets.get(b_id, bookmark.summary or bookmark.user_comment or "")
                matched_results.append(
                    SearchResultItem(bookmark=bookmark, score=rrf_scores[b_id], relevant_snippet=snippet)
                )

        # 5. If STILL 0 results, use DeepSeek AI Semantic Search Fallback!
        if not matched_results:
            ai_matches = await self._deepseek_semantic_fallback(
                user_id=user_id,
                query=query,
                bookmark_repo=bookmark_repo,
            )
            if ai_matches:
                matched_results = ai_matches

        total_count = len(matched_results)
        paged_results = matched_results[offset : offset + limit]
        return paged_results, total_count
