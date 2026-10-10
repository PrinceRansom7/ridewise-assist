# DECISIONS.md — RideWise Assist Architecture Decisions

## Decision 1: Plain Python routing instead of LangGraph

**Decision:** Use a `WorkflowRouter` class with keyword-based intent classification and conditional tool calls, not LangGraph.

**Rationale:** The routing logic for RideWise Assist is fundamentally deterministic: a question either needs the database, the policy index, a calculation, or some combination of those three. LangGraph is most valuable for multi-agent loops, parallel tool calls, or complex conditional branching that changes based on intermediate tool output. None of those patterns apply here. The plain Python router is:
- Easier to read and explain in a technical evaluation
- Faster to test (no graph state machinery to mock)
- Easier to debug when something goes wrong

**Alternatives considered:**
- LangGraph: adds ~200 lines of boilerplate and state schema definition with no runtime benefit for this use case
- ReAct agent: would allow the LLM to decide which tools to call — but that means the LLM is making authorization decisions, which violates the security requirement that the backend enforces all access control

**Known weakness:** Keyword classification can mis-route edge cases (e.g., "I want to know about my account" triggers policy retrieval unnecessarily). This is low-risk — wrong retrieval wastes tokens but does not produce wrong answers.

---

## Decision 2: all-MiniLM-L6-v2 + BM25 hybrid instead of a larger embedding model

**Decision:** Use `sentence-transformers/all-MiniLM-L6-v2` for dense embeddings, combined with BM25Okapi for lexical retrieval, merged via Reciprocal Rank Fusion.

**Rationale:** At the time of ingestion:
- System RAM: ~16 GB total, ~3 GB free
- No GPU available
- `Qwen3-Embedding-0.6B` and `bge-m3` would each require 2–4 GB to load

MiniLM-L6-v2 loads in under 5 seconds and uses ~90 MB. More importantly, the policy documents are short (5 PDFs, ~15 pages total), producing only ~200–300 chunks. At this scale, BM25 alone would likely achieve good recall; the dense model adds semantic matching for paraphrased questions. RRF merges both without needing calibrated score thresholds.

**Alternatives considered:**
- `Qwen3-Embedding-0.6B`: likely OOM on this machine during the hackathon
- `bge-m3`: supports multi-vector retrieval (ColBERT-style) but adds complexity without justification for 300 chunks
- TF-IDF only: simpler but would miss semantic paraphrasing (e.g., "charge" vs "penalty")

**What to improve:** Replace with `bge-m3` or a fine-tuned embedding model once compute is available. Evaluate with the included `evaluate_retrieval.py` script.

---

## Decision 3: Hardcoded verified content for scanned PDF annexures

**Decision:** The two scanned-image annexures (RW-POL-03 Annexure 1 and RW-POL-04 Annexure A) are stored as pre-verified text strings in `ingest.py:SCANNED_PAGE_CONTENT`, not extracted via OCR.

**Rationale:** Both annexures contain the most critical policy rules in the system:
- The v2 cancellation refund table (4 slabs: 100%/75%/25%/0%)
- The tier grace period table (GOLD=30 min, STANDARD/SILVER=None)

Getting these wrong would produce wrong financial advice. pytesseract was not installed, and installing it during a 3-hour hackathon risks breaking the environment. The images were inspected visually and the content was read directly — there is no ambiguity. The pre-verified strings are annotated with the source document, page, and approval date.

**Trade-off:** If the policy is updated and the scanned annexure changes, someone must update `SCANNED_PAGE_CONTENT` manually. This is an acceptable trade-off for a hackathon but should be replaced by proper OCR (Docling EasyOCR/Tesseract pipeline) in production.

**Alternatives considered:**
- Install pytesseract at runtime: risks env conflicts, Tesseract binary not always available on Windows
- Skip the annexures entirely: unacceptable — the refund table in v2 and the tier benefits table are critical policy content that the RAG system must be able to retrieve

---

## Failure Cases and Known Weaknesses

| Weakness | Impact | Mitigation |
|---|---|---|
| Keyword router mis-classifies edge cases | Low — extra retrieval wastes tokens | Add a lightweight LLM classifier if budget allows |
| SQL ownership filter is a best-effort heuristic | Medium — complex nested queries may not get filtered | Constrain allowed SQL patterns further; use a query builder |
| No reranker | Low — RRF provides good ordering for 300 chunks | Add a cross-encoder if evaluation shows MRR < 0.5 |
| Scanned annexure content is hardcoded | Medium — won't update automatically | Implement Docling OCR pipeline when torchvision is available |
| LLM is the final response layer | Medium — can still hallucinate if evidence is weak | Add confidence gating and return "I don't know" if no evidence meets threshold |
| No rate limiting on user queries | Low risk in hackathon context | Add st.session_state rate limiter in production |
| `booked_at` is not stored in the router's DB query | Low — affects auto-calc trigger | Pass `booked_at` from the JOIN query result |

---

## What I Would Improve Over the Next Week

1. **Replace the keyword router with a fast LLM classifier** — a single-token classification call (SQL / RAG / BOTH / GREETING) using the LLM would handle edge cases correctly and is cheap at ~50 tokens.

2. **Add a cross-encoder reranker** — evaluate the retrieval script output first; if Recall@1 is below 0.7, add `cross-encoder/ms-marco-MiniLM-L-6-v2` to rerank the top-6 candidates.

3. **Add conversation memory trimming** — the current design passes the full history in every LLM call. For long conversations, trim to the last 10 turns + system prompt.

4. **Upgrade to `bge-m3`** — with GPU or more RAM, this would improve recall on paraphrased and multilingual queries.

5. **Implement proper OCR for scanned pages** — use Docling's built-in OCR pipeline once `torchvision` is installed, and validate against the pre-verified strings.

6. **Add a Streamlit admin view** — let agents see database stats, retrieval scores, and chunk sources for each answer (improves trust and debuggability).

7. **Write end-to-end tests with a stubbed LLM** — the current tests cover calculations and SQL safety but not the full LLM response flow.
