"""
Tests for SQL safety, read-only enforcement, and ownership isolation.

Run: pytest tests/test_sql_safety.py -v
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ridewise.database.sql_tool import run_query
from src.ridewise.auth.session import SessionState, SessionRole


def customer_session(customer_id: str = "CUST0001") -> SessionState:
    s = SessionState()
    s.role = SessionRole.CUSTOMER
    s.customer_id = customer_id
    s.customer_tier = "GOLD"
    return s


def agent_session() -> SessionState:
    s = SessionState()
    s.role = SessionRole.AGENT
    s.agent_id = "TestAgent"
    return s


class TestReadOnlyEnforcement:
    def test_select_allowed(self):
        result = run_query("SELECT customer_id FROM customers LIMIT 1")
        assert result["error"] is None
        assert len(result["rows"]) == 1

    def test_insert_rejected(self):
        result = run_query("INSERT INTO customers (customer_id) VALUES ('X')")
        assert result["error"] is not None
        assert "Forbidden" in result["error"] or "Only SELECT" in result["error"]

    def test_update_rejected(self):
        result = run_query("UPDATE customers SET full_name = 'hacker' WHERE 1=1")
        assert result["error"] is not None

    def test_delete_rejected(self):
        result = run_query("DELETE FROM customers")
        assert result["error"] is not None

    def test_drop_rejected(self):
        result = run_query("DROP TABLE customers")
        assert result["error"] is not None

    def test_pragma_rejected(self):
        result = run_query("PRAGMA table_info(customers)")
        assert result["error"] is not None

    def test_multiple_statements_rejected(self):
        result = run_query("SELECT 1; SELECT 2")
        assert result["error"] is not None

    def test_sql_comment_rejected(self):
        result = run_query("SELECT * FROM customers -- comment")
        assert result["error"] is not None

    def test_unknown_table_rejected(self):
        result = run_query("SELECT * FROM secret_data")
        assert result["error"] is not None
        assert "Unknown table" in result["error"]


class TestCustomerOwnershipEnforcement:
    def test_customer_sees_only_own_bookings(self):
        session = customer_session("CUST0001")
        result = run_query(
            "SELECT booking_id, customer_id FROM bookings", session=session
        )
        assert result["error"] is None
        for row in result["rows"]:
            assert row["customer_id"] == "CUST0001", (
                f"Row belongs to {row['customer_id']}, not CUST0001"
            )

    def test_customer_cannot_see_other_customer_bookings(self):
        session = customer_session("CUST0001")
        # Even an explicit WHERE for another customer is overridden
        result = run_query(
            "SELECT booking_id, customer_id FROM bookings WHERE customer_id = 'CUST0002'",
            session=session,
        )
        # Should return 0 rows (ownership filter restricts to CUST0001)
        for row in result.get("rows", []):
            assert row["customer_id"] == "CUST0001"

    def test_agent_can_see_all_bookings(self):
        session = agent_session()
        result = run_query(
            "SELECT DISTINCT customer_id FROM bookings ORDER BY customer_id LIMIT 10",
            session=session,
        )
        assert result["error"] is None
        customer_ids = {r["customer_id"] for r in result["rows"]}
        assert len(customer_ids) > 1, "Agent should see multiple customers"

    def test_customer_payments_filtered_by_owner(self):
        """Payments must only show for the authenticated customer's bookings."""
        session = customer_session("CUST0001")
        result = run_query(
            "SELECT p.payment_id, b.customer_id FROM payments p "
            "JOIN bookings b ON p.booking_id = b.booking_id",
            session=session,
        )
        assert result["error"] is None
        for row in result["rows"]:
            assert row["customer_id"] == "CUST0001"

    def test_customer_query_with_order_by(self):
        """ORDER BY must not cause syntax error when WHERE is injected."""
        session = customer_session("CUST0001")
        result = run_query(
            "SELECT b.booking_id, b.customer_id FROM bookings b ORDER BY b.scheduled_start DESC",
            session=session,
        )
        assert result["error"] is None
        for row in result["rows"]:
            assert row["customer_id"] == "CUST0001"

    def test_customer_query_with_where_and_order_by(self):
        """WHERE + ORDER BY + LIMIT must all preserve correct SQL order."""
        session = customer_session("CUST0001")
        result = run_query(
            "SELECT b.booking_id, b.customer_id FROM bookings b WHERE b.booking_status = 'CMP' ORDER BY b.scheduled_start DESC LIMIT 5",
            session=session,
        )
        assert result["error"] is None
        for row in result["rows"]:
            assert row["customer_id"] == "CUST0001"


class TestParameterizationAndInjection:
    def test_basic_parameterized_query(self):
        result = run_query(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            params=("CUST0001",),
        )
        assert result["error"] is None
        assert result["rows"][0]["customer_id"] == "CUST0001"

    def test_sql_injection_in_param_does_not_execute(self):
        """SQL injection via parameter should be caught by parameterization."""
        result = run_query(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            params=("' OR '1'='1",),
        )
        # Parameterized query — should return 0 rows, not all customers
        assert result["error"] is None
        assert len(result["rows"]) == 0


class TestRowLimits:
    def test_result_capped(self):
        result = run_query("SELECT * FROM bookings")
        assert result["error"] is None
        assert len(result["rows"]) <= 100
