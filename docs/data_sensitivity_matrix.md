# Data Sensitivity Classification Matrix — RideWise Assist

This document defines the field-level access control, sensitivity classification, masking rules, and disclosure policies across all five operational tables in `ridewise.db`.

---

## 1. Access Classification Framework

The system classifies data into four explicit tiers:

- **Level 1 — Customer-Facing & Public Information**: Published rental rates, public fleet attributes, active hubs, general policies, and canonical city names. Accessible to both customers and support agents.
- **Level 2 — Customer-Owned Records**: A customer's own bookings, payment records, damage reports, tier, and account status. Strictly isolated to the record owner (`session.customer_id`) or an authorized support agent.
- **Level 3 — Restricted Personal & Operational Information**: Direct PII (`phone`, `email`, `date_of_birth`, `driving_license_no`), payment gateway identifiers (`transaction_ref`), and internal free-text notes (`remarks`, `description`). Subject to mandatory masking or sanitization.
- **Level 4 — Secrets**: API keys, deployment endpoints, database credentials, environment variables, and authentication tokens. **Never disclosed under any condition.**

---

## 2. Table and Column Classification Matrix

### Table: `customers`
| Column | Type | Category | Sensitivity Level | Customer Access Rule | Support Agent Access Rule | Masking / Transformation | Permitted Uses |
|---|---|---|---|---|---|---|---|
| `customer_id` | TEXT (PK) | Identity | Level 2 | Self only | Full access | Plaintext | Account identification, join key |
| `full_name` | TEXT | PII | Level 2 | Self only | Full access | Plaintext | Greeting, account verification |
| `phone` | TEXT | Restricted PII | Level 3 | Self only | Masked by default | Masked (`******2345`) | Verification, masked support |
| `email` | TEXT | Restricted PII | Level 3 | Self only | Masked by default | Masked (`a***@domain.com`) | Account notification |
| `city` | TEXT | Demographics | Level 1 | Self only | Full access | Normalized (`Bengaluru`) | Fleet distribution, city queries |
| `date_of_birth` | TEXT | Sensitive PII | Level 3 | Omit | Omit | `[REDACTED: PII]` | Internal age verification only |
| `driving_license_no` | TEXT | Highly Sensitive | Level 3 | Omit / Masked | Masked (`KA01******2345`) | Masked | KYC validation |
| `kyc_status` | TEXT | Operational | Level 2 | Self only | Full access | Plaintext (`VERIFIED`, etc.) | Rental eligibility checking |
| `customer_tier` | TEXT | Loyalty | Level 1/2 | Self only | Full access | Plaintext (`GOLD`, etc.) | Policy grace period & pricing |
| `created_at` | TEXT | Operational | Level 2 | Self only | Full access | Plaintext | Account tenure analysis |

---

### Table: `vehicles`
| Column | Type | Category | Sensitivity Level | Customer Access Rule | Support Agent Access Rule | Masking / Transformation | Permitted Uses |
|---|---|---|---|---|---|---|---|
| `vehicle_id` | TEXT (PK) | Fleet ID | Level 1 | Self bookings | Full access | Plaintext | Fleet tracking, booking join |
| `registration_no` | TEXT | Fleet ID | Level 1 | Self bookings | Full access | Plaintext | Vehicle identification |
| `vehicle_type` | TEXT | Fleet Spec | Level 1 | Public | Full access | Plaintext | Filter bike vs scooter vs EV |
| `model` | TEXT | Fleet Spec | Level 1 | Public | Full access | Plaintext | Model recommendations |
| `hub_name` | TEXT | Location | Level 1 | Public | Full access | Plaintext | Pickup / return location |
| `hourly_rate` | REAL | Pricing | Level 1 | Public | Full access | Plaintext (₹) | Trip rental & late fee calculation |
| `daily_rate` | REAL | Pricing | Level 1 | Public | Full access | Plaintext (₹) | Long-term rental calculation |
| `battery_capacity_kwh`| REAL | Fleet Spec | Level 1 | Public | Full access | Plaintext | EV specification |
| `status` | TEXT | Operational | Level 1/3 | Available only | Full access | Plaintext (`ACTIVE`, `MAINTENANCE`, `RETIRED`) | Fleet maintenance operations |
| `commissioned_on` | TEXT | Operational | Level 3 | Omit | Full access | Plaintext | Fleet lifecycle analysis |

---

