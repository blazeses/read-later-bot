import html
from typing import Optional
from urllib.parse import urlparse

try:
    from app.core.logger import log
except Exception:
    import logging
    log = logging.getLogger("video_extractor")
from app.db.models import ContentType
from app.extractors.base import BaseExtractor, ExtractedContent
from app.services.video_downloader import (
    extract_video_metadata,
    is_video_url,
)


class VideoExtractor(BaseExtractor):
    """
    Extractor for video platforms (YouTube, TikTok, Instagram, Twitter/X, Reddit, Vimeo, etc.)
    using Yoinks / yt-dlp metadata extraction.
    """

    async def extract(self, source: str, **kwargs) -> ExtractedContent:
        url = source
        domain = "video"
        try:
            domain = urlparse(url).netloc or "video"
        except Exception:
            pass

        meta = await extract_video_metadata(url)
        if not meta:
            return ExtractedContent(
                title="Видеоматериал",
                author=None,
                date=None,
                text="",
                language=None,
                domain=domain,
                content_type=ContentType.YOUTUBE,
                url=url,
                success=False,
                error_message="Не удалось получить информацию о видео.",
            )

        title = meta.get("title") or "Видео"
        author = meta.get("uploader")
        duration_str = meta.get("duration_str") or ""
        platform = meta.get("extractor") or domain
        thumbnail = meta.get("thumbnail")
        description = meta.get("description") or ""

        text_parts = [f"🎬 Видео ({platform})"]
        if author:
            text_parts.append(f"👤 Автор: {author}")
        if duration_str:
            text_parts.append(f"⏱ Длительность: {duration_str}")
        if description:
            text_parts.append(f"\n{description}")

        full_text = "\n".join(text_parts)

        return ExtractedContent(
            title=title,
            author=author,
            date=None,
            text=full_text,
            language=None,
            domain=domain,
            content_type=ContentType.YOUTUBE,
            url=url,
            success=True,
            image_url=thumbnail,
        )
