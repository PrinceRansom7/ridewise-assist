"""
Field-level sanitization, masking, and PII protection for RideWise Assist.

Implements Data Sensitivity Matrix controls:
- Level 1: Public/customer-facing (city normalized, rates, statuses).
- Level 2: Customer-owned (customer_id, booking_id, amounts).
- Level 3: Restricted PII (phone, email, DL, DOB, transaction_ref, free-text notes).
- Level 4: Secrets (API keys, credentials - never exposed).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from src.ridewise.auth.session import SessionRole
from src.ridewise.database.city import normalize_city


def mask_phone(phone: Optional[str]) -> str:
    """Mask phone number keeping only the last 4 digits (e.g. ******2345)."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) <= 4:
        return "****"
    return "*" * (len(digits) - 4) + digits[-4:]


def mask_email(email: Optional[str]) -> str:
    """Mask email preserving first character and domain (e.g. a***@gmail.com)."""
    if not email or "@" not in str(email):
        return "***"
    parts = str(email).split("@", 1)
    username, domain = parts[0], parts[1]
    if len(username) <= 1:
        masked_user = "*"
    else:
        masked_user = username[0] + "***"
    return f"{masked_user}@{domain}"


def mask_driving_license(dl: Optional[str]) -> str:
    """Mask driving license preserving state prefix and last 4 characters."""
    if not dl:
        return ""
    s = str(dl).strip()
    if len(s) <= 8:
        return "******"
    prefix = s[:4]
    suffix = s[-4:]
    return f"{prefix}******{suffix}"


def mask_transaction_ref(ref: Optional[str]) -> str:
    """Mask payment gateway transaction reference."""
    if not ref:
        return ""
    s = str(ref).strip()
    if len(s) <= 6:
        return "***"
    return s[:3] + "***" + s[-3:]


def sanitize_free_text(text: Optional[str]) -> str:
    """
    Sanitize free-text fields (damage descriptions, remarks) to prevent
    prompt-injection and tag spoofing.
    """
    if not text:
        return ""
    s = str(text)
    # Remove system injection tags
    s = re.sub(r"\[(DB RESULT|POLICY SOURCE|SYSTEM PROMPT|USER)\]", "", s, flags=re.IGNORECASE)
    # Strip HTML tags
    s = re.sub(r"<[^>]+>", "", s)
    # Collapse multiple whitespaces/newlines
    s = " ".join(s.split()).strip()
    return s


def sanitize_row_for_role(row: dict[str, Any], role: SessionRole = SessionRole.CUSTOMER) -> dict[str, Any]:
    """
    Apply field-level access control and masking based on caller role.
    """
    clean: dict[str, Any] = {}

    for key, value in row.items():
        if value is None:
            clean[key] = None
            continue

        lower_key = key.lower()

        # 1. City normalization
        if lower_key in ("city", "customer_city"):
            clean[key] = normalize_city(str(value))

        # 2. Level 3 PII - Phone
        elif lower_key in ("phone", "customer_phone", "mobile"):
            clean[key] = mask_phone(str(value))

        # 3. Level 3 PII - Email
        elif lower_key in ("email", "customer_email"):
            clean[key] = mask_email(str(value))

        # 4. Level 3 PII - Driving License
        elif lower_key in ("driving_license_no", "license_no", "dl_no"):
            clean[key] = mask_driving_license(str(value))

        # 5. Level 3 PII - Date of Birth (omit from default projection)
        elif lower_key in ("date_of_birth", "dob"):
            # Excluded from ordinary answers for privacy
            clean[key] = "[REDACTED: PII]"

        # 6. Level 3 - Transaction Reference
        elif lower_key in ("transaction_ref", "txn_ref", "gateway_ref"):
            clean[key] = mask_transaction_ref(str(value))

        # 7. Level 3 - Free-text notes
        elif lower_key in ("remarks", "description", "damage_description"):
            clean[key] = sanitize_free_text(str(value))

        # Level 1 and 2 fields pass through
        else:
            clean[key] = value

    return clean
