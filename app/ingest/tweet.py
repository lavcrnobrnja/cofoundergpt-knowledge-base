"""Tweet extraction via xurl CLI."""
import asyncio
import json
import re
import subprocess
from urllib.parse import urlparse

import httpx

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
        result = await asyncio.to_thread(
            subprocess.run,
            ["/opt/homebrew/bin/xurl", "read", tweet_id],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            data = resp.get("data", resp) # handle both wrapped and unwrapped just in case
            
            text = data.get("text", "")
            
            # Check for X Article title
            article_data = data.get("article", {})
            if not article_data:
                article_data = resp.get("article", {})
                
            if isinstance(article_data, dict) and article_data.get("title"):
                article_title = article_data.get("title")
                # If text is basically just the t.co link, replace it with the article title
                if text.startswith("https://t.co/") and len(text.split()) == 1:
                    text = article_title
                else:
                    text = f"{article_title}\n\n{text}"
                    
            # Resolve author: prefer username from includes.users, fall back to author_id
            author_id = data.get("author_id")
            author = None
            includes_users = resp.get("includes", {}).get("users", [])
            if author_id and includes_users:
                for u in includes_users:
                    if u.get("id") == author_id:
                        author = u.get("username") or u.get("name")
                        break
            if not author:
                author = data.get("username") or data.get("author") or author_id
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

            # If this is an X Article, try to fetch the full article content
            if isinstance(article_data, dict) and article_data.get("title"):
                article_content = await _fetch_x_article(url)
                if article_content:
                    text = f"{article_data['title']}\n\n{article_content}"
    except Exception:
        pass

    # Fallback
    if not text:
        text = f"Tweet {tweet_id} from {url}"

    # For X Articles, use the article title; for regular tweets, truncate text
    if isinstance(article_data, dict) and article_data.get("title"):
        title = article_data["title"]
    else:
        title = text[:80] + "..." if len(text) > 80 else text

    return {
        "title": title,
        "author": author,
        "published_at": created_at,
        "raw_content": text,
        "metadata": {"tweet_id": tweet_id},
        "linked_urls": linked_urls,
    }


async def _fetch_x_article(tweet_url: str) -> str | None:
    """Fetch the full text of an X Article via Jina Reader.
    
    X Articles are long-form posts whose content isn't in the API response.
    Jina Reader (r.jina.ai) can extract the full article text.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            resp = await client.get(
                f"https://r.jina.ai/{tweet_url}",
                headers={"Accept": "text/plain"},
            )
            if resp.status_code == 200 and len(resp.text) > 200:
                content = resp.text.strip()
                # Jina prepends metadata lines (Title:, URL Source:, etc.)
                # Find the actual content after "Markdown Content:" marker
                marker = "Markdown Content:"
                idx = content.find(marker)
                if idx != -1:
                    content = content[idx + len(marker):].strip()
                return content
    except Exception:
        pass
    
    return None
