import re
from typing import Optional
import httpx
from app.core.config import settings
from app.core.logger import log
from app.db.models import ContentType
from app.extractors.base import BaseExtractor, ExtractedContent
from app.extractors.ssrf import create_safe_async_client
from app.extractors.url_utils import extract_domain

TWITTER_URL_REGEX = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/status/(\d+)",
    re.IGNORECASE,
)


class TwitterExtractor(BaseExtractor):
    """
    Extracts tweets/posts from X (formerly Twitter) using public mirror APIs (vxtwitter / fxtwitter).
    Provides graceful fallback when mirrors are unreachable.
    """
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout

    async def extract(self, source: str, **kwargs) -> ExtractedContent:
        url = source
        match = TWITTER_URL_REGEX.search(url)
        if not match:
            return ExtractedContent(
                title="Пост в X (Twitter)",
                author=None,
                date=None,
                text=f"Ссылка: {url}",
                language=None,
                domain=extract_domain(url),
                content_type=ContentType.TWEET,
                url=url,
                success=True,
                error_message=None,
            )

        username = match.group(1)
        tweet_id = match.group(2)

        # 1. Try VxTwitter JSON API: https://api.vxtwitter.com/{user}/status/{id}
        api_url = f"https://api.vxtwitter.com/{username}/status/{tweet_id}"
        try:
            async with create_safe_async_client(
                timeout=self.timeout,
                user_agent=settings.USER_AGENT,
            ) as client:
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    tweet_text = data.get("text", "")
                    user_name = data.get("user_name", username)
                    user_screen_name = data.get("user_screen_name", username)
                    date = data.get("date", None)
                    media_urls = data.get("mediaURLs", [])
                    image_url = media_urls[0] if media_urls and isinstance(media_urls, list) else None

                    full_author = f"{user_name} (@{user_screen_name})"
                    title = f"Пост от {full_author}"

                    return ExtractedContent(
                        title=title,
                        author=full_author,
                        date=date,
                        text=tweet_text or f"Пост от {full_author}\n{url}",
                        language=None,
                        domain="x.com",
                        content_type=ContentType.TWEET,
                        url=url,
                        success=True,
                        image_url=image_url,
                    )
        except Exception as e:
            log.warning(f"VxTwitter API failed for {url}: {e}")

        # 2. Try FxTwitter oEmbed fallback: https://publish.twitter.com/oembed?url={url}
        oembed_url = f"https://publish.twitter.com/oembed?url={url}"
        try:
            async with create_safe_async_client(
                timeout=self.timeout,
                user_agent=settings.USER_AGENT,
            ) as client:
                resp = await client.get(oembed_url)
                if resp.status_code == 200:
                    data = resp.json()
                    html_snippet = data.get("html", "")
                    clean_text = re.sub(r"<[^>]+>", " ", html_snippet).strip()
                    author_name = data.get("author_name", username)

                    return ExtractedContent(
                        title=f"Пост от {author_name} (@{username})",
                        author=f"{author_name} (@{username})",
                        date=None,
                        text=clean_text or f"Пост от @{username}\n{url}",
                        language=None,
                        domain="x.com",
                        content_type=ContentType.TWEET,
                        url=url,
                        success=True,
                    )
        except Exception as e:
            log.warning(f"Twitter oEmbed failed for {url}: {e}")

        # 3. Graceful degradation: save raw post without failing
        return ExtractedContent(
            title=f"Пост в X (@{username})",
            author=f"@{username}",
            date=None,
            text=f"Ссылка на пост в X (Twitter): {url}\n\n(Текст не удалось извлечь автоматически из-за ограничений API X.com)",
            language=None,
            domain="x.com",
            content_type=ContentType.TWEET,
            url=url,
            success=True,
            error_message=None,
        )
