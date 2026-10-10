"""
Deterministic financial and policy calculations for RideWise Assist.

IMPORTANT: All monetary calculations use Python's Decimal type.
The LLM must never perform these calculations — it only receives results.

Policy sources
──────────────
RW-POL-01 v3.0  — rental charges, GST, deposits
RW-POL-02 v1    — cancellation refunds for bookings < 2026-07-01 00:00 IST
RW-POL-03 v2    — cancellation refunds for bookings >= 2026-07-01 00:00 IST
RW-POL-04 v2.1  — late fees, EV battery penalty, damage
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

# ── Timezone helpers ──────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

# Policy v2 cutoff: bookings made on or after 2026-07-01 00:00 IST use v2
POLICY_V2_CUTOFF_IST = datetime(2026, 7, 1, 0, 0, 0, tzinfo=IST)

# Grace period in minutes per customer tier (RW-POL-04 Annexure A, verified)
GRACE_PERIOD_MINUTES: dict[str, int] = {
    "STANDARD": 0,
    "SILVER": 0,
    "GOLD": 30,
}

# EV battery penalty (RW-POL-04 §2)
EV_LOW_BATTERY_THRESHOLD_PCT: int = 10
EV_LOW_BATTERY_FEE: Decimal = Decimal("150.00")

# GST rate
GST_RATE: Decimal = Decimal("0.18")


def _parse_utc(ts: Optional[str]) -> Optional[datetime]:
    """Parse a UTC timestamp string from the database."""
    if ts is None:
        return None
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _to_ist(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert a UTC datetime to IST."""
    if dt is None:
        return None
    return dt.astimezone(IST)


