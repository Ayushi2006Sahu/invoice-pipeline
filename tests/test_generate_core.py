import json
from decimal import Decimal
from invoice_pipeline.generate import InvoiceFactory, ground_truth, write_ground_truth
from invoice_pipeline.rules import is_valid_invoice_id
def test_clean_invoices_are_arithmetically_consistent():
    f = InvoiceFactory(seed=1)
    for _ in range(100):
        inv = f.clean()
        assert sum(i.amount for i in inv.line_items) == inv.subtotal
        assert inv.subtotal - inv.discount + inv.tax == inv.total
        assert inv.total > 0
def test_credit_note_amounts_are_all_negative_and_formula_holds():
    f = InvoiceFactory(seed=2)
    for _ in range(30):
        inv = f.clean(document_type="credit_note")
        assert inv.subtotal < 0 and inv.total < 0 and inv.tax <= 0
        assert all(i.amount < 0 for i in inv.line_items)
        assert inv.subtotal - inv.discount + inv.tax == inv.total
def test_same_seed_gives_same_data():
    a = [InvoiceFactory(seed=7).clean() for _ in range(1)]
    b = [InvoiceFactory(seed=7).clean() for _ in range(1)]
    assert a == b
    fa, fb = InvoiceFactory(seed=7), InvoiceFactory(seed=7)
    assert [fa.clean() for _ in range(20)] == [fb.clean() for _ in range(20)]
def test_invoice_numbers_valid_and_unique():
    f = InvoiceFactory(seed=3)
    docs = [f.clean() for _ in range(100)]
    numbers = [d.invoice_no for d in docs]
    assert all(is_valid_invoice_id(n) for n in numbers)
    assert len(set(numbers)) == len(numbers)
def test_clean_ground_truth_needs_no_review_and_is_json_safe(tmp_path):
    inv = InvoiceFactory(seed=4).clean(with_due_date=False)
    gt = ground_truth("doc_0001", inv)
    assert gt["expected_defects"] == []
    assert gt["expected_review"] is False
    assert gt["fields"]["due_date"] is None
    path = tmp_path / "doc_0001.json"
    write_ground_truth(path, gt)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert Decimal(loaded["fields"]["total"]) == inv.total
def test_defective_ground_truth_uses_shared_rules():
    inv = InvoiceFactory(seed=5).clean()
    gt = ground_truth("doc_0002", inv, defects=["TOTAL_MISMATCH"])
    assert gt["expected_review"] is True
    assert gt["expected_review_reasons"] == ["TOTAL_MISMATCH"]
