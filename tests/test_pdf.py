"""Tests for PDF ingestion."""
import pytest
from unittest.mock import patch, MagicMock
from app.ingest import detect_source_type


class TestDetectPdfType:
    def test_local_path(self):
        assert detect_source_type("/tmp/paper.pdf") == "pdf"

    def test_local_path_uppercase(self):
        assert detect_source_type("/home/user/REPORT.PDF") == "pdf"

    def test_file_uri(self):
        assert detect_source_type("file:///Users/me/doc.pdf") == "pdf"

    def test_url_ending_pdf(self):
        assert detect_source_type("https://arxiv.org/pdf/2401.12345.pdf") == "pdf"

    def test_not_pdf(self):
        assert detect_source_type("/tmp/notes.txt") != "pdf"

    def test_url_not_pdf(self):
        assert detect_source_type("https://example.com/article") == "article"


class TestPdfExtractor:
    @pytest.mark.asyncio
    async def test_extract_pdf(self, tmp_path):
        """Create a real tiny PDF and extract text from it."""
        import pymupdf
        pdf_path = str(tmp_path / "test.pdf")
        doc = pymupdf.open()
        doc.set_metadata({"title": "Test Paper", "author": "Jane Doe"})
        page = doc.new_page()
        page.insert_text((72, 72), "This is a test PDF with enough content to pass the twenty char minimum.")
        doc.save(pdf_path)
        doc.close()

        from app.ingest.pdf import extract_pdf
        result = await extract_pdf(pdf_path)
        assert result["title"] == "Test Paper"
        assert result["author"] == "Jane Doe"
        assert "test PDF" in result["raw_content"]
        assert result["metadata"]["page_count"] == 1

    @pytest.mark.asyncio
    async def test_extract_multi_page(self, tmp_path):
        import pymupdf
        pdf_path = str(tmp_path / "multi.pdf")
        doc = pymupdf.open()
        for i in range(3):
            page = doc.new_page()
            page.insert_text((72, 72), f"Content on page {i + 1} with sufficient length for testing purposes.")
        doc.save(pdf_path)
        doc.close()

        from app.ingest.pdf import extract_pdf
        result = await extract_pdf(pdf_path)
        assert result["metadata"]["page_count"] == 3
        assert "page 1" in result["raw_content"]
        assert "page 3" in result["raw_content"]


class TestPdfIngestEndpoint:
    @pytest.mark.asyncio
    async def test_ingest_pdf_file(self, client, tmp_path):
        """Full roundtrip: ingest a local PDF via API."""
        import pymupdf
        pdf_path = str(tmp_path / "ingest_test.pdf")
        doc = pymupdf.open()
        doc.set_metadata({"title": "Ingested Paper"})
        page = doc.new_page()
        page.insert_text((72, 72), "This paper discusses important findings about knowledge management systems and their applications.")
        doc.save(pdf_path)
        doc.close()

        with patch("app.main.BackgroundTasks.add_task"):
            resp = await client.post("/ingest", json={"url": pdf_path})
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_type"] == "pdf"
        assert data["title"] == "Ingested Paper"
        assert data["status"] == "pending"
