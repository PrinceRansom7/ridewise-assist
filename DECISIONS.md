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

**Known weakness:** Keyword classification can mis-route edge cases (e.g., "I want to know about my account" triggers policy retrieval unnecessarily). This is low-risk — extra retrieval wastes tokens but does not produce wrong answers.

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

## Decision 4: Embedding-driven Schema Retrieval for Dynamic SQL Generation and Agent Visibility

**Decision:** Embed the `data_dictionary.md` tables using `sentence-transformers/all-MiniLM-L6-v2` and compare user query embeddings to retrieve relevant schema tables. Construct targeted SQL queries dynamically while enforcing customer isolation and field-level PII masking.

**Rationale:**
- Initial hardcoded SQL join anchored on `bookings` prevented support agents from viewing standalone vehicle fleet status, open damage claims, payments, and customer breakdowns.
- Embedding table definitions allows the router to dynamically identify whether a query touches `vehicles`, `damage_reports`, `payments`, `customers`, or `bookings`.
- Customer Row-Level Security (`_enforce_customer_ownership`) remains strictly enforced in Python before query execution, guaranteeing that dynamic SQL cannot leak cross-tenant data.
- Field-level masking in `sanitizer.py` ensures phone numbers, emails, driving licenses, and transaction refs are automatically protected across customer and agent workflows.

---

## Decision 5: Canonical City Normalization at Database and Application Layers

**Decision:** Implement centralized city normalization in `src/ridewise/database/city.py` and register a custom SQLite function `normalize_city()` on every database connection.

**Rationale:**
- Customer records contain inconsistent city strings (`"Bengaluru"`, `"Bangalore"`, `"BLR"`, `"Bangalore Urban"`, `"bengaluru"`).
- Without normalization, aggregations like `GROUP BY city` fractured counts across identical geographic entities.
- Registering `normalize_city` as a deterministic SQLite function allows seamless `GROUP BY normalize_city(city)` queries without modifying raw source database records.
- User queries containing city variants are dynamically resolved to the canonical `"Bengaluru"` prior to SQL filtering and prompt construction.

---

## Decision 6: Deterministic Financial Calculations via Decimal Python Engines

**Decision:** Implement refund schedules, late-return fees, and EV battery penalty calculations as pure deterministic Python functions using `decimal.Decimal` in `src/ridewise/business_logic/calculations.py`, completely separate from the LLM.

**Rationale:**
- Large Language Models frequently make subtle arithmetic errors when calculating tier grace period offsets, partial-hour late ceilings, and 18% GST additions.
- Exact financial reconciliation requires legal reproducibility:
  - Cancellation refunds: Tier grace periods (Gold: 2h, Silver: 1h) and window slabs (>24h: 100%, 6–24h: 75%, 2–6h: 25%, <2h: 0%).
  - Late return fees: Exact hourly rate ceiling calculation + 18% statutory GST.
  - EV low-battery penalties: Strict tier percentage thresholds.
- Pre-calculating results and passing them in `[CALCULATION RESULT]` ensures the LLM acts purely as a synthesizer rather than an unverified calculator.

---

## Decision 7: Defense-in-Depth Privacy & Prompt-Injection Isolation

**Decision:** Enforce isolation at every architectural boundary:
1. **Connection Sandbox:** Database connections use SQLite URI `mode=ro` (read-only). All DDL/DML tokens (`INSERT`, `UPDATE`, `DROP`, `ALTER`, `;`, `--`) are rejected prior to execution.
2. **Row-Level Security (RLS):** In customer sessions, SQL queries are dynamically parsed and injected with mandatory ownership clauses (`customer_id = 'SESSION_ID'`).
3. **Field-Level Masking:** PII fields (`phone`, `email`, `driving_license_number`, `transaction_ref`) are sanitized via regex before results enter LLM context.
4. **Tag Isolation:** Database and policy passages are strictly encapsulated within `[DB RESULT]` and `[POLICY SOURCE]` tags. The system prompt explicitly instructs the LLM to treat content within these tags as untrusted data rather than instructions.

---

## Decision 8: Self-Healing Hybrid RAG Lifecycle (Lazy Auto-Load)

**Decision:** Implement on-demand initialization (`is_ready()` and `load_retrieval_system()`) inside `retrieve()` in `src/ridewise/retrieval/hybrid.py`.

**Rationale:**
- In Streamlit's multi-threaded worker model or across hot-reloads, global memory state (`_chunks`, `_bm25`, `_faiss_index`) could unbind while `@st.cache_resource` reported initialization.
- Failing to auto-load raised silent `RuntimeError` exceptions, leading to empty `[POLICY SOURCE]` contexts in the UI.
- The self-healing check verifies that `_chunks`, `_bm25`, and `_faiss_index` are active; if any are uninitialized, it seamlessly loads persisted indexes on the fly without user disruption.

---

## Failure Cases and Known Weaknesses

| Weakness | Impact | Current Status & Mitigation |
|---|---|---|
| Keyword router mis-classifies edge cases | Low — extra retrieval wastes tokens | Intent keyword sets expanded; future: add fast 1-token LLM classifier |
| Dynamic SQL customer isolation | High if unhandled | **Resolved:** Python AST/alias-aware `_enforce_customer_ownership` dynamically injects tenant filters on all tables |
| Inconsistent city spelling in DB | Medium — broken aggregations | **Resolved:** `normalize_city()` SQLite function standardizes Bangalore/BLR into canonical `"Bengaluru"` |
| Arithmetic & GST hallucination | High — incorrect financial advice | **Resolved:** Deterministic `Decimal` business logic engine in `calculations.py` calculates fees before LLM generation |
| PII exposure in LLM prompt | High — regulatory violation | **Resolved:** Field-level masking in `sanitizer.py` redacts phone, email, driving license, and transaction refs |
| Empty policy context on worker rebind | Medium — system reports policy unavailable | **Resolved:** Self-healing auto-load in `hybrid.py:retrieve()` ensures indexes load automatically on demand |
| Scanned annexure content is hardcoded | Medium — won't update automatically | Verified static content for hackathon; future: implement Docling OCR pipeline |
| No rate limiting on user queries | Low risk in hackathon context | Add `st.session_state` rate limiter in production |
