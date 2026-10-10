"""
Tests for deterministic business logic calculations.

All thresholds and policy rules are tested against the verified source documents.
Run: pytest tests/test_calculations.py -v
"""
import sys
from pathlib import Path
from decimal import Decimal
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ridewise.business_logic.calculations import (
    calc_cancellation_refund,
    calc_ev_battery_penalty,
    calc_late_fee,
    calc_rental_charge,
    select_policy_version,
)


class TestPolicyVersionSelection:
    """RW-POL-02/03: Version selected by booked_at in IST."""

    def test_booking_before_cutoff_uses_v1(self):
        # 2026-06-30 18:29:00 UTC = 2026-06-30 23:59 IST → v1
        assert select_policy_version("2026-06-30 18:29:00") == 1

    def test_booking_exactly_at_cutoff_uses_v2(self):
        # 2026-06-30 18:30:00 UTC = 2026-07-01 00:00 IST → v2
        assert select_policy_version("2026-06-30 18:30:00") == 2

    def test_booking_after_cutoff_uses_v2(self):
        assert select_policy_version("2026-07-01 00:00:00") == 2

    def test_early_booking_uses_v1(self):
        assert select_policy_version("2026-01-15 09:00:00") == 1


class TestCancellationRefundV1:
    """RW-POL-02 v1: 100% / 50% / 0% slabs."""

    def _refund(self, booked_at, scheduled_start, cancelled_at, charge=1000.0, gst=180.0, by="CUSTOMER"):
        return calc_cancellation_refund(booked_at, scheduled_start, cancelled_at, charge, gst, by)

    def test_24h_or_more_gets_100pct(self):
        r = self._refund(
            "2026-05-01 09:00:00",  # v1
            "2026-05-10 09:00:00",  # pickup
            "2026-05-09 08:59:00",  # 24h1m before → 100%
        )
        assert r.refund_pct == 100
        assert r.refund_amount == Decimal("1000.00")
        assert r.gst_refund == Decimal("180.00")

    def test_exactly_24h_before_gets_100pct(self):
        r = self._refund(
            "2026-05-01 09:00:00",
            "2026-05-10 09:00:00",
            "2026-05-09 09:00:00",  # exactly 24h → 100%
        )
        assert r.refund_pct == 100

    def test_6h_to_24h_gets_50pct(self):
        r = self._refund(
            "2026-05-01 09:00:00",
            "2026-05-10 09:00:00",
            "2026-05-09 21:00:00",  # 12h before → 50%
        )
        assert r.refund_pct == 50
        assert r.refund_amount == Decimal("500.00")

    def test_exactly_6h_before_gets_50pct(self):
        r = self._refund(
            "2026-05-01 09:00:00",
            "2026-05-10 09:00:00",
            "2026-05-10 03:00:00",  # exactly 6h → 50%
        )
        assert r.refund_pct == 50

    def test_less_than_6h_gets_0pct(self):
        r = self._refund(
            "2026-05-01 09:00:00",
            "2026-05-10 09:00:00",
            "2026-05-10 06:00:00",  # 3h before → 0%
        )
        assert r.refund_pct == 0
        assert r.refund_amount == Decimal("0.00")

    def test_ridewise_cancellation_always_100pct(self):
        r = self._refund(
            "2026-05-01 09:00:00",
            "2026-05-10 09:00:00",
            "2026-05-10 08:55:00",  # 5 min before pickup
            by="RIDEWISE",
        )
        assert r.refund_pct == 100


class TestCancellationRefundV2:
    """RW-POL-03 v2 Annexure 1: 100% / 75% / 25% / 0% slabs."""

    def _refund(self, scheduled_start, cancelled_at, charge=1000.0, gst=180.0, by="CUSTOMER"):
        return calc_cancellation_refund(
            "2026-07-15 09:00:00",  # booked after cutoff → v2
            scheduled_start, cancelled_at, charge, gst, by
        )

    def test_48h_or_more_gets_100pct(self):
        r = self._refund("2026-07-20 09:00:00", "2026-07-18 08:59:00")
        assert r.refund_pct == 100
        assert r.policy_version == 2

    def test_exactly_48h_gets_100pct(self):
        r = self._refund("2026-07-20 09:00:00", "2026-07-18 09:00:00")
        assert r.refund_pct == 100

    def test_12h_to_48h_gets_75pct(self):
        r = self._refund("2026-07-20 09:00:00", "2026-07-19 21:00:00")  # 12h before
        assert r.refund_pct == 75
        assert r.refund_amount == Decimal("750.00")

    def test_2h_to_12h_gets_25pct(self):
        r = self._refund("2026-07-20 09:00:00", "2026-07-20 03:00:00")  # 6h before
        assert r.refund_pct == 25
        assert r.refund_amount == Decimal("250.00")

    def test_less_than_2h_gets_0pct(self):
        r = self._refund("2026-07-20 09:00:00", "2026-07-20 08:00:00")  # 1h before
        assert r.refund_pct == 0

    def test_exactly_2h_gets_25pct(self):
        r = self._refund("2026-07-20 09:00:00", "2026-07-20 07:00:00")  # exactly 2h
        assert r.refund_pct == 25

    def test_exactly_12h_gets_75pct(self):
        r = self._refund("2026-07-20 09:00:00", "2026-07-19 21:00:00")  # exactly 12h
        assert r.refund_pct == 75


