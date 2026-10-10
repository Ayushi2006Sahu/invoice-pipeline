import json
from datetime import date
from decimal import Decimal as D
import pytest
from invoice_pipeline.dataset import write_dataset
from invoice_pipeline.db import connect, get_or_create_run, init_db, record_validation, save_invoice
from invoice_pipeline.extractors.base import ALL_FIELDS, ExtractedItem, ExtractionResult
from invoice_pipeline.extractors.regex import RegexExtractor
from invoice_pipeline.ingest import ingest
from invoice_pipeline.rules import CHECK_ORDER, SEVERITY, DocKey, Policy
from invoice_pipeline.validate import db_rows, failed_codes, validate
def item(amount="100.00", unit="100.00", qty=1):
    return ExtractedItem("Chair", qty, None if unit is None else D(unit),
                         None if amount is None else D(amount))
def base(**over):
    r = ExtractionResult(
        document_type="invoice", vendor="Acme Pvt Ltd", invoice_no="ACM-2026-0001",
        invoice_date=date(2026, 3, 12), due_date=date(2026, 4, 11), currency="INR",
        subtotal=D("100.00"), discount=D("0.00"), tax=D("18.00"), total=D("118.00"),
        line_items=[item()],
    )
    conf = {f: ("x", 1.0) for f in ALL_FIELDS}
    conf["vendor"] = ("Acme Pvt Ltd", 0.9)
    conf["invoice_date"] = ("12 Mar 2026", 1.0)
    conf["due_date"] = ("11 Apr 2026", 1.0)
    r.field_conf = conf
    r.signals = {"text_empty": False, "junk_ratio": 0.0,
                 "recognized_line_ratio": 1.0, "table_found": True}
    for k, v in over.items():
        setattr(r, k, v)
    return r
def status(results, code):
    return next(x.status for x in results if x.code == code)
def detail(results, code):
    return next(x.detail for x in results if x.code == code)
def key(vendor="Acme Pvt Ltd", no="ACM-2026-0001", d=date(2026, 3, 12), total="118.00"):
    return DocKey(vendor, no, d, D(total))
CN = dict(document_type="credit_note", subtotal=D("-100.00"), discount=D("-10.00"),
          tax=D("-16.20"), total=D("-106.20"), line_items=[item(amount="-100.00")])
# ------------------------------------------------------------ structure
def test_check_order_covers_every_severity_code():
    assert len(CHECK_ORDER) == len(set(CHECK_ORDER))
    assert set(CHECK_ORDER) == set(SEVERITY)
def test_clean_base_has_no_failures_and_reports_every_check_in_order():
    res = validate(base())
    assert [x.code for x in res] == list(CHECK_ORDER)
    assert failed_codes(res) == []
# ----------------------------------------------------------- arithmetic
def test_total_check_rounding_boundary():
    cases = [("118.00", "passed"), ("118.01", "passed"), ("117.99", "passed"),
             ("118.02", "failed"), ("117.98", "failed")]
    for total, expected in cases:
        assert status(validate(base(total=D(total))), "TOTAL_MISMATCH") == expected, total
def test_total_check_skips_but_missing_field_fails_when_a_figure_is_absent():
    res = validate(base(tax=None))
    assert status(res, "TOTAL_MISMATCH") == "skipped"
    assert status(res, "MISSING_FIELD") == "failed"
    assert "tax" in detail(res, "MISSING_FIELD")
def test_absent_discount_counts_as_zero():
    assert status(validate(base(discount=None)), "TOTAL_MISMATCH") == "passed"
def test_subtotal_check_boundary():
    assert status(validate(base(line_items=[item(amount="100.01")])), "SUBTOTAL_MISMATCH") == "passed"
    assert status(validate(base(line_items=[item(amount="100.02")])), "SUBTOTAL_MISMATCH") == "failed"
