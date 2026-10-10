"""
RideWise Assist — Central configuration.

All paths are resolved relative to the project root (the directory containing
this file's parent). Override any setting with an environment variable or a
.env file placed in the project root.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Project root ──────────────────────────────────────────────────────────────
# config/ is one level below the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH: Path = Path(os.getenv("DB_PATH", PROJECT_ROOT / "data" / "source" / "ridewise.db"))

# ── Documents ─────────────────────────────────────────────────────────────────
DOCS_SOURCE_DIR: Path = Path(
    os.getenv("DOCS_SOURCE_DIR", PROJECT_ROOT / "documents" / "source")
)
DOCS_PROCESSED_DIR: Path = Path(
    os.getenv("DOCS_PROCESSED_DIR", PROJECT_ROOT / "documents" / "processed")
)

# ── Vector index ──────────────────────────────────────────────────────────────
VECTOR_INDEX_DIR: Path = Path(
    os.getenv("VECTOR_INDEX_DIR", PROJECT_ROOT / "data" / "processed" / "faiss_index")
)
BM25_INDEX_PATH: Path = Path(
    os.getenv("BM25_INDEX_PATH", PROJECT_ROOT / "data" / "processed" / "bm25_index.pkl")
)
CHUNKS_PATH: Path = Path(
    os.getenv("CHUNKS_PATH", PROJECT_ROOT / "data" / "processed" / "chunks.json")
)

# ── Embedding model ───────────────────────────────────────────────────────────
EMBED_MODEL: str = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# ── Retrieval ─────────────────────────────────────────────────────────────────
TOP_K: int = int(os.getenv("TOP_K", "6"))
CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))   # target tokens per chunk
CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "75"))

# ── LLM (NeoStats Azure OpenAI-compatible) ────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL: str = os.getenv(
    "OPENAI_BASE_URL", "https://neo-hackathon.services.ai.azure.com/openai/v1/"
)
OPENAI_DEPLOYMENT: str = os.getenv("OPENAI_DEPLOYMENT", "Neo-Hackathon")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-oss-120b")
LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "30"))
LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "2"))

# ── SQL safety ────────────────────────────────────────────────────────────────
SQL_MAX_ROWS: int = int(os.getenv("SQL_MAX_ROWS", "100"))
SQL_TIMEOUT: int = int(os.getenv("SQL_TIMEOUT", "5"))  # seconds

# ── IST timezone offset (for policy version selection) ───────────────────────
IST_OFFSET_HOURS: int = 5
IST_OFFSET_MINUTES: int = 30

# ── Policy version cutoff (stored as UTC for comparison) ─────────────────────
# RW-POL-02 applies to bookings made BEFORE 2026-07-01 00:00 IST
# = 2026-06-30 18:30:00 UTC
POLICY_V2_CUTOFF_UTC: str = "2026-06-30 18:30:00"

# ── Known tables and allowed columns (for SQL validation) ────────────────────
ALLOWED_TABLES: set[str] = {
    "customers", "vehicles", "bookings", "payments", "damage_reports"
}

# Sensitive columns that must never be exposed to the customer's own LLM prompt
# (they are accessible via structured query but redacted before passing to LLM)
SENSITIVE_COLUMNS: dict[str, set[str]] = {
    "customers": {"driving_license_no", "date_of_birth"},
}
