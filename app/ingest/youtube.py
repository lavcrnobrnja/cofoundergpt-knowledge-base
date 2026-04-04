"""YouTube extraction — metadata via yt-dlp + transcript via youtube-transcript-api."""
import json
import re
import subprocess
from urllib.parse import parse_qs, urlparse


def extract_video_id(url: str) -> str:
    """Extract video ID from various YouTube URL formats."""
    parsed = urlparse(url)

    # youtu.be/VIDEO_ID
    if parsed.hostname in ("youtu.be",):
        return parsed.path.lstrip("/")

    # youtube.com/shorts/VIDEO_ID
    match = re.match(r"/shorts/([^/?&]+)", parsed.path)
    if match:
        return match.group(1)

    # youtube.com/watch?v=VIDEO_ID
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]

    raise ValueError(f"Cannot extract video ID from: {url}")


async def extract_youtube(url: str) -> dict:
    """Extract YouTube video transcript and metadata.

    Returns: {"title": str, "author": str|None, "published_at": str|None,
              "raw_content": str, "metadata": {"video_id": str, "duration": int|None}}
    """
    video_id = extract_video_id(url)

    # Get metadata via yt-dlp (no download)
    meta = {}
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            meta = json.loads(result.stdout)
    except Exception:
        pass

    # Get transcript
    raw_content = ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        raw_content = " ".join(entry["text"] for entry in transcript_list)
    except Exception:
        # Fallback: use description if no transcript
        raw_content = meta.get("description", "")

    # Normalize upload_date YYYYMMDD → ISO
    published_at = None
    upload_date = meta.get("upload_date")
    if upload_date and len(upload_date) == 8:
        published_at = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

    return {
        "title": meta.get("title", f"YouTube: {video_id}"),
        "author": meta.get("uploader", None),
        "published_at": published_at,
        "raw_content": raw_content,
        "metadata": {"video_id": video_id, "duration": meta.get("duration")},
    }
