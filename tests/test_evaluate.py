import copy
import json
from decimal import Decimal as D
import pytest
from invoice_pipeline import cli
from invoice_pipeline.dataset import write_dataset
from invoice_pipeline.db import (
    connect, get_or_create_run, init_db, load_dataset, load_ground_truth, save_document,
)
from invoice_pipeline.evaluate import (
    compare_lines, evaluate_run, load_queries, outcome, ratio, render_markdown, write_reports,
)
from invoice_pipeline.extractors.regex import RegexExtractor
from invoice_pipeline.generate import InvoiceFactory, ground_truth
from invoice_pipeline.models import Invoice, LineItem
from invoice_pipeline.pipeline import run_pipeline
# ---------------------------------------------------------------- basics
def test_ratio_with_no_eligible_documents_is_none_not_zero():
    assert ratio(0, 0)["rate"] is None
    assert ratio(0, 5)["rate"] == 0.0
    assert ratio(3, 4)["rate"] == 0.75
def test_outcome_three_states():
    assert outcome(None, None) == "correct"          # field genuinely absent
    assert outcome(None, "x") == "wrong"             # extractor invented a value
    assert outcome("x", None) == "missing"
    assert outcome("x", "x") == "correct"
    assert outcome("Acme", "acme") == "wrong"        # strings are not normalised
    assert outcome(0, 0) == "correct" and outcome(0, None) == "missing"
def test_named_queries_are_loaded():
    assert {"documents", "gt_defects_all", "failed_checks", "defect_detection",
            "extra_detections", "review_by_reason", "low_confidence_only"} <= set(load_queries())
def test_compare_lines_is_per_cell_and_by_line_number():
    row = lambda d, q, u, a: {"description": d, "qty": q, "unit_price_minor": u, "amount_minor": a}
    gt = {1: row("A", 1, 100, 100), 2: row("B", 2, 50, 100)}
    cells, doc = compare_lines(gt, {1: row("A", 1, 100, 100), 2: row("B", 2, 50, 100)})
    assert doc == "correct"
    cells, doc = compare_lines(gt, {1: row("A", 1, 100, 100), 2: row("B", 2, 50, 99)})
    assert doc == "wrong" and ("amount_minor", "wrong") in cells
    cells, doc = compare_lines(gt, {})
    assert doc == "missing" and all(o == "missing" for _, o in cells)
    assert compare_lines({}, {})[1] == "correct"
# ------------------------------------------------------ hand-built database
def blank_invoice():
    return Invoice(vendor=None, invoice_no=None, document_type=None, invoice_date=None,
                   due_date=None, currency=None, line_items=[], subtotal=None, discount=None,
                   tax=None, total=None)
@pytest.fixture(scope="module")
def hand(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("hand") / "h.db")
    init_db(conn)
    f = InvoiceFactory(seed=21)
    run = get_or_create_run(conn, "hand")
    def add(n, defects=(), edit=None, garbled=False, review=False, checks=()):
        doc = f"doc_{n:04d}"
        inv = f.clean()
        conn.execute("INSERT INTO documents VALUES (?,?,?,?)", (doc, f"{doc}.txt", "txt", "x"))
        conn.commit()
        gt = ground_truth(doc, inv, list(defects))
        gt["fields_readable"] = not garbled
        load_ground_truth(conn, gt)
        pred = blank_invoice() if garbled else copy.deepcopy(inv)
        if edit:
            edit(pred)
        save_document(conn, run, doc, n, pred, 0.9, {}, list(checks), review)
    add(1)                                                                   # perfect, auto
    add(2, edit=lambda p: setattr(p, "total", p.total + D("1.00")))           # wrong total, auto
    add(3, edit=lambda p: setattr(p, "vendor", None), review=True,
        checks=[("MISSING_FIELD", False, "vendor")])                         # missing vendor, review
    add(4, defects=["TOTAL_MISMATCH"], review=True,
        checks=[("TOTAL_MISMATCH", False, None)])                            # defect caught
    add(5, defects=["SUBTOTAL_MISMATCH"])                                    # defect MISSED, auto
    add(6, defects=["GARBLED_TEXT"], garbled=True, review=True,
        checks=[("GARBLED_TEXT", False, None)])                              # garbled, flagged
    rep = evaluate_run(conn, run)
    yield rep
    conn.close()
def test_hand_counts_and_extraction(hand):
    c = hand["counts"]
    assert (c["documents"], c["processed"], c["failed"]) == (6, 6, 0)
    assert (c["auto_approved"], c["needs_review"], c["garbled"], c["readable_processed"]) == (3, 3, 1, 5)
    fields = hand["extraction"]["fields"]
    assert (fields["vendor"]["correct"], fields["vendor"]["missing"], fields["vendor"]["wrong"]) == (4, 1, 0)
    assert (fields["total_minor"]["correct"], fields["total_minor"]["wrong"]) == (4, 1)
    assert hand["extraction"]["exact_match"] == ratio(3, 5)
    for f in fields.values():                       # garbled doc is excluded from field accuracy
        assert f["correct"] + f["missing"] + f["wrong"] == 5
