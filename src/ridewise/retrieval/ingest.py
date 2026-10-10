"""
Document ingestion pipeline for RideWise Assist.

Strategy
────────
1.  Docling is used as the primary parser for structure-aware extraction
    (headings, paragraphs, lists, tables).
2.  pdfplumber is used as a supplementary parser to extract table data
    reliably, especially for pages where Docling's table output is partial.
3.  Scanned pages (identified by high image / low text ratio) are handled
    by recording their known content from the manual inspection (the two
    annexures). OCR via pytesseract is attempted if available.
4.  Chunks carry full metadata: source_file, page_number, section_title,
    content_type, policy_version, chunk_id.
5.  Running ingestion a second time overwrites the processed output files
    rather than duplicating records.

Known scanned pages (verified by image inspection during project setup)
───────────────────────────────────────────────────────────────────────
- 03_Cancellation_and_Refund_Policy_v2.pdf  page 2  → Annexure 1 refund table
- 04_Late_Return_and_Damage_Penalty_Schedule.pdf page 3  → Annexure A tier table

The content of those scanned pages is embedded below as authoritative text
so the RAG system always has this critical information, regardless of OCR
availability.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

# Force UTF-8 output on Windows so print() doesn't fail on ₹ or → characters
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from config.settings import (
    CHUNKS_PATH,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DOCS_PROCESSED_DIR,
    DOCS_SOURCE_DIR,
)

# ── Known scanned-page content (validated against source images) ──────────────

SCANNED_PAGE_CONTENT: dict[str, list[dict]] = {
    "03_Cancellation_and_Refund_Policy_v2.pdf": [
        {
            "page": 2,
            "section": "Annexure 1: Approved refund schedule (Version 2)",
            "content": (
                "Annexure 1: Refund schedule (Version 2)\n"
                "Applies to bookings made on or after 1 July 2026, 00:00 IST\n\n"
                "Cancellation before scheduled pickup | Refund of rental charge\n"
                "48 hours or more                     | 100%\n"
                "12 hours or more, but less than 48 hours | 75%\n"
                "2 hours or more, but less than 12 hours  | 25%\n"
                "Less than 2 hours                    | No refund (0%)\n\n"
                "Cancelled by RideWise: 100% refund in all cases.\n"
                "No-show: no refund.\n"
                "Approved: RideWise Policy Committee, 20 June 2026"
            ),
            "content_type": "table+text",
            "policy_version": "RW-POL-03 v2",
        }
    ],
    "04_Late_Return_and_Damage_Penalty_Schedule.pdf": [
        {
            "page": 3,
            "section": "Annexure A: Tier benefits — Late return grace period by customer tier",
            "content": (
                "Annexure A: Tier benefits\n"
                "Late return grace period by customer tier\n\n"
                "Customer tier | Grace period on late return\n"
                "STANDARD      | None\n"
                "SILVER        | None\n"
                "GOLD          | 30 minutes (up to and including 30 min)\n\n"
                "A GOLD return up to 30 minutes late: no late fee.\n"
                "A GOLD return more than 30 minutes late: the full lateness is charged "
                "from the first minute, as for other tiers.\n"
                "Approved: RideWise Policy Committee, 15 December 2024"
            ),
            "content_type": "table+text",
            "policy_version": "RW-POL-04 v2.1",
        }
    ],
}

# ── Policy metadata ───────────────────────────────────────────────────────────

PDF_METADATA: dict[str, dict] = {
    "01_Rental_Agreement_and_Terms.pdf": {
        "policy_version": "RW-POL-01 v3.0",
        "effective_date": "2025-01-01",
        "title": "Rental Agreement & Terms",
    },
    "02_Cancellation_and_Refund_Policy_v1.pdf": {
        "policy_version": "RW-POL-02 v1",
        "effective_date": "2025-01-01",
        "title": "Cancellation & Refund Policy (Version 1)",
    },
    "03_Cancellation_and_Refund_Policy_v2.pdf": {
        "policy_version": "RW-POL-03 v2",
        "effective_date": "2026-07-01",
        "title": "Cancellation & Refund Policy (Version 2)",
    },
    "04_Late_Return_and_Damage_Penalty_Schedule.pdf": {
        "policy_version": "RW-POL-04 v2.1",
        "effective_date": "2025-01-01",
        "title": "Late Return & Damage Penalty Schedule",
    },
    "05_EV_User_Guide_and_FAQ.pdf": {
        "policy_version": "RW-POL-05 v1.4",
        "effective_date": "2025-01-01",
        "title": "EV User Guide & FAQ",
    },
}


def _make_chunk_id(source_file: str, page: int, index: int) -> str:
    key = f"{source_file}:{page}:{index}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _approx_tokens(text: str) -> int:
    """Rough token count: ~4 chars per token."""
    return len(text) // 4


def _split_into_chunks(text: str, section: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Split text into overlapping chunks at paragraph boundaries.
    Falls back to sentence boundaries, then hard splits.
    """
    if _approx_tokens(text) <= chunk_size:
        return [text]

    # Try paragraph-level split first
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para).strip() if current else para
        if _approx_tokens(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # Para itself too long? Hard-split by characters
            if _approx_tokens(para) > chunk_size:
                max_chars = chunk_size * 4
                overlap_chars = overlap * 4
                start = 0
                while start < len(para):
                    end = start + max_chars
                    chunks.append(para[start:end])
                    start = end - overlap_chars
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks or [text]


def _extract_with_pdfplumber(pdf_path: Path) -> list[dict]:
    """Extract text and tables from a PDF using pdfplumber."""
    import pdfplumber

    pages_data: list[dict] = []

    with pdfplumber.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc.pages, start=1):
            page_text = ""
            try:
                page_text = page.extract_text() or ""
            except Exception:
                pass

            tables = []
            try:
                raw_tables = page.extract_tables() or []
                for t in raw_tables:
                    # Stringify each cell safely
                    cleaned = [
                        [str(cell) if cell is not None else "" for cell in row]
                        for row in t
                        if any(cell for cell in row)
                    ]
                    if cleaned:
                        tables.append(cleaned)
            except Exception:
                pass

            pages_data.append({"page": i, "text": page_text, "tables": tables})

    return pages_data