def test_subtotal_check_skips_when_it_cannot_run():
    assert status(validate(base(line_items=[])), "SUBTOTAL_MISMATCH") == "skipped"
    assert status(validate(base(subtotal=None)), "SUBTOTAL_MISMATCH") == "skipped"
    assert status(validate(base(line_items=[item(amount=None)])), "SUBTOTAL_MISMATCH") == "skipped"
# ------------------------------------------------------------ sign rules
def test_valid_credit_note_with_negative_discount_passes_everything():
    res = validate(base(**CN))
    assert failed_codes(res) == []
    assert status(res, "TOTAL_MISMATCH") == "passed"
    assert status(res, "SUBTOTAL_MISMATCH") == "passed"
    assert status(res, "CREDIT_NOTE_SIGN_MISMATCH") == "passed"
    assert status(res, "NEGATIVE_AMOUNT_ON_INVOICE") == "passed"
def test_credit_note_with_wrong_discount_sign_breaks_the_formula():
    res = validate(base(**{**CN, "discount": D("10.00")}))
    assert status(res, "TOTAL_MISMATCH") == "failed"
def test_credit_note_with_positive_total_fails_sign_check():
    res = validate(base(document_type="credit_note"))
    assert status(res, "CREDIT_NOTE_SIGN_MISMATCH") == "failed"
    assert status(res, "NEGATIVE_AMOUNT_ON_INVOICE") == "passed"
def test_invoice_with_negative_total_is_flagged():
    res = validate(base(subtotal=D("-100.00"), tax=D("-18.00"), total=D("-118.00"),
                        line_items=[item(amount="-100.00")]))
    assert status(res, "NEGATIVE_AMOUNT_ON_INVOICE") == "failed"
    assert status(res, "CREDIT_NOTE_SIGN_MISMATCH") == "passed"
def test_sign_checks_skip_when_type_unknown():
    res = validate(base(document_type=None))
    assert status(res, "NEGATIVE_AMOUNT_ON_INVOICE") == "skipped"
    assert status(res, "CREDIT_NOTE_SIGN_MISMATCH") == "skipped"
    assert status(res, "MISSING_FIELD") == "failed"
# ----------------------------------------------------------------- dates
def test_due_before_invoice():
    assert status(validate(base(due_date=date(2026, 3, 11))), "DUE_BEFORE_INVOICE") == "failed"
    assert status(validate(base(due_date=date(2026, 3, 12))), "DUE_BEFORE_INVOICE") == "passed"
def test_absent_due_date_is_skipped_and_is_not_a_missing_field():
    r = base(due_date=None)
    r.field_conf["due_date"] = (None, 0.0)
    res = validate(r)
    assert status(res, "DUE_BEFORE_INVOICE") == "skipped"
    assert status(res, "MISSING_FIELD") == "passed"
def test_unparseable_due_date_is_a_missing_field_not_a_silent_skip():
    r = base(due_date=None)
    r.field_conf["due_date"] = ("31/02/2026", 0.0)
    res = validate(r)
    assert status(res, "DUE_BEFORE_INVOICE") == "skipped"
    assert status(res, "MISSING_FIELD") == "failed"
    assert "due_date" in detail(res, "MISSING_FIELD")
def test_ambiguous_date_check():
    def run(inv_raw, due_raw="11 Apr 2026"):
        r = base()
        r.field_conf["invoice_date"] = (inv_raw, 1.0)
        r.field_conf["due_date"] = (due_raw, 1.0)
        return status(validate(r), "AMBIGUOUS_DATE")
    assert run("03/04/2026") == "failed"
    assert run("03/03/2026") == "passed"
    assert run("25/03/2026") == "passed"
    assert run("12 Mar 2026", "03/04/2026") == "failed"      # due date counts too
    assert run(None, None) == "skipped"
def test_future_invoice_date_is_policy_driven():
    future = base(invoice_date=date(2026, 12, 1))
    assert status(validate(future), "FUTURE_INVOICE_DATE") == "failed"
    allow = Policy(allow_future_invoice_date=True)
    assert status(validate(future, policy=allow), "FUTURE_INVOICE_DATE") == "passed"
    assert status(validate(base(invoice_date=Policy().as_of_date)), "FUTURE_INVOICE_DATE") == "passed"
    assert status(validate(base(invoice_date=None)), "FUTURE_INVOICE_DATE") == "skipped"
