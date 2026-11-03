import re
from typing import List, Optional


CANONICAL_CATEGORIES = [
    "💼 Работа & IT",
    "📈 Бизнес & Финансы",
    "🔬 Наука & Образование",
    "🎬 Медиа & Развлечения",
    "🛒 Быт & Покупки",
    "💡 Разное",
]

CATEGORY_ALIASES = {
    "работа & it": "💼 Работа & IT",
    "работа": "💼 Работа & IT",
    "it": "💼 Работа & IT",
    "разработка": "💼 Работа & IT",
    "программирование": "💼 Работа & IT",
    "бизнес & финансы": "📈 Бизнес & Финансы",
    "бизнес": "📈 Бизнес & Финансы",
    "финансы": "📈 Бизнес & Финансы",
    "экономика": "📈 Бизнес & Финансы",
    "наука & образование": "🔬 Наука & Образование",
    "наука": "🔬 Наука & Образование",
    "образование": "🔬 Наука & Образование",
    "саморазвитие": "🔬 Наука & Образование",
    "медиа & развлечения": "🎬 Медиа & Развлечения",
    "медиа": "🎬 Медиа & Развлечения",
    "развлечения": "🎬 Медиа & Развлечения",
    "видео": "🎬 Медиа & Развлечения",
    "музыка": "🎬 Медиа & Развлечения",
    "быт & покупки": "🛒 Быт & Покупки",
    "покупки": "🛒 Быт & Покупки",
    "быт": "🛒 Быт & Покупки",
    "дом": "🛒 Быт & Покупки",
    "разное": "💡 Разное",
    "другое": "💡 Разное",
}


def detect_category(
    title: str = "",
    text: str = "",
    tags: Optional[List[str]] = None,
    url: Optional[str] = None,
    llm_category: Optional[str] = None,
) -> str:
    """
    Intelligently assigns a canonical category to a bookmark.
    Priority:
    1. Valid LLM detected category
    2. URL domain patterns
    3. Keyword and tag matching heuristics
    4. Default fallback: '💡 Разное'
    """
    if llm_category:
        clean_llm = llm_category.strip().lower()
        if clean_llm in CATEGORY_ALIASES:
            return CATEGORY_ALIASES[clean_llm]
        for canonical in CANONICAL_CATEGORIES:
            if canonical.lower() in clean_llm:
                return canonical

    url_str = (url or "").lower()
    # 1. URL Domain rules
    if any(d in url_str for d in ("youtube.com", "youtu.be", "tiktok.com", "twitch.tv", "spotify.com", "soundcloud.com", "kinopoisk.ru", "imdb.com")):
        return "🎬 Медиа & Развлечения"
    if any(d in url_str for d in ("github.com", "gitlab.com", "stackoverflow.com", "habr.com", "dev.to", "medium.com")):
        return "💼 Работа & IT"
    if any(d in url_str for d in ("rbc.ru", "forbes.ru", "tinkoff.ru", "sberbank.ru", "bloomberg.com", "vc.ru")):
        return "📈 Бизнес & Финансы"
    if any(d in url_str for d in ("coursera.org", "stepik.org", "arxiv.org", "wikipedia.org", "postnauka.ru", "cyberleninka.ru")):
        return "🔬 Наука & Образование"
    if any(d in url_str for d in ("ozon.ru", "wildberries.ru", "avito.ru", "aliexpress", "market.yandex.ru")):
        return "🛒 Быт & Покупки"

    # 2. Text and Tags keyword rules
    tag_str = " ".join(tags or []).lower()
    combined_content = f"{title.lower()} {tag_str} {text[:2000].lower()}"

    it_keywords = (
        "python", "javascript", "typescript", "rust", "golang", "sql", "api",
        "backend", "frontend", "linux", "docker", "kubernetes", "баг", "код",
        "программирование", "разработка", "фреймворк", "архитектура", "рефакторинг",
        "алгоритм", "devops", "git", "ci/cd", "postgresql", "fastapi", "aiogram"
    )
    if any(k in combined_content for k in it_keywords):
        return "💼 Работа & IT"

    finance_keywords = (
        "инвестиции", "акции", "рынок", "крипта", "bitcoin", "криптовалют", "стартап",
        "маркетинг", "продажи", "налоги", "бюджет", "прибыль", "выручка", "бизнес",
        "экономика", "дивиденд", "инфляция", "банк", "финанс", "капитал", "менеджмент"
    )
    if any(k in combined_content for k in finance_keywords):
        return "📈 Бизнес & Финансы"

    science_keywords = (
        "курс", "лекция", "исследование", "физика", "биология", "психология", "история",
        "математика", "наука", "медицина", "здоровье", "диссертация", "учебник",
        "когнитивн", "нейробиолог", "книга", "чтение", "саморазвитие", "мозг", "мышление"
    )
    if any(k in combined_content for k in science_keywords):
        return "🔬 Наука & Образование"

    media_keywords = (
        "кино", "фильм", "сериал", "музыка", "трек", "альбом", "игра", "гейминг",
        "трейлер", "мем", "юмор", "шоу", "подкаст", "видео", "актёр", "режиссер",
        "концерт", "арт", "дизайн", "иллюстрация"
    )
    if any(k in combined_content for k in media_keywords):
        return "🎬 Медиа & Развлечения"

    shopping_keywords = (
        "рецепт", "готовка", "кулинар", "ремонт", "интерьер", "покупка", "скидка",
        "промокод", "магазин", "одежда", "обувь", "техника", "быт", "уборка", "дача",
        "сад", "квартира", "посуда", "мебель"
    )
    if any(k in combined_content for k in shopping_keywords):
        return "🛒 Быт & Покупки"

    return "💡 Разное"