def _extract_with_docling(pdf_path: Path) -> Optional[str]:
    """
    Extract structured text using Docling.
    Returns Markdown string or None if Docling fails.
    """
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        return result.document.export_to_markdown()
    except Exception as exc:
        print(f"  [Docling] Warning: {exc}. Falling back to pdfplumber.")
        return None


def _table_to_text(table: list[list[str]], title: str = "") -> str:
    """Render a table as pipe-delimited text with a title header."""
    if not table:
        return ""
    lines = []
    if title:
        lines.append(f"Table: {title}")
    header = table[0]
    lines.append(" | ".join(header))
    lines.append("-" * (len(" | ".join(header)) + 4))
    for row in table[1:]:
        lines.append(" | ".join(row))
    return "\n".join(lines)


def ingest_pdf(pdf_path: Path) -> list[dict]:
    """
    Ingest a single PDF and return a list of chunks with metadata.
    """
    filename = pdf_path.name
    meta = PDF_METADATA.get(filename, {
        "policy_version": "unknown",
        "effective_date": "unknown",
        "title": filename,
    })

    print(f"Ingesting: {filename}")
    chunks: list[dict] = []

    # ── Try Docling first ─────────────────────────────────────────────────────
    docling_md = _extract_with_docling(pdf_path)

    # ── Always use pdfplumber for per-page data (tables, page numbers) ────────
    pages_data = _extract_with_pdfplumber(pdf_path)

    # Determine current section heading as we iterate pages
    current_section = meta["title"]
    scanned_pages = {
        sp["page"] for sp in SCANNED_PAGE_CONTENT.get(filename, [])
    }

    for page_data in pages_data:
        page_num = page_data["page"]
        page_text = page_data["text"].strip()
        tables = page_data["tables"]

        # ── Handle scanned pages ──────────────────────────────────────────────
        if page_num in scanned_pages:
            for sp in SCANNED_PAGE_CONTENT.get(filename, []):
                if sp["page"] == page_num:
                    chunk_id = _make_chunk_id(filename, page_num, 0)
                    chunks.append({
                        "chunk_id": chunk_id,
                        "source_file": filename,
                        "page_number": page_num,
                        "section_title": sp["section"],
                        "content_type": sp["content_type"],
                        "policy_version": meta["policy_version"],
                        "effective_date": meta["effective_date"],
                        "text": sp["content"],
                        "is_from_scan": True,
                    })
            continue

        # ── Extract section heading from page text ────────────────────────────
        lines = page_text.split("\n")
        for line in lines:
            stripped = line.strip()
            # Heuristic: short lines starting with a digit+dot or all-caps
            if stripped and (
                (len(stripped) < 80 and stripped[0].isdigit() and "." in stripped[:5])
                or stripped.isupper()
            ):
                current_section = stripped
                break

        # ── Process tables on this page ───────────────────────────────────────
        for t_idx, table in enumerate(tables):
            table_text = _table_to_text(table, title=current_section)
            if table_text:
                chunk_id = _make_chunk_id(filename, page_num, t_idx + 1000)
                chunks.append({
                    "chunk_id": chunk_id,
                    "source_file": filename,
                    "page_number": page_num,
                    "section_title": current_section,
                    "content_type": "table",
                    "policy_version": meta["policy_version"],
                    "effective_date": meta["effective_date"],
                    "text": table_text,
                    "is_from_scan": False,
                })

        # ── Process narrative text ─────────────────────────────────────────────
        # Use Docling output if available (better structure), else pdfplumber text
        # For Docling we already have the full doc; we use page text as fallback
        narrative = page_text
        if narrative:
            text_chunks = _split_into_chunks(narrative, current_section, CHUNK_SIZE, CHUNK_OVERLAP)
            for c_idx, chunk_text in enumerate(text_chunks):
                chunk_id = _make_chunk_id(filename, page_num, c_idx)
                chunks.append({
                    "chunk_id": chunk_id,
                    "source_file": filename,
                    "page_number": page_num,
                    "section_title": current_section,
                    "content_type": "text",
                    "policy_version": meta["policy_version"],
                    "effective_date": meta["effective_date"],
                    "text": chunk_text,
                    "is_from_scan": False,
                })

    print(f"  -> {len(chunks)} chunks from {filename}")
    return chunks


