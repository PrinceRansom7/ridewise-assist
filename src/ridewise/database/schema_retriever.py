"""
Schema retriever and embedding-driven SQL query generator for RideWise Assist.

Implements:
1. Embedding the tables and column definitions from data_dictionary.md.
2. Comparing user query embedding with data dictionary embeddings (cosine similarity).
3. Using the retrieved schema context to generate accurate, safe SQL queries for both
   customer and support agent sessions.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from config.settings import DB_PATH
from src.ridewise.auth.session import SessionRole, SessionState
from src.ridewise.database.city import extract_city_from_text, normalize_city

# ── Structured Data Dictionary Schema Chunks ──────────────────────────────────
# Derived from data/source/data_dictionary.md

TABLE_SCHEMAS: dict[str, dict[str, Any]] = {
    "vehicles": {
        "table": "vehicles",
        "description": "Two-wheel vehicle fleet records, vehicle registration, model, hub location, rental rates, EV battery capacity, and status (ACTIVE, MAINTENANCE, RETIRED).",
        "primary_key": "vehicle_id",
        "columns": [
            "vehicle_id TEXT (PK, format VEH001)",
            "registration_no TEXT (e.g. KA-01-AB-4521)",
            "vehicle_type TEXT (BIKE, SCOOTER, EV_SCOOTER, EV_BIKE)",
            "model TEXT (Make and model name, e.g. Royal Enfield, Ather 450X, Honda Activa)",
            "hub_name TEXT (Pickup hub, e.g. Koramangala, Indiranagar, Whitefield, HSR Layout)",
            "hourly_rate REAL (Rental rate in ₹ per hour)",
            "daily_rate REAL (Rental rate in ₹ per 24 hours)",
            "battery_capacity_kwh REAL (Battery capacity in kWh for EVs; NULL for petrol)",
            "status TEXT (Vehicle status: ACTIVE, MAINTENANCE, RETIRED)",
            "commissioned_on TEXT (Date vehicle joined fleet, YYYY-MM-DD)",
        ],
        "foreign_keys": [],
        "search_text": (
            "table vehicles: fleet, bike, scooter, ev, model, vehicle_id, registration, hub_name, "
            "hourly_rate, daily_rate, battery_capacity_kwh, status ACTIVE MAINTENANCE RETIRED, commissioned_on"
        ),
    },
    "bookings": {
        "table": "bookings",
        "description": "Rental trip bookings made by customers for vehicles, pickup and return times, booking status, and return battery percentage.",
        "primary_key": "booking_id",
        "columns": [
            "booking_id TEXT (PK, format B-0001)",
            "customer_id TEXT (FK -> customers.customer_id)",
            "vehicle_id TEXT (FK -> vehicles.vehicle_id)",
            "booked_at TEXT (Timestamp UTC when booking was created)",
            "scheduled_start TEXT (Planned pickup time UTC)",
            "scheduled_end TEXT (Planned return time UTC)",
            "actual_return_at TEXT (Actual return time UTC, NULL if ongoing/cancelled)",
            "cancelled_at TEXT (Cancellation time UTC, NULL if not cancelled)",
            "booking_status TEXT (Status code: UPC upcoming, ONG ongoing, CMP completed, CNL_U cancelled by user, CNL_S cancelled by RideWise, NSH no-show)",
            "return_battery_pct INTEGER (Battery % at return for EVs, NULL otherwise)",
        ],
        "foreign_keys": ["customer_id -> customers", "vehicle_id -> vehicles"],
        "search_text": (
            "table bookings: booking_id, customer_id, vehicle_id, rental trip, pickup time scheduled_start, "
            "return time scheduled_end actual_return_at, cancelled_at, booking_status UPC ONG CMP CNL_U CNL_S NSH, "
            "return_battery_pct, booked_at"
        ),
    },
    "payments": {
        "table": "payments",
        "description": "Financial payment transactions, rental charges, security deposits, late fee penalties, refunds, GST amounts, payment status and payment methods.",
        "primary_key": "payment_id",
        "columns": [
            "payment_id TEXT (PK, format PAY00001)",
            "booking_id TEXT (FK -> bookings.booking_id)",
            "payment_type TEXT (BOOKING, DEPOSIT, PENALTY, REFUND)",
            "amount REAL (Amount in ₹ excluding GST; negative for refunds)",
            "gst_amount REAL (GST tax charged in ₹, 18%)",
            "payment_method TEXT (UPI, CARD, WALLET, CASH)",
            "payment_status TEXT (Transaction status: SUCCESS, FAILED, PENDING)",
            "transaction_ref TEXT (Payment gateway reference ID)",
            "paid_at TEXT (Payment timestamp UTC)",
            "remarks TEXT (Free-text notes or failure remarks)",
        ],
        "foreign_keys": ["booking_id -> bookings"],
        "search_text": (
            "table payments: payment_id, booking_id, payment_type BOOKING DEPOSIT PENALTY REFUND, amount, "
            "gst_amount, payment_method UPI CARD WALLET CASH, payment_status SUCCESS FAILED PENDING, "
            "transaction_ref, paid_at, remarks, refund transaction, money, charges, invoice"
        ),
    },
    "damage_reports": {
        "table": "damage_reports",
        "description": "Damage incidents reported by customers or hub staff, damage categories, severity, repair cost assessments, and claim dispute statuses.",
        "primary_key": "report_id",
        "columns": [
            "report_id TEXT (PK, format DMG0001)",
            "booking_id TEXT (FK -> bookings.booking_id)",
            "vehicle_id TEXT (FK -> vehicles.vehicle_id)",
            "reported_by TEXT (CUSTOMER, HUB_STAFF)",
            "damage_category TEXT (SCRATCH, DENT, TYRE, BATTERY, LOST_KEY, OTHER)",
            "severity TEXT (Severity level: LOW, MEDIUM, HIGH)",
            "description TEXT (Free-text description of the damage incident)",
            "assessed_cost REAL (Repair cost assessed by hub in ₹)",
            "claim_status TEXT (Claim status: OPEN, CHARGED, WAIVED, DISPUTED)",
            "reported_at TEXT (Timestamp UTC when damage was reported)",
        ],
        "foreign_keys": ["booking_id -> bookings", "vehicle_id -> vehicles"],
        "search_text": (
            "table damage_reports: report_id, booking_id, vehicle_id, damage incident, scratch, dent, tyre, battery, "
            "severity LOW MEDIUM HIGH, assessed_cost, repair fee, claim_status OPEN CHARGED WAIVED DISPUTED, description"
        ),
    },
    "customers": {
        "table": "customers",
        "description": "Customer accounts, profile information, full name, phone, email, registered city, date of birth, driving license number, KYC status, and customer loyalty tier.",
        "primary_key": "customer_id",
        "columns": [
            "customer_id TEXT (PK, format CUST0001)",
            "full_name TEXT (Customer's full name)",
            "phone TEXT (10-digit mobile number)",
            "email TEXT (Email address)",
            "city TEXT (City customer registered from: Bengaluru, Bangalore, BLR, Mysuru, etc.)",
            "date_of_birth TEXT (Date of birth YYYY-MM-DD)",
            "driving_license_no TEXT (Driving license number)",
            "kyc_status TEXT (KYC status: VERIFIED, PENDING, REJECTED)",
            "customer_tier TEXT (Tier: STANDARD, SILVER, GOLD)",
            "created_at TEXT (Account registration timestamp UTC)",
        ],
        "foreign_keys": [],
        "search_text": (
            "table customers: customer_id, full_name, user profile, account, phone, email, city Bengaluru Bangalore BLR, "
            "date_of_birth, driving_license_no, kyc_status VERIFIED PENDING REJECTED, customer_tier GOLD SILVER STANDARD"
        ),
    },
}

# ── Lazy-loaded Schema Embeddings Cache ───────────────────────────────────────
_schema_table_names: list[str] = list(TABLE_SCHEMAS.keys())
_schema_embeddings: Optional[np.ndarray] = None


def _get_schema_embeddings() -> np.ndarray:
    """Compute and cache embeddings for all data dictionary tables."""
    global _schema_embeddings
    if _schema_embeddings is None:
        from src.ridewise.retrieval.hybrid import _embed
        texts = [
            f"{s['table']}. {s['description']}\nColumns: {', '.join(s['columns'])}\n{s['search_text']}"
            for s in TABLE_SCHEMAS.values()
        ]
        _schema_embeddings = _embed(texts)
    return _schema_embeddings


def retrieve_relevant_tables(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """
    Compare query embedding against data_dictionary table embeddings.
    Returns the top_k most relevant table schemas ranked by cosine similarity.
    """
    from src.ridewise.retrieval.hybrid import _embed

    query_vec = _embed([query])[0]  # shape (dim,)
    table_vecs = _get_schema_embeddings()  # shape (N, dim)

    # Dot product gives cosine similarity since vectors are L2-normalized
    scores = np.dot(table_vecs, query_vec)
    ranked_indices = np.argsort(-scores)[:top_k]

    results = []
    for idx in ranked_indices:
        t_name = _schema_table_names[idx]
        schema = TABLE_SCHEMAS[t_name].copy()
        schema["similarity_score"] = float(scores[idx])
        results.append(schema)

    return results


def format_schema_for_prompt(schemas: list[dict[str, Any]]) -> str:
    """Format retrieved table schemas into prompt-friendly markdown."""
    lines = ["Available Database Schema:"]
    for s in schemas:
        lines.append(f"\nTable: {s['table']}")
        lines.append(f"Description: {s['description']}")
        lines.append("Columns:")
        for col in s["columns"]:
            lines.append(f"  - {col}")
        if s["foreign_keys"]:
            lines.append(f"Foreign Keys: {', '.join(s['foreign_keys'])}")
    return "\n".join(lines)


# ── Dynamic SQL Query Generator ───────────────────────────────────────────────

_SQL_PROMPT_TEMPLATE = """
You are a precision SQLite SQL generator for the RideWise application.
Given the user's question, role, and the relevant schema from the RideWise data dictionary, generate a single, safe, read-only SQLite SELECT query.

