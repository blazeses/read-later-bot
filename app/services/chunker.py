import hashlib
import re
from dataclasses import dataclass
from typing import List


@dataclass
class TextChunk:
    chunk_index: int
    text: str
    chunk_hash: str


class TextChunker:
    """
    Splits text into chunks of 500-800 tokens (~2000-3200 chars) with 10-15% overlap,
    respecting paragraph and sentence boundaries.
    """
    def __init__(self, target_chars: int = 2400, overlap_chars: int = 300):
        self.target_chars = target_chars
        self.overlap_chars = overlap_chars

    def chunk_text(self, text: str) -> List[TextChunk]:
        clean_text = text.strip()
        if not clean_text:
            return []

        # If text is small enough, return as single chunk
        if len(clean_text) <= self.target_chars:
            chunk_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
            return [TextChunk(chunk_index=0, text=clean_text, chunk_hash=chunk_hash)]

        # Split into natural paragraphs
        paragraphs = [p.strip() for p in clean_text.split("\n\n") if p.strip()]
        chunks: List[str] = []
        current_chunk: List[str] = []
        current_length = 0

        for para in paragraphs:
            para_len = len(para)

            # If a single paragraph is larger than target_chars, split by sentences
            if para_len > self.target_chars:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                for sentence in sentences:
                    sent_len = len(sentence)
                    if current_length + sent_len > self.target_chars and current_chunk:
                        chunk_str = "\n\n".join(current_chunk)
                        chunks.append(chunk_str)
                        # Overlap: keep last piece
                        last_piece = current_chunk[-1] if current_chunk else ""
                        current_chunk = [last_piece] if len(last_piece) <= self.overlap_chars else []
                        current_length = sum(len(p) for p in current_chunk)

                    current_chunk.append(sentence)
                    current_length += sent_len
            else:
                if current_length + para_len > self.target_chars and current_chunk:
                    chunk_str = "\n\n".join(current_chunk)
                    chunks.append(chunk_str)
                    last_piece = current_chunk[-1]
                    current_chunk = [last_piece] if len(last_piece) <= self.overlap_chars else []
                    current_length = sum(len(p) for p in current_chunk)

                current_chunk.append(para)
                current_length += para_len

        if current_chunk:
            chunk_str = "\n\n".join(current_chunk)
            if not chunks or chunk_str != chunks[-1]:
                chunks.append(chunk_str)

        # Build dataclass with SHA256 hashes
        result: List[TextChunk] = []
        for idx, c_text in enumerate(chunks):
            c_hash = hashlib.sha256(c_text.encode("utf-8")).hexdigest()
            result.append(TextChunk(chunk_index=idx, text=c_text, chunk_hash=c_hash))

        return result


chunker = TextChunker()
