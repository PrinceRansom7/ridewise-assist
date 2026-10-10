import sys
sys.path.insert(0, '.')
from src.ridewise.auth.session import SessionState, SessionRole
from src.ridewise.database.sql_tool import run_query

s = SessionState()
s.role = SessionRole.CUSTOMER
s.customer_id = 'CUST0001'
s.customer_tier = 'GOLD'

# Test 1: query with LIMIT clause (was causing WHERE/LIMIT ordering bug)
result = run_query('SELECT booking_id, booking_status FROM bookings LIMIT 3', session=s)
print('Test 1 (LIMIT clause):', result['error'], '| rows:', len(result['rows']))
assert result['error'] is None, result['error']
assert len(result['rows']) > 0

# Test 2: aliased JOIN query
result2 = run_query(
    'SELECT b.booking_id, b.customer_id FROM bookings b JOIN vehicles v ON b.vehicle_id = v.vehicle_id',
    session=s
)
print('Test 2 (alias JOIN):', result2['error'], '| rows:', len(result2['rows']))
assert result2['error'] is None, result2['error']
for r in result2['rows']:
    cid = r['customer_id']
    assert cid == 'CUST0001', 'Ownership isolation failed: got ' + str(cid)
print('  All rows = CUST0001: PASS')

print()
print('All integration checks PASSED.')
