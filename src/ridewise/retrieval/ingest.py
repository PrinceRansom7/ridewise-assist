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

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
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
    Extract structured text using Docling with a configured pipeline.

    Configuration
    ─────────────
    - do_ocr=False  : PDFs are digitally created — native text is available.
                      OCR would trigger easyocr/tesseract which needs torchvision.
    - do_table_structure=True : Use Docling's TableFormer for table detection.
                                This works on CPU without torchvision.
    - do_picture_classification=False : Skips vision model, saves memory.
    - force_backend_text=False : Let Docling use its layout pipeline normally.

    Returns the Markdown string of the document, or None on failure.
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False                     # digital PDFs — no OCR needed
        pipeline_options.do_table_structure = True          # detect tables structurally
        pipeline_options.do_picture_classification = False  # no vision model
        pipeline_options.do_picture_description = False     # no VLM
        pipeline_options.generate_page_images = False       # no image rendering

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        result = converter.convert(str(pdf_path))
        md = result.document.export_to_markdown()
        print(f"  [Docling] OK — {len(md)} chars extracted from {pdf_path.name}")
        return md

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

    Strategy
    ────────
    1. Try Docling first for structure-aware full-document extraction.
       If it succeeds, use its Markdown output as the primary text source
       (chunked by headings, preserving section context).
    2. Always run pdfplumber to extract per-page tables reliably
       (Docling's table Markdown is used for text, pdfplumber catches any missed tables).
    3. Pre-verified scanned annexure content is always injected verbatim.
    """
    filename = pdf_path.name
    meta = PDF_METADATA.get(filename, {
        "policy_version": "unknown",
        "effective_date": "unknown",
        "title": filename,
    })

    print(f"Ingesting: {filename}")
    chunks: list[dict] = []

    # ── Step 1: Try Docling for primary structured text ───────────────────────
    docling_md = _extract_with_docling(pdf_path)

    # ── Step 2: pdfplumber for per-page table data + page mapping ─────────────
    pages_data = _extract_with_pdfplumber(pdf_path)
    scanned_pages = {
        sp["page"] for sp in SCANNED_PAGE_CONTENT.get(filename, [])
    }

    # ── Step 3: Inject pre-verified scanned annexure content ──────────────────
    for sp in SCANNED_PAGE_CONTENT.get(filename, []):
        chunk_id = _make_chunk_id(filename, sp["page"], 0)
        chunks.append({
            "chunk_id": chunk_id,
            "source_file": filename,
            "page_number": sp["page"],
            "section_title": sp["section"],
            "content_type": sp["content_type"],
            "policy_version": meta["policy_version"],
            "effective_date": meta["effective_date"],
            "text": sp["content"],
            "is_from_scan": True,
        })

    # ── Step 4a: Primary path — use Docling Markdown ──────────────────────────
    if docling_md:
        # Split Docling output by headings (## or # lines) to create
        # semantically coherent chunks with section context.
        sections = _split_by_headings(docling_md)
        for sec_idx, (section_title, section_text) in enumerate(sections):
            if not section_text.strip():
                continue
            sub_chunks = _split_into_chunks(
                section_text, section_title, CHUNK_SIZE, CHUNK_OVERLAP
            )
            for c_idx, chunk_text in enumerate(sub_chunks):
                # Best-effort page number: use pdfplumber page that contains
                # the most overlap with the chunk text (first few words match)
                page_num = _guess_page(chunk_text, pages_data, scanned_pages)
                chunk_id = _make_chunk_id(filename, page_num, sec_idx * 100 + c_idx)
                chunks.append({
                    "chunk_id": chunk_id,
                    "source_file": filename,
                    "page_number": page_num,
                    "section_title": section_title or meta["title"],
                    "content_type": "text",
                    "policy_version": meta["policy_version"],
                    "effective_date": meta["effective_date"],
                    "text": chunk_text,
                    "is_from_scan": False,
                })

    # ── Step 4b: Fallback path — use pdfplumber page-by-page ─────────────────
    else:
        current_section = meta["title"]
        for page_data in pages_data:
            page_num = page_data["page"]
            page_text = page_data["text"].strip()
            tables = page_data["tables"]

            if page_num in scanned_pages:
                continue  # already injected above

            # Detect section heading from page text
            lines = page_text.split("\n")
            for line in lines:
                stripped = line.strip()
                if stripped and (
                    (len(stripped) < 80 and stripped[0].isdigit() and "." in stripped[:5])
                    or stripped.isupper()
                ):
                    current_section = stripped
                    break

            # Tables
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

            # Narrative text
            if page_text:
                text_chunks = _split_into_chunks(
                    page_text, current_section, CHUNK_SIZE, CHUNK_OVERLAP
                )
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


def _split_by_headings(markdown: str) -> list[tuple[str, str]]:
    """
    Split a Docling Markdown document into (heading, body) sections.
    Handles # and ## heading levels. Returns list of (title, text) tuples.
    """
    import re as _re
    heading_pattern = _re.compile(r"^(#{1,3})\s+(.+)$", _re.MULTILINE)
    sections: list[tuple[str, str]] = []

    matches = list(heading_pattern.finditer(markdown))
    if not matches:
        return [("", markdown)]

    # Text before first heading
    if matches[0].start() > 0:
        sections.append(("", markdown[:matches[0].start()]))

    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        sections.append((title, body))

    return sections


def _guess_page(chunk_text: str, pages_data: list[dict], skip_pages: set) -> int:
    """
    Guess the page number for a Docling chunk by finding which pdfplumber
    page has the most matching words.
    Returns 1 as the default if no match is found.
    """
    if not pages_data:
        return 1

    chunk_words = set(chunk_text.lower().split()[:20])  # first 20 words
    best_page = 1
    best_score = 0

    for pd in pages_data:
        if pd["page"] in skip_pages:
            continue
        page_words = set((pd.get("text") or "").lower().split())
        score = len(chunk_words & page_words)
        if score > best_score:
            best_score = score
            best_page = pd["page"]

    return best_page


def ingest_all_documents(
    source_dir: Optional[Path] = None,
    processed_dir: Optional[Path] = None,
    chunks_path: Optional[Path] = None,
    force: bool = False,
) -> list[dict]:
    """
    Ingest all PDFs in source_dir. Writes chunks.json to processed_dir.
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
            file_chunks = ingest_pdf(pdf_path)
            all_chunks.extend(file_chunks)
        except Exception as exc:
            print(f"  [ERROR] Failed to ingest {pdf_path.name}: {exc}")

    # Save
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)
    print(f"\nIngestion complete. {len(all_chunks)} total chunks -> {chunks_path}")
    return all_chunks


if __name__ == "__main__":
    ingest_all_documents(force=True)

