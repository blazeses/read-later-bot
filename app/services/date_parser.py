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


MSK_TZ = timezone(timedelta(hours=3))


def parse_user_reminder_time(text: str, base_tz: timezone = MSK_TZ) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Parses user natural language reminder time (e.g. '15 мин', 'через 2 часа', '21:00', 'завтра 12:00').
    Enforces minimum threshold of 15 minutes.
    Returns (target_utc_datetime, error_message_or_none).
    """
    clean = text.strip().lower()
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now(base_tz)

    # 1. Direct minutes regex: '15 мин', 'через 30 минут', '45m'
    m_min = re.match(r"^(?:через\s+)?(\d+)\s*(?:мин(?:ут[ыа]?)?|m|min)$", clean)
    if m_min:
        mins = int(m_min.group(1))
        if mins < 15:
            return None, "Минимальное время напоминания — не менее 15 минут от текущего момента."
        return now_utc + timedelta(minutes=mins), None

    # 2. Direct hours regex: '1 ч', '2 часа', 'через 1.5 часа'
    m_hr = re.match(r"^(?:через\s+)?(\d+(?:[.,]\d+)?)\s*(?:ч(?:ас(?:а|ов)?)?|h|hour)$", clean)
    if m_hr:
        hrs = float(m_hr.group(1).replace(",", "."))
        mins = int(hrs * 60)
        if mins < 15:
            return None, "Минимальное время напоминания — не менее 15 минут от текущего момента."
        return now_utc + timedelta(minutes=mins), None

    # 3. Direct days regex: 'через 2 дня', '3 дня'
    m_day = re.match(r"^(?:через\s+)?(\d+)\s*(?:дн(?:ей|я)|день|д|days?)$", clean)
    if m_day:
        days = int(m_day.group(1))
        target_local = (now_local + timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
        return target_local.astimezone(timezone.utc), None

    # 4. Pure time of day HH:MM (e.g. '19:30', '21:00')
    m_time = re.match(r"^(?:в\s+)?([01]?\d|2[0-3])[:.]([0-5]\d)$", clean)
    if m_time:
        h, m = int(m_time.group(1)), int(m_time.group(2))
        target_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
        # If specified time today is within 15 minutes or in the past, shift to tomorrow
        if target_local <= now_local + timedelta(minutes=14, seconds=45):
            target_local += timedelta(days=1)
        target_utc = target_local.astimezone(timezone.utc)
        return target_utc, None

    # 5. Fallback: robust dateparser with Russian & English
    try:
        import dateparser
        parsed = dateparser.parse(
            clean,
            languages=["ru", "en"],
            settings={
                "PREFER_DATES_FROM": "future",
                "TIMEZONE": "+0300",
                "RETURN_AS_TIMEZONE_AWARE": True,
            },
        )
        if parsed:
            target_utc = parsed.astimezone(timezone.utc)
            if target_utc < now_utc + timedelta(minutes=14, seconds=45):
                return None, "Минимальное время напоминания — не менее 15 минут от текущего момента. Укажите время позже."
            return target_utc, None
    except Exception:
        pass

    return None, "Не удалось распознать время. Напишите, например: «15 минут», «через 2 часа», «завтра в 14:00» или «21:30»."


def try_parse_direct_reminder(text: str, base_tz: timezone = MSK_TZ) -> Optional[Tuple[datetime, str, Optional[str]]]:
    """
    Parses direct conversational commands like:
    - 'напомни через 30 минут позвонить врачу'
    - 'напомни завтра в 15:00 сдать отчет'
    - 'напомни мне в 21:00 выключить свет'
    Returns (target_utc_datetime, task_text, error_message_if_any) or None if not a reminder command.
    """
    clean = text.strip()
    m = re.match(r"^(?:напомни(?:ть)?|remind(?:\s+me)?)\s+(?:мне\s+)?(.+)$", clean, re.IGNORECASE)
    if not m:
        return None
    rest = m.group(1).strip()

    # 1. Relative: 'через X (мин|час|дней) <task>'
    m_rel = re.match(
        r"^(через\s+\d+(?:[.,]\d+)?\s*(?:мин(?:ут[ыа]?)?|ч(?:ас(?:а|ов)?)?|дн(?:ей|я)|день))\s+(?:что\s+|про\s+|о\s+|:\s*)?(.+)$",
        rest,
        re.IGNORECASE,
    )
    if m_rel:
        time_part, task_part = m_rel.group(1), m_rel.group(2)
        dt, err = parse_user_reminder_time(time_part, base_tz=base_tz)
        return dt, task_part.strip(), err

    # 2. Time of day: 'завтра/сегодня [в HH:MM] <task>'
    m_day = re.match(
        r"^(завтра|сегодня)?(?:\s*в\s+([01]?\d|2[0-3])[:.]([0-5]\d))\s+(?:что\s+|про\s+|о\s+|:\s*)?(.+)$",
        rest,
        re.IGNORECASE,
    )
    if m_day:
        day_word = m_day.group(1) or ""
        time_part = f"{day_word} в {m_day.group(2)}:{m_day.group(3)}".strip()
        task_part = m_day.group(4)
        dt, err = parse_user_reminder_time(time_part, base_tz=base_tz)
        return dt, task_part.strip(), err

    # 3. Day only: 'завтра <task>'
    m_tom = re.match(
        r"^(завтра(?:\s+утром|\s+вечером|\s+днем)?)\s+(?:что\s+|про\s+|о\s+|:\s*)?(.+)$",
        rest,
        re.IGNORECASE,
    )
    if m_tom:
        time_part = m_tom.group(1)
        task_part = m_tom.group(2)
        dt, err = parse_user_reminder_time(time_part, base_tz=base_tz)
        return dt, task_part.strip(), err

    return None


