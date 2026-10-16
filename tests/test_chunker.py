import hashlib
from app.services.chunker import TextChunker


def test_chunker_short_text():
    chunker = TextChunker(target_chars=500, overlap_chars=50)
    text = "Короткий текст заметки."
    chunks = chunker.chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].text == text
    assert chunks[0].chunk_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_chunker_long_paragraphs():
    chunker = TextChunker(target_chars=200, overlap_chars=40)
    para1 = "Первый длинный абзац текста, в котором достаточно много слов для проверки разбиения."
    para2 = "Второй длинный абзац текста, который должен перейти в следующий чанк с оверлапом."
    para3 = "Третий абзац завершает эту тестовую последовательность."
    full_text = f"{para1}\n\n{para2}\n\n{para3}"

    chunks = chunker.chunk_text(full_text)
    assert len(chunks) >= 2
    for idx, c in enumerate(chunks):
        assert c.chunk_index == idx
        assert len(c.chunk_hash) == 64
        assert len(c.text) > 0
