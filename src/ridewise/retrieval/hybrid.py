"""
Hybrid retrieval: BM25 (lexical) + FAISS (dense vector) with Reciprocal Rank Fusion.

Architecture
────────────
- BM25 catches exact keyword and number matches (crucial for policy thresholds).
- FAISS + MiniLM-L6-v2 catches semantic similarity and paraphrased questions.
- RRF merges ranked lists from both retrievers without needing score calibration.
- Both indexes are built from the same chunk list produced by ingest.py.
- Indexes are persisted to disk and loaded on startup (no re-embedding on restart).
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config.settings import (
    BM25_INDEX_PATH,
    CHUNKS_PATH,
    EMBED_MODEL,
    TOP_K,
    VECTOR_INDEX_DIR,
)

# ── Lazy-loaded globals ───────────────────────────────────────────────────────
_chunks: Optional[list[dict]] = None
_bm25 = None  # rank_bm25.BM25Okapi
_faiss_index = None  # faiss.IndexFlatIP
_embedder = None  # SentenceTransformer
_embed_dim: int = 0


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + lower-case tokenizer for BM25."""
    import re
    return re.findall(r"[a-zA-Z0-9₹%]+", text.lower())


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        print(f"Loading embedding model: {EMBED_MODEL}")
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder


def _embed(texts: list[str]) -> np.ndarray:
    """Return L2-normalized embeddings for a list of texts."""
    embedder = _get_embedder()
    vecs = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.array(vecs, dtype=np.float32)


def build_indexes(chunks: list[dict], force: bool = False) -> None:
    """
    Build and persist BM25 and FAISS indexes from a list of chunks.

    Parameters
    ----------
    chunks: List of chunk dicts (must have a 'text' field).
    force:  Rebuild even if persisted indexes exist.
    """
    global _chunks, _bm25, _faiss_index, _embed_dim

    VECTOR_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    BM25_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

    faiss_path = VECTOR_INDEX_DIR / "index.faiss"
    needs_build = force or not faiss_path.exists() or not BM25_INDEX_PATH.exists()

    if not needs_build:
        print("Indexes already exist — skipping rebuild. Use force=True to rebuild.")
        _chunks = chunks
        _load_indexes()
        return

    print(f"Building indexes for {len(chunks)} chunks...")
    texts = [c["text"] for c in chunks]

    # ── BM25 ──────────────────────────────────────────────────────────────────
    from rank_bm25 import BM25Okapi
    tokenized = [_tokenize(t) for t in texts]
    _bm25 = BM25Okapi(tokenized)
    with open(BM25_INDEX_PATH, "wb") as f:
        pickle.dump({"bm25": _bm25, "tokenized": tokenized}, f)
    print(f"  BM25 index saved → {BM25_INDEX_PATH}")

    # ── FAISS ─────────────────────────────────────────────────────────────────
    import faiss
    vecs = _embed(texts)
    _embed_dim = vecs.shape[1]
    index = faiss.IndexFlatIP(vecs.shape[1])  # Inner product on normalized vecs = cosine
    index.add(vecs)
    faiss.write_index(index, str(faiss_path))
    _faiss_index = index
    print(f"  FAISS index saved → {faiss_path}")

    _chunks = chunks
    print("Indexes built successfully.")


def _load_indexes() -> None:
    """Load persisted indexes from disk."""
    global _bm25, _faiss_index, _embed_dim

    if _bm25 is None:
        with open(BM25_INDEX_PATH, "rb") as f:
            data = pickle.load(f)
        _bm25 = data["bm25"]

    if _faiss_index is None:
        import faiss
        faiss_path = VECTOR_INDEX_DIR / "index.faiss"
        _faiss_index = faiss.read_index(str(faiss_path))
        _embed_dim = _faiss_index.d


def load_retrieval_system(force_rebuild: bool = False) -> None:
    """
    Load chunks and indexes into memory.
    Call this once at application startup.
    """
    global _chunks

    # Load chunks
    if not CHUNKS_PATH.exists():
        raise FileNotFoundError(
            f"Chunks file not found at {CHUNKS_PATH}. "
            "Run `python scripts/ingest_documents.py` first."
        )
    with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
        _chunks = json.load(f)
    print(f"Loaded {len(_chunks)} chunks from {CHUNKS_PATH}")

    faiss_path = VECTOR_INDEX_DIR / "index.faiss"
    if force_rebuild or not faiss_path.exists() or not BM25_INDEX_PATH.exists():
        build_indexes(_chunks, force=True)
    else:
        _load_indexes()
        print(f"Indexes loaded (BM25 + FAISS dim={_faiss_index.d if _faiss_index else '?'})")


def _rrf_merge(
    bm25_ranked: list[int],
    faiss_ranked: list[int],
    k: int = 60,
) -> list[tuple[int, float]]:
    """
    Reciprocal Rank Fusion.

    Parameters
    ----------
    bm25_ranked, faiss_ranked: Lists of chunk indices ordered by score.
    k: RRF constant (standard value 60).

    Returns
    -------
    List of (chunk_index, rrf_score) sorted descending by score.
    """
    scores: dict[int, float] = {}
    for rank, idx in enumerate(bm25_ranked, start=1):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    for rank, idx in enumerate(faiss_ranked, start=1):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def is_ready() -> bool:
    """Return True if chunks, BM25, and FAISS index are loaded and ready."""
    return _chunks is not None and _bm25 is not None and _faiss_index is not None


def retrieve(query: str, top_k: Optional[int] = None) -> list[dict]:
    """
    Retrieve the most relevant chunks for a query using hybrid RRF.

    Returns a list of chunk dicts with an added ``score`` field.
    """
    if not is_ready():
        load_retrieval_system()

    top_k = top_k or TOP_K
    n_candidates = min(len(_chunks), max(top_k * 5, 30))

    # ── BM25 retrieval ────────────────────────────────────────────────────────
    query_tokens = _tokenize(query)
    bm25_scores = _bm25.get_scores(query_tokens)
    bm25_ranked = np.argsort(bm25_scores)[::-1][:n_candidates].tolist()

    # ── FAISS retrieval ───────────────────────────────────────────────────────
    q_vec = _embed([query])
    _, faiss_idxs = _faiss_index.search(q_vec, n_candidates)
    faiss_ranked = faiss_idxs[0].tolist()

    # ── RRF merge ─────────────────────────────────────────────────────────────
    merged = _rrf_merge(bm25_ranked, faiss_ranked)

    results = []
    for idx, score in merged[:top_k]:
        chunk = dict(_chunks[idx])
        chunk["score"] = score
        results.append(chunk)

    return results


def format_chunks_for_llm(chunks: list[dict]) -> str:
    """
    Format retrieved chunks as clearly labelled context for the LLM prompt.
    Each chunk is wrapped with [POLICY SOURCE] tags to separate it from
    user instructions (prompt-injection hardening).
    """
    if not chunks:
        return "[POLICY SOURCE] No relevant policy passages found."

    parts = []
    for i, chunk in enumerate(chunks, start=1):
        header = (
            f"[POLICY SOURCE {i}] "
            f"{chunk['source_file']} | "
            f"p.{chunk['page_number']} | "
            f"{chunk['policy_version']} | "
            f"Section: {chunk['section_title']}"
        )
        parts.append(f"{header}\n{chunk['text']}")

    return "\n\n---\n\n".join(parts)
