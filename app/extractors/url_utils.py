import re
import urllib.parse
from typing import List, Optional, Set

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


def normalize_emoji_digits(text: Optional[str]) -> str:
    """
    Заменяет цифры-эмодзи и кейкапы (1️⃣, 2️⃣ и т.д., включая кастомные эмодзи TG Premium)
    на обычные стандартные цифры.
    """
    if not text:
        return "" if text is None else text
    replacements = {
        "1️⃣": "1.", "2️⃣": "2.", "3️⃣": "3.", "4️⃣": "4.", "5️⃣": "5.",
        "6️⃣": "6.", "7️⃣": "7.", "8️⃣": "8.", "9️⃣": "9.", "0️⃣": "0.", "🔟": "10.",
        "1\ufe0f\u20e3": "1.", "2\ufe0f\u20e3": "2.", "3\ufe0f\u20e3": "3.", "4\ufe0f\u20e3": "4.", "5\ufe0f\u20e3": "5.",
        "6\ufe0f\u20e3": "6.", "7\ufe0f\u20e3": "7.", "8\ufe0f\u20e3": "8.", "9\ufe0f\u20e3": "9.", "0\ufe0f\u20e3": "0.",
        "1\u20e3": "1.", "2\u20e3": "2.", "3\u20e3": "3.", "4\u20e3": "4.", "5\u20e3": "5.",
        "6\u20e3": "6.", "7\u20e3": "7.", "8\u20e3": "8.", "9\u20e3": "9.", "0\u20e3": "0.",
    }
    for emo, digit in replacements.items():
        text = text.replace(emo, digit)
    return text