### Table: `bookings`
| Column | Type | Category | Sensitivity Level | Customer Access Rule | Support Agent Access Rule | Masking / Transformation | Permitted Uses |
|---|---|---|---|---|---|---|---|
| `booking_id` | TEXT (PK) | Transaction | Level 2 | Self only | Full access | Plaintext (`B-0001`) | Booking reference, joining |
| `customer_id` | TEXT (FK) | Identity | Level 2 | Self only | Full access | Plaintext | Ownership enforcement key |
| `vehicle_id` | TEXT (FK) | Fleet Ref | Level 2 | Self only | Full access | Plaintext | Vehicle lookup |
| `booked_at` | TEXT | Temporal | Level 2 | Self only | Full access | Plaintext | Policy v1 vs v2 cutoff logic |
| `scheduled_start` | TEXT | Temporal | Level 2 | Self only | Full access | Plaintext | Pickup schedule |
| `scheduled_end` | TEXT | Temporal | Level 2 | Self only | Full access | Plaintext | Due date & late fee calculation |
| `actual_return_at`| TEXT | Temporal | Level 2 | Self only | Full access | Plaintext | Actual return & lateness |
| `cancelled_at` | TEXT | Temporal | Level 2 | Self only | Full access | Plaintext | Cancellation refund calculation |
| `booking_status` | TEXT | Status | Level 2 | Self only | Full access | Plaintext (`UPC`, `ONG`, `CMP`, etc.) | State evaluation |
| `return_battery_pct` | INTEGER | Operational | Level 2 | Self only | Full access | Plaintext | EV penalty calculation (<10% = ₹150) |

---

### Table: `payments`
| Column | Type | Category | Sensitivity Level | Customer Access Rule | Support Agent Access Rule | Masking / Transformation | Permitted Uses |
|---|---|---|---|---|---|---|---|
| `payment_id` | TEXT (PK) | Transaction | Level 2 | Self only | Full access | Plaintext (`PAY00001`) | Invoice reference |
| `booking_id` | TEXT (FK) | Reference | Level 2 | Self only | Full access | Plaintext | Booking relationship |
| `payment_type` | TEXT | Financial | Level 2 | Self only | Full access | Plaintext (`BOOKING`, `DEPOSIT`, `REFUND`) | Ledger breakdown |
| `amount` | REAL | Financial | Level 2 | Self only | Full access | Plaintext (₹) | Net amount |
| `gst_amount` | REAL | Tax | Level 2 | Self only | Full access | Plaintext (₹) | 18% GST ledger |
| `payment_method` | TEXT | Financial | Level 2 | Self only | Full access | Plaintext (`UPI`, `CARD`, `CASH`) | Payment reconciliation |
| `payment_status` | TEXT | Operational | Level 2 | Self only | Full access | Plaintext (`SUCCESS`, `FAILED`, `PENDING`) | Payment verification |
| `transaction_ref`| TEXT | Gateway ID | Level 3 | Masked | Masked by default | Masked (`TXN***210`) | Bank reconciliation |
| `paid_at` | TEXT | Temporal | Level 2 | Self only | Full access | Plaintext | Transaction audit |
| `remarks` | TEXT | Free-text | Level 3 | Sanitized | Sanitized | Tag & prompt injection stripped | Failure explanation |

---

### Table: `damage_reports`
| Column | Type | Category | Sensitivity Level | Customer Access Rule | Support Agent Access Rule | Masking / Transformation | Permitted Uses |
|---|---|---|---|---|---|---|---|
| `report_id` | TEXT (PK) | Incident ID | Level 2 | Self only | Full access | Plaintext (`DMG0001`) | Claim reference |
| `booking_id` | TEXT (FK) | Reference | Level 2 | Self only | Full access | Plaintext | Booking relationship |
| `vehicle_id` | TEXT (FK) | Reference | Level 2 | Self only | Full access | Plaintext | Fleet damage tracking |
| `reported_by` | TEXT | Operational | Level 2 | Self only | Full access | Plaintext (`CUSTOMER`, `HUB_STAFF`) | Incident origin |
| `damage_category`| TEXT | Incident | Level 2 | Self only | Full access | Plaintext (`SCRATCH`, `DENT`, etc.) | Assessment categorization |
| `severity` | TEXT | Operational | Level 2 | Self only | Full access | Plaintext (`LOW`, `MEDIUM`, `HIGH`) | Severity rating |
| `description` | TEXT | Free-text | Level 3 | Self only | Full access | Sanitized (HTML/injection stripped) | Claim assessment explanation |
| `assessed_cost` | REAL | Financial | Level 2 | Self only | Full access | Plaintext (₹) | Repair estimate |
| `claim_status` | TEXT | Legal Status | Level 2 | Self only | Full access | Plaintext (`OPEN`, `CHARGED`, `WAIVED`, `DISPUTED`)| Financial liability status |
| `reported_at` | TEXT | Temporal | Level 2 | Self only | Full access | Plaintext | Incident audit timeline |

---

## 3. Implementation Enforcement

1. **Row-Level Security (RLS)**:
   - For all customer sessions, queries touching customer-owned tables (`bookings`, `payments`, `damage_reports`, `customers`) are rewritten at [`src/ridewise/database/sql_tool.py:L178`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/src/ridewise/database/sql_tool.py#L178) to inject `WHERE <alias>.customer_id = ?`.
2. **Field-Level Masking & Sanitization**:
   - Implemented in [`src/ridewise/database/sanitizer.py`](file:///c:/Users/Acer/Downloads/Neostats%20Hackathon%20AI%20Usecase/ridewise-assist/src/ridewise/database/sanitizer.py).
   - Applied automatically across all returned rows in `sql_tool.py:run_query`.
