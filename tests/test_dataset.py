from collections import Counter
import pytest
from invoice_pipeline.dataset import build_specs, doc_key, document_text, write_dataset
from invoice_pipeline.generate import invoice_fields
from invoice_pipeline.render import LAYOUTS
from invoice_pipeline.rules import (
    SEVERITY, classify_duplicate, is_valid_invoice_id, within_tolerance,
)
@pytest.fixture(scope="module")
def specs():
    return build_specs()
def test_dataset_size_and_unique_ids(specs):
    assert len(specs) == 100
    assert len({s.doc_id for s in specs}) == 100
def test_every_defect_type_appears_at_least_three_times(specs):
    counts = Counter(d for s in specs for d in s.defects)
    for code in set(SEVERITY) - {"LOW_CONFIDENCE"}:
        assert counts[code] >= 3, code
def test_same_seed_same_dataset():
    a, b = build_specs(42), build_specs(42)
    assert a == b
    assert [document_text(s) for s in a] == [document_text(s) for s in b]
def test_defect_free_documents_are_consistent(specs):
    for s in specs:
        if s.defects:
            continue
        inv = s.inv
        assert sum(i.amount for i in inv.line_items) == inv.subtotal
        assert within_tolerance(inv.subtotal - inv.discount + inv.tax, inv.total)
        assert is_valid_invoice_id(inv.invoice_no)
def test_injected_defects_are_really_present(specs):
    for s in specs:
        inv = s.inv
        if "TOTAL_MISMATCH" in s.defects:
            assert not within_tolerance(inv.subtotal - inv.discount + inv.tax, inv.total)
        if "SUBTOTAL_MISMATCH" in s.defects:
            assert sum(i.amount for i in inv.line_items) != inv.subtotal
        if "DUE_BEFORE_INVOICE" in s.defects:
            assert inv.due_date < inv.invoice_date
        if "INVALID_INVOICE_ID" in s.defects:
            assert not is_valid_invoice_id(inv.invoice_no)
        if "NO_LINE_ITEMS" in s.defects:
            assert inv.line_items == []
def test_rounding_cases_are_within_tolerance_but_not_exact(specs):
    rounding = [s for s in specs if "ROUNDING_WITHIN_TOLERANCE" in s.tags]
    assert len(rounding) == 4
    for s in rounding:
        calc = s.inv.subtotal - s.inv.discount + s.inv.tax
        assert calc != s.inv.total
        assert within_tolerance(calc, s.inv.total)
        assert s.defects == []
def test_ambiguous_dates_only_where_labelled(specs):
    for layout in LAYOUTS.values():
        assert "/" not in layout.date_fmt   # clean layouts never use numeric slash dates
    for s in specs:
        if "AMBIGUOUS_DATE" in s.defects:
            assert s.render["date_fmt"] == "%d/%m/%Y"
            assert s.inv.invoice_date.day <= 12
            assert s.inv.invoice_date.day != s.inv.invoice_date.month
        else:
            assert "date_fmt" not in s.render
def test_duplicates_follow_originals_and_match_rules(specs):
    by_id = {s.doc_id: s for s in specs}
    for s in specs:
        if s.duplicate_of:
            o = by_id[s.duplicate_of]
            assert o.doc_id < s.doc_id
            assert classify_duplicate(doc_key(o.inv), doc_key(s.inv)) == "DUPLICATE_INVOICE"
        if s.conflicts_with:
            o = by_id[s.conflicts_with]
            assert o.doc_id < s.doc_id
            assert classify_duplicate(doc_key(o.inv), doc_key(s.inv)) == "DUPLICATE_ID_CONFLICT"
    for b in (s for s in specs if "SAME_ID_DIFFERENT_VENDOR" in s.tags):
        a = next(x for x in specs if x is not b
                 and x.inv.invoice_no == b.inv.invoice_no and x.inv.vendor != b.inv.vendor)
        assert classify_duplicate(doc_key(a.inv), doc_key(b.inv)) is None
        assert b.defects == []
def test_no_negative_zero_in_ground_truth_fields(specs):
    for s in specs:
        f = invoice_fields(s.inv)
        for k in ("subtotal", "discount", "tax", "total"):
            assert f[k] != "-0.00"
def test_write_dataset_creates_files_and_readable_pdfs(tmp_path):
    import json
    import pdfplumber
    manifest = write_dataset(tmp_path)
    assert manifest["total_documents"] == 100
    assert len(list((tmp_path / "invoices").glob("*.txt"))) == 100
    assert len(list((tmp_path / "invoices").glob("*.pdf"))) == 94   # 6 garbled are txt only
    assert len(list((tmp_path / "ground_truth").glob("*.json"))) == 100
    s = next(x for x in build_specs() if x.plain)
    with pdfplumber.open(tmp_path / "invoices" / f"{s.doc_id}.pdf") as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert s.inv.invoice_no in text
    gt = json.loads((tmp_path / "ground_truth" / f"{s.doc_id}.json").read_text(encoding="utf-8"))
    assert gt["expected_defects"] == [] and gt["expected_review"] is False
