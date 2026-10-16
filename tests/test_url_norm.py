import pytest
from app.extractors.url_utils import extract_urls, normalize_url


def test_normalize_url_strips_utm_and_trackers():
    raw = "https://Example.com/article/?utm_source=twitter&utm_medium=social&fbclid=IwAR123&id=42#section-2"
    expected = "https://example.com/article?id=42"
    assert normalize_url(raw) == expected


def test_normalize_url_trailing_slash():
    assert normalize_url("https://example.com/blog/") == "https://example.com/blog"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_url_query_sorting():
    url1 = "https://example.com/item?b=2&a=1"
    url2 = "https://example.com/item?a=1&b=2"
    assert normalize_url(url1) == normalize_url(url2)


def test_extract_multiple_urls():
    text = (
        "Посмотри эти две статьи: https://habr.com/ru/articles/123/ "
        "и ещё интересное видео https://youtu.be/dQw4w9WgXcQ?t=10, круто!"
    )
    urls = extract_urls(text)
    assert len(urls) == 2
    assert "https://habr.com/ru/articles/123/" in urls
    assert "https://youtu.be/dQw4w9WgXcQ?t=10" in urls
