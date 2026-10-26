from typing import List, Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram Bot
    BOT_TOKEN: str = Field(default="", description="Telegram Bot Token from @BotFather")
    ADMIN_IDS: str = Field(default="", description="Comma-separated Telegram user IDs of admins")
    WHITELIST_IDS: str = Field(
        default="", 
        description="Comma-separated Telegram user IDs allowed to use the bot. Empty means open to all"
    )

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/readlater",
        description="Async PostgreSQL connection string"
    )

    # Vector Store (Qdrant)
    QDRANT_URL: str = Field(default="http://localhost:6333", description="Qdrant service URL")
    QDRANT_API_KEY: Optional[str] = Field(default=None, description="Qdrant API key if secured")
    QDRANT_COLLECTION: str = Field(default="bookmark_chunks", description="Qdrant collection name")

    # Embeddings Provider ('none' uses DeepSeek + PostgreSQL fulltext search without external quota limits)
    EMBEDDING_PROVIDER: str = Field(
        default="none",
        description="Embedding provider to use: 'none' (DeepSeek+Postgres), 'openai', 'gemini' or 'local'"
    )
    EMBEDDING_MODEL: str = Field(default="text-embedding-3-small", description="Model name for embeddings")
    EMBEDDING_DIMENSIONS: int = Field(default=1536, description="Embedding dimension")
    LOCAL_EMBEDDING_MODEL: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        description="SentenceTransformers or FastEmbed model name when EMBEDDING_PROVIDER=local"
    )

    # LLM Provider
    LLM_PROVIDER: str = Field(
        default="deepseek",
        description="LLM provider for summaries and tags: 'deepseek', 'openai', 'gemini' or 'anthropic'"
    )
    LLM_MODEL: str = Field(default="deepseek-chat", description="LLM model name")

    # API Keys & Custom Base URLs
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    OPENAI_BASE_URL: Optional[str] = Field(default=None, description="Custom base URL for OpenAI-compatible providers")
    DEEPSEEK_API_KEY: Optional[str] = Field(default=None, description="DeepSeek API Key (https://api.deepseek.com)")
    GEMINI_API_KEY: Optional[str] = Field(default=None)
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None)

    # Content Ingestion & Security
    MAX_FILE_SIZE_MB: int = Field(default=20, description="Max allowed file size for downloads in MB")
    MAX_TEXT_LENGTH: int = Field(default=500_000, description="Max character length of article text to process")
    REQUEST_TIMEOUT_SECONDS: int = Field(default=15, description="Timeout for web page downloads")
    MAX_REDIRECTS: int = Field(default=5, description="Max redirects allowed when fetching URLs")
    USER_AGENT: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 ReadLaterBot/1.0",
        description="User-Agent header for fetching web pages"
    )

    # Search & RAG
    CHUNK_SIZE_TOKENS: int = Field(default=600, description="Target chunk size in tokens")
    CHUNK_OVERLAP_TOKENS: int = Field(default=90, description="Overlap between consecutive chunks")
    RRF_K: int = Field(default=60, description="Constant k for Reciprocal Rank Fusion")
    TOP_K_SEARCH: int = Field(default=5, description="Number of bookmarks to return per search page")

    # Background Tasks & Digest
    CONCURRENT_WORKERS: int = Field(default=3, description="Number of concurrent background ingestion workers")
    DIGEST_ENABLED: bool = Field(default=True, description="Enable weekly digest scheduler")
    DIGEST_DAY_OF_WEEK: int = Field(default=0, description="0=Monday, 6=Sunday")
    DIGEST_HOUR: int = Field(default=10, description="Hour of the day to send digest (0-23 UTC)")

    @property
    def admin_id_list(self) -> List[int]:
        if not self.ADMIN_IDS.strip():
            return []
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip().isdigit()]

    @property
    def whitelist_id_list(self) -> List[int]:
        if not self.WHITELIST_IDS.strip():
            return []
        return [int(x.strip()) for x in self.WHITELIST_IDS.split(",") if x.strip().isdigit()]


settings = Settings()
