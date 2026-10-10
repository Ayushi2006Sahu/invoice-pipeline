import inspect
import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from invoice_pipeline.dataset import garble, write_dataset
from invoice_pipeline.db import (
    connect, get_or_create_run, init_db, register_document, save_invoice,
)
from invoice_pipeline.extractors import get_extractor
from invoice_pipeline.extractors.regex import RegexExtractor, document_number_style, parse_money
from invoice_pipeline.generate import InvoiceFactory
from invoice_pipeline.ingest import ingest
from invoice_pipeline.render import LAYOUTS, render_text
from invoice_pipeline.rules import is_ambiguous_date_text
SCALARS = ("document_type", "vendor", "invoice_no", "invoice_date", "due_date",
           "currency", "subtotal", "discount", "tax", "total")
def inv_values(inv):
    v = {k: getattr(inv, k) for k in SCALARS}
    v["line_items"] = [(i.description, i.qty, i.unit_price, i.amount) for i in inv.line_items]
    return v
def res_values(res):
    v = {k: getattr(res, k) for k in SCALARS}
    v["line_items"] = [(i.description, i.qty, i.unit_price, i.amount) for i in res.line_items]
    return v
def gt_values(gt):
    f = gt["fields"]
    dec = lambda x: None if x is None else Decimal(x)
    day = lambda x: None if x is None else date.fromisoformat(x)
    return {
        "document_type": gt["document_type"], "vendor": f["vendor"],
        "invoice_no": f["invoice_no"], "invoice_date": day(f["invoice_date"]),
        "due_date": day(f["due_date"]), "currency": f["currency"],
        "subtotal": dec(f["subtotal"]), "discount": dec(f["discount"]),
        "tax": dec(f["tax"]), "total": dec(f["total"]),
        "line_items": [(i["description"], i["qty"], Decimal(i["unit_price"]),
                        Decimal(i["amount"])) for i in f["line_items"]],
    }
def make_doc(no="ACM-2026-0001", date_txt="12 Mar 2026", subtotal="100.00",
             discount="0.00", tax="18.00", total="118.00", row="Chair 1 100.00 100.00"):
    lines = ["INVOICE", "Acme Pvt Ltd", ""]
    if no is not None:
        lines.append(f"Invoice No: {no}")
    if date_txt is not None:
        lines.append(f"Invoice Date: {date_txt}")
    lines += ["", "Description Qty Unit Price Amount", "-" * 59, row, "-" * 59,
              f"Subtotal: {subtotal}", f"Discount: {discount}",
              f"Tax (18%): {tax}", f"Total: {total}"]
    return "\n".join(lines) + "\n"
def primary(out, doc_id):
    pdf = out / "invoices" / f"{doc_id}.pdf"
    return pdf if pdf.exists() else out / "invoices" / f"{doc_id}.txt"
@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("ds")
    write_dataset(out)
    gts = {p.stem: json.loads(p.read_text(encoding="utf-8"))
           for p in sorted((out / "ground_truth").glob("doc_*.json"))}
    return out, gts
# ------------------------------------------------ layouts A-D, exact match
@pytest.mark.parametrize("layout", ["A", "B", "C", "D"])
@pytest.mark.parametrize("doc_type", ["invoice", "credit_note"])
def test_layouts_extract_exactly(layout, doc_type):
    f = InvoiceFactory(seed=11)
    for _ in range(15):
        inv = f.clean(document_type=doc_type)
        res = RegexExtractor().extract(render_text(inv, LAYOUTS[layout]))
        assert "extractor_error" not in res.signals
        assert res_values(res) == inv_values(inv)
def test_absent_due_date_is_none_with_no_raw_text():
    inv = InvoiceFactory(seed=3).clean(with_due_date=False)
    res = RegexExtractor().extract(render_text(inv, LAYOUTS["A"]))
    assert res.due_date is None
    assert res.field_conf["due_date"] == (None, 0.0)
# ------------------------------------------------------- number parsing
def test_parse_money_english_and_european_exact():
    assert parse_money("INR 1,234.50", "en") == Decimal("1234.50")
    assert parse_money("INR 1.234,50", "eu") == Decimal("1234.50")
    assert parse_money("Rs. 75,821.77", "en") == Decimal("75821.77")
    assert parse_money("3.676,95", "eu") == Decimal("3676.95")
    assert parse_money("0.50", "en") == Decimal("0.50")
def test_parse_money_ambiguous_token_needs_document_style():
    assert parse_money("1,234", "en") == Decimal("1234")
    assert parse_money("1,234", "eu") is None        # would be 1.234, not money
    assert parse_money("1.234", "eu") == Decimal("1234")
    assert parse_money("1.234", "en") is None
    assert parse_money("1,234", "unknown") is None   # no evidence, no guess
    assert parse_money("1,234", "conflict") is None
    assert parse_money("1,234,567", "unknown") == Decimal("1234567")  # repeated sep = thousands
