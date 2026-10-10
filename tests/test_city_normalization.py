import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ridewise.database.city import normalize_city, extract_city_from_text
from src.ridewise.database.sql_tool import run_query
from src.ridewise.auth.session import SessionState, SessionRole
from src.ridewise.workflow.router import _build_agent_general_sql, _build_customer_sql


def test_unit_normalization():
    assert normalize_city("Bangalore") == "Bengaluru"
    assert normalize_city("BLR") == "Bengaluru"
    assert normalize_city("bangalore ") == "Bengaluru"
    assert normalize_city("Bengaluru") == "Bengaluru"
    assert normalize_city("Mysore") == "Mysuru"
    assert normalize_city("Mangalore") == "Mangaluru"
    assert extract_city_from_text("How many users in bangalore?") == "Bengaluru"
    assert extract_city_from_text("Show customers in BLR") == "Bengaluru"
    assert extract_city_from_text("Are there bookings in Bengaluru?") == "Bengaluru"


def test_agent_city_count_queries():
    # Bangalore
    sql_bng = _build_agent_general_sql("How many customers from Bangalore?")
    res_bng = run_query(sql_bng)
    assert res_bng["rows"][0]["customer_count"] == 164

    # BLR
    sql_blr = _build_agent_general_sql("How many customers from BLR?")
    res_blr = run_query(sql_blr)
    assert res_blr["rows"][0]["customer_count"] == 164

    # Bengaluru
    sql_bgl = _build_agent_general_sql("How many customers from Bengaluru?")
    res_bgl = run_query(sql_bgl)
    assert res_bgl["rows"][0]["customer_count"] == 164


def test_agent_city_breakdown():
    sql_breakdown = _build_agent_general_sql("Show breakdown of customers by city")
    res = run_query(sql_breakdown)
    counts = {r["city"]: r["customer_count"] for r in res["rows"]}
    assert counts["Bengaluru"] == 164
    assert "Bangalore" not in counts
    assert "BLR" not in counts
    assert "bangalore " not in counts


def test_customer_city_normalization():
    # CUST0002 has 'Bangalore' stored in the raw database
    sess = SessionState()
    sess.role = SessionRole.CUSTOMER
    sess.customer_id = "CUST0002"

    sql = _build_customer_sql(sess, "What is my registered city?")
    res = run_query(sql, session=sess)
    assert len(res["rows"]) > 0
    assert res["rows"][0]["city"] == "Bengaluru"


if __name__ == "__main__":
    test_unit_normalization()
    test_agent_city_count_queries()
    test_agent_city_breakdown()
    test_customer_city_normalization()
    print("All city normalization tests PASSED successfully!")
