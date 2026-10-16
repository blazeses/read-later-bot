import asyncio
import json
import math
import re
from typing import List
import httpx

from app.core.config import settings
from app.core.logger import log
from app.providers.llm.base import (
    TAGGING_PROMPT_TEMPLATE,
    BaseLLMProvider,
    LLMEnrichmentResult,
)


class AnthropicLLMProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str | None = settings.ANTHROPIC_API_KEY,
        model: str = "claude-3-haiku-20240307",
    ):
        self.api_key = api_key or ""
        self.model = model
        self.url = "https://api.anthropic.com/v1/messages"

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

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0.2,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }

        delay = 1.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(self.url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    content_blocks = data.get("content", [])
                    if content_blocks:
                        raw_text = content_blocks[0].get("text", "")
                        # Extract json substring if wrapped in markdown code blocks
                        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
                        if json_match:
                            parsed = json.loads(json_match.group(0))
                            return LLMEnrichmentResult(
                                summary=parsed.get("summary", title),
                                tags=[t.strip().lower() for t in parsed.get("tags", []) if t.strip()][:7],
                                reading_time_min=max(1, int(parsed.get("reading_time_min", default_reading_time))),
                            )
                except Exception as e:
                    log.warning(f"Anthropic LLM attempt {attempt + 1} failed: {e}")
                    if attempt == 2:
                        break
                    await asyncio.sleep(delay)
                    delay *= 2.0

        return LLMEnrichmentResult(
            summary=title,
            tags=["новое"],
            reading_time_min=default_reading_time,
        )
