import asyncio
from typing import List
import httpx

from app.core.config import settings
from app.core.logger import log
from app.providers.embeddings.base import BaseEmbeddingProvider


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(
        self,
        api_key: str | None = settings.GEMINI_API_KEY,
        model: str = "models/text-embedding-004",
        dimension: int = 768,
    ):
        self.api_key = api_key or ""
        self._model = model
        self._dimension = dimension
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/{self._model}:batchEmbedContents"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        batch_size = 32
        all_embeddings: List[List[float]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                requests = [
                    {
                        "model": self._model,
                        "content": {"parts": [{"text": t[:2048]}]},
                    }
                    for t in batch
                ]
                payload = {"requests": requests}

                delay = 1.0
                for attempt in range(3):
                    try:
                        resp = await client.post(
                            f"{self.endpoint}?key={self.api_key}",
                            json=payload,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        embeddings = [item["values"] for item in data.get("embeddings", [])]
                        all_embeddings.extend(embeddings)
                        break
                    except Exception as e:
                        log.warning(f"Gemini embedding batch attempt {attempt + 1} failed: {e}")
                        if attempt == 2:
                            raise
                        await asyncio.sleep(delay)
                        delay *= 2.0

        return all_embeddings

    async def embed_query(self, query: str) -> List[float]:
        res = await self.embed_texts([query])
        return res[0]