# ------------------------------------------------------- ids and structure
def test_invalid_and_missing_invoice_id():
    assert status(validate(base(invoice_no="ACM 2026/5")), "INVALID_INVOICE_ID") == "failed"
    assert status(validate(base(invoice_no="acm-2026-0005")), "INVALID_INVOICE_ID") == "failed"
    res = validate(base(invoice_no=None))
    assert status(res, "INVALID_INVOICE_ID") == "skipped"
    assert status(res, "MISSING_FIELD") == "failed"
def test_empty_table_is_no_line_items_but_no_table_is_missing_field():
    res = validate(base(line_items=[]))
    assert status(res, "NO_LINE_ITEMS") == "failed"
    assert status(res, "MISSING_FIELD") == "passed"
    r = base(line_items=[])
    r.signals["table_found"] = False
    res = validate(r)
    assert status(res, "NO_LINE_ITEMS") == "skipped"
    assert status(res, "MISSING_FIELD") == "failed"
    assert "line_items" in detail(res, "MISSING_FIELD")
def test_unreadable_line_item_is_a_missing_field():
    res = validate(base(line_items=[item(unit=None)]))
    assert status(res, "MISSING_FIELD") == "failed"
    assert "line_item_1" in detail(res, "MISSING_FIELD")
# ------------------------------------------------------------ duplicates
def test_exact_duplicate():
    res = validate(base(), [key()])
    assert status(res, "DUPLICATE_INVOICE") == "failed"
    assert status(res, "DUPLICATE_ID_CONFLICT") == "passed"
def test_same_id_with_different_total_or_date_is_a_conflict():
    for prior in (key(total="250.00"), key(d=date(2026, 1, 5))):
        res = validate(base(), [prior])
        assert status(res, "DUPLICATE_ID_CONFLICT") == "failed"
        assert status(res, "DUPLICATE_INVOICE") == "passed"
def test_different_vendor_same_id_is_not_flagged():
    res = validate(base(), [key(vendor="Zenith Corp")])
    assert status(res, "DUPLICATE_INVOICE") == "passed"
    assert status(res, "DUPLICATE_ID_CONFLICT") == "passed"
def test_duplicate_id_comparison_ignores_case_and_vendor_suffix_style():
    res = validate(base(), [key(vendor="ACME PRIVATE LIMITED", no="acm-2026-0001")])
    assert status(res, "DUPLICATE_INVOICE") == "failed"
def test_exact_duplicate_wins_over_conflict():
    res = validate(base(), [key(total="250.00"), key()])
    assert status(res, "DUPLICATE_INVOICE") == "failed"
    assert status(res, "DUPLICATE_ID_CONFLICT") == "passed"
def test_duplicate_checks_skip_when_key_is_incomplete():
    res = validate(base(total=None), [key()])
    assert status(res, "DUPLICATE_INVOICE") == "skipped"
    assert status(res, "DUPLICATE_ID_CONFLICT") == "skipped"
def test_validate_is_pure_and_does_not_touch_prior():
    prior = [key(), key(total="250.00")]
    before = list(prior)
    r = base()
    assert validate(r, prior) == validate(r, prior)
    assert prior == before
# ------------------------------------------------- uncertainty checks
def test_garbled_text_triggers_and_boundaries():
    def run(**sig):
        r = base()
        r.signals.update(sig)
        return status(validate(r), "GARBLED_TEXT")
    assert run() == "passed"
    assert run(text_empty=True) == "failed"
    assert run(junk_ratio=0.06) == "failed"
    assert run(junk_ratio=0.05) == "passed"
    assert run(recognized_line_ratio=0.49) == "failed"
    assert run(recognized_line_ratio=0.50) == "passed"