def _money(value: float | str | Decimal) -> Decimal:
    """Round to 2 decimal places using half-up rounding."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ── Policy version selection ──────────────────────────────────────────────────

def select_policy_version(booked_at_utc: str) -> int:
    """
    Return the cancellation policy version (1 or 2) for a booking.

    Version is decided by when the booking was MADE (booked_at), converted to
    IST, per RW-POL-02 and RW-POL-03 scope clauses.
    """
    booked_utc = _parse_utc(booked_at_utc)
    if booked_utc is None:
        raise ValueError(f"Cannot parse booked_at: {booked_at_utc!r}")
    booked_ist = _to_ist(booked_utc)
    return 2 if booked_ist >= POLICY_V2_CUTOFF_IST else 1


# ── Refund calculation ────────────────────────────────────────────────────────

@dataclass
class RefundResult:
    policy_version: int
    refund_pct: int              # 0, 25, 50, 75, or 100
    rental_charge_paid: Decimal
    refund_amount: Decimal       # before GST
    gst_refund: Decimal
    total_refund: Decimal        # refund_amount + gst_refund
    lead_hours: float            # hours between cancellation and pickup
    explanation: str
    source_ref: str


def calc_cancellation_refund(
    booked_at_utc: str,
    scheduled_start_utc: str,
    cancelled_at_utc: str,
    rental_charge_paid: float,
    gst_paid: float,
    cancelled_by: str = "CUSTOMER",  # "CUSTOMER" or "RIDEWISE"
) -> RefundResult:
    """
    Calculate the expected cancellation refund.

    Parameters correspond to booking record fields.
    ``cancelled_by`` should be "RIDEWISE" when booking_status is CNL_S.
    """
    version = select_policy_version(booked_at_utc)
    charge = _money(rental_charge_paid)
    gst = _money(gst_paid)

    # RideWise cancellation → always 100% refund (both policy versions)
    if cancelled_by.upper() == "RIDEWISE":
        refund_pct = 100
        lead_hours = 0.0
        explanation = "Cancelled by RideWise — 100% refund applies regardless of timing."
        src = "RW-POL-02 §2 / RW-POL-03 §2"
    else:
        cancelled_dt = _parse_utc(cancelled_at_utc)
        pickup_dt = _parse_utc(scheduled_start_utc)
        if cancelled_dt is None or pickup_dt is None:
            raise ValueError("Invalid timestamp in cancellation record.")

        lead_seconds = (pickup_dt - cancelled_dt).total_seconds()
        lead_hours = lead_seconds / 3600.0

        if version == 1:
            # RW-POL-02 §1.1
            if lead_hours >= 24:
                refund_pct = 100
                bracket = "≥ 24 hours before pickup"
            elif lead_hours >= 6:
                refund_pct = 50
                bracket = "≥ 6 h but < 24 h before pickup"
            else:
                refund_pct = 0
                bracket = "< 6 hours before pickup"
            explanation = f"Policy v1 applies (booked before 1 Jul 2026 IST). Lead time {lead_hours:.1f} h ({bracket}). Refund: {refund_pct}%."
            src = "RW-POL-02 §1.1 (p. 1)"
        else:
            # RW-POL-03 Annexure 1 (verified from scanned image)
            if lead_hours >= 48:
                refund_pct = 100
                bracket = "≥ 48 hours before pickup"
            elif lead_hours >= 12:
                refund_pct = 75
                bracket = "≥ 12 h but < 48 h before pickup"
            elif lead_hours >= 2:
                refund_pct = 25
                bracket = "≥ 2 h but < 12 h before pickup"
            else:
                refund_pct = 0
                bracket = "< 2 hours before pickup"
            explanation = f"Policy v2 applies (booked on/after 1 Jul 2026 IST). Lead time {lead_hours:.1f} h ({bracket}). Refund: {refund_pct}%."
            src = "RW-POL-03 Annexure 1 p. 2 (signed copy)"

    # Apply refund percentage
    refund_amount = _money(charge * Decimal(refund_pct) / 100)
    # GST refunded in same proportion as rental charge refund (RW-POL-01 §4.3)
    if charge > 0:
        gst_refund = _money(gst * Decimal(refund_pct) / 100)
    else:
        gst_refund = Decimal("0.00")

    total_refund = refund_amount + gst_refund

    return RefundResult(
        policy_version=version,
        refund_pct=refund_pct,
        rental_charge_paid=charge,
        refund_amount=refund_amount,
        gst_refund=gst_refund,
        total_refund=total_refund,
        lead_hours=lead_hours,
        explanation=explanation,
        source_ref=src,
    )


# ── Late fee calculation ──────────────────────────────────────────────────────

@dataclass
class LateFeeResult:
    late_minutes: int
    grace_minutes: int
    chargeable_late_minutes: int   # 0 if within grace period
    late_hours_charged: int        # ceil of chargeable minutes / 60
    hourly_rate: Decimal
    late_fee_per_hour: Decimal     # 1.5 × hourly rate
    late_fee_subtotal: Decimal     # before GST
    gst_amount: Decimal
    total_with_gst: Decimal
    explanation: str
    source_ref: str = "RW-POL-04 §1 v2.1 (p. 1–2); Annexure A p. 3"


def calc_late_fee(
    scheduled_end_utc: str,
    actual_return_utc: str,
    hourly_rate: float,
    customer_tier: str = "STANDARD",
) -> LateFeeResult:
    """
    Calculate the late-return fee.

    RW-POL-04 §1.2: each late hour OR PART THEREOF is charged at 1.5×
    the hourly rate.

    RW-POL-04 §1.3 + Annexure A: GOLD customers have a 30-minute grace
    period. If the return is within the grace period → no fee. If beyond
    the grace period → the FULL lateness (from minute 0, not from minute 30)
    is charged.
    """
    scheduled = _parse_utc(scheduled_end_utc)
    actual = _parse_utc(actual_return_utc)
    if scheduled is None or actual is None:
        raise ValueError("Invalid timestamp in late fee calculation.")

    late_td = actual - scheduled
    late_minutes = max(0, int(late_td.total_seconds() / 60))

    tier_upper = (customer_tier or "STANDARD").upper()
    grace_minutes = GRACE_PERIOD_MINUTES.get(tier_upper, 0)

    if late_minutes <= grace_minutes:
        # Within grace period — no fee
        chargeable_late_minutes = 0
        late_hours_charged = 0
        hrly = _money(hourly_rate)
        fee_per_hour = _money(hrly * Decimal("1.5"))
        subtotal = Decimal("0.00")
        gst = Decimal("0.00")
        explanation = (
            f"Returned {late_minutes} min late. GOLD grace period is 30 min — no fee."
        )
    else:
        # Full lateness is charged from minute 0 (grace does not subtract)
        chargeable_late_minutes = late_minutes
        late_hours_charged = math.ceil(chargeable_late_minutes / 60)
        hrly = _money(hourly_rate)
        fee_per_hour = _money(hrly * Decimal("1.5"))
        subtotal = _money(fee_per_hour * late_hours_charged)
        gst = _money(subtotal * GST_RATE)
        grace_note = (
            f" (GOLD grace period does not reduce the {late_minutes} min charged)"
            if grace_minutes > 0 else ""
        )
        explanation = (
            f"Returned {late_minutes} min late{grace_note}. "
            f"Charged {late_hours_charged} hour(s) × ₹{fee_per_hour} (1.5 × ₹{hrly}/h) = ₹{subtotal} + GST ₹{gst}."
        )

    return LateFeeResult(
        late_minutes=late_minutes,
        grace_minutes=grace_minutes,
        chargeable_late_minutes=chargeable_late_minutes,
        late_hours_charged=late_hours_charged,
        hourly_rate=_money(hourly_rate),
        late_fee_per_hour=_money(_money(hourly_rate) * Decimal("1.5")),
        late_fee_subtotal=subtotal,
        gst_amount=gst,
        total_with_gst=subtotal + gst,
        explanation=explanation,
    )


# ── EV battery penalty ────────────────────────────────────────────────────────

@dataclass
class BatteryPenaltyResult:
    return_battery_pct: int
    penalty_applies: bool
    fee_subtotal: Decimal
    gst_amount: Decimal
    total_with_gst: Decimal
    explanation: str
    source_ref: str = "RW-POL-04 §2 v2.1 (p. 2)"


def calc_ev_battery_penalty(return_battery_pct: Optional[int]) -> BatteryPenaltyResult:
    """
    Calculate the EV low-battery return fee.

    RW-POL-04 §2: below 10% → flat ₹150 fee + 18% GST.
    At or above 10% → no fee.
    This fee is independent of any late-return fee.
    """
    if return_battery_pct is None:
        return BatteryPenaltyResult(
            return_battery_pct=-1,
            penalty_applies=False,
            fee_subtotal=Decimal("0.00"),
            gst_amount=Decimal("0.00"),
            total_with_gst=Decimal("0.00"),
            explanation="Not an EV booking — battery penalty does not apply.",
        )

    applies = return_battery_pct < EV_LOW_BATTERY_THRESHOLD_PCT
    if applies:
        subtotal = EV_LOW_BATTERY_FEE
        gst = _money(subtotal * GST_RATE)
        explanation = (
            f"Battery returned at {return_battery_pct}% (below 10% threshold). "
            f"Flat fee ₹{subtotal} + GST ₹{gst} = ₹{subtotal + gst}."
        )
    else:
        subtotal = Decimal("0.00")
        gst = Decimal("0.00")
        explanation = (
            f"Battery returned at {return_battery_pct}% (≥ 10%). No low-battery fee."
        )

    return BatteryPenaltyResult(
        return_battery_pct=return_battery_pct,
        penalty_applies=applies,
        fee_subtotal=subtotal,
        gst_amount=gst,
        total_with_gst=subtotal + gst,
        explanation=explanation,
    )


# ── Rental charge calculation ─────────────────────────────────────────────────

def calc_rental_charge(
    booked_hours: float,
    hourly_rate: float,
    daily_rate: float,
) -> dict:
    """
    Calculate the optimal rental charge (RW-POL-01 §4.1–4.2).

    Every full 24-hour block is charged at the daily rate. Remaining hours
    are charged at hourly rate, but the combination is capped at the next
    daily rate if that would be cheaper.
    """
    hrly = _money(hourly_rate)
    daily = _money(daily_rate)
    hours = Decimal(str(booked_hours))

    full_days = int(hours // 24)
    remaining_hours = hours - (full_days * 24)

    base = _money(full_days * daily)

    if remaining_hours > 0:
        hourly_cost = _money(remaining_hours * hrly)
        # Cap remaining hours at one daily rate
        remaining_cost = min(hourly_cost, daily)
        base += remaining_cost

    gst = _money(base * GST_RATE)
    return {
        "booked_hours": float(hours),
        "rental_charge": float(base),
        "gst": float(gst),
        "total": float(base + gst),
        "source_ref": "RW-POL-01 §4.1–4.2 v3.0 (p. 1–2)",
    }
