import sqlite3
from decimal import Decimal
import pytest
from invoice_pipeline.dataset import write_dataset
from invoice_pipeline.db import (
    connect, finalize_invoice, from_minor, get_or_create_run, init_db, load_dataset,
    load_ground_truth, record_validation, save_invoice, to_minor,
)
from invoice_pipeline.generate import InvoiceFactory, ground_truth
from invoice_pipeline.models import LineItem
@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    yield c
    c.close()
def add_doc(conn, doc_id="doc_0001"):
    conn.execute("INSERT INTO documents VALUES (?,?,?,?)", (doc_id, f"{doc_id}.txt", "txt", "x"))
    conn.commit()
def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
# ---- money
def test_money_roundtrip():
    assert to_minor(Decimal("1234.50")) == 123450
    assert from_minor(123450) == Decimal("1234.50")
    assert to_minor("0.01") == 1 and to_minor(Decimal("-99.99")) == -9999
    assert to_minor(Decimal("0.00")) == 0 and to_minor(7) == 700
def test_money_rejects_fractional_paise():
    for bad in ("1.005", Decimal("0.001"), "12.345"):
        with pytest.raises(ValueError):
            to_minor(bad)
def test_money_rejects_float_and_nonfinite():
    with pytest.raises(TypeError):
        to_minor(1234.5)
    for bad in ("NaN", "Infinity", "abc"):
        with pytest.raises(ValueError):
            to_minor(bad)
# ---- schema and constraints
def test_schema_tables_exist_and_foreign_keys_on(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "documents", "invoices", "line_items", "field_confidence",
            "validation_results", "review_queue", "gt_documents", "gt_defects"} <= names
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
def test_run_key_makes_run_idempotent(conn):
    a = get_or_create_run(conn, "regex", run_key="k1")
    assert get_or_create_run(conn, "regex", run_key="k1") == a
    assert get_or_create_run(conn, "regex", run_key="k2") != a
    assert get_or_create_run(conn, "regex") != get_or_create_run(conn, "regex")
def test_same_doc_twice_in_one_run_is_rejected(conn):
    add_doc(conn)
    run = get_or_create_run(conn, "regex")
    inv = InvoiceFactory(seed=1).clean()
    save_invoice(conn, run, "doc_0001", 1, inv)
    with pytest.raises(sqlite3.IntegrityError):
        save_invoice(conn, run, "doc_0001", 2, inv)
    assert count(conn, "invoices") == 1
def test_foreign_key_violation_is_rejected(conn):
    run = get_or_create_run(conn, "regex")
    with pytest.raises(sqlite3.IntegrityError):
        save_invoice(conn, run, "doc_9999", 1, InvoiceFactory(seed=1).clean())
    assert count(conn, "invoices") == 0
def test_save_invoice_rolls_back_on_bad_money(conn):
    add_doc(conn)
    run = get_or_create_run(conn, "regex")
    inv = InvoiceFactory(seed=1).clean()
    inv.line_items.append(LineItem("bad", 1, Decimal("1.005"), Decimal("1.005")))
    with pytest.raises(ValueError):
        save_invoice(conn, run, "doc_0001", 1, inv)
    assert count(conn, "invoices") == 0 and count(conn, "line_items") == 0
def test_status_requires_processed(conn):
    add_doc(conn)
    run = get_or_create_run(conn, "regex")
    row = save_invoice(conn, run, "doc_0001", 1, InvoiceFactory(seed=1).clean())
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE invoices SET status='auto_approved' WHERE invoice_row_id=?", (row,))
    conn.rollback()
    assert conn.execute("SELECT processing_status, status FROM invoices").fetchone()[:] == ("pending", None)
def test_validation_rejects_unknown_code_and_stores_severity(conn):
    add_doc(conn)
    run = get_or_create_run(conn, "regex")
    row = save_invoice(conn, run, "doc_0001", 1, InvoiceFactory(seed=1).clean())
    with pytest.raises(ValueError):
        record_validation(conn, row, [("NOT_A_CODE", False, None)])
    record_validation(conn, row, [("TOTAL_MISMATCH", False, "off by 8.88"),
                                  ("AMBIGUOUS_DATE", True, None)])
    got = {r["check_code"]: (r["severity"], r["passed"]) for r in
           conn.execute("SELECT * FROM validation_results")}
    assert got == {"TOTAL_MISMATCH": ("hard", 0), "AMBIGUOUS_DATE": ("soft", 1)}