def test_low_confidence_threshold():
    assert status(validate(base()), "LOW_CONFIDENCE") == "passed"
    low, ok = base(), base()
    for f in ALL_FIELDS:
        low.field_conf[f] = ("x", 0.75)
        ok.field_conf[f] = ("x", 0.875)
    assert status(validate(low), "LOW_CONFIDENCE") == "failed"
    assert status(validate(ok), "LOW_CONFIDENCE") == "passed"
# ---------------------------------------------------------- database fit
def test_db_rows_leave_out_skipped_checks_and_fit_the_schema(tmp_path):
    conn = connect(tmp_path / "v.db")
    init_db(conn)
    conn.execute("INSERT INTO documents VALUES (?,?,?,?)", ("doc_0001", "doc_0001.txt", "txt", "x"))
    conn.commit()
    r = base(due_date=None)
    r.field_conf["due_date"] = (None, 0.0)
    results = validate(r)
    row = save_invoice(conn, get_or_create_run(conn, "regex"), "doc_0001", 1, r, 0.9, r.field_conf)
    record_validation(conn, row, db_rows(results))
    n = conn.execute("SELECT COUNT(*) FROM validation_results").fetchone()[0]
    assert n == len(CHECK_ORDER) - 1          # only DUE_BEFORE_INVOICE was skipped
    conn.close()
# ===================================================== whole dataset
def primary(out, doc_id):
    pdf = out / "invoices" / f"{doc_id}.pdf"
    return pdf if pdf.exists() else out / "invoices" / f"{doc_id}.txt"
@pytest.fixture(scope="module")
def validated(tmp_path_factory):
    out = tmp_path_factory.mktemp("val")
    write_dataset(out)
    gts = {p.stem: json.loads(p.read_text(encoding="utf-8"))
           for p in sorted((out / "ground_truth").glob("doc_*.json"))}
    ex = RegexExtractor()
    prior, rows = [], {}
    for doc_id in sorted(gts):                      # processing order = doc id
        ing = ingest(primary(out, doc_id))
        assert ing.ok, doc_id
        res = ex.extract(ing.text)
        rows[doc_id] = (gts[doc_id], res, validate(res, prior))
        parts = (res.vendor, res.invoice_no, res.invoice_date, res.total)
        if all(x is not None for x in parts):
            prior.append(DocKey(*parts))
    return rows
def test_clean_documents_have_zero_failed_checks(validated):
    n = 0
    for doc_id, (gt, res, results) in validated.items():
        if gt["expected_defects"]:
            continue
        assert failed_codes(results) == [], (doc_id, failed_codes(results))
        n += 1
    assert n == 51
def test_every_expected_defect_is_detected_on_readable_documents(validated):
    misses, checked = [], 0
    for doc_id, (gt, res, results) in validated.items():
        if not gt["fields_readable"]:
            continue
        expected = set(gt["expected_defects"])
        checked += len(expected)
        gone = expected - set(failed_codes(results))
        if gone:
            misses.append((doc_id, sorted(gone)))
    assert checked > 40
    assert misses == []
def test_document_level_review_decision_matches_ground_truth(validated):
    for doc_id, (gt, res, results) in validated.items():
        if not gt["fields_readable"]:
            continue
        assert bool(failed_codes(results)) == gt["expected_review"], doc_id
def test_unreadable_documents_are_all_flagged(validated):
    n = 0
    for doc_id, (gt, res, results) in validated.items():
        if gt["fields_readable"]:
            continue
        failed = failed_codes(results)
        assert failed, doc_id                       # never silently approved
        if gt["render"]["garble_mode"] in ("empty", "noise"):
            assert "GARBLED_TEXT" in failed, doc_id
        n += 1
    assert n == 6
def test_ambiguous_date_alone_does_not_trigger_low_confidence(validated):
    n = 0
    for doc_id, (gt, res, results) in validated.items():
        if gt["fields_readable"] and "AMBIGUOUS_DATE" in gt["expected_defects"]:
            assert status(results, "AMBIGUOUS_DATE") == "failed", doc_id
            assert status(results, "LOW_CONFIDENCE") == "passed", doc_id
            n += 1
    assert n == 5
