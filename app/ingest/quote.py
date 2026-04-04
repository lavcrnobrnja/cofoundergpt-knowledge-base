"""Raw text / quote ingestion."""


async def extract_quote(text: str, attribution: str | None = None) -> dict:
    """Handle raw text/quote ingestion.

    Returns: {"title": str, "author": str|None, "raw_content": str, "metadata": {}}
    """
    title = text[:80] + "..." if len(text) > 80 else text

    return {
        "title": f'"{title}"',
        "author": attribution,
        "published_at": None,
        "raw_content": text,
        "metadata": {},
    }
