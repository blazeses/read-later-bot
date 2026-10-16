import asyncio
from typing import List
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logger import log
from app.providers.embeddings.base import BaseEmbeddingProvider


class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(
        self,
        api_key: str | None = settings.OPENAI_API_KEY,
        model: str = settings.EMBEDDING_MODEL,
        dimension: int = settings.EMBEDDING_DIMENSIONS,
    ):
        self._model = model
        self._dimension = dimension
        self.client = AsyncOpenAI(api_key=api_key or "sk-dummy")

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

    async def _call_with_retry(self, texts: List[str], retries: int = 3) -> List[List[float]]:
        delay = 1.0
        for attempt in range(retries):
            try:
                response = await self.client.embeddings.create(
                    model=self._model,
                    input=texts,
                )
                return [item.embedding for item in response.data]
            except Exception as e:
                log.warning(f"OpenAI embedding attempt {attempt + 1} failed: {e}")
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(delay)
                delay *= 2.0
        return []

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        # Batch up to 64 items per call
        batch_size = 64
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = await self._call_with_retry(batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    async def embed_query(self, query: str) -> List[float]:
        res = await self.embed_texts([query])
        return res[0]
