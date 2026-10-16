from abc import ABC, abstractmethod
from typing import List


class BaseEmbeddingProvider(ABC):
    @property
    @abstractmethod
    def dimension(self) -> int:
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Compute embeddings for a batch of documents/passages."""
        pass

    @abstractmethod
    async def embed_query(self, query: str) -> List[float]:
        """Compute embedding for a single user search query."""
        pass


class NoneEmbeddingProvider(BaseEmbeddingProvider):
    """Fallback provider when vector embeddings are disabled or handled solely via PostgreSQL fulltext + DeepSeek."""
    @property
    def dimension(self) -> int:
        return 0

    @property
    def model_name(self) -> str:
        return "none"

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return []

    async def embed_query(self, query: str) -> List[float]:
        return []

