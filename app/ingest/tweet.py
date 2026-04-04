"""Tweet extraction via xurl CLI."""
import json
import re
import subprocess


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

            # Extract URLs from tweet text
            linked_urls = re.findall(r"https?://[^\s]+", text)
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
