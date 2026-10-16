import re
from typing import Optional
import httpx
import trafilatura
from bs4 import BeautifulSoup
from readability import Document

from app.core.config import settings
from app.core.logger import log
from app.db.models import ContentType
from app.extractors.base import BaseExtractor, ExtractedContent
from app.extractors.ssrf import SSRFSecurityError, create_safe_async_client
from app.extractors.url_utils import extract_domain


class WebPageExtractor(BaseExtractor):
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    async def extract(self, source: str, **kwargs) -> ExtractedContent:
        url = source
        domain = extract_domain(url)

        try:
            async with create_safe_async_client(
                timeout=self.timeout,
                max_redirects=settings.MAX_REDIRECTS,
                user_agent=settings.USER_AGENT,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

                # Enforce size limit
                content_len = len(response.content)
                if content_len > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
                    return ExtractedContent(
                        title=f"Page from {domain}",
                        author=None,
                        date=None,
                        text="",
                        language=None,
                        domain=domain,
                        content_type=ContentType.ARTICLE,
                        url=url,
                        success=False,
                        error_message="Размер страницы превышает 20 МБ.",
                    )

                html = response.text

        except SSRFSecurityError as e:
            log.warning(f"SSRF blocked for URL {url}: {e}")
            return ExtractedContent(
                title=f"Blocked URL ({domain})",
                author=None,
                date=None,
                text="",
                language=None,
                domain=domain,
                content_type=ContentType.ARTICLE,
                url=url,
                success=False,
                error_message=f"Ошибка безопасности: доступ к локальному или приватному адресу запрещен ({e})",
            )
        except httpx.HTTPStatusError as e:
            log.warning(f"HTTP error {e.response.status_code} fetching {url}")
            return ExtractedContent(
                title=f"Статья ({domain})",
                author=None,
                date=None,
                text="",
                language=None,
                domain=domain,
                content_type=ContentType.ARTICLE,
                url=url,
                success=False,
                error_message=f"Ошибка HTTP {e.response.status_code} (возможно, paywall или защита от ботов).",
            )
        except Exception as e:
            log.warning(f"Network error fetching {url}: {e}")
            return ExtractedContent(
                title=f"Статья ({domain})",
                author=None,
                date=None,
                text="",
                language=None,
                domain=domain,
                content_type=ContentType.ARTICLE,
                url=url,
                success=False,
                error_message=f"Не удалось загрузить страницу: {str(e)[:100]}",
            )

        # 1. Primary extractor: Trafilatura
        extracted_text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            output_format="txt",
            url=url,
        )
        metadata = trafilatura.extract_metadata(html, default_url=url)

        title = metadata.title if metadata and metadata.title else None
        author = metadata.author if metadata and metadata.author else None
        date = metadata.date if metadata and metadata.date else None
        language = None

        # 2. Fallback extractor: Readability-lxml
        if not extracted_text or len(extracted_text.strip()) < 100:
            try:
                doc = Document(html)
                clean_html = doc.summary()
                soup = BeautifulSoup(clean_html, "html.parser")
                extracted_text = soup.get_text(separator="\n\n").strip()
                if not title:
                    title = doc.title()
            except Exception as e:
                log.debug(f"Readability fallback failed: {e}")

        # If title still missing, parse HTML title tag
        if not title:
            try:
                soup = BeautifulSoup(html[:10000], "html.parser")
                if soup.title and soup.title.string:
                    title = soup.title.string.strip()
            except Exception:
                pass

        if not title:
            title = f"Статья из {domain}"

        # Extract lead image / og:image
        image_url = None
        if metadata and getattr(metadata, "image", None):
            image_url = metadata.image

        if not image_url and html:
            try:
                soup = BeautifulSoup(html[:50000], "html.parser")
                og_tag = (
                    soup.find("meta", property="og:image")
                    or soup.find("meta", attrs={"name": "twitter:image"})
                    or soup.find("meta", attrs={"name": "twitter:image:src"})
                    or soup.find("link", rel="image_src")
                )
                if og_tag and og_tag.get("content"):
                    raw_img = og_tag.get("content").strip()
                    if raw_img.startswith("http://") or raw_img.startswith("https://"):
                        image_url = raw_img
            except Exception as e:
                log.debug(f"Failed to parse og:image: {e}")

        # Clean whitespace and enforce text length limit
        if extracted_text:
            extracted_text = re.sub(r"\n{3,}", "\n\n", extracted_text).strip()
            if len(extracted_text) > settings.MAX_TEXT_LENGTH:
                extracted_text = extracted_text[:settings.MAX_TEXT_LENGTH] + "\n\n... [текст обрезан]"

        has_text = bool(extracted_text and len(extracted_text.strip()) >= 50)
        return ExtractedContent(
            title=title,
            author=author,
            date=date,
            text=extracted_text if has_text else "",
            language=language,
            domain=domain,
            content_type=ContentType.ARTICLE,
            url=url,
            success=has_text,
            error_message=None if has_text else "Не удалось извлечь текст статьи (paywall, блокировка ботов или SPA на JS).",
            image_url=image_url,
        )
