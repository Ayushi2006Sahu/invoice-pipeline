"""Single source of truth for severity, policy and decision rules.

Both the data generator (to label ground truth) and the validator
(to detect problems) import from here, so they can never disagree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Severity(str, Enum):
    HARD = "hard"            # data is internally inconsistent
    SOFT = "soft"            # probably fine, a human should look
    UNCERTAIN = "uncertain"  # extractor could not read it reliably


SEVERITY: dict[str, Severity] = {
    # hard
    "TOTAL_MISMATCH": Severity.HARD,
    "SUBTOTAL_MISMATCH": Severity.HARD,
    "DUE_BEFORE_INVOICE": Severity.HARD,
    "NO_LINE_ITEMS": Severity.HARD,
    "INVALID_INVOICE_ID": Severity.HARD,
    "DUPLICATE_INVOICE": Severity.HARD,
    # soft
    "AMBIGUOUS_DATE": Severity.SOFT,
    "FUTURE_INVOICE_DATE": Severity.SOFT,
    "NEGATIVE_AMOUNT_ON_INVOICE": Severity.SOFT,
    "CREDIT_NOTE_SIGN_MISMATCH": Severity.SOFT,
    "DUPLICATE_ID_CONFLICT": Severity.SOFT,
    # uncertain
    "MISSING_FIELD": Severity.UNCERTAIN,
    "GARBLED_TEXT": Severity.UNCERTAIN,
    "LOW_CONFIDENCE": Severity.UNCERTAIN,
}


@dataclass(frozen=True)
class Policy:
    # Fixed date instead of today(), so tests and data stay reproducible.
    as_of_date: date = date(2026, 10, 10)
    rounding_tolerance: Decimal = Decimal("0.01")
    confidence_threshold: float = 0.80
    allow_future_invoice_date: bool = False
    max_future_days: int = 0
    due_date_required: bool = False
    invoice_id_pattern: str = r"^[A-Z]{2,4}-\d{4}-\d{3,6}$"


DEFAULT_POLICY = Policy()


def within_tolerance(a: Decimal, b: Decimal, policy: Policy = DEFAULT_POLICY) -> bool:
    return abs(a - b) <= policy.rounding_tolerance


def is_future_invoice_date(d: date, policy: Policy = DEFAULT_POLICY) -> bool:
    if policy.allow_future_invoice_date:
        return False
    return (d - policy.as_of_date).days > policy.max_future_days


def normalize_vendor(name: str) -> str:
    s = re.sub(r"[^\w\s]", " ", name.lower())
    s = re.sub(r"\bprivate\b", "pvt", s)
    s = re.sub(r"\blimited\b", "ltd", s)
    return " ".join(s.split())


@dataclass(frozen=True)
class DocKey:
    vendor: str
    invoice_no: str
    invoice_date: date
    total: Decimal


def classify_duplicate(earlier: DocKey, later: DocKey) -> str | None:
    """Return DUPLICATE_INVOICE, DUPLICATE_ID_CONFLICT or None.

    `later` is the document being checked, `earlier` one already accepted.
    """
    if normalize_vendor(earlier.vendor) != normalize_vendor(later.vendor):
        return None
    if earlier.invoice_no != later.invoice_no:
        return None
    if (earlier.invoice_date, earlier.total) == (later.invoice_date, later.total):
        return "DUPLICATE_INVOICE"
    return "DUPLICATE_ID_CONFLICT"


def review_reasons(defects: list[str], policy: Policy = DEFAULT_POLICY) -> list[str]:
    """Defects that actually force a human review under this policy."""
    reasons = []
    for d in defects:
        if d not in SEVERITY:
            raise ValueError(f"Unknown defect code: {d}")
        if d == "FUTURE_INVOICE_DATE" and policy.allow_future_invoice_date:
            continue
        reasons.append(d)
    return sorted(set(reasons))


def expected_review(defects: list[str], policy: Policy = DEFAULT_POLICY) -> bool:
    return len(review_reasons(defects, policy)) > 0