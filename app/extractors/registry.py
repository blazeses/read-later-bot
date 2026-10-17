import os
from typing import Optional
from app.db.models import ContentType
from app.extractors.base import BaseExtractor, ExtractedContent
from app.extractors.documents import DocumentExtractor
from app.extractors.twitter import TWITTER_URL_REGEX, TwitterExtractor
from app.extractors.web import WebPageExtractor
from app.extractors.youtube import YOUTUBE_REGEX, YouTubeExtractor


class ContentExtractorRegistry:
    def __init__(self):
        self.web_extractor = WebPageExtractor()
        self.twitter_extractor = TwitterExtractor()
        self.youtube_extractor = YouTubeExtractor()
        self.doc_extractor = DocumentExtractor()

    async def extract_url(self, url: str) -> ExtractedContent:
        """Determines URL type and calls the appropriate extractor."""
        if TWITTER_URL_REGEX.search(url):
            return await self.twitter_extractor.extract(url)
        elif YOUTUBE_REGEX.search(url):
            return await self.youtube_extractor.extract(url)
        else:
            return await self.web_extractor.extract(url)

    async def extract_file(self, file_path: str, filename: Optional[str] = None) -> ExtractedContent:
        return await self.doc_extractor.extract(file_path, filename=filename)

    def create_note_content(
        self,
        text: str,
        user_comment: Optional[str] = None,
        telegram_photo_id: Optional[str] = None,
        telegram_video_id: Optional[str] = None,
        author: Optional[str] = None,
        url: Optional[str] = None,
    ) -> ExtractedContent:
        from urllib.parse import urlparse
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        default_label = "Видео-заметка" if telegram_video_id else ("Фото-заметка" if telegram_photo_id else "Заметка")
        title = lines[0][:80] if lines else default_label
        if len(title) == 80:
            title += "..."

        domain = "telegram" if author else "note"
        if url:
            try:
                parsed_dom = urlparse(url).netloc
                if parsed_dom:
                    domain = parsed_dom
            except Exception:
                pass

        return ExtractedContent(
            title=title,
            author=author,
            date=None,
            text=text,
            language=None,
            domain=domain,
            content_type=ContentType.NOTE,
            url=url,
            success=True,
            telegram_photo_id=telegram_photo_id,
            telegram_video_id=telegram_video_id,
        )


extractor_registry = ContentExtractorRegistry()
