"""PDF extraction using PyMuPDF."""
import asyncio
from pathlib import Path

import pymupdf


def _extract_sync(path: str) -> dict:
    """Synchronous PDF extraction — runs in thread pool."""
    doc = pymupdf.open(path)
    title = doc.metadata.get("title") or Path(path).stem
    author = doc.metadata.get("author") or None
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text)
    doc.close()
    raw_content = "\n\n".join(pages)
    return {
        "title": title,
        "author": author,
        "published_at": None,
        "raw_content": raw_content,
        "metadata": {"page_count": len(pages)},
    }


async def extract_pdf(path: str) -> dict:
    """Extract text content from a PDF file.

    Args:
        path: Local filesystem path to the PDF.

    Returns: {"title": str, "author": str|None, "published_at": None,
              "raw_content": str, "metadata": {"page_count": int}}
    """
    return await asyncio.to_thread(_extract_sync, path)
