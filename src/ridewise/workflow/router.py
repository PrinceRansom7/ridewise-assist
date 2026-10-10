"""
Workflow router for RideWise Assist.

Routes each user message through the appropriate combination of tools:
  - SQL (structured data queries)
  - RAG (policy document retrieval)
  - Business logic (deterministic calculations)
  - LLM (grounded response generation)

Design: plain Python conditional routing. No LangGraph needed for this
deterministic, single-turn workflow.

Request classification
──────────────────────
Uses keyword heuristics for speed. An LLM-based classifier could improve
recall but adds latency and cost — not justified for this use case.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from src.ridewise.auth.session import SessionRole, SessionState
from src.ridewise.database.sql_tool import format_result_for_llm, run_query
from src.ridewise.llm.client import chat
from src.ridewise.llm.prompts import build_rag_message, build_system_prompt
from src.ridewise.retrieval.hybrid import format_chunks_for_llm, retrieve

# ── Intent classification ─────────────────────────────────────────────────────

_DB_KEYWORDS = {
    "booking", "bookings", "book", "rental", "vehicle", "trip", "ride",
    "payment", "paid", "charge", "invoice", "receipt", "refund", "transaction",
    "damage", "report", "claim", "status", "history", "account", "profile",
    "wallet", "deposit", "penalty", "late fee", "cancell", "cancel",
    "scheduled", "return", "pickup", "hub", "balance",
}

_POLICY_KEYWORDS = {
    "policy", "rule", "refund", "cancell", "how much", "what is the fee",
    "what happens", "grace", "tier", "gold", "silver", "standard",
    "late", "battery", "ev", "electric", "percent", "%", "hours", "threshold",
    "kyc", "eligib", "deposit", "no-show", "noshow", "gst", "tax",
    "damage", "scratch", "claim", "waived", "charged", "disputed",
    "version", "effective", "when does", "allowed", "permitted",
}

_CALC_KEYWORDS = {
    "calculate", "how much would", "what would", "compute", "amount",
    "total", "cost", "fee for", "refund for", "penalty for",
}

_GREETING_PATTERNS = re.compile(
    r"^\s*(hi|hello|hey|good\s+\w+|thanks|thank you|bye|goodbye|ok|okay|sure|got it)\W*$",
    re.IGNORECASE,
)


def _classify(text: str) -> dict[str, bool]:
    lower = text.lower()
    words = set(re.findall(r"[a-zA-Z]+", lower))

    is_greeting = bool(_GREETING_PATTERNS.match(text))
    needs_db = bool(words & _DB_KEYWORDS) and not is_greeting
    needs_policy = bool(words & _POLICY_KEYWORDS) and not is_greeting
    needs_calc = bool(words & _CALC_KEYWORDS)

    return {
        "is_greeting": is_greeting,
        "needs_db": needs_db,
        "needs_policy": needs_policy,
        "needs_calc": needs_calc,
    }


# ── SQL query builder helpers ─────────────────────────────────────────────────

def _build_customer_sql(session: SessionState) -> Optional[str]:
    """
    Build a comprehensive SQL query for a customer's recent data.
    The ownership filter will be injected by sql_tool.run_query.
    """
    if not session.customer_id:
        return None
    return """
        SELECT
            b.booking_id, b.booking_status, b.scheduled_start, b.scheduled_end,
            b.actual_return_at, b.cancelled_at, b.return_battery_pct,
            v.model, v.vehicle_type, v.hub_name, v.hourly_rate, v.daily_rate,
            p.payment_type, p.amount, p.gst_amount, p.payment_status,
            p.payment_method, p.paid_at, p.remarks,
            d.report_id, d.damage_category, d.severity, d.assessed_cost,
            d.claim_status, d.description AS damage_description
        FROM bookings b
        JOIN vehicles v ON b.vehicle_id = v.vehicle_id
        LEFT JOIN payments p ON p.booking_id = b.booking_id
        LEFT JOIN damage_reports d ON d.booking_id = b.booking_id
        ORDER BY b.scheduled_start DESC
    """


def _build_agent_general_sql(user_msg: str) -> Optional[str]:
    """
    For agent sessions, return a broad query if the question mentions
    specific booking IDs, customer IDs, or vehicle IDs.
    """
    # Look for explicit IDs in the message
    booking_ids = re.findall(r"\bB-\d{4}\b", user_msg, re.IGNORECASE)
    customer_ids = re.findall(r"\bCUST\d{4}\b", user_msg, re.IGNORECASE)
    vehicle_ids = re.findall(r"\bVEH\d{3}\b", user_msg, re.IGNORECASE)

    conditions = []
    if booking_ids:
        ids = ", ".join(f"'{b}'" for b in booking_ids)
        conditions.append(f"b.booking_id IN ({ids})")
    if customer_ids:
        ids = ", ".join(f"'{c}'" for c in customer_ids)
        conditions.append(f"b.customer_id IN ({ids})")
    if vehicle_ids:
        ids = ", ".join(f"'{v}'" for v in vehicle_ids)
        conditions.append(f"b.vehicle_id IN ({ids})")

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    return f"""
        SELECT
            b.booking_id, b.customer_id, c.full_name, c.customer_tier,
            b.booking_status, b.scheduled_start, b.scheduled_end,
            b.actual_return_at, b.cancelled_at, b.return_battery_pct,
            v.model, v.vehicle_type, v.hub_name, v.hourly_rate,
            p.payment_type, p.amount, p.gst_amount, p.payment_status, p.paid_at,
            d.damage_category, d.severity, d.assessed_cost, d.claim_status
        FROM bookings b
        JOIN customers c ON b.customer_id = c.customer_id
        JOIN vehicles v ON b.vehicle_id = v.vehicle_id
        LEFT JOIN payments p ON p.booking_id = b.booking_id
        LEFT JOIN damage_reports d ON d.booking_id = b.booking_id
        {where_clause}
        ORDER BY b.scheduled_start DESC
    """


# ── Calculation trigger ───────────────────────────────────────────────────────

def _maybe_calculate(
    user_msg: str,
    db_rows: list[dict],
    session: SessionState,
) -> str:
    """
    Attempt deterministic calculations if the question suggests it.
    Returns a formatted calculation result string, or empty string.
    """
    if not db_rows:
        return ""

    results: list[str] = []
    lower = user_msg.lower()

    for row in db_rows:
        # ── Cancellation refund ───────────────────────────────────────────────
        if any(kw in lower for kw in ["refund", "cancel", "cancell"]):
            if (
                row.get("booking_status") in ("CNL_U", "CNL_S")
                and row.get("cancelled_at")
                and row.get("payment_type") == "BOOKING"
                and row.get("payment_status") == "SUCCESS"
                and row.get("amount") is not None
            ):
                try:
                    from src.ridewise.business_logic.calculations import (
                        calc_cancellation_refund,
                    )
                    cancelled_by = (
                        "RIDEWISE" if row.get("booking_status") == "CNL_S" else "CUSTOMER"
                    )
                    result = calc_cancellation_refund(
                        booked_at_utc=row["booked_at"] if "booked_at" in row else "",
                        scheduled_start_utc=row["scheduled_start"],
                        cancelled_at_utc=row["cancelled_at"],
                        rental_charge_paid=float(row["amount"]),
                        gst_paid=float(row.get("gst_amount") or 0),
                        cancelled_by=cancelled_by,
                    )
                    results.append(
                        f"Booking {row.get('booking_id', '?')}: {result.explanation} "
                        f"Expected refund: ₹{result.total_refund} "
                        f"(₹{result.refund_amount} + GST ₹{result.gst_refund}). "
                        f"Source: {result.source_ref}"
                    )
                    break  # one calc per conversation turn
                except Exception as exc:
                    results.append(f"Calculation error: {exc}")

        # ── Late fee ─────────────────────────────────────────────────────────
        if any(kw in lower for kw in ["late", "late fee", "overdue"]):
            if (
                row.get("booking_status") in ("CMP", "ONG")
                and row.get("actual_return_at")
                and row.get("scheduled_end")
                and row.get("hourly_rate") is not None
            ):
                try:
                    from src.ridewise.business_logic.calculations import calc_late_fee
                    tier = session.customer_tier or row.get("customer_tier") or "STANDARD"
                    result = calc_late_fee(
                        scheduled_end_utc=row["scheduled_end"],
                        actual_return_utc=row["actual_return_at"],
                        hourly_rate=float(row["hourly_rate"]),
                        customer_tier=tier,
                    )
                    if result.late_minutes > 0:
                        results.append(
                            f"Booking {row.get('booking_id', '?')}: {result.explanation} "
                            f"Source: {result.source_ref}"
                        )
                        break
                except Exception as exc:
                    results.append(f"Late fee calculation error: {exc}")

        # ── EV battery penalty ────────────────────────────────────────────────
        if any(kw in lower for kw in ["battery", "charge", "ev"]):
            if row.get("return_battery_pct") is not None:
                try:
                    from src.ridewise.business_logic.calculations import (
                        calc_ev_battery_penalty,
                    )
                    result = calc_ev_battery_penalty(int(row["return_battery_pct"]))
                    results.append(
                        f"Booking {row.get('booking_id', '?')}: {result.explanation} "
                        f"Source: {result.source_ref}"
                    )
                    break
                except Exception as exc:
                    results.append(f"Battery penalty calculation error: {exc}")

    return "\n".join(results)


# ── Main router ───────────────────────────────────────────────────────────────

def process_message(user_msg: str, session: SessionState) -> str:
    """
    Main entry point: route a user message through the appropriate tools
    and return a grounded LLM response.

    Parameters
    ----------
    user_msg: The raw user message text.
    session:  Trusted session state (established at login, not from user input).

    Returns
    -------
    The assistant's response string.
    """
    if not session.is_authenticated:
        return (
            "⚠️ You are not authenticated. Please select a session type and log in."
        )

    intent = _classify(user_msg)
    db_result_str = ""
    policy_context_str = ""
    calc_result_str = ""
    db_rows: list[dict] = []

    # ── Greeting: respond directly, no tool calls ──────────────────────────────
    if intent["is_greeting"] and not intent["needs_db"] and not intent["needs_policy"]:
        system_prompt = build_system_prompt(session)
        messages = list(session.history) + [{"role": "user", "content": user_msg}]
        return chat(messages, system_prompt=system_prompt)

    # ── SQL tool ───────────────────────────────────────────────────────────────
    if intent["needs_db"]:
        if session.is_customer:
            sql = _build_customer_sql(session)
        else:
            sql = _build_agent_general_sql(user_msg)

        if sql:
            result = run_query(sql, session=session)
            db_result_str = format_result_for_llm(result)
            db_rows = result.get("rows", [])

    # ── RAG tool ───────────────────────────────────────────────────────────────
    if intent["needs_policy"] or intent["needs_calc"]:
        try:
            chunks = retrieve(user_msg)
            policy_context_str = format_chunks_for_llm(chunks)
        except RuntimeError:
            # Retrieval system not loaded — note it but continue
            policy_context_str = (
                "[POLICY SOURCE] Policy index not loaded. "
                "Run ingest_documents.py and restart."
            )

    # ── Deterministic calculations ─────────────────────────────────────────────
    if intent["needs_calc"] and db_rows:
        calc_result_str = _maybe_calculate(user_msg, db_rows, session)

    # ── Build LLM context message ─────────────────────────────────────────────
    evidence_message = build_rag_message(
        user_question=user_msg,
        db_result=db_result_str,
        policy_context=policy_context_str,
        calc_result=calc_result_str,
    )

    system_prompt = build_system_prompt(session)
    messages = list(session.history) + [{"role": "user", "content": evidence_message}]

    response = chat(messages, system_prompt=system_prompt)
    return response
