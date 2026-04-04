"""Tweet extraction via xurl CLI."""
import json
import re
import subprocess
from urllib.parse import urlparse

# Domains that are Twitter/X itself — not external content
_TWITTER_DOMAINS = {
    "t.co", "twitter.com", "www.twitter.com",
    "x.com", "www.x.com", "pic.twitter.com",
    "mobile.twitter.com", "mobile.x.com",
}


def _filter_external_urls(urls: list[str]) -> list[str]:
    """Keep only external URLs — filter out Twitter/X self-links and t.co shorteners."""
    external = []
    for url in urls:
        try:
            host = urlparse(url).hostname or ""
            if host.lower() not in _TWITTER_DOMAINS:
                external.append(url)
        except Exception:
            pass
    return external


def extract_tweet_id(url: str) -> str:
    """Extract tweet ID from URL (last numeric path segment)."""
    match = re.search(r"/status/(\d+)", url)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract tweet ID from: {url}")


async def extract_tweet(url: str) -> dict:
    """Extract tweet content via xurl CLI.

    Returns: {"title": str, "author": str|None, "published_at": str|None,
              "raw_content": str, "metadata": {"tweet_id": str}, "linked_urls": list[str]}
    """
    tweet_id = extract_tweet_id(url)

    text = ""
    author = None
    created_at = None
    linked_urls = []

    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/xurl", "read", tweet_id],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            text = data.get("text", "")
            author = data.get("author_id") or data.get("username") or data.get("author")
            created_at = data.get("created_at")

            # Extract URLs from tweet entities or text
            if isinstance(data, dict) and "entities" in data:
                urls_data = data.get("entities", {}).get("urls", [])
                for u in urls_data:
                    expanded = u.get("expanded_url") or u.get("url", "")
                    if expanded:
                        linked_urls.append(expanded)
            if not linked_urls:
                linked_urls = re.findall(r"https?://[^\s]+", text)

            # Filter out Twitter/X links and t.co shorteners (keep external URLs only)
            linked_urls = _filter_external_urls(linked_urls)
    except Exception:
        pass

    # Fallback
    if not text:
        text = f"Tweet {tweet_id} from {url}"

    title = text[:80] + "..." if len(text) > 80 else text

    return {
        "title": title,
        "author": author,
        "published_at": created_at,
        "raw_content": text,
        "metadata": {"tweet_id": tweet_id},
        "linked_urls": linked_urls,
    }