def test_hand_auto_approved_and_bad_list(hand):
    a = hand["auto_approved"]
    assert a["count"] == 3
    assert a["field_correct"] == ratio(2, 3)
    assert a["defect_free"] == ratio(2, 3)
    bad = {b["doc_id"]: b for b in a["bad"]}
    assert set(bad) == {"doc_0002", "doc_0005"}
    assert bad["doc_0002"]["field_errors"] == ["total_minor=wrong"]
    assert bad["doc_0005"]["field_errors"] == []
    assert bad["doc_0005"]["expected_defects"] == ["SUBTOTAL_MISMATCH"]
def test_hand_catch_metrics(hand):
    k = hand["catch"]
    assert k["review_recall"] == ratio(2, 3)
    assert k["false_review_rate"] == ratio(1, 3)
    assert k["error_catch_rate"] == ratio(2, 3)
    assert k["defect_level"]["readable"]["TOTAL_MISMATCH"] == ratio(1, 1)
    assert k["defect_level"]["readable"]["SUBTOTAL_MISMATCH"] == ratio(0, 1)
    assert k["defect_level"]["garbled"]["GARBLED_TEXT"] == ratio(1, 1)
    extra = [x for x in k["extra_detections"] if x["code"] == "MISSING_FIELD"]
    assert len(extra) == 1 and extra[0]["n"] == 1 and extra[0]["readable"] is True
def test_hand_garbled_document_is_listed_separately(hand):
    g = hand["garbled"]
    assert len(g["documents"]) == 1
    d = g["documents"][0]
    assert d["doc_id"] == "doc_0006" and d["flagged"] and d["garbled_text_fired"]
    assert hand["review"]["by_reason"]
    assert hand["review"]["low_confidence_only"] == 0
def one_doc(tmp_path, inv, pred, review=False):
    conn = connect(tmp_path / "one.db")
    init_db(conn)
    conn.execute("INSERT INTO documents VALUES (?,?,?,?)", ("doc_0001", "doc_0001.txt", "txt", "x"))
    conn.commit()
    load_ground_truth(conn, ground_truth("doc_0001", inv, []))
    run = get_or_create_run(conn, "one")
    save_document(conn, run, "doc_0001", 1, pred, 0.9, {}, [], review)
    rep = evaluate_run(conn, run)
    conn.close()
    return rep
def test_line_item_cells_are_judged_individually(tmp_path):
    inv = InvoiceFactory(seed=3).clean()
    inv.line_items = [LineItem("Chair", 1, D("10.00"), D("10.00")),
                      LineItem("Desk", 2, D("5.00"), D("10.00"))]
    pred = copy.deepcopy(inv)
    pred.line_items = [LineItem("Chairs", 1, D("10.00"), D("10.00"))]   # typo, and row 2 is gone
    rep = one_doc(tmp_path, inv, pred)
    assert rep["extraction"]["fields"]["line_items"]["wrong"] == 1
    cells = rep["extraction"]["line_cells"]
    assert (cells["description"]["correct"], cells["description"]["wrong"], cells["description"]["missing"]) == (0, 1, 1)
    for f in ("qty", "unit_price_minor", "amount_minor"):
        assert (cells[f]["correct"], cells[f]["missing"], cells[f]["wrong"]) == (1, 1, 0)
def test_zero_denominators_are_na_not_zero(tmp_path):
    inv = InvoiceFactory(seed=4).clean()
    rep = one_doc(tmp_path, inv, copy.deepcopy(inv))     # perfect, auto-approved, no defects anywhere
    k = rep["catch"]
    assert k["review_recall"]["rate"] is None            # nothing was supposed to be reviewed
    assert k["false_review_rate"]["rate"] == 0.0         # something was eligible and none went wrong
    assert k["error_catch_rate"]["rate"] is None
    assert json.loads(json.dumps(rep))["catch"]["review_recall"]["rate"] is None
    assert "n/a" in render_markdown(rep)
# ------------------------------------------------------ dataset-level checks
class BadVendor:
    """Deliberately wrong extractor: confidently corrupts the vendor, which no validator checks."""
    name = "bad-vendor"
    def __init__(self):
        self.inner = RegexExtractor()
    def extract(self, text):
        res = self.inner.extract(text)
        if res.vendor is not None:
            res.vendor += " X"
        return res
