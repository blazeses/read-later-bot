from typing import List
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import log
from app.db.repositories import EmbeddingCacheRepository
from app.providers.embeddings.base import BaseEmbeddingProvider
from app.providers.vectorstore.base import VectorChunk
from app.services.chunker import TextChunk


class EmbeddingService:
    def __init__(self, provider: BaseEmbeddingProvider):
        self.provider = provider

    async def get_or_create_embeddings(
        self,
        chunks: List[TextChunk],
        session: AsyncSession,
    ) -> List[VectorChunk]:
        if not chunks:
            return []

        cache_repo = EmbeddingCacheRepository(session)
        hashes = [c.chunk_hash for c in chunks]
        cached_vectors = await cache_repo.get_batch(hashes, self.provider.model_name)

        missing_indices: List[int] = []
        missing_texts: List[str] = []

        for idx, chunk in enumerate(chunks):
            if chunk.chunk_hash not in cached_vectors:
                missing_indices.append(idx)
                missing_texts.append(chunk.text)

        # Compute embeddings for cache misses
        if missing_texts:
            log.info(
                f"Embedding cache miss: computing {len(missing_texts)} of {len(chunks)} chunks via {self.provider.model_name}..."
            )
            new_vectors = await self.provider.embed_texts(missing_texts)
            to_save = {}
            for idx, vec in zip(missing_indices, new_vectors):
                chunk = chunks[idx]
                cached_vectors[chunk.chunk_hash] = vec
                to_save[chunk.chunk_hash] = vec

            await cache_repo.save_batch(to_save, self.provider.model_name)
        else:
            log.debug(f"Embedding cache hit: all {len(chunks)} chunks found in PostgreSQL cache.")

        # Reconstruct ordered VectorChunks
        result: List[VectorChunk] = []
        for chunk in chunks:
            vec = cached_vectors[chunk.chunk_hash]
            result.append(
                VectorChunk(
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    vector=vec,
                )
            )

        return result
