"""Application configuration."""
import os
from pathlib import Path

# Gemini API key — from environment, never hardcoded
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Model names (spec-locked — do not modify)
FLASH_MODEL = "gemini-3-flash-preview"
PRO_MODEL = "gemini-3.1-pro-preview"
EMBEDDING_MODEL = "gemini-embedding-2-preview"

# Embedding dimensions
EMBEDDING_DIMENSIONS = 3072

# Paths
_project_dir = Path(__file__).parent.parent
DB_PATH = Path(os.environ.get("KB_DB_PATH", str(_project_dir / "data" / "knowledge.db")))
WIKI_DIR = _project_dir / "wiki"
DATA_DIR = _project_dir / "data"
