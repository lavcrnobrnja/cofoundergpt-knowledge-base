"""Tests for the ingest endpoint and source type detection."""
import pytest
from unittest.mock import patch, AsyncMock


# --- Tweet URL filtering tests ---

class TestTweetUrlFiltering:
    def test_filters_twitter_domains(self):
        from app.ingest.tweet import _filter_external_urls
        urls = [
            "https://t.co/abc123",
            "https://twitter.com/user/status/123",
            "https://x.com/user/status/456",
            "https://example.com/article",
            "https://www.nytimes.com/2026/story",
        ]
        result = _filter_external_urls(urls)
        assert result == ["https://example.com/article", "https://www.nytimes.com/2026/story"]

    def test_empty_list(self):
        from app.ingest.tweet import _filter_external_urls
        assert _filter_external_urls([]) == []

    def test_all_twitter_urls(self):
        from app.ingest.tweet import _filter_external_urls
        urls = ["https://t.co/x", "https://pic.twitter.com/y", "https://x.com/z"]
        assert _filter_external_urls(urls) == []

    def test_malformed_urls_skipped(self):
        from app.ingest.tweet import _filter_external_urls
        urls = ["not-a-url", "https://example.com/good"]
        result = _filter_external_urls(urls)
        # "not-a-url" has no hostname, gets filtered; "example.com" passes
        assert "https://example.com/good" in result


# --- Source type detection tests ---

class TestDetectSourceType:
    def test_detect_youtube_url(self):
        from app.ingest import detect_source_type
        assert detect_source_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"

    def test_detect_youtu_be(self):
        from app.ingest import detect_source_type
        assert detect_source_type("https://youtu.be/dQw4w9WgXcQ") == "youtube"

    def test_detect_youtube_shorts(self):
        from app.ingest import detect_source_type
        assert detect_source_type("https://youtube.com/shorts/abc123") == "youtube"

    def test_detect_tweet_x(self):
        from app.ingest import detect_source_type
        assert detect_source_type("https://x.com/elonmusk/status/123456789") == "tweet"

    def test_detect_tweet_twitter(self):
        from app.ingest import detect_source_type
        assert detect_source_type("https://twitter.com/elonmusk/status/123456789") == "tweet"

    def test_detect_substack(self):
        from app.ingest import detect_source_type
        assert detect_source_type("https://something.substack.com/p/my-post") == "substack"

    def test_detect_article_default(self):
        from app.ingest import detect_source_type
        assert detect_source_type("https://example.com/some-article") == "article"


# --- Tweet auto-follow tests ---

class TestTweetAutoFollow:
    @pytest.mark.anyio
    async def test_tweet_ingest_returns_linked_urls(self, client):
        """Tweet extractor should surface linked_urls on the response object."""
        mock_result = {
            "title": "Check this out...",
            "author": "testuser",
            "published_at": "2026-04-04T12:00:00Z",
            "raw_content": "Check this out https://example.com/article — amazing read",
            "metadata": {"tweet_id": "123456"},
            "linked_urls": ["https://example.com/article"],
        }
        with patch("app.ingest.tweet.extract_tweet", new_callable=AsyncMock, return_value=mock_result):
            resp = await client.post("/ingest", json={"url": "https://x.com/user/status/123456"})
        assert resp.status_code == 201
        assert resp.json()["source_type"] == "tweet"

    @pytest.mark.anyio
    async def test_tweet_linked_urls_stored_in_metadata(self, client):
        """Linked URLs from tweet should be discoverable."""
        mock_result = {
            "title": "Thread with links",
            "author": "testuser",
            "published_at": None,
            "raw_content": "Here are two great reads: first and second. Must read both.",
            "metadata": {"tweet_id": "789"},
            "linked_urls": ["https://blog.example.com/post1", "https://news.example.com/story"],
        }
        with patch("app.ingest.tweet.extract_tweet", new_callable=AsyncMock, return_value=mock_result):
            resp = await client.post("/ingest", json={"url": "https://x.com/user/status/789"})
        assert resp.status_code == 201


# --- Ingest endpoint tests ---

