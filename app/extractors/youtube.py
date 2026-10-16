import asyncio
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


def _fetch_subtitles_sync(video_id: str) -> tuple[str, Optional[str]]:
    """
    Синхронная функция для вызова в отдельном потоке:
    Ищет сначала ручные, затем автогенерированные субтитры, с переводом на русский.
    """
    languages_priority = ["ru", "en", "uk", "be", "de", "fr", "es"]
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # 1. Попытка найти ручные субтитры
        transcript = None
        try:
            transcript = transcript_list.find_manually_created_transcript(languages_priority)
        except Exception:
            pass

        # 2. Попытка найти автогенерированные субтитры
        if not transcript:
            try:
                transcript = transcript_list.find_generated_transcript(languages_priority)
            except Exception:
                pass

        # 3. Если есть субтитры на любом другом языке — берем их и переводим на русский
        if not transcript:
            for t in transcript_list:
                try:
                    transcript = t.translate("ru")
                    break
                except Exception:
                    transcript = t
                    break

        if transcript:
            data = transcript.fetch()
            lines = [item["text"] for item in data if item.get("text")]
            clean_text = " ".join(lines)
            clean_text = re.sub(r"\s+", " ", clean_text).strip()
            return clean_text, transcript.language_code

    except (NoTranscriptFound, TranscriptsDisabled):
        log.info(f"Субтитры отключены или отсутствуют для видео {video_id}")
    except Exception as e:
        log.warning(f"Ошибка получения субтитров YouTube для {video_id}: {e}")

    return "", None


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
        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

        # 1. Извлечение метаданных через oEmbed
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
                    if data.get("thumbnail_url"):
                        thumbnail_url = data.get("thumbnail_url")
        except Exception as e:
            log.warning(f"YouTube oEmbed metadata fetch failed for {video_id}: {e}")

        # 2. Неблокирующее получение субтитров в пуле потоков
        transcript_text, lang = await asyncio.to_thread(_fetch_subtitles_sync, video_id)

        # 3. Формирование итогового текста
        if transcript_text:
            full_text = f"Субтитры видео:\n\n{transcript_text}"
        else:
            full_text = f"Видео: {title}\nАвтор канала: {author or 'YouTube'}\n(Субтитры недоступны для данного видео)"

        if len(full_text) > settings.MAX_TEXT_LENGTH:
            full_text = full_text[:settings.MAX_TEXT_LENGTH] + "\n\n... [субтитры обрезаны]"

        return ExtractedContent(
            title=title,
            author=author,
            date=None,
            text=full_text,
            language=lang,
            domain="youtube.com",
            content_type=ContentType.YOUTUBE,
            url=url,
            success=True,
            error_message=None if transcript_text else "Субтитры отсутствуют, сохранены название и обложка видео.",
            image_url=thumbnail_url,
        )
