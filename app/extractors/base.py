from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from app.db.models import ContentType


@dataclass
class ExtractedContent:
    title: Optional[str]
    author: Optional[str]
    date: Optional[str]
    text: str
    language: Optional[str]
    domain: Optional[str]
    content_type: ContentType
    url: Optional[str] = None
    success: bool = True
    error_message: Optional[str] = None
    image_url: Optional[str] = None
    telegram_photo_id: Optional[str] = None
    telegram_video_id: Optional[str] = None

    @property
    def has_sufficient_text(self) -> bool:
        return bool(self.text and len(self.text.strip()) > 50)


class BaseExtractor(ABC):
    @abstractmethod
    async def extract(self, source: str, **kwargs) -> ExtractedContent:
        """Extract content from source (URL, raw text, or file path)."""
        pass