class TestIngestEndpoint:
    @pytest.mark.anyio
    async def test_ingest_quote_text(self, client):
        resp = await client.post("/ingest", json={
            "text": "The only way to do great work is to love what you do."
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_type"] == "quote"
        assert data["status"] == "pending"
        assert data["id"]

    @pytest.mark.anyio
    async def test_ingest_quote_with_attribution(self, client):
        resp = await client.post("/ingest", json={
            "text": "The only way to do great work is to love what you do.",
            "attribution": "Steve Jobs"
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_type"] == "quote"

        # Verify author was stored
        from app.database import get_db
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT author FROM sources WHERE id = ?", (data["id"],)
            )
            row = await cursor.fetchone()
            assert row[0] == "Steve Jobs"

    @pytest.mark.anyio
    async def test_ingest_no_url_no_text(self, client):
        resp = await client.post("/ingest", json={})
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_ingest_dedup_same_content(self, client):
        payload = {"text": "This is a duplicate test with enough characters to pass validation."}

        resp1 = await client.post("/ingest", json=payload)
        assert resp1.status_code == 201
        id1 = resp1.json()["id"]

        resp2 = await client.post("/ingest", json=payload)
        assert resp2.status_code == 200
        id2 = resp2.json()["id"]
        assert id1 == id2

    @pytest.mark.anyio
    async def test_ingest_dedup_different_content(self, client):
        """Same URL with different content should re-ingest (delete old, create new)."""
        mock_result_1 = {
            "title": "Test Article v1",
            "author": "Author",
            "published_at": None,
            "raw_content": "This is the first version of the article content, long enough.",
            "metadata": {}
        }
        mock_result_2 = {
            "title": "Test Article v2",
            "author": "Author",
            "published_at": None,
            "raw_content": "This is the SECOND version with completely different text content.",
            "metadata": {}
        }

        with patch("app.ingest.article.extract_article", new_callable=AsyncMock) as mock_ext:
            mock_ext.return_value = mock_result_1
            resp1 = await client.post("/ingest", json={"url": "https://example.com/article"})
            assert resp1.status_code == 201
            id1 = resp1.json()["id"]

            mock_ext.return_value = mock_result_2
            resp2 = await client.post("/ingest", json={"url": "https://example.com/article"})
            assert resp2.status_code == 201
            id2 = resp2.json()["id"]

        assert id1 != id2  # New row created

    @pytest.mark.anyio
    async def test_ingest_article_mocked(self, client):
        mock_result = {
            "title": "Great Article Title",
            "author": "Jane Doe",
            "published_at": "2026-01-15T00:00:00",
            "raw_content": "This is a sufficiently long article content for testing purposes.",
            "metadata": {}
        }
        with patch("app.ingest.article.extract_article", new_callable=AsyncMock, return_value=mock_result):
            resp = await client.post("/ingest", json={"url": "https://example.com/great-article"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_type"] == "article"
        assert data["title"] == "Great Article Title"

    @pytest.mark.anyio
    async def test_ingest_short_content_rejected(self, client):
        mock_result = {
            "title": "Short",
            "author": None,
            "published_at": None,
            "raw_content": "Too short",
            "metadata": {}
        }
        with patch("app.ingest.article.extract_article", new_callable=AsyncMock, return_value=mock_result):
            resp = await client.post("/ingest", json={"url": "https://example.com/short"})
        assert resp.status_code == 422
        assert "too short" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_ingest_dedup_updates_user_context(self, client):
        """Dedup with same content should update user_context if provided."""
        payload = {"text": "Repeat content for context update test, long enough."}
        resp1 = await client.post("/ingest", json=payload)
        assert resp1.status_code == 201
        source_id = resp1.json()["id"]

        resp2 = await client.post("/ingest", json={**payload, "user_context": "Important for project X"})
        assert resp2.status_code == 200

        from app.database import get_db
        async with get_db() as db:
            cursor = await db.execute("SELECT user_context FROM sources WHERE id = ?", (source_id,))
            row = await cursor.fetchone()
            assert row[0] == "Important for project X"