def test_finalize_creates_review_queue_row_only_when_needed(conn):
    add_doc(conn, "doc_0001")
    add_doc(conn, "doc_0002")
    run = get_or_create_run(conn, "regex")
    f = InvoiceFactory(seed=1)
    a = save_invoice(conn, run, "doc_0001", 1, f.clean())
    b = save_invoice(conn, run, "doc_0002", 2, f.clean())
    finalize_invoice(conn, a, needs_review=True)
    finalize_invoice(conn, b, needs_review=False)
    assert [r[0] for r in conn.execute("SELECT invoice_row_id FROM review_queue")] == [a]
    assert conn.execute("SELECT status FROM invoices WHERE invoice_row_id=?", (b,)).fetchone()[0] == "auto_approved"
# ---- ground truth
def test_ground_truth_loader_rejects_inconsistent_review_flag(conn):
    add_doc(conn)
    gt = ground_truth("doc_0001", InvoiceFactory(seed=1).clean(), ["TOTAL_MISMATCH"])
    gt["expected_review"] = False
    with pytest.raises(ValueError):
        load_ground_truth(conn, gt)
    assert count(conn, "gt_documents") == 0
def test_ground_truth_loader_rejects_unknown_defect(conn):
    add_doc(conn)
    gt = ground_truth("doc_0001", InvoiceFactory(seed=1).clean())
    gt["expected_defects"] = ["BOGUS"]
    with pytest.raises(ValueError):
        load_ground_truth(conn, gt)
def test_pipeline_writes_never_touch_ground_truth(conn):
    add_doc(conn)
    inv = InvoiceFactory(seed=1).clean()
    load_ground_truth(conn, ground_truth("doc_0001", inv, ["TOTAL_MISMATCH"]))
    snap = lambda: ([tuple(r) for r in conn.execute("SELECT * FROM gt_documents")],
                    [tuple(r) for r in conn.execute("SELECT * FROM gt_defects")])
    before = snap()
    run = get_or_create_run(conn, "regex")
    row = save_invoice(conn, run, "doc_0001", 1, inv, 0.9, {"total": ("Total: 1", 0.9)})
    record_validation(conn, row, [("TOTAL_MISMATCH", True, None)])   # detector says "fine"
    finalize_invoice(conn, row, needs_review=False)
    assert snap() == before
    assert conn.execute("SELECT COUNT(*) FROM gt_defects WHERE defect_code='TOTAL_MISMATCH'").fetchone()[0] == 1
def test_load_full_generated_dataset(conn, tmp_path):
    manifest = write_dataset(tmp_path / "data")
    assert load_dataset(conn, tmp_path / "data") == 100
    assert count(conn, "gt_documents") == 100
    assert conn.execute("SELECT SUM(expected_review) FROM gt_documents").fetchone()[0] == manifest["expected_in_review"]
    defects = {r["defect_code"]: r["c"] for r in conn.execute(
        "SELECT defect_code, COUNT(*) c FROM gt_defects GROUP BY defect_code")}
    assert defects == manifest["defect_counts"]
    fmts = {r["file_format"]: r["c"] for r in conn.execute(
        "SELECT file_format, COUNT(*) c FROM documents GROUP BY file_format")}
    assert fmts == {"pdf": 94, "txt": 6}
    assert load_dataset(conn, tmp_path / "data") == 100      # reload is idempotent
    assert count(conn, "gt_documents") == 100
def test_source_choice_does_not_change_ground_truth_and_sha_is_file_bytes(tmp_path):
    import hashlib
    write_dataset(tmp_path / "data")
    c = connect(tmp_path / "a.db"); init_db(c)
    load_dataset(c, tmp_path / "data")
    snap = lambda: ([tuple(r) for r in c.execute("SELECT * FROM gt_documents ORDER BY doc_id")],
                    [tuple(r) for r in c.execute("SELECT * FROM gt_defects ORDER BY doc_id, defect_code")])
    before = snap()
    # sha256 must equal the hash of the registered file's raw bytes
    for r in c.execute("SELECT source_file, sha256 FROM documents"):
        from pathlib import Path
        assert hashlib.sha256(Path(r["source_file"]).read_bytes()).hexdigest() == r["sha256"]
    # a garbled doc has no PDF, so it is registered as txt and still expects review
    row = c.execute("SELECT d.doc_id, d.file_format, g.expected_review FROM documents d "
                    "JOIN gt_documents g USING (doc_id) JOIN gt_defects x USING (doc_id) "
                    "WHERE x.defect_code='GARBLED_TEXT' LIMIT 1").fetchone()
    assert row["file_format"] == "txt" and row["expected_review"] == 1
    # reloading must not alter ground truth
    load_dataset(c, tmp_path / "data")
    assert snap() == before
    c.close()
