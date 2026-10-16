import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch

from app.db.models import ContentType
from app.extractors.documents import DocumentExtractor
from app.extractors.twitter import TwitterExtractor
from app.extractors.youtube import YouTubeExtractor


@pytest.mark.asyncio
async def test_document_extractor_txt_and_md():
    extractor = DocumentExtractor()
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Тестовый заголовок\n\nЭто содержимое документа для проверки работы экстрактора.")
        temp_path = f.name

    try:
        content = await extractor.extract(temp_path, filename="sample.md")
        assert content.success is True
        assert "Тестовый заголовок" in content.text
        assert content.content_type == ContentType.DOCUMENT
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@pytest.mark.asyncio
async def test_twitter_extractor_mock():
    extractor = TwitterExtractor()

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "text": "Тестовый твит о Python и AI",
        "user_name": "Developer",
        "user_screen_name": "dev_ai",
        "date": "2026-10-15",
    }

    with patch("app.extractors.twitter.create_safe_async_client") as mock_client_factory:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_factory.return_value = mock_client

        content = await extractor.extract("https://x.com/dev_ai/status/1234567890")
        assert content.success is True
        assert "Тестовый твит о Python и AI" in content.text
        assert "Developer" in content.author
        assert content.content_type == ContentType.TWEET


@pytest.mark.asyncio
async def test_youtube_extractor_mock():
    extractor = YouTubeExtractor()

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "title": "Интересная лекция по RAG",
        "author_name": "TechChannel",
    }

    with patch("app.extractors.youtube.create_safe_async_client") as mock_client_factory, \
         patch("app.extractors.youtube.YouTubeTranscriptApi.get_transcript") as mock_transcript:

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_factory.return_value = mock_client

        mock_transcript.return_value = [
            {"text": "Привет всем!"},
            {"text": "Сегодня мы говорим о векторных базах данных."},
        ]

        content = await extractor.extract("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert content.success is True
        assert "Интересная лекция по RAG" in content.title
        assert "TechChannel" in content.author
        assert "Сегодня мы говорим о векторных базах данных" in content.text
        assert content.content_type == ContentType.YOUTUBE
