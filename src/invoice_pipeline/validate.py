"""Independent judge of an extraction. Pure: no I/O, no clock, no mutation.
validate() returns one CheckResult per code in CHECK_ORDER:
  passed   the check ran and the condition held
  failed   the check ran and the condition failed
  skipped  the check could not run (data absent or not applicable)
A skipped check is never a pass, but it is also not a reason to approve: a
field that is absent makes MISSING_FIELD fail, so nothing slips through quietly.
Confidence is a review signal, not proof that an extraction is correct.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence
from .extractors.base import REQUIRED_FIELDS, ExtractionResult
from .rules import (
    CHECK_ORDER, DEFAULT_POLICY, DocKey, Policy, classify_duplicate,
    is_ambiguous_date_text, is_future_invoice_date, is_valid_invoice_id,
    within_tolerance,
)
PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"
ZERO = Decimal("0")
@dataclass(frozen=True)
class CheckResult:
    code: str
    status: str
    detail: Optional[str] = None
def _ok(code, detail=None):
    return CheckResult(code, PASSED, detail)
def _fail(code, detail):
    return CheckResult(code, FAILED, detail)
def _skip(code, reason):
    return CheckResult(code, SKIPPED, reason)
def _raw(res: ExtractionResult, name: str):
    return res.field_conf.get(name, (None, 0.0))[0]
# ----------------------------------------------------------- arithmetic
def _total_mismatch(r, policy):
    c = "TOTAL_MISMATCH"
    if r.subtotal is None or r.tax is None or r.total is None:
        return _skip(c, "subtotal, tax or total not read")
    discount = r.discount if r.discount is not None else ZERO
    expected = r.subtotal - discount + r.tax
    if within_tolerance(expected, r.total, policy):
        return _ok(c)
    return _fail(c, f"subtotal - discount + tax = {expected}, printed total = {r.total}")
def _subtotal_mismatch(r, policy):
    c = "SUBTOTAL_MISMATCH"
    if not r.line_items or r.subtotal is None:
        return _skip(c, "no line items or no subtotal")
    if any(i.amount is None for i in r.line_items):
        return _skip(c, "a line amount could not be read")
    s = sum((i.amount for i in r.line_items), ZERO)
    if within_tolerance(s, r.subtotal, policy):
        return _ok(c)
    return _fail(c, f"line items sum to {s}, printed subtotal = {r.subtotal}")
# ---------------------------------------------------------------- dates
def _due_before_invoice(r, policy):
    c = "DUE_BEFORE_INVOICE"
    if r.invoice_date is None or r.due_date is None:
        return _skip(c, "invoice date or due date absent or unreadable")
    if r.due_date < r.invoice_date:
        return _fail(c, f"due {r.due_date} is before invoice date {r.invoice_date}")
    return _ok(c)
def _ambiguous_date(r, policy):
    c = "AMBIGUOUS_DATE"
    raws = [x for x in (_raw(r, "invoice_date"), _raw(r, "due_date")) if x]
    if not raws:
        return _skip(c, "no date text found")
    hit = [x for x in raws if is_ambiguous_date_text(x)]
    if hit:
        return _fail(c, f"date text can be read two ways: {', '.join(hit)}")
    return _ok(c)
def _future_invoice_date(r, policy):
    c = "FUTURE_INVOICE_DATE"
    if r.invoice_date is None:
        return _skip(c, "invoice date absent or unreadable")
    if is_future_invoice_date(r.invoice_date, policy):
        return _fail(c, f"{r.invoice_date} is after as-of date {policy.as_of_date}")
    return _ok(c)
# ------------------------------------------------------ structure and ids
def _no_line_items(r, policy):
    c = "NO_LINE_ITEMS"
    if not r.signals.get("table_found", True):
        return _skip(c, "no line item table found (see MISSING_FIELD)")
    if len(r.line_items) == 0:
        return _fail(c, "line item table has no rows")
    return _ok(c)
def _invalid_invoice_id(r, policy):
    c = "INVALID_INVOICE_ID"
    if r.invoice_no is None:
        return _skip(c, "invoice number absent (see MISSING_FIELD)")
    if is_valid_invoice_id(r.invoice_no, policy):
        return _ok(c)
    return _fail(c, f"{r.invoice_no!r} does not match {policy.invoice_id_pattern}")
def _duplicates(r, prior: Sequence[DocKey]):
    d, k = "DUPLICATE_INVOICE", "DUPLICATE_ID_CONFLICT"
    parts = (r.vendor, r.invoice_no, r.invoice_date, r.total)
    if any(x is None for x in parts):
        why = "vendor, invoice number, date or total not read"
        return _skip(d, why), _skip(k, why)
    cur = DocKey(*parts)
    verdicts = [classify_duplicate(p, cur) for p in prior]
    if d in verdicts:
        return (_fail(d, f"same vendor, number, date and total as an earlier {r.invoice_no}"),
                _ok(k, "reported as exact duplicate instead"))
    if k in verdicts:
        return (_ok(d),
                _fail(k, f"{r.invoice_no} already used by this vendor with a different date or total"))
    return _ok(d), _ok(k)
# ------------------------------------------------------------ sign rules
def _negative_amount(r, policy):
    c = "NEGATIVE_AMOUNT_ON_INVOICE"
    if r.document_type is None or r.total is None:
        return _skip(c, "document type or total not read")
    if r.document_type == "invoice" and r.total < 0:
        return _fail(c, f"invoice with negative total {r.total}")
    return _ok(c)
def _credit_note_sign(r, policy):
    c = "CREDIT_NOTE_SIGN_MISMATCH"
    if r.document_type is None or r.total is None:
        return _skip(c, "document type or total not read")
    if r.document_type == "credit_note" and r.total > 0:
        return _fail(c, f"credit note with positive total {r.total}")
    return _ok(c)
# ----------------------------------------------------- uncertainty checks
def _missing_field(r, policy):
    c = "MISSING_FIELD"
    missing = []
    for f in REQUIRED_FIELDS:
        if f == "line_items":
            if not r.signals.get("table_found", True):
                missing.append(f)          # an empty table is NO_LINE_ITEMS, not missing
        elif getattr(r, f) is None:
            missing.append(f)
    unreadable = [f for f in ("due_date", "discount", "currency")
                  if _raw(r, f) is not None and getattr(r, f) is None]
    for n, i in enumerate(r.line_items, 1):
        if i.qty is None or i.unit_price is None or i.amount is None:
            unreadable.append(f"line_item_{n}")
    if not missing and not unreadable:
        return _ok(c)
    bits = []
    if missing:
        bits.append("missing: " + ", ".join(missing))
    if unreadable:
        bits.append("found but unreadable: " + ", ".join(unreadable))
    return _fail(c, "; ".join(bits))
def _garbled_text(r, policy):
    c = "GARBLED_TEXT"
    sig = r.signals
    why = []
    if sig.get("text_empty"):
        why.append("text is empty")
    junk = sig.get("junk_ratio", 0.0)
    if junk > policy.garbled_max_junk_ratio:
        why.append(f"junk ratio {junk:.3f} > {policy.garbled_max_junk_ratio}")
    rec = sig.get("recognized_line_ratio", 1.0)
    if rec < policy.garbled_min_recognized_ratio:
        why.append(f"recognized lines {rec:.2f} < {policy.garbled_min_recognized_ratio}")
    return _fail(c, "; ".join(why)) if why else _ok(c)
def _low_confidence(r, policy):
    c = "LOW_CONFIDENCE"
    mean = r.mean_required_field_confidence
    if mean < policy.confidence_threshold:
        return _fail(c, f"mean required-field confidence {mean:.3f} < {policy.confidence_threshold}")
    return _ok(c)
# ------------------------------------------------------------------ API
def validate(
    result: ExtractionResult,
    prior: Sequence[DocKey] = (),
    policy: Policy = DEFAULT_POLICY,
) -> list[CheckResult]:
    """Run every check. `prior` = documents processed BEFORE this one (caller's job)."""
    dup, conflict = _duplicates(result, prior)
    results = [
        _total_mismatch(result, policy), _subtotal_mismatch(result, policy),
        _due_before_invoice(result, policy), _no_line_items(result, policy),
        _invalid_invoice_id(result, policy), dup, conflict,
        _ambiguous_date(result, policy), _future_invoice_date(result, policy),
        _negative_amount(result, policy), _credit_note_sign(result, policy),
        _missing_field(result, policy), _garbled_text(result, policy),
        _low_confidence(result, policy),
    ]
    by_code = {c.code: c for c in results}
    return [by_code[code] for code in CHECK_ORDER]
def failed_codes(results: Sequence[CheckResult]) -> list[str]:
    return [r.code for r in results if r.status == FAILED]
def db_rows(results: Sequence[CheckResult]):
    """(code, passed, detail) for checks that ran. Skipped checks get no row."""
    return [(r.code, r.status == PASSED, r.detail) for r in results if r.status != SKIPPED]
