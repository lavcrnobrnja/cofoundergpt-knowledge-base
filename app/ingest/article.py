"""Article extraction using newspaper4k."""
import newspaper


async def extract_article(url: str) -> dict:
    """Extract article content using newspaper4k.

    Returns: {"title": str, "author": str|None, "published_at": str|None,
              "raw_content": str, "metadata": {}}
    """
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
