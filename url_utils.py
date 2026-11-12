import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(
    r"https?://(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s<>'\"`()]*[^\s<>'\"`().,?!])?",
    re.IGNORECASE,
)

TRACKING_PARAMS: Set[str] = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_cid",
    "utm_reader",
    "fbclid",
    "gclid",
    "yclid",
    "ysclid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "_ga",
    "ref",
    "fref",
    "src",
    "igshid",
}


def extract_urls(text: Optional[str], entities: Optional[list] = None) -> List[str]:
    """
    Extract all valid HTTP/HTTPS URLs from text and Telegram message entities (including text_link hyperlinks).
    """
    urls: List[str] = []
    seen: Set[str] = set()

    # 1. Telegram entities (handles formatted text links: [тут](https://...))
    if entities:
        for ent in entities:
            u = None
            ent_type = getattr(ent, "type", None)
            if ent_type == "text_link" and getattr(ent, "url", None):
                u = ent.url.strip()
            elif ent_type == "url" and text:
                offset = getattr(ent, "offset", 0)
                length = getattr(ent, "length", 0)
                u = text[offset : offset + length].strip()
            if u and u not in seen and u.startswith(("http://", "https://")):
                seen.add(u)
                urls.append(u)

    # 2. Raw regex search in text string
    if text:
        for u in URL_REGEX.findall(text):
            u_clean = u.strip()
            if u_clean not in seen:
                seen.add(u_clean)
                urls.append(u_clean)

    return urls


def normalize_url(raw_url: str) -> str:
    """
    Normalizes URL for deduplication:
    - Lowercase scheme and netloc
    - Strip query parameters that match tracking tokens
    - Sort remaining query parameters
    - Strip trailing slashes (except root)
    - Strip URL fragment (#anchor)
    """
    raw_url = raw_url.strip()
    parsed = urllib.parse.urlparse(raw_url)

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Remove standard port if specified
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]

    # Clean path
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Filter query params
    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    filtered_params = [
        (k, v) for k, v in query_params if k.lower() not in TRACKING_PARAMS
    ]
    # Sort query parameters for consistent hash/url
    filtered_params.sort(key=lambda x: x[0])
    query = urllib.parse.urlencode(filtered_params)

    # Reconstruct without fragment
    normalized = urllib.parse.urlunparse((scheme, netloc, path or "/", "", query, ""))
    return normalized


