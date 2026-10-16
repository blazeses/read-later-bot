from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class LLMEnrichmentResult:
    summary: str
    tags: List[str]
    reading_time_min: int


TAGGING_PROMPT_TEMPLATE = """Ты — интеллектуальный ассистент каталогизации базы знаний и закладок.
Проанализируй следующий контент и сгенерируй:
1. Краткое саммари (2–3 емких предложения, передающих суть и главные выводы).
2. От 3 до 7 тегов в нижнем регистре на языке контента.
ВАЖНО:
- СТРОГО ЗАПРЕЩЕНО ПЕРЕВОДИТЬ КОНТЕНТ. Язык ответа (саммари и теги) ОБЯЗАН СТРОГО СОВПАДАТЬ с языком оригинального текста: русский текст -> ответ на русском, английский текст -> ответ на английском. Никакого перевода!
- Если среди существующих тегов пользователя есть подходящие по смыслу, ПЕРЕИСПОЛЬЗУЙ ИХ, чтобы не плодить дубликаты!
Существующие теги пользователя: {existing_tags}
3. Примерное расчетное время чтения в минутах (целое число от 1).

Формат ответа строго валидный JSON:
{{
  "summary": "Краткое описание на языке оригинала...",
  "tags": ["тег1", "тег2", "тег3"],
  "reading_time_min": 5
}}

Заголовок: {title}
Контент:
{text}
"""


class BaseLLMProvider(ABC):
    @abstractmethod
    async def enrich_bookmark(
        self,
        title: str,
        text: str,
        existing_tags: List[str],
    ) -> LLMEnrichmentResult:
        pass