@pytest.fixture(scope="module")
def dev(tmp_path_factory):
    root = tmp_path_factory.mktemp("dev")
    data = root / "data"
    write_dataset(data)
    db = root / "dev.db"
    summary = run_pipeline(data, db, "regex")
    conn = connect(db)
    init_db(conn)
    load_dataset(conn, data)
    rep = evaluate_run(conn, summary.run_id, data)
    yield conn, rep, data, summary
    conn.close()
def test_gt_line_items_are_loaded(dev):
    conn, _, data, _ = dev
    expected = sum(len(json.loads(p.read_text(encoding="utf-8"))["fields"]["line_items"])
                   for p in (data / "ground_truth").glob("doc_*.json"))
    assert conn.execute("SELECT COUNT(*) FROM gt_line_items").fetchone()[0] == expected > 100
def test_dev_dataset_counts_match_pipeline_summary(dev):
    _, rep, _, s = dev
    c = rep["counts"]
    assert (c["documents"], c["processed"], c["failed"]) == (100, 100, 0)
    assert (c["auto_approved"], c["needs_review"]) == (s.auto_approved, s.needs_review) == (51, 49)
def test_dev_dataset_metrics(dev):
    _, rep, _, _ = dev
    e, a, k = rep["extraction"], rep["auto_approved"], rep["catch"]
    assert e["exact_match"] == ratio(94, 94)      # circular on purpose, see report limitations
    assert all(f["accuracy"]["rate"] == 1.0 for f in e["fields"].values())
    assert a["field_correct"] == ratio(51, 51) and a["defect_free"] == ratio(51, 51) and a["bad"] == []
    assert k["review_recall"] == ratio(49, 49) and k["false_review_rate"] == ratio(0, 51)
    assert k["defect_level"]["readable"] and all(r["rate"] == 1.0 for r in k["defect_level"]["readable"].values())
def test_dev_dataset_garbled_section(dev):
    _, rep, _, _ = dev
    g = rep["garbled"]
    assert len(g["documents"]) == 6 and all(d["flagged"] for d in g["documents"])
    assert {m: v["n"] for m, v in g["by_mode"].items()} == {"char_swap": 2, "truncated": 2, "empty": 1, "noise": 1}
    # whether GARBLED_TEXT fires for char_swap / truncated is a finding, deliberately not asserted
def test_bad_extractor_is_exposed_by_the_evaluation(dev, tmp_path):
    _, _, data, _ = dev
    db = tmp_path / "bad.db"
    s = run_pipeline(data, db, extractor=BadVendor())
    conn = connect(db)
    init_db(conn)
    load_dataset(conn, data)
    rep = evaluate_run(conn, s.run_id, data)
    conn.close()
    a = rep["auto_approved"]
    assert a["count"] > 0 and len(a["bad"]) == a["count"]
    assert a["field_correct"]["n"] == 0 and a["field_correct"]["rate"] == 0.0
    assert rep["extraction"]["fields"]["vendor"]["correct"] == 0
    assert rep["catch"]["error_catch_rate"]["rate"] < 1.0
def test_evaluation_does_not_touch_ground_truth(dev):
    conn, _, data, s = dev
    tables = ("gt_documents", "gt_defects", "gt_line_items")
    snap = lambda: [[tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in tables]
    before = snap()
    evaluate_run(conn, s.run_id, data)
    assert snap() == before
def test_unknown_run_and_missing_ground_truth_raise(dev, tmp_path):
    conn, _, data, _ = dev
    with pytest.raises(ValueError):
        evaluate_run(conn, 999)
    db = tmp_path / "nogt.db"
    s = run_pipeline(data, db, "regex")
    c2 = connect(db)
    with pytest.raises(ValueError, match="ground truth"):
        evaluate_run(c2, s.run_id)
    c2.close()
def test_reports_are_written_with_all_sections(dev, tmp_path):
    _, rep, _, _ = dev
    md, js = write_reports(rep, tmp_path / "reports", "sample")
    assert md.name == "eval_sample_regex.md" and js.name == "eval_sample_regex.json"
    text = md.read_text(encoding="utf-8")
    for section in ("Headline", "Extraction accuracy", "Bad auto-approvals", "Defect catch",
                    "Garbled-input stress test", "Limitations"):
        assert section in text, section
    assert json.loads(js.read_text(encoding="utf-8"))["run"]["extractor"] == "regex"
def test_cli_evaluate_end_to_end(tmp_path, capsys):
    data, db, out = tmp_path / "d", tmp_path / "c.db", tmp_path / "rep"
    assert cli.main(["generate", "--out", str(data)]) == 0
    assert cli.main(["run", "--data", str(data), "--db", str(db)]) == 0
    capsys.readouterr()
    assert cli.main(["evaluate", "--data", str(data), "--db", str(db), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Review recall" in printed and "report written" in printed
    assert (out / "eval_d_regex.md").exists() and (out / "eval_d_regex.json").exists()
