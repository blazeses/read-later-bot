from functools import lru_cache
from app.core.config import settings
from app.providers.embeddings.base import BaseEmbeddingProvider, NoneEmbeddingProvider
from app.providers.embeddings.gemini import GeminiEmbeddingProvider
from app.providers.embeddings.local import LocalEmbeddingProvider
from app.providers.embeddings.openai import OpenAIEmbeddingProvider
from app.providers.llm.anthropic import AnthropicLLMProvider
from app.providers.llm.base import BaseLLMProvider
from app.providers.llm.gemini import GeminiLLMProvider
from app.providers.llm.openai import OpenAILLMProvider
from app.providers.vectorstore.base import BaseVectorStore
from app.providers.vectorstore.qdrant import QdrantVectorStore


@lru_cache()
def get_vector_store() -> BaseVectorStore:
    return QdrantVectorStore(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY,
        collection_name=settings.QDRANT_COLLECTION,
    )


@lru_cache()
def get_embedding_provider() -> BaseEmbeddingProvider:
    provider = (settings.EMBEDDING_PROVIDER or "none").strip().lower()
    if provider in ("none", "disabled", "deepseek", "deepseek_search", "false", "0", ""):
        return NoneEmbeddingProvider()
    elif provider == "openai":
        return OpenAIEmbeddingProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.EMBEDDING_MODEL,
            dimension=settings.EMBEDDING_DIMENSIONS,
        )
    elif provider == "gemini":
        return GeminiEmbeddingProvider(
            api_key=settings.GEMINI_API_KEY,
            model="models/text-embedding-004",
            dimension=768,
        )
    elif provider == "local":
        return LocalEmbeddingProvider(
            model_name=settings.LOCAL_EMBEDDING_MODEL,
        )
    else:
        return NoneEmbeddingProvider()


@lru_cache()
def get_llm_provider() -> BaseLLMProvider:
    provider = (settings.LLM_PROVIDER or "deepseek").strip().lower()
    if provider == "deepseek":
        return OpenAILLMProvider(
            api_key=settings.DEEPSEEK_API_KEY or settings.OPENAI_API_KEY,
            model=settings.LLM_MODEL or "deepseek-chat",
            base_url="https://api.deepseek.com",
        )
    elif provider == "openai":
        return OpenAILLMProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.LLM_MODEL,
            base_url=settings.OPENAI_BASE_URL,
        )
    elif provider == "gemini":
        return GeminiLLMProvider(
            api_key=settings.GEMINI_API_KEY,
            model="gemini-1.5-flash",
        )
    elif provider == "anthropic":
        return AnthropicLLMProvider(
            api_key=settings.ANTHROPIC_API_KEY,
            model="claude-3-haiku-20240307",
        )
    else:
        return OpenAILLMProvider(
            api_key=settings.DEEPSEEK_API_KEY or settings.OPENAI_API_KEY,
            model=settings.LLM_MODEL or "deepseek-chat",
            base_url="https://api.deepseek.com",
        )
