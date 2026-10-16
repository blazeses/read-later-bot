import re
from typing import Optional
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled

from app.core.config import settings
from app.core.logger import log
from app.db.models import ContentType
from app.extractors.base import BaseExtractor, ExtractedContent
from app.extractors.ssrf import create_safe_async_client

YOUTUBE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)


def extract_video_id(url: str) -> Optional[str]:
    match = YOUTUBE_REGEX.search(url)
    return match.group(1) if match else None


class YouTubeExtractor(BaseExtractor):
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout

    async def extract(self, source: str, **kwargs) -> ExtractedContent:
        url = source
        video_id = extract_video_id(url)
        if not video_id:
            return ExtractedContent(
                title="YouTube Video",
                author=None,
                date=None,
                text="",
                language=None,
                domain="youtube.com",
                content_type=ContentType.YOUTUBE,
                url=url,
                success=False,
                error_message="Неверная ссылка на YouTube-видео",
            )

        title = f"YouTube Video ({video_id})"
        author = None

        # 1. Fetch metadata via oEmbed
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        try:
            async with create_safe_async_client(
                timeout=self.timeout,
                user_agent=settings.USER_AGENT,
            ) as client:
                resp = await client.get(oembed_url)
                if resp.status_code == 200:
                    data = resp.json()
                    title = data.get("title", title)
                    author = data.get("author_name", None)
        except Exception as e:
            log.warning(f"YouTube oEmbed metadata fetch failed for {video_id}: {e}")

        # 2. Fetch subtitles/transcript
        transcript_text = ""
        try:
            # Prefer Russian, then English
            transcript_list = YouTubeTranscriptApi.get_transcript(
                video_id,
                languages=["ru", "en", "uk", "be", "de", "fr", "es"],
            )
            sub_lines = [item["text"] for item in transcript_list if item.get("text")]
            transcript_text = " ".join(sub_lines)
            transcript_text = re.sub(r"\s+", " ", transcript_text).strip()
        except (NoTranscriptFound, TranscriptsDisabled):
            log.info(f"No transcripts found for YouTube video {video_id}")
        except Exception as e:
            log.warning(f"YouTubeTranscriptApi error for {video_id}: {e}")

        # Prepare full text
        full_text = ""
        if transcript_text:
            full_text = f"Субтитры видео:\n\n{transcript_text}"
        else:
            full_text = f"Видео: {title}\nАвтор канала: {author or 'YouTube'}\n(Субтитры недоступны)"

        if len(full_text) > settings.MAX_TEXT_LENGTH:
            full_text = full_text[:settings.MAX_TEXT_LENGTH] + "\n\n... [субтитры обрезаны]"

        return ExtractedContent(
            title=title,
            author=author,
            date=None,
            text=full_text,
            language=None,
            domain="youtube.com",
            content_type=ContentType.YOUTUBE,
            url=url,
            success=True,
            error_message=None if transcript_text else "Субтитры недоступны, сохранено название и метаданные видео.",
        )