def test_parse_money_rejects_garbage():
    for bad in ["abc", "", "1,2345", "12.5", "1,23,456.00", "1.234.56", "--5", None]:
        assert parse_money(bad, "en") is None, bad
def test_parse_money_negative_prefixes():
    assert parse_money("-INR 500.00", "en") == Decimal("-500.00")
    assert parse_money("INR -500.00", "en") == Decimal("-500.00")
    assert parse_money("-Rs. 1,234.50", "en") == Decimal("-1234.50")
    assert parse_money("INR 0.00", "en") == Decimal("0.00")
def test_document_number_style_vote():
    assert document_number_style(["1,234.50", "2,000.00"]) == "en"
    assert document_number_style(["1.234,50", "2.000,00"]) == "eu"
    assert document_number_style(["1,234.50", "2.000,00"]) == "conflict"
    assert document_number_style([]) == "unknown"
    assert document_number_style(["1,234"]) == "unknown"
    assert document_number_style(["500"]) == "unknown"
def test_conflicting_number_styles_cap_confidence():
    res = RegexExtractor().extract(make_doc(tax="18,00"))
    assert res.signals["number_style"] == "conflict"
    assert res.tax == Decimal("18.00")
    for f in ("subtotal", "tax", "total", "line_items"):
        assert res.field_conf[f][1] == 0.7, f
def test_unknown_style_with_ambiguous_total_is_none():
    doc = make_doc(subtotal="1,234", discount="0", tax="0", total="1,234",
                   row="Chair 1 1,234 1,234")
    res = RegexExtractor().extract(doc)
    assert res.signals["number_style"] == "unknown"
    assert res.total is None
    assert res.field_conf["total"] == ("1,234", 0.0)
    assert res.tax == Decimal("0")
# ------------------------------------------------------------------ dates
def test_ambiguous_date_helper():
    assert is_ambiguous_date_text("03/04/2026")
    assert not is_ambiguous_date_text("03/03/2026")
    assert not is_ambiguous_date_text("25/03/2026")
    assert not is_ambiguous_date_text("2026-03-04")
    assert not is_ambiguous_date_text("04 Mar 2026")
def test_ambiguous_date_is_parsed_dmy_with_lower_confidence():
    res = RegexExtractor().extract(make_doc(date_txt="03/04/2026"))
    assert res.invoice_date == date(2026, 4, 3)
    assert res.field_conf["invoice_date"] == ("03/04/2026", 0.7)
def test_non_ambiguous_numeric_dates_keep_full_confidence():
    for raw, expected in [("03/03/2026", date(2026, 3, 3)), ("25/03/2026", date(2026, 3, 25))]:
        res = RegexExtractor().extract(make_doc(date_txt=raw))
        assert res.invoice_date == expected
        assert res.field_conf["invoice_date"][1] == 1.0
def test_impossible_date_is_none_but_raw_text_is_kept():
    res = RegexExtractor().extract(make_doc(date_txt="31/02/2026"))
    assert res.invoice_date is None
    assert res.field_conf["invoice_date"] == ("31/02/2026", 0.0)
# ----------------------------------------------------------------- fields
def test_invalid_invoice_ids_are_captured_verbatim():
    for bad_id in ["ACM 2026/5", "acm-2026-0005", "ACM2026-0005", "ACM-26-5"]:
        res = RegexExtractor().extract(make_doc(no=bad_id))
        assert res.invoice_no == bad_id
        assert res.field_conf["invoice_no"] == (bad_id, 1.0)
def test_missing_invoice_number():
    res = RegexExtractor().extract(make_doc(no=None))
    assert res.invoice_no is None
    assert res.field_conf["invoice_no"] == (None, 0.0)
def test_missing_invoice_date():
    res = RegexExtractor().extract(make_doc(date_txt=None))
    assert res.invoice_date is None
    assert res.field_conf["invoice_date"] == (None, 0.0)
# ------------------------------------------------------------- robustness
def test_empty_text():
    res = RegexExtractor().extract("")
    assert res.signals["text_empty"] is True
    assert res.mean_required_field_confidence == 0.0
    assert res.required_field_coverage == 0.0
    assert res.line_items == []
@pytest.mark.parametrize("mode", ["empty", "truncated", "char_swap", "noise"])
def test_garbled_inputs_never_raise_and_lose_confidence(mode):
    clean_text = render_text(InvoiceFactory(seed=5).clean(), LAYOUTS["C"])
    clean = RegexExtractor().extract(clean_text)
    bad = RegexExtractor().extract(garble(clean_text, mode, random.Random(3)))
    assert "extractor_error" not in bad.signals
    assert bad.mean_required_field_confidence < clean.mean_required_field_confidence
def test_binary_junk_does_not_raise():
    junk = "\x00\x01\x02" * 40 + "\n" * 5 + "\u00e9\u20ac 1,2,3 ::: ---"
    res = RegexExtractor().extract(junk)
    assert "extractor_error" not in res.signals
    assert res.mean_required_field_confidence < 0.5
