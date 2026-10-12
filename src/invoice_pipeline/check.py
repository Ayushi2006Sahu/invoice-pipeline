"""Check ONE invoice file and explain the decision. No database, no reports.
Exit codes: 0 approved, 1 needs review, 2 file could not be read.
Approved only means no check failed. It does not prove every field is right
(for example nothing examines the vendor).
"""
from __future__ import annotations
from pathlib import Path
from .extractors import get_extractor
from .ingest import ingest
from .route import decide
from .rules import SEVERITY
from .validate import FAILED, PASSED, validate
def _v(x) -> str:
    return "-" if x is None else str(x)
def check_file(path, extractor_name=None):
    p = Path(path)
    out = [f"FILE: {p}"]
    ing = ingest(p)
    if not ing.ok:
        out += [f"RESULT: COULD NOT READ ({ing.error_code})", f"  {ing.error_message}",
                "Not approved and not queued. An unreadable file is a failure, not a pass."]
        return out, 2
    ex = get_extractor(extractor_name)
    res = ex.extract(ing.text)
    results = validate(res)
    dec = decide(results)
    out += [f"EXTRACTOR: {ex.name}", "", "EXTRACTED FIELDS"]
    for label, val in (("document type", res.document_type), ("vendor", res.vendor),
                       ("invoice no", res.invoice_no), ("invoice date", res.invoice_date),
                       ("due date", res.due_date), ("currency", res.currency),
                       ("subtotal", res.subtotal), ("discount", res.discount),
                       ("tax", res.tax), ("total", res.total)):
        out.append(f"  {label:<14}{_v(val)}")
    out.append(f"  {'line items':<14}{len(res.line_items)}")
    for i in res.line_items:
        out.append(f"    - {_v(i.description)} | qty {_v(i.qty)} | unit {_v(i.unit_price)} | amount {_v(i.amount)}")
    out.append(f"  mean required-field confidence: {res.mean_required_field_confidence:.2f}")
    out += ["", "CHECKS"]
    skipped = []
    for r in results:
        if r.status == "skipped":
            skipped.append(r.code)
            continue
        tag = "PASS" if r.status == PASSED else "FAIL"
        line = f"  [{tag}] {r.code} ({SEVERITY[r.code].value})"
        if r.status == FAILED and r.detail:
            line += f": {r.detail}"
        out.append(line)
    if skipped:
        out.append("  skipped (could not run): " + ", ".join(skipped))
    out.append("  note: duplicate checks need earlier documents, so a single file is compared with nothing")
    out.append("")
    if dec.needs_review:
        out.append("DECISION: NEEDS REVIEW")
        for code in dec.reasons:
            out.append(f"  reason: {code} ({SEVERITY[code].value})")
        return out, 1
    out += ["DECISION: APPROVED",
            "  No check failed. This does not prove every field is correct (the vendor, for example, is not checked)."]
    return out, 0
