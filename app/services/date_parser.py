import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


def parse_date_range_from_query(query: str) -> Tuple[str, Optional[datetime], Optional[datetime]]:
    """
    Detects natural language date filters in Russian and English,
    extracts the corresponding (date_from, date_to) UTC range,
    and returns the cleaned search query without the date trigger phrases.
    """
    now = datetime.now(timezone.utc)
    clean_query = query
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None

    patterns = [
        # Today
        (
            r"\b(?:за\s+)?сегодня\b|\btoday\b",
            lambda: (
                datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc),
                now,
            ),
        ),
        # Yesterday
        (
            r"\b(?:за\s+)?вчера\b|\byesterday\b",
            lambda: (
                datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc) - timedelta(days=1),
                datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=timezone.utc) - timedelta(days=1),
            ),
        ),
        # Week / 7 days
        (
            r"\b(?:за\s+)?(?:последн(?:юю|ей)\s+)?недел[юеи]\b|\b(?:past|last)\s+week\b",
            lambda: (now - timedelta(days=7), now),
        ),
        # Month / 30 days
        (
            r"\b(?:за\s+)?(?:последн(?:ий|ем)\s+)?месяц[еа]?\b|\b(?:past|last)\s+month\b",
            lambda: (now - timedelta(days=30), now),
        ),
        # Year / 365 days
        (
            r"\b(?:за\s+)?(?:последн(?:ий|ем)\s+)?год[ау]?\b|\b(?:past|last)\s+year\b",
            lambda: (now - timedelta(days=365), now),
        ),
        # N days (e.g. "за 3 дня", "past 5 days")
        (
            r"\b(?:за\s+)?(\d+)\s*(?:дн(?:ей|я)|дня)\b|\b(?:past|last)\s+(\d+)\s*days?\b",
            None,  # dynamic handler
        ),
    ]

    for pattern, handler in patterns:
        match = re.search(pattern, clean_query, re.IGNORECASE)
        if match:
            if handler is not None:
                date_from, date_to = handler()
            else:
                days_str = match.group(1) or match.group(2)
                days = int(days_str)
                date_from = now - timedelta(days=days)
                date_to = now

            clean_query = re.sub(pattern, " ", clean_query, flags=re.IGNORECASE).strip()
            clean_query = re.sub(r"\s+", " ", clean_query)
            break

    return clean_query.strip(), date_from, date_to
