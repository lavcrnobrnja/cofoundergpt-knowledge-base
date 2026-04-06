"""Tweet extraction via xurl CLI."""
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# yt-dlp lives in the project venv, not global PATH
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_YTDLP_BIN = os.path.join(_PROJECT_DIR, ".venv", "bin", "yt-dlp")

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
    article_title = None  # set if this is an X Article

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["/opt/homebrew/bin/xurl", "read", tweet_id],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            data = resp.get("data", resp)

            text = data.get("text", "")

            # Check for X Article
            article_data = data.get("article") or resp.get("article") or {}
            if isinstance(article_data, dict) and article_data.get("title"):
                article_title = article_data["title"]

            # Resolve author: prefer username from includes.users, fall back to author_id
            author_id = data.get("author_id")
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

            # Filter out Twitter/X links and t.co shorteners
            linked_urls = _filter_external_urls(linked_urls)

            # For X Articles, fetch the full body via Jina Reader (API doesn't include it)
            if article_title:
                article_content = await _fetch_x_article(url)
                if article_content:
                    text = f"{article_title}\n\n{article_content}"
                else:
                    # Jina failed — use article title + whatever tweet text we have
                    text = f"{article_title}\n\n{text}" if text else article_title
    except Exception as e:
        logger.warning(f"xurl read failed for tweet {tweet_id}: {e}")

    # Check for video attachment — uses raw API call with media expansion
    video_transcript = None
    has_video = False
    if not article_title:  # Skip video check for X Articles
        try:
            video_info = await _detect_video(tweet_id)
            if video_info:
                has_video = True
                logger.info(f"Video detected on tweet {tweet_id}: duration={video_info.get('duration_ms')}ms")
                video_transcript = await _transcribe_tweet_video(url)
                if video_transcript:
                    text = f"{text}\n\n[Video transcript]\n{video_transcript}"
                    logger.info(f"Video transcript added: {len(video_transcript)} chars")
                else:
                    logger.warning(f"Video detected but transcription failed for tweet {tweet_id}")
        except Exception as e:
            logger.warning(f"Video detection/transcription error for tweet {tweet_id}: {e}")

    # Fallback
    if not text:
        text = f"Tweet {tweet_id} from {url}"

    # Title: use article title for X Articles, truncated text for regular tweets
    title = article_title if article_title else (text[:80] + "..." if len(text) > 80 else text)

    metadata = {"tweet_id": tweet_id}
    if has_video:
        metadata["has_video"] = True
        if video_transcript:
            metadata["video_transcribed"] = True

    return {
        "title": title,
        "author": author,
        "published_at": created_at,
        "raw_content": text,
        "metadata": metadata,
        "linked_urls": linked_urls,
    }


async def _detect_video(tweet_id: str) -> dict | None:
    """Check if tweet has a video attachment via X API with media expansion.
    
    Returns dict with video info (type, duration_ms) or None if no video.
    """
    try:
        api_url = (
            f"/2/tweets/{tweet_id}"
            f"?tweet.fields=attachments"
            f"&expansions=attachments.media_keys"
            f"&media.fields=type,duration_ms,preview_image_url"
        )
        result = await asyncio.to_thread(
            subprocess.run,
            ["/opt/homebrew/bin/xurl", api_url],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        
        resp = json.loads(result.stdout)
        media_list = resp.get("includes", {}).get("media", [])
        for m in media_list:
            if m.get("type") == "video":
                return {
                    "type": "video",
                    "duration_ms": m.get("duration_ms"),
                    "preview_image_url": m.get("preview_image_url"),
                }
    except Exception as e:
        logger.debug(f"Video detection failed for {tweet_id}: {e}")
    
    return None


async def _transcribe_tweet_video(tweet_url: str) -> str | None:
    """Download video from tweet via yt-dlp, transcribe with Whisper.
    
    Returns transcript text or None on failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")
        
        # Download audio only via yt-dlp (it supports X/Twitter natively)
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    _YTDLP_BIN,
                    "--extract-audio",
                    "--audio-format", "mp3",
                    "--audio-quality", "5",  # lower quality = smaller file, faster
                    "-o", os.path.join(tmpdir, "audio.%(ext)s"),
                    "--no-playlist",
                    "--no-warnings",
                    tweet_url,
                ],
                capture_output=True, text=True, timeout=120,
                cwd=tmpdir,
            )
            if result.returncode != 0:
                logger.warning(f"yt-dlp failed: {result.stderr[:200]}")
                return None
            
            # yt-dlp may output to slightly different filename
            if not os.path.exists(audio_path):
                # Find the mp3 file
                for f in os.listdir(tmpdir):
                    if f.endswith(".mp3"):
                        audio_path = os.path.join(tmpdir, f)
                        break
                else:
                    logger.warning("yt-dlp produced no mp3 file")
                    return None
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp timed out")
            return None
        except Exception as e:
            logger.warning(f"yt-dlp error: {e}")
            return None
        
        # Check file size — skip very large files (>50MB)
        file_size = os.path.getsize(audio_path)
        if file_size > 50 * 1024 * 1024:
            logger.warning(f"Audio file too large ({file_size/1024/1024:.1f}MB), skipping transcription")
            return None
        
        # Transcribe with Whisper
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "/opt/homebrew/bin/whisper",
                    audio_path,
                    "--model", "base",
                    "--output_format", "txt",
                    "--output_dir", tmpdir,
                    "--language", "en",
                    "--fp16", "False",
                ],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                logger.warning(f"Whisper failed: {result.stderr[:200]}")
                return None
            
            # Read transcript — Whisper outputs audio.txt
            txt_path = audio_path.rsplit(".", 1)[0] + ".txt"
            if os.path.exists(txt_path):
                with open(txt_path) as f:
                    transcript = f.read().strip()
                if len(transcript) > 20:
                    return transcript
                else:
                    logger.warning(f"Transcript too short ({len(transcript)} chars)")
                    return None
            else:
                logger.warning("Whisper produced no output file")
                return None
        except subprocess.TimeoutExpired:
            logger.warning("Whisper timed out")
            return None
        except Exception as e:
            logger.warning(f"Whisper error: {e}")
            return None


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
