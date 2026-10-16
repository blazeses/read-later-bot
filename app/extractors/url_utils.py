import re
import urllib.parse
from typing import List, Set

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


def extract_urls(text: str) -> List[str]:
    """Extract all valid HTTP/HTTPS URLs from text."""
    if not text:
        return []
    return URL_REGEX.findall(text)


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
