#!/usr/bin/env python
"""
Ingest all policy PDFs and build retrieval indexes.

Usage:
    cd ridewise-assist
    python scripts/ingest_documents.py           # incremental (skip if cached)
    python scripts/ingest_documents.py --force   # always rebuild
"""
import sys
import argparse
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ridewise.retrieval.ingest import ingest_all_documents
from src.ridewise.retrieval.hybrid import build_indexes


def main():
    parser = argparse.ArgumentParser(description="Ingest RideWise policy documents")
    parser.add_argument("--force", action="store_true", help="Force re-ingestion and index rebuild")
    args = parser.parse_args()

    print("=" * 60)
    print("RideWise Assist — Document Ingestion")
    print("=" * 60)

    print("\n[1/2] Ingesting PDF documents...")
    chunks = ingest_all_documents(force=args.force)

    print(f"\n[2/2] Building retrieval indexes ({len(chunks)} chunks)...")
    build_indexes(chunks, force=args.force)

    print("\n✅ Ingestion complete. You can now start the app with:")
    print("   streamlit run app.py")


if __name__ == "__main__":
    main()