{schema_context}

RULES:
1. Generate ONLY ONE valid SQLite SELECT query.
2. Output ONLY the raw SQL code. No markdown fences (```sql), no explanations, no comments.
3. Only use tables and columns defined in the schema above.
4. For string matching in city names, use normalize_city(city) or normalize_city(c.city).
5. If session role is 'customer', you MUST include customer_id = '{customer_id}' or join on customer_id.
6. If session role is 'agent', generate appropriate queries across operational tables (vehicles, damage_reports, payments, bookings, customers).
7. Default ORDER BY to recent timestamps where applicable, and limit rows to 50.
"""


def generate_sql_from_embeddings(user_msg: str, session: SessionState) -> Optional[str]:
    """
    1. Retrieve relevant table schemas using embedding comparison with data_dictionary.
    2. Construct a tailored SQL query grounded in the retrieved schema.
    """
    from src.ridewise.llm.client import chat

    # 1. Retrieve top relevant tables using embedding comparison
    relevant_tables = retrieve_relevant_tables(user_msg, top_k=3)
    schema_context = format_schema_for_prompt(relevant_tables)

    # 2. Build system instruction
    customer_id = session.customer_id or ""
    role_str = "customer" if session.is_customer else "agent"

    system_prompt = _SQL_PROMPT_TEMPLATE.format(
        schema_context=schema_context,
        customer_id=customer_id,
    )

    user_prompt = f"User Role: {role_str}\nUser Question: {user_msg}"

    try:
        raw_sql = chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=system_prompt,
            temperature=0.0,
        )

        # Clean any accidental markdown code fences
        cleaned_sql = raw_sql.strip()
        cleaned_sql = re.sub(r"^```(?:sql)?", "", cleaned_sql, flags=re.IGNORECASE).strip()
        cleaned_sql = re.sub(r"```$", "", cleaned_sql).strip()

        # Sanity check that it's a SELECT statement
        if cleaned_sql.upper().startswith("SELECT"):
            return cleaned_sql
    except Exception as exc:
        print(f"Error in dynamic SQL generation: {exc}")

    # Fallback to deterministic builder if LLM call is unavailable
    return _deterministic_fallback(user_msg, session, relevant_tables)


def _deterministic_fallback(user_msg: str, session: SessionState, matched_tables: list[dict]) -> Optional[str]:
    """Fallback query builder if dynamic LLM generation times out."""
    lower = user_msg.lower()
    top_table = matched_tables[0]["table"] if matched_tables else "bookings"

    if session.is_customer:
        cid = session.customer_id
        if top_table == "customers":
            return f"SELECT customer_id, full_name, email, phone, normalize_city(city) AS city, customer_tier, kyc_status FROM customers WHERE customer_id = '{cid}'"
        return f"""
            SELECT b.booking_id, b.booking_status, b.scheduled_start, b.scheduled_end,
                   v.model, v.hub_name, p.amount, p.payment_status, d.claim_status
            FROM bookings b
            JOIN vehicles v ON b.vehicle_id = v.vehicle_id
            LEFT JOIN payments p ON p.booking_id = b.booking_id
            LEFT JOIN damage_reports d ON d.booking_id = b.booking_id
            WHERE b.customer_id = '{cid}'
            ORDER BY b.scheduled_start DESC LIMIT 20
        """

    # Agent fallbacks
    if "vehicle" in lower or top_table == "vehicles":
        return "SELECT vehicle_id, registration_no, model, vehicle_type, hub_name, hourly_rate, status FROM vehicles ORDER BY status, vehicle_id LIMIT 50"
    if "damage" in lower or "claim" in lower or top_table == "damage_reports":
        return "SELECT report_id, booking_id, vehicle_id, damage_category, severity, assessed_cost, claim_status, reported_at FROM damage_reports ORDER BY reported_at DESC LIMIT 50"
    if "payment" in lower or "refund" in lower or top_table == "payments":
        return "SELECT payment_id, booking_id, payment_type, amount, gst_amount, payment_status, payment_method, paid_at FROM payments ORDER BY paid_at DESC LIMIT 50"
    if "customer" in lower or top_table == "customers":
        return "SELECT customer_id, full_name, email, phone, normalize_city(city) AS city, customer_tier, kyc_status FROM customers ORDER BY created_at DESC LIMIT 50"

    return """
        SELECT b.booking_id, b.customer_id, c.full_name, normalize_city(c.city) AS city,
               b.booking_status, b.scheduled_start, b.scheduled_end, v.model, v.hub_name
        FROM bookings b
        JOIN customers c ON b.customer_id = c.customer_id
        JOIN vehicles v ON b.vehicle_id = v.vehicle_id
        ORDER BY b.scheduled_start DESC LIMIT 50
    """
