"""Test configuration and fixtures."""
import os
import pytest
import httpx
from pathlib import Path

# Override DB path before importing app modules
os.environ["KB_DB_PATH"] = ""  # Will be set per-test via fixture


@pytest.fixture(autouse=True)
async def setup_temp_db(tmp_path):
    """Set up a temporary database and wiki dir for each test."""
    db_path = str(tmp_path / "test.db")
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    os.environ["KB_DB_PATH"] = db_path

    # Re-import to pick up new paths
    from app import config
    config.DB_PATH = Path(db_path)
    config.WIKI_DIR = wiki_dir

    from app.database import init_db
    await init_db()

    yield db_path


@pytest.fixture
async def client(setup_temp_db):
    """Async test client for the FastAPI app."""
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


pytest_plugins = []
