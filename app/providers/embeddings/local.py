import asyncio
from typing import List
from app.core.config import settings
from app.core.logger import log
from app.providers.embeddings.base import BaseEmbeddingProvider


class LocalEmbeddingProvider(BaseEmbeddingProvider):
    """
    Local embedding provider using SentenceTransformers or FastEmbed in a background thread.
    Automatically prepends 'passage: ' and 'query: ' for E5-family models.
    """
    def __init__(
        self,
        model_name: str = settings.LOCAL_EMBEDDING_MODEL,
        dimension: int = 384,
    ):
        self._model_name = model_name
        self._dimension = dimension
        self._model = None
        self._is_e5 = "e5" in model_name.lower()

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                log.info(f"Loading local embedding model '{self._model_name}'...")
                self._model = SentenceTransformer(self._model_name)
                # Auto-detect dimension
                self._dimension = self._model.get_sentence_embedding_dimension()
                log.info(f"Local model loaded. Embedding dimension: {self._dimension}")
            except ImportError:
                # Fallback to fastembed if sentence_transformers is not installed
                try:
                    from fastembed import TextEmbedding
                    log.info(f"Loading FastEmbed model '{self._model_name}'...")
                    try:
                        self._model = TextEmbedding(model_name=self._model_name)
                    except ValueError:
                        fallback_model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
                        log.warning(
                            f"Model '{self._model_name}' is not in FastEmbed registry. "
                            f"Automatically falling back to multilingual model '{fallback_model}'"
                        )
                        self._model_name = fallback_model
                        self._is_e5 = False
                        self._model = TextEmbedding(model_name=fallback_model)
                except ImportError:
                    raise RuntimeError(
                        "Neither 'sentence_transformers' nor 'fastembed' is installed. "
                        "Please install one of them to use local embeddings."
                    )

    @property
    def dimension(self) -> int:
        if self._model is None:
            self._load_model()
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model_name

    def _sync_embed(self, texts: List[str]) -> List[List[float]]:
        self._load_model()
        if hasattr(self._model, "encode"):
            embeddings = self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
            return [vec.tolist() for vec in embeddings]
        else:
            # FastEmbed generator
            return [list(vec) for vec in self._model.embed(texts)]

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        formatted = [f"passage: {t}" if self._is_e5 else t for t in texts]
        return await asyncio.to_thread(self._sync_embed, formatted)

    async def embed_query(self, query: str) -> List[float]:
        formatted = f"query: {query}" if self._is_e5 else query
        res = await asyncio.to_thread(self._sync_embed, [formatted])
        return res[0]