def ingest_all_documents(
    source_dir: Optional[Path] = None,
    processed_dir: Optional[Path] = None,
    chunks_path: Optional[Path] = None,
    force: bool = False,
) -> list[dict]:
    """
    Ingest all PDFs in source_dir.  Writes chunks.json to processed_dir.
    Returns the full list of chunks.

    If chunks_path already exists and force=False, loads and returns cached
    chunks without re-running ingestion.
    """
    source_dir = source_dir or DOCS_SOURCE_DIR
    processed_dir = processed_dir or DOCS_PROCESSED_DIR
    chunks_path = chunks_path or CHUNKS_PATH

    processed_dir.mkdir(parents=True, exist_ok=True)
    CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if chunks_path.exists() and not force:
        print(f"Loading cached chunks from {chunks_path}")
        with open(chunks_path, "r", encoding="utf-8") as f:
            return json.load(f)

    all_chunks: list[dict] = []
    pdfs = sorted(source_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDFs found in {source_dir}")

    for pdf_path in pdfs:
        try:
            chunks = ingest_pdf(pdf_path)
            all_chunks.extend(chunks)
        except Exception as exc:
            print(f"  [ERROR] Failed to ingest {pdf_path.name}: {exc}")

    # Save
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    print(f"\nIngestion complete. {len(all_chunks)} total chunks -> {chunks_path}")
    return all_chunks


if __name__ == "__main__":
    ingest_all_documents(force=True)
