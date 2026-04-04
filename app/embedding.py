"""Embedding utilities — Gemini Embedding 2 (chunk, embed, cosine similarity)."""
import numpy as np
from google import genai
from app import config


def get_gemini_client():
    """Get or create Gemini client for embeddings."""
    return genai.Client(api_key=config.GEMINI_API_KEY)


async def embed_text(text: str) -> list[float]:
    """Embed a single text. Returns list of 3072 floats."""
    import asyncio
    client = get_gemini_client()
    result = await asyncio.to_thread(
        client.models.embed_content,
        model=config.EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batch embed multiple texts."""
    if not texts:
        return []
    import asyncio
    client = get_gemini_client()
    result = await asyncio.to_thread(
        client.models.embed_content,
        model=config.EMBEDDING_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


def serialize_embedding(embedding: list[float]) -> bytes:
    """Convert embedding to bytes for SQLite BLOB storage."""
    return np.array(embedding, dtype=np.float32).tobytes()


def deserialize_embedding(blob: bytes) -> list[float]:
    """Convert SQLite BLOB back to list of floats."""
    return np.frombuffer(blob, dtype=np.float32).tolist()


def chunk_text(text: str, max_chunk_chars: int = 4000, overlap_chars: int = 200) -> list[dict]:
    """Chunk text for embedding.

    Rules from spec:
    - Short (<4K chars): 1 chunk
    - Medium (4K-16K): 2-4 chunks with ~200 char overlap
    - Long (>16K): ~4K char chunks with overlap, break at paragraphs

    Returns list of {"text": str, "token_count": int (estimated)}
    """
    if len(text) <= max_chunk_chars:
        return [{"text": text, "token_count": len(text) // 4}]

    chunks = []
    # Split on paragraphs first; for continuous text (e.g. transcripts) also split on sentences
    paragraphs = text.split("\n\n")
    # If text has no paragraph breaks (e.g. YouTube transcript), split on sentence boundaries
    if len(paragraphs) == 1 and len(text) > max_chunk_chars:
        import re
        # Split on sentence-ending punctuation followed by space
        sentences = re.split(r'(?<=[.!?])\s+', text)
        paragraphs = sentences
    current_chunk = ""

    for para in paragraphs:
        # If a single segment is larger than max_chunk_chars, force-split it
        if len(para) > max_chunk_chars:
            # Flush current chunk first
            if current_chunk.strip():
                chunks.append({"text": current_chunk.strip(), "token_count": len(current_chunk) // 4})
                current_chunk = ""
            # Hard split the oversized segment
            for i in range(0, len(para), max_chunk_chars - overlap_chars):
                segment = para[i:i + max_chunk_chars]
                chunks.append({"text": segment.strip(), "token_count": len(segment) // 4})
            continue
        if len(current_chunk) + len(para) + 2 > max_chunk_chars and current_chunk:
            chunks.append({"text": current_chunk.strip(), "token_count": len(current_chunk) // 4})
            # Overlap: take last overlap_chars of current chunk
            overlap = current_chunk[-overlap_chars:] if len(current_chunk) > overlap_chars else ""
            current_chunk = overlap + " " + para
        else:
            current_chunk = current_chunk + " " + para if current_chunk else para

    if current_chunk.strip():
        chunks.append({"text": current_chunk.strip(), "token_count": len(current_chunk) // 4})

    return chunks if chunks else [{"text": text, "token_count": len(text) // 4}]


def sample_content(text: str, max_chars: int = 8000) -> str:
    """For long documents, take equal slices from start, middle, end."""
    if len(text) <= max_chars:
        return text

    third = max_chars // 3
    start = text[:third]
    mid_point = len(text) // 2
    middle = text[mid_point - third // 2: mid_point + third // 2]
    end = text[-third:]

    return f"{start}\n\n[...]\n\n{middle}\n\n[...]\n\n{end}"
