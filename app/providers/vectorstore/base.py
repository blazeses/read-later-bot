from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List
import uuid


@dataclass
class VectorChunk:
    chunk_index: int
    text: str
    vector: List[float]


@dataclass
class VectorSearchResult:
    bookmark_id: uuid.UUID
    chunk_index: int
    text: str
    score: float
    payload: Dict[str, Any]


class BaseVectorStore(ABC):
    @abstractmethod
    async def init_store(self, dimension: int) -> None:
        """Initialize collection, index, and schemas."""
        pass

    @abstractmethod
    async def upsert_chunks(
        self,
        user_id: int,
        bookmark_id: uuid.UUID,
        chunks: List[VectorChunk],
    ) -> None:
        """Store chunk vectors with strict tenant isolation by user_id."""
        pass

    @abstractmethod
    async def delete_by_bookmark(self, user_id: int, bookmark_id: uuid.UUID) -> None:
        """Delete all vectors for a specific bookmark under user_id."""
        pass

    @abstractmethod
    async def search(
        self,
        user_id: int,
        query_vector: List[float],
        limit: int = 20,
    ) -> List[VectorSearchResult]:
        """Perform similarity search strictly filtered by user_id."""
        pass