def extract_domain(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc.lower().split(":")[0]
    except Exception:
        return ""


_EMOJI_DIGITS_MAP: Dict[str, str] = {
    "1️⃣": "1", "2️⃣": "2", "3️⃣": "3", "4️⃣": "4", "5️⃣": "5",
    "6️⃣": "6", "7️⃣": "7", "8️⃣": "8", "9️⃣": "9", "0️⃣": "0", "🔟": "10",
    "1\ufe0f\u20e3": "1", "2\ufe0f\u20e3": "2", "3\ufe0f\u20e3": "3", "4\ufe0f\u20e3": "4", "5\ufe0f\u20e3": "5",
    "6\ufe0f\u20e3": "6", "7\ufe0f\u20e3": "7", "8\ufe0f\u20e3": "8", "9\ufe0f\u20e3": "9", "0\ufe0f\u20e3": "0",
    "1\u20e3": "1", "2\u20e3": "2", "3\u20e3": "3", "4\u20e3": "4", "5\u20e3": "5",
    "6\u20e3": "6", "7\u20e3": "7", "8\u20e3": "8", "9\u20e3": "9", "0\u20e3": "0",
}
# ①..⑳ (1..20)
for _i in range(20):
    _EMOJI_DIGITS_MAP[chr(0x2460 + _i)] = str(_i + 1)
# ❶..❿ (1..10)
for _i in range(10):
    _EMOJI_DIGITS_MAP[chr(0x2776 + _i)] = str(_i + 1)
# ➀..➉ (1..10)
for _i in range(10):
    _EMOJI_DIGITS_MAP[chr(0x2780 + _i)] = str(_i + 1)
# ➊..➓ (1..10)
for _i in range(10):
    _EMOJI_DIGITS_MAP[chr(0x278A + _i)] = str(_i + 1)
# ０..９ (0..9)
for _i in range(10):
    _EMOJI_DIGITS_MAP[chr(0xFF10 + _i)] = str(_i)


def normalize_emoji_digits(text: Optional[str]) -> str:
    """
    Заменяет цифры-эмодзи, кейкапы (1️⃣, 2️⃣ и т.д.) и юникодные цифры в кружках
    на обычные стандартные цифры.
    """
    if not text:
        return "" if text is None else text
    for emo, digit in _EMOJI_DIGITS_MAP.items():
        text = text.replace(emo, digit)
    return text


def heal_placeholder_number_emojis(text: Optional[str]) -> str:
    """
    Восстанавливает числовые значения в заголовках постов из каналов, где автор использовал
    кастомные эмодзи Telegram Premium (отображающиеся как 🔴🔴 вместо цифр 20).
    Если в тексте есть список пунктов (например, 20 инструментов), заменяет плейсхолдеры 🔴🔴
    на точное количество пунктов. Также заменяет маркеры списков 🔴 на аккуратные буллеты •.
    """
    if not text:
        return "" if text is None else text

    # Находим пункты списка (строки, начинающиеся со значков списков)
    bullet_regex = re.compile(
        r"^[🔴🟢🔵🟣🟠🟡⚫⚪🟥🟧🟨🟩🟦🟪⬛⬜🔘🔻🔺🔹🔸💎⚡🔥💡📌👉✅✔•\-–—*]\s+([^\n]+)",
        re.MULTILINE,
    )
    items = bullet_regex.findall(text)

    if len(items) >= 2:
        placeholder_pattern = re.compile(
            r"(\b[Зз]абираем\b|\b[Тт]оп\b|\bТОП\b|\b[Пп]одборка\b|\b[Лл]учшие\b|\b[Вв]от\b|\b[Сс]обрали\b|\b[Нн]ашли\b|\b[Дд]ержите\b|\b[Лл]овите\b|\b[Сс]писок\b|\b[Бб]аза\b|\b[Гг]айд\b|\b[Вв]сего\b)?"
            r"(\s*[-—–]?\s*)([🔴🟢🔵🟣🟠🟡⚫⚪🟥🟧🟨🟩🟦🟪⬛⬜🔘]{1,3})"
            r"(\s+)(мощнейших|лучших|крутых|полезных|топовых|бесплатных|нейросетей|инструментов|сервисов|тулз|тулов|фишек|сайтов|ботов|промптов|нейронок|приложений|плагинов|расширений|книг|фильмов|курсов|уроков|способов|шагов|правил|советов|секретов|приемов|команд|библиотек|ресурсов|моделей|генераторов|альтернатив|аналогов|штук|вещей|идей|[а-яА-Яa-zA-Z]+)",
            re.IGNORECASE,
        )

        def _sub_placeholder(m: re.Match) -> str:
            prefix = m.group(1) or ""
            space1 = (m.group(2) or "") if prefix else ""
            space2 = m.group(4)
            word = m.group(5)
            return f"{prefix}{space1}{len(items)}{space2}{word}"

        text = placeholder_pattern.sub(_sub_placeholder, text, count=1)

    # Исправляем строчные маркеры 🔴 1. -> 1.
    text = re.sub(r"^[🔴🟢🔵🟣🟠🟡⚫⚪🟥🟧🟨🟩🟦🟪⬛⬜🔘]\s*(\d+[\.\)])\s*", r"\1 ", text, flags=re.MULTILINE)
    # Исправляем цветные шары в начале строк пунктов списка на стандартные маркеры •
    text = re.sub(r"^[🔴🟢🔵🟣🟠🟡⚫⚪🟥🟧🟨🟩🟦🟪⬛⬜🔘]\s*", "• ", text, flags=re.MULTILINE)
    return text


_sticker_set_cache: Dict[str, Any] = {}


async def resolve_telegram_custom_emojis(
    bot: Any,
    text: Optional[str],
    entities: Optional[List[Any]],
) -> str:
    """
    Разрешает кастомные эмодзи Telegram Premium (custom_emoji) в реальные символы/цифры.
    Использует get_custom_emoji_stickers / get_sticker_set Telegram Bot API для получения
    информации о кастомных стикерах и заменяет их по UTF-16 смещениям.
    После этого применяет нормализацию цифр и эвристику для списков.
    """
    if not text:
        return "" if text is None else text

    if bot and entities:
        custom_entities = [
            e for e in entities
            if getattr(e, "type", "") == "custom_emoji" and getattr(e, "custom_emoji_id", None)
        ]
        if custom_entities:
            try:
                unique_ids = list({e.custom_emoji_id for e in custom_entities})
                stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=unique_ids)
                sticker_map = {s.custom_emoji_id: s for s in (stickers or []) if getattr(s, "custom_emoji_id", None)}

                set_names = {s.set_name for s in sticker_map.values() if getattr(s, "set_name", None)}
                for sname in set_names:
                    if sname not in _sticker_set_cache:
                        try:
                            _sticker_set_cache[sname] = await bot.get_sticker_set(name=sname)
                        except Exception:
                            _sticker_set_cache[sname] = None

                sorted_entities = sorted(custom_entities, key=lambda e: e.offset, reverse=True)
                encoded = text.encode("utf-16-le")

                for ent in sorted_entities:
                    stk = sticker_map.get(ent.custom_emoji_id)
                    resolved_char = None
                    if stk:
                        stk_emoji = getattr(stk, "emoji", "") or ""
                        norm_emoji = normalize_emoji_digits(stk_emoji)
                        if norm_emoji and norm_emoji != stk_emoji and any(c.isdigit() for c in norm_emoji):
                            resolved_char = norm_emoji
                        elif stk.set_name and _sticker_set_cache.get(stk.set_name):
                            sset = _sticker_set_cache[stk.set_name]
                            stickers_in_set = getattr(sset, "stickers", []) or []
                            ids_in_set = [s.custom_emoji_id for s in stickers_in_set]
                            if ent.custom_emoji_id in ids_in_set:
                                idx = ids_in_set.index(ent.custom_emoji_id)
                                title_lower = (getattr(sset, "title", "") or "").lower()
                                name_lower = (getattr(sset, "name", "") or "").lower()
                                is_num_pack = any(
                                    kw in title_lower or kw in name_lower
                                    for kw in ("num", "digit", "цифр", "count", "score")
                                )
                                if is_num_pack:
                                    if len(stickers_in_set) == 10:
                                        resolved_char = str(idx)
                                    elif len(stickers_in_set) >= 10 and idx < 10:
                                        resolved_char = str(idx)
                                elif len(stickers_in_set) == 10:
                                    resolved_char = str(idx)

                    if resolved_char is not None:
                        byte_start = ent.offset * 2
                        byte_end = (ent.offset + ent.length) * 2
                        char_start = len(encoded[:byte_start].decode("utf-16-le"))
                        char_end = len(encoded[:byte_end].decode("utf-16-le"))
                        text = text[:char_start] + resolved_char + text[char_end:]
                        encoded = text.encode("utf-16-le")
            except Exception as e:
                logger.debug(f"Error resolving Telegram custom emojis: {e}")

    text = normalize_emoji_digits(text)
    text = heal_placeholder_number_emojis(text)
    return text


