from datetime import date
from decimal import Decimal
import pytest
from invoice_pipeline.rules import (
    DocKey, Policy, classify_duplicate, expected_review,
    is_future_invoice_date, is_valid_invoice_id, normalize_vendor,
    review_reasons, within_tolerance,
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
def test_duplicate_invoice_id_case_insensitive():
    earlier = key(no="inv-2026-0001")
    later = key(no=" INV-2026-0001 ")
    assert classify_duplicate(earlier, later) == "DUPLICATE_INVOICE"
def test_future_date_is_policy_driven():
    future = date(2026, 12, 1)
    assert is_future_invoice_date(future)
    assert not is_future_invoice_date(future, Policy(allow_future_invoice_date=True))
def test_expected_review_rules():
    assert not expected_review([])
    assert expected_review(["AMBIGUOUS_DATE"])
    assert not expected_review(["FUTURE_INVOICE_DATE"], Policy(allow_future_invoice_date=True))
def test_unknown_defect_raises_error():
    with pytest.raises(ValueError):
        review_reasons(["UNKNOWN_DEFECT"])
def test_invoice_id_format():
    assert is_valid_invoice_id("INV-2026-1001")
    assert not is_valid_invoice_id("INV-1001")
    assert not is_valid_invoice_id("inv-2026-1001")   # strict: lowercase is invalid
    assert not is_valid_invoice_id("INV-2026-1001\n")  # trailing newline rejected
