"""Article extraction using newspaper4k."""
import asyncio

import newspaper


def _extract_sync(url: str) -> dict:
    """Synchronous extraction — runs in thread pool."""
    article = newspaper.Article(url)
    article.download()
    article.parse()
    return {
        "title": article.title or url,
        "author": ", ".join(article.authors) if article.authors else None,
        "published_at": article.publish_date.isoformat() if article.publish_date else None,
        "raw_content": article.text,
        "metadata": {},
    }


async def extract_article(url: str) -> dict:
    """Extract article content using newspaper4k.

    Returns: {"title": str, "author": str|None, "published_at": str|None,
              "raw_content": str, "metadata": {}}
    """
    return await asyncio.to_thread(_extract_sync, url)
