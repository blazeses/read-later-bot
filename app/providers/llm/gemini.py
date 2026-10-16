import asyncio
import json
import math
from typing import List
import httpx

from app.core.config import settings
from app.core.logger import log
from app.providers.llm.base import (
    TAGGING_PROMPT_TEMPLATE,
    BaseLLMProvider,
    LLMEnrichmentResult,
)


class GeminiLLMProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str | None = settings.GEMINI_API_KEY,
        model: str = "gemini-1.5-flash",
    ):
        self.api_key = api_key or ""
        self.model = model
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    async def enrich_bookmark(
        self,
        title: str,
        text: str,
        existing_tags: List[str],
    ) -> LLMEnrichmentResult:
        word_count = len(text.split())
        default_reading_time = max(1, math.ceil(word_count / 200))

        if not text.strip():
            return LLMEnrichmentResult(
                summary="Текст контента отсутствует.",
                tags=["без текста"],
                reading_time_min=1,
            )

        prompt = TAGGING_PROMPT_TEMPLATE.format(
            existing_tags=", ".join(existing_tags) if existing_tags else "нет",
            title=title,
            text=text[:6000],
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        delay = 1.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(
                        f"{self.url}?key={self.api_key}",
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        content_text = candidates[0]["content"]["parts"][0]["text"]
                        parsed = json.loads(content_text)
                        return LLMEnrichmentResult(
                            summary=parsed.get("summary", title),
                            tags=[t.strip().lower() for t in parsed.get("tags", []) if t.strip()][:7],
                            reading_time_min=max(1, int(parsed.get("reading_time_min", default_reading_time))),
                        )
                except Exception as e:
                    log.warning(f"Gemini LLM attempt {attempt + 1} failed: {e}")
                    if attempt == 2:
                        break
                    await asyncio.sleep(delay)
                    delay *= 2.0

        return LLMEnrichmentResult(
            summary=title,
            tags=["новое"],
            reading_time_min=default_reading_time,
        )
