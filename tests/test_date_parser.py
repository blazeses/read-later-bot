from datetime import datetime, timezone
from app.services.date_parser import parse_date_range_from_query


def test_parse_past_month():
    query = "архитектура микросервисов за последний месяц"
    clean, d_from, d_to = parse_date_range_from_query(query)
    assert clean == "архитектура микросервисов"
    assert d_from is not None
    assert d_to is not None
    diff_days = (d_to - d_from).days
    assert 29 <= diff_days <= 31


def test_parse_past_week():
    query = "python async frameworks past week"
    clean, d_from, d_to = parse_date_range_from_query(query)
    assert clean == "python async frameworks"
    assert d_from is not None
    diff_days = (d_to - d_from).days
    assert 6 <= diff_days <= 8


def test_parse_today():
    query = "заметки про docker за сегодня"
    clean, d_from, d_to = parse_date_range_from_query(query)
    assert clean == "заметки про docker"
    assert d_from is not None
    assert d_from.hour == 0
    assert d_from.minute == 0


def test_no_date():
    query = "машинное обучение и трансформеры"
    clean, d_from, d_to = parse_date_range_from_query(query)
    assert clean == query
    assert d_from is None
    assert d_to is None