# ---------------------------------------------- whole generated dataset
def test_dataset_readable_docs_match_ground_truth(dataset):
    out, gts = dataset
    ex = RegexExtractor()
    checked = 0
    for doc_id, gt in gts.items():
        if not gt["fields_readable"]:
            continue
        r = ingest(primary(out, doc_id))          # PDF preferred, like the DB loader
        assert r.ok, (doc_id, r.error_message)
        res = ex.extract(r.text)
        assert "extractor_error" not in res.signals, doc_id
        assert res_values(res) == gt_values(gt), doc_id
        checked += 1
    assert checked == 94
def test_txt_and_pdf_extraction_are_identical(dataset):
    out, gts = dataset
    ex = RegexExtractor()
    compared = 0
    for doc_id, gt in gts.items():
        pdf = out / "invoices" / f"{doc_id}.pdf"
        if not pdf.exists():
            continue
        a = ex.extract(ingest(pdf).text)
        b = ex.extract(ingest(out / "invoices" / f"{doc_id}.txt").text)
        assert res_values(a) == res_values(b), doc_id
        assert {k: v[1] for k, v in a.field_conf.items()} == {k: v[1] for k, v in b.field_conf.items()}
        compared += 1
    assert compared == 94
def test_clean_docs_have_full_coverage_and_high_confidence(dataset):
    out, gts = dataset
    ex = RegexExtractor()
    n = 0
    for doc_id, gt in gts.items():
        if gt["expected_defects"]:
            continue
        res = ex.extract(ingest(primary(out, doc_id)).text)
        assert res.required_field_coverage == 1.0, doc_id
        assert res.mean_required_field_confidence >= 0.98, doc_id
        assert res.signals["recognized_line_ratio"] == 1.0, doc_id
        assert res.signals["junk_ratio"] == 0.0, doc_id
        n += 1
    assert n == 51
def test_ambiguous_defect_docs_get_lower_date_confidence(dataset):
    out, gts = dataset
    ex = RegexExtractor()
    seen = 0
    for doc_id, gt in gts.items():
        if not gt["fields_readable"]:
            continue
        res = ex.extract(ingest(primary(out, doc_id)).text)
        c = res.field_conf["invoice_date"][1]
        if "AMBIGUOUS_DATE" in gt["expected_defects"]:
            assert c == 0.7, doc_id
            seen += 1
        else:
            assert c == (1.0 if gt["fields"]["invoice_date"] else 0.0), doc_id
    assert seen == 5
def test_garbled_docs_do_not_raise_and_are_visible_in_signals(dataset):
    out, gts = dataset
    ex = RegexExtractor()
    n = 0
    for doc_id, gt in gts.items():
        if gt["fields_readable"]:
            continue
        r = ingest(primary(out, doc_id))
        assert r.ok                     # garbled content is readable, not a read failure
        res = ex.extract(r.text)
        assert "extractor_error" not in res.signals
        assert res.mean_required_field_confidence < 1.0
        if gt["render"]["garble_mode"] == "empty":
            assert res.signals["text_empty"] is True
        n += 1
    assert n == 6
# ----------------------------------------------------- interface / fairness
def test_get_extractor_default_and_env(monkeypatch):
    monkeypatch.delenv("EXTRACTOR", raising=False)
    assert get_extractor().name == "regex"
    monkeypatch.setenv("EXTRACTOR", "REGEX")
    assert get_extractor().name == "regex"
def test_get_extractor_unknown_name_raises():
    with pytest.raises(ValueError):
        get_extractor("does-not-exist")
def test_extractor_has_no_layout_parameter():
    assert list(inspect.signature(RegexExtractor.extract).parameters) == ["self", "text"]
def test_regex_source_has_no_heldout_labels():
    import re
    src = Path(inspect.getsourcefile(RegexExtractor)).read_text(encoding="utf-8").lower()
    for label in ["ref no", "dated", "pay by", "supplier", "gross amount",
                  "concession", "igst", "sales invoice"]:
        assert not re.search(rf"\b{re.escape(label)}\b", src), label
def test_extraction_result_can_be_saved_to_the_database(tmp_path, dataset):
    out, gts = dataset
    doc_id = next(k for k, g in gts.items() if not g["expected_defects"])
    conn = connect(tmp_path / "x.db")
    init_db(conn)
    register_document(conn, primary(out, doc_id))
    res = RegexExtractor().extract(ingest(primary(out, doc_id)).text)
    run = get_or_create_run(conn, "regex")
    row = save_invoice(conn, run, doc_id, 1, res, res.mean_required_field_confidence, res.field_conf)
    got = conn.execute("SELECT total_minor FROM invoices WHERE invoice_row_id=?", (row,)).fetchone()
    assert got["total_minor"] == int(res.total.scaleb(2))
    n = conn.execute("SELECT COUNT(*) FROM field_confidence WHERE invoice_row_id=?", (row,)).fetchone()[0]
    assert n == 11
    conn.close()
