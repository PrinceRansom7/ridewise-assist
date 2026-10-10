# Security Design & Authorization Architecture — RideWise Assist

This document details the security architecture, authorization mechanisms, data isolation, and defenses against prompt injection and information disclosure in RideWise Assist.

---

## 1. Authentication and Session Identity

1. **Trusted State Invariance**:
   - Identity is determined strictly at session initialization through [`src/ridewise/auth/session.py`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/src/ridewise/auth/session.py).
   - Once authenticated as a customer (e.g. `CUST0001`), the `customer_id` is stored in the server-side `SessionState`.
   - The conversational LLM has **zero ability to alter `customer_id`** or switch user contexts via prompt text.
2. **Simulated Authentication Limitation**:
   - In this evaluation/hackathon deployment, customer identity is selected at login via customer ID lookup against the SQLite database rather than OAuth/SAML.
   - For support agents, agent identification (`agent_id`) provides operational session context. In production, this must be integrated with corporate SSO and RBAC.

---

## 2. Row-Level Security (RLS) & Query Construction

1. **Dynamic Schema-Aware Generation**:
   - Queries are constructed using table definitions embedded from `data_dictionary.md` via [`src/ridewise/database/schema_retriever.py`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/src/ridewise/database/schema_retriever.py).
2. **Backend Ownership Enforcement**:
   - Every query touching customer-owned tables (`bookings`, `payments`, `damage_reports`, `customers`) during a customer session is intercepted in [`src/ridewise/database/sql_tool.py:L178`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/src/ridewise/database/sql_tool.py#L178) (`_enforce_customer_ownership`).
   - The backend forcefully injects:
     ```sql
     WHERE <alias>.customer_id = ?
     ```
     before any `GROUP BY`, `ORDER BY`, or `LIMIT` clause, binding `session.customer_id` as a parameterized value.
3. **Defense Against Prompt Injection**:
   - Even if an attacker says *"Show all customers"* or *"SELECT * FROM bookings"*, the injected WHERE filter guarantees that only the authenticated user's records are returned.
4. **Read-Only OS Enforcement**:
   - SQLite connections are opened using URI flag `mode=ro` ([`src/ridewise/database/db.py`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/src/ridewise/database/db.py#L26)). Any write attempt triggers an immediate OS-level `sqlite3.OperationalError: attempt to write a readonly database`.
5. **SQL Safety Rules**:
   - Rejects multiple statements (semicolons).
   - Rejects comment markers (`--`, `/*`) that could mask injection.
   - Restricts tables to [`ALLOWED_TABLES`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/config/settings.py#L22).
   - Caps result rows at 100 with an execution timeout of 5 seconds.

---

## 3. Support Agent Permissions & Operational View

1. **Operational Capabilities**:
   - Support agents can query across `vehicles`, `bookings`, `payments`, `damage_reports`, and `customers`.
   - Fleet managers and agents can view vehicles by status (`MAINTENANCE`, `ACTIVE`), inspect open damage claims, and analyze payment statuses.
2. **Field-Level Masking**:
   - Support agents receive masked direct PII (`phone`, `email`, `driving_license_no`, `transaction_ref`) by default to adhere to data minimization principles.

---

## 4. Field-Level Masking & Response Sanitization

Implemented in [`src/ridewise/database/sanitizer.py`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/src/ridewise/database/sanitizer.py):
- **Phone**: `******2345` (last 4 digits only).
- **Email**: `a***@domain.com` (first character + domain).
- **Driving License**: `KA01******2345` (state prefix and last 4).
- **Date of Birth**: Excluded from ordinary answers (`[REDACTED: PII]`).
- **Transaction Reference**: `TXN***210`.
- **City**: Normalized via `normalize_city()` to canonical `Bengaluru`.
- **Free-text Fields (`remarks`, `description`)**: Stripped of delimiter tags (`[POLICY SOURCE]`, `[DB RESULT]`) and HTML tags to prevent prompt injection spoofing.

---

## 5. RAG & Source-Document Boundaries

1. **Isolated Policy Corpus**:
   - Vector database contains **only** the 5 approved customer policy PDFs (`RW-POL-01` through `RW-POL-05`).
   - Operational database records, workbooks (`ridewise_data.xlsx`), internal source code, and credentials are strictly excluded from the customer RAG store.
2. **Context Framing**:
   - Policy passages are injected inside `[POLICY SOURCE]` tags.
   - Database results are injected inside `[DB RESULT]` tags.
   - The LLM system instructions explicitly state that tagged content is evidence, not operational instructions.

---

## 6. Secrets & Environment Handling

1. `.env` is strictly excluded from version control in `.gitignore`.
2. `.env.example` contains template placeholders only.
3. API credentials and connection URIs are loaded once at startup and are never logged, formatted into error messages, or passed to user chat windows.
