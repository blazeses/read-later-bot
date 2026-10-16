import asyncio
import json
import math
from typing import List
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logger import log
from app.providers.llm.base import (
    TAGGING_PROMPT_TEMPLATE,
    BaseLLMProvider,
    LLMEnrichmentResult,
)


class OpenAILLMProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: str | None = settings.OPENAI_API_KEY,
        model: str = settings.LLM_MODEL,
        base_url: str | None = settings.OPENAI_BASE_URL,
    ):
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key or "sk-dummy",
            base_url=base_url,
        )

    async def enrich_bookmark(
        self,
        title: str,
        text: str,
        existing_tags: List[str],
    ) -> LLMEnrichmentResult:
        # Fallback reading time calculation (~200 words per minute)
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
            text=text[:6000],  # send first 6k chars to fit within context
        )

        delay = 1.0
        for attempt in range(3):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a helpful knowledge management assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                raw_json = response.choices[0].message.content or "{}"
                data = json.loads(raw_json)

                summary = data.get("summary", "").strip() or title
                tags = [t.strip().lower() for t in data.get("tags", []) if t.strip()]
                reading_time = data.get("reading_time_min", default_reading_time)

                return LLMEnrichmentResult(
                    summary=summary,
                    tags=tags[:7] if tags else ["закладка"],
                    reading_time_min=max(1, int(reading_time)),
                )
            except Exception as e:
                log.warning(f"OpenAI LLM attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    break
                await asyncio.sleep(delay)
                delay *= 2.0

        # Graceful fallback on LLM failure
        return LLMEnrichmentResult(
            summary=title,
            tags=["новое"],
            reading_time_min=default_reading_time,
        )
