import time
import uuid
from typing import List
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.core.logger import log
from app.providers.vectorstore.base import (
    BaseVectorStore,
    VectorChunk,
    VectorSearchResult,
)


class QdrantVectorStore(BaseVectorStore):
    def __init__(
        self,
        url: str = settings.QDRANT_URL,
        api_key: str | None = settings.QDRANT_API_KEY,
        collection_name: str = settings.QDRANT_COLLECTION,
    ):
        self.url = url
        self.api_key = api_key
        self.collection_name = collection_name
        self.client = AsyncQdrantClient(url=self.url, api_key=self.api_key)

    async def init_store(self, dimension: int) -> None:
        try:
            exists = await self.client.collection_exists(self.collection_name)
            if not exists:
                log.info(f"Creating Qdrant collection '{self.collection_name}' with dim {dimension}...")
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=qmodels.VectorParams(
                        size=dimension,
                        distance=qmodels.Distance.COSINE,
                    ),
                )

                # Create payload indexes for fast filtered lookups and deletions
                await self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="user_id",
                    field_schema=qmodels.PayloadSchemaType.INTEGER,
                )
                await self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="bookmark_id",
                    field_schema=qmodels.PayloadSchemaType.KEYWORD,
                )
                log.info(f"Qdrant collection '{self.collection_name}' created with indexes.")
        except Exception as e:
            log.warning(f"Qdrant init warning: {e}")

    async def upsert_chunks(
        self,
        user_id: int,
        bookmark_id: uuid.UUID,
        chunks: List[VectorChunk],
    ) -> None:
        if not chunks:
            return

        points = []
        now = int(time.time())
        for chunk in chunks:
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{bookmark_id}_{chunk.chunk_index}"))
            payload = {
                "user_id": user_id,
                "bookmark_id": str(bookmark_id),
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "created_at": now,
            }
            points.append(
                qmodels.PointStruct(
                    id=point_id,
                    vector=chunk.vector,
                    payload=payload,
                )
            )

        await self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )

    async def delete_by_bookmark(self, user_id: int, bookmark_id: uuid.UUID) -> None:
        delete_filter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="user_id",
                    match=qmodels.MatchValue(value=user_id),
                ),
                qmodels.FieldCondition(
                    key="bookmark_id",
                    match=qmodels.MatchValue(value=str(bookmark_id)),
                ),
            ]
        )
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=qmodels.FilterSelector(filter=delete_filter),
            wait=True,
        )

    async def search(
        self,
        user_id: int,
        query_vector: List[float],
        limit: int = 20,
    ) -> List[VectorSearchResult]:
        user_filter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="user_id",
                    match=qmodels.MatchValue(value=user_id),
                )
            ]
        )

        response = await self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            query_filter=user_filter,
            limit=limit,
            with_payload=True,
        )

        results: List[VectorSearchResult] = []
        for hit in response:
            payload = hit.payload or {}
            b_id_str = payload.get("bookmark_id")
            if not b_id_str:
                continue
            results.append(
                VectorSearchResult(
                    bookmark_id=uuid.UUID(b_id_str),
                    chunk_index=payload.get("chunk_index", 0),
                    text=payload.get("text", ""),
                    score=float(hit.score),
                    payload=payload,
                )
            )
        return results