class TestLateFee:
    """RW-POL-04 v2.1: Late fee = 1.5× hourly rate per part-hour."""

    def test_5_min_late_counts_as_1_hour(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 09:05:00",  # 5 min late
            hourly_rate=100.0,
            customer_tier="STANDARD",
        )
        assert r.late_hours_charged == 1
        assert r.late_fee_subtotal == Decimal("150.00")

    def test_59_min_late_counts_as_1_hour(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 09:59:00",
            hourly_rate=100.0,
            customer_tier="STANDARD",
        )
        assert r.late_hours_charged == 1

    def test_61_min_late_counts_as_2_hours(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 10:01:00",
            hourly_rate=100.0,
            customer_tier="STANDARD",
        )
        assert r.late_hours_charged == 2
        assert r.late_fee_subtotal == Decimal("300.00")

    def test_3h_15min_late_counts_as_4_hours(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 12:15:00",
            hourly_rate=100.0,
            customer_tier="STANDARD",
        )
        assert r.late_hours_charged == 4

    def test_gold_within_30min_grace_no_fee(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 09:30:00",  # exactly 30 min → within grace
            hourly_rate=100.0,
            customer_tier="GOLD",
        )
        assert r.late_fee_subtotal == Decimal("0.00")
        assert r.late_hours_charged == 0

    def test_gold_at_30min_exactly_no_fee(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 09:30:00",
            hourly_rate=120.0,
            customer_tier="GOLD",
        )
        assert r.late_fee_subtotal == Decimal("0.00")

    def test_gold_31min_late_charged_from_minute_0(self):
        """GOLD >30 min late: full 31 min = 1 full hour charged, not 1 min."""
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 09:31:00",  # 31 min → 1 charged hour, full lateness
            hourly_rate=100.0,
            customer_tier="GOLD",
        )
        assert r.late_hours_charged == 1
        assert r.late_fee_subtotal == Decimal("150.00")

    def test_standard_no_grace(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 09:01:00",
            hourly_rate=80.0,
            customer_tier="STANDARD",
        )
        assert r.late_hours_charged == 1

    def test_gst_applied(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 10:00:00",  # 60 min → 1 hour
            hourly_rate=100.0,
            customer_tier="STANDARD",
        )
        assert r.gst_amount == Decimal("27.00")   # 18% of 150
        assert r.total_with_gst == Decimal("177.00")

    def test_not_late_no_fee(self):
        r = calc_late_fee(
            "2026-08-01 09:00:00",
            "2026-08-01 08:50:00",  # returned early
            hourly_rate=100.0,
            customer_tier="STANDARD",
        )
        assert r.late_fee_subtotal == Decimal("0.00")


class TestEVBatteryPenalty:
    """RW-POL-04 §2: Below 10% = ₹150 flat + GST."""

    def test_below_10pct_applies_fee(self):
        r = calc_ev_battery_penalty(5)
        assert r.penalty_applies is True
        assert r.fee_subtotal == Decimal("150.00")
        assert r.gst_amount == Decimal("27.00")
        assert r.total_with_gst == Decimal("177.00")

    def test_exactly_10pct_no_fee(self):
        r = calc_ev_battery_penalty(10)
        assert r.penalty_applies is False
        assert r.fee_subtotal == Decimal("0.00")

    def test_above_10pct_no_fee(self):
        r = calc_ev_battery_penalty(80)
        assert r.penalty_applies is False

    def test_0pct_battery_fee_applies(self):
        r = calc_ev_battery_penalty(0)
        assert r.penalty_applies is True

    def test_none_battery_not_ev(self):
        r = calc_ev_battery_penalty(None)
        assert r.penalty_applies is False
        assert r.fee_subtotal == Decimal("0.00")


class TestRentalChargeCalculation:
    """RW-POL-01 §4.1–4.2: Hourly/daily rate with daily-rate cap."""

    def test_4h_at_hourly_rate(self):
        r = calc_rental_charge(4, hourly_rate=80.0, daily_rate=700.0)
        # 4 × 80 = 320 < 700 → use hourly
        assert abs(r["rental_charge"] - 320.0) < 0.01

    def test_10h_capped_at_daily(self):
        r = calc_rental_charge(10, hourly_rate=80.0, daily_rate=700.0)
        # 10 × 80 = 800 > 700 → cap at 700
        assert abs(r["rental_charge"] - 700.0) < 0.01

    def test_24h_equals_daily(self):
        r = calc_rental_charge(24, hourly_rate=80.0, daily_rate=700.0)
        assert abs(r["rental_charge"] - 700.0) < 0.01

    def test_27h_one_daily_plus_3h(self):
        r = calc_rental_charge(27, hourly_rate=80.0, daily_rate=700.0)
        # 1 daily (700) + 3 × 80 (240) = 940
        assert abs(r["rental_charge"] - 940.0) < 0.01

    def test_gst_18pct(self):
        r = calc_rental_charge(4, hourly_rate=80.0, daily_rate=700.0)
        expected_gst = round(320 * 0.18, 2)
        assert abs(r["gst"] - expected_gst) < 0.01
