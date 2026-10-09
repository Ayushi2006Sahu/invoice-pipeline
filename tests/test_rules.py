from datetime import date
from decimal import Decimal
from invoice_pipeline.rules import (
    DocKey, Policy, classify_duplicate, expected_review,
    is_future_invoice_date, normalize_vendor, within_tolerance,
)
def key(vendor="Acme Pvt Ltd", no="INV-2026-0001", d=date(2026, 9, 1), total="100.00"):
    return DocKey(vendor, no, d, Decimal(total))
def test_rounding_boundary():
    assert within_tolerance(Decimal("100.00"), Decimal("100.01"))
    assert not within_tolerance(Decimal("100.00"), Decimal("100.02"))
def test_vendor_normalization():
    assert normalize_vendor("ACME PRIVATE LIMITED") == normalize_vendor("Acme Pvt. Ltd.")
def test_duplicate_vs_conflict_vs_different_vendor():
    assert classify_duplicate(key(), key()) == "DUPLICATE_INVOICE"
    assert classify_duplicate(key(), key(total="250.00")) == "DUPLICATE_ID_CONFLICT"
    assert classify_duplicate(key(), key(vendor="Zenith Corp")) is None
def test_future_date_is_policy_driven():
    future = date(2026, 12, 1)
    assert is_future_invoice_date(future)
    assert not is_future_invoice_date(future, Policy(allow_future_invoice_date=True))
def test_expected_review_rules():
    assert not expected_review([])
    assert expected_review(["AMBIGUOUS_DATE"])
    assert not expected_review(["FUTURE_INVOICE_DATE"], Policy(allow_future_invoice_date=True))
