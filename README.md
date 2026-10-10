# RideWise Assist

AI-powered conversational assistant for RideWise — NeoStats AI Engineer Intern Hackathon.

Answers questions about bookings, payments, damage claims, and rental policies using a local SQLite database and a hybrid RAG system over five policy documents.

---

## Architecture

```
User (Streamlit UI)
    │
    ▼
Session Manager          ← identity established at login, never from chat
    │
    ▼
Workflow Router          ← keyword-based intent classification
    ├── SQL Tool         ← read-only, ownership-enforced, parameterized
    ├── RAG Tool         ← BM25 + FAISS + Reciprocal Rank Fusion
    └── Business Logic   ← deterministic Python (Decimal) calculations
         │
         ▼
    LLM (Azure OpenAI)   ← grounded response with citations
         │
         ▼
    Streamlit Response
```

**Request flow for a customer asking "What is my cancellation refund?":**
1. Session confirms `customer_id = CUST0001` from login state (not from the message).
2. Router classifies: needs_db=True, needs_policy=True, needs_calc=True.
3. SQL tool runs a JOIN query with `WHERE bookings.customer_id = 'CUST0001'` injected by the backend.
4. RAG retrieves the relevant cancellation policy passages (BM25 + FAISS hybrid).
5. Calculations module computes the exact refund amount using `Decimal`.
6. LLM generates a response citing the policy document, page, and calculation breakdown.

---

## Folder Structure

```
ridewise-assist/
├── app.py                        # Streamlit entry point
├── README.md
├── DECISIONS.md
├── requirements.txt
├── .env.example                  # Template — no secrets
├── .env                          # Actual credentials — git-ignored
├── .gitignore
├── config/
│   └── settings.py               # All config, loaded from .env
├── data/
│   ├── source/
│   │   ├── ridewise.db           # Original SQLite database
│   │   └── data_dictionary.md
│   └── processed/
│       ├── chunks.json           # Extracted document chunks
│       └── faiss_index/          # FAISS vector index
│           └── index.faiss
│       └── bm25_index.pkl        # BM25 serialized index
├── documents/
│   ├── source/                   # Original policy PDFs
│   └── processed/                # (reserved for Docling structured output)
├── docs/
│   ├── data_sensitivity_matrix.md# Field-level sensitivity & access rules
│   └── security_design.md        # Comprehensive security architecture
├── src/ridewise/
│   ├── auth/session.py           # Session state and role management
│   ├── database/
│   │   ├── city.py               # City normalization (Bengaluru, Bangalore, BLR)
│   │   ├── db.py                 # Read-only SQLite connection
│   │   ├── sanitizer.py          # Field-level masking & PII protection
│   │   ├── schema_retriever.py   # Embedding-driven schema retrieval & dynamic SQL
│   │   └── sql_tool.py           # Safe SQL execution + ownership filter
│   ├── retrieval/
│   │   ├── ingest.py             # PDF ingestion pipeline
│   │   └── hybrid.py            # BM25 + FAISS + RRF retrieval
│   ├── workflow/router.py        # Request routing and orchestration
│   ├── llm/
│   │   ├── client.py             # LLM API client
│   │   └── prompts.py            # System/user prompt templates
│   └── business_logic/
│       └── calculations.py       # Deterministic financial calculations
├── scripts/
│   ├── ingest_documents.py       # Run this first to build the index
│   └── evaluate_retrieval.py     # Retrieval quality evaluation
└── tests/
    ├── test_calculations.py      # Business logic tests (37 tests)
    ├── test_city_normalization.py# City normalization tests (4 tests)
    ├── test_schema_retriever.py  # Schema embeddings & masking tests (5 tests)
    └── test_sql_safety.py        # SQL enforcement & RLS tests (18 tests)
    └── test_end_to_end.py        # (extend as needed)
```

---

## Prerequisites

- Python 3.11+
- All packages in `requirements.txt` (most are pre-installed in the hackathon environment)
- The NeoStats API key in `.env`

---

## Installation & Setup

```bash
# 1. Go to the project directory
cd "ridewise-assist"

# 2. Install any missing dependencies
pip install -r requirements.txt

# 3. Copy the environment template and add your API key
copy .env.example .env
# Edit .env and set OPENAI_API_KEY

# 4. Ingest documents and build retrieval indexes (do this once)
python scripts/ingest_documents.py

# 5. Launch the app
streamlit run app.py
```

A new developer should be running within **5 minutes** assuming dependencies are installed and the API key is available.

---

## How to Run Locally

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. Select **Customer** or **Support Agent** from the sidebar and log in.

For customer login, use any valid `customer_id` from the database (e.g. `CUST0001`).

---

## Document Ingestion

```bash
# Normal (uses cache if already ingested)
python scripts/ingest_documents.py

# Force rebuild (clears cache)
python scripts/ingest_documents.py --force
```

The ingestion pipeline:
1. Uses **Docling** for structure-aware PDF parsing.
2. Uses **pdfplumber** for table extraction and page-by-page text.
3. Injects **pre-verified content** for the two scanned annexures (PDF-03 p.2, PDF-04 p.3).
4. Chunks content at ~500 tokens with 75-token overlap.
5. Builds a **BM25** lexical index and a **FAISS** dense vector index.

---

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Just business logic
python -m pytest tests/test_calculations.py -v

# Just SQL safety + authorization
python -m pytest tests/test_sql_safety.py -v
```

**53 tests** across SQL safety, ownership enforcement, business logic calculations (all policy thresholds, boundary values, GST, grace periods).

---

## Retrieval Evaluation

```bash
python scripts/evaluate_retrieval.py
```

Runs 23 ground-truth questions and reports **Recall@1**, **Recall@3**, **Recall@5**, **MRR**, and **keyword recall**.

---

## Supported Use Cases

### Customer session
- View own bookings, payment history, damage claims
- Calculate expected cancellation refunds (policy v1 and v2)
- Ask about late return fees, EV battery penalties
- Get policy guidance with document citations

### Support Agent session
- Cross-customer booking and payment lookups
- Policy explanation and cross-reference
- Damage claim status review

### Limitations
- The retrieval system loads indexes at startup — first load takes ~10 seconds.
- Calculation auto-trigger relies on keyword matching; ambiguous questions may not trigger it. Ask explicitly ("calculate my refund").
- The LLM response quality depends on the NeoStats API availability.
- Deposit return records are not in the database (processed by payment gateway per RW-POL-01 §5.3).

---

## Security Design

| Layer | Mechanism |
|---|---|
| Customer identity | Established at login via DB lookup; never re-read from chat messages |
| SQL access | SQLite `mode=ro` URI — OS-level read-only; no application bypass possible |
| Ownership filter | `WHERE customer_id = ?` injected by backend for every customer session query |
| Input validation | Forbidden keywords, multi-statement, and comment detection before execution |
| Prompt injection | Retrieved content tagged `[POLICY SOURCE]`/`[DB RESULT]`; system prompt instructs LLM to ignore commands in these tags |
| Credential safety | API key in `.env` only; never logged, printed, or committed |
| LLM boundary | LLM never the sole security check; all authorization enforced in Python |

---

## Troubleshooting

**"Policy index not loaded"** — Run `python scripts/ingest_documents.py` from inside `ridewise-assist/`.

**"OPENAI_API_KEY is not set"** — Check that `.env` exists and contains `OPENAI_API_KEY=...`.

**Slow first response** — The embedding model loads on first retrieval. Subsequent queries are fast.

**Customer ID not found** — IDs are in the format `CUST0001` through `CUST0200`.
