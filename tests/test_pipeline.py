import json
import shutil
import sqlite3
from decimal import Decimal as D
import pytest
import invoice_pipeline.pipeline as pipeline_mod
from invoice_pipeline import cli
from invoice_pipeline.dataset import write_dataset
from invoice_pipeline.db import connect, init_db, load_dataset
from invoice_pipeline.extractors.base import ExtractedItem
from invoice_pipeline.extractors.regex import RegexExtractor
from invoice_pipeline.pipeline import PipelineFatalError, run_pipeline
class Flaky:
    """Regex extractor that misbehaves on one chosen call (1-based)."""
    name = "regex"
    def __init__(self, raise_on=None, bad_money_on=None):
        self.inner, self.calls = RegexExtractor(), 0
        self.raise_on, self.bad_money_on = raise_on, bad_money_on
    def extract(self, text):
        self.calls += 1
        if self.calls == self.raise_on:
            raise RuntimeError("boom")
        res = self.inner.extract(text)
        if self.calls == self.bad_money_on:      # fails during the DB write, after earlier rows
            res.line_items.append(ExtractedItem("bad", 1, D("1.005"), D("1.005")))
        return res
def q(db, sql, *args):
    c = connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()
def scalar(db, sql, *args):
    return q(db, sql, *args)[0][0]
@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    d = tmp_path_factory.mktemp("pipe") / "data"
    manifest = write_dataset(d)
    gts = {p.stem: json.loads(p.read_text(encoding="utf-8"))
           for p in sorted((d / "ground_truth").glob("doc_*.json"))}
    return d, manifest, gts
@pytest.fixture(scope="module")
def baseline(ds, tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "base.db"
    return db, run_pipeline(ds[0], db, "regex")
def corruptible(gts):
    """A clean, non-duplicate document with a PDF, safe to damage."""
    linked = {g["duplicate_of"] for g in gts.values()} | {g["conflicts_with"] for g in gts.values()}
    return next(k for k, g in sorted(gts.items())
                if not g["expected_defects"] and k not in linked and g["fields_readable"])
def damaged_copy(ds, tmp_path):
    d = tmp_path / "data"
    shutil.copytree(ds[0], d)
    victim = corruptible(ds[2])
    (d / "invoices" / f"{victim}.pdf").write_bytes(b"not a pdf")
    return d, victim
# ---------------------------------------------------------- end to end
def test_end_to_end_counts(ds, baseline):
    db, s = baseline
    assert s.total_documents == 100 and s.processed == 100 and s.failed == []
    assert s.auto_approved + s.needs_review == 100
    assert s.needs_review == ds[1]["expected_in_review"]
    assert scalar(db, "SELECT COUNT(*) FROM invoices") == 100
def test_review_queue_matches_needs_review_count(baseline):
    db, s = baseline
    assert scalar(db, "SELECT COUNT(*) FROM review_queue") == s.needs_review
    assert scalar(db, "SELECT COUNT(*) FROM invoices WHERE status='needs_review'") == s.needs_review
    assert scalar(db, "SELECT COUNT(*) FROM review_queue q JOIN invoices i USING (invoice_row_id) "
                      "WHERE i.status = 'auto_approved'") == 0
def test_duplicates_flagged_only_on_the_later_document(ds, baseline):
    db, _ = baseline
    flagged = {r[0] for r in q(db, "SELECT i.doc_id FROM invoices i JOIN validation_results v "
                                   "USING (invoice_row_id) WHERE v.check_code='DUPLICATE_INVOICE' "
                                   "AND v.passed=0")}
    expected = {k for k, g in ds[2].items() if g["duplicate_of"]}
    assert flagged == expected and len(expected) == 3
    originals = {g["duplicate_of"] for g in ds[2].values() if g["duplicate_of"]}
    assert not (originals & flagged)
def test_every_processed_invoice_has_its_validation_rows(baseline):
    db, _ = baseline
    bad = scalar(db, "SELECT COUNT(*) FROM invoices i WHERE processing_status='processed' AND "
                     "NOT EXISTS (SELECT 1 FROM validation_results v "
                     "WHERE v.invoice_row_id = i.invoice_row_id)")
    assert bad == 0
    assert scalar(db, "SELECT COUNT(*) FROM field_confidence") == 100 * 11
# --------------------------------------------------------- idempotency
def test_rerun_with_same_run_key_creates_no_duplicates(ds, tmp_path):
    db = tmp_path / "k.db"
    a = run_pipeline(ds[0], db, "regex", run_key="r1")
    n = scalar(db, "SELECT COUNT(*) FROM invoices")
    b = run_pipeline(ds[0], db, "regex", run_key="r1")
    assert b.run_id == a.run_id and b.resumed_skipped == 100
    assert scalar(db, "SELECT COUNT(*) FROM invoices") == n == 100
    assert scalar(db, "SELECT COUNT(*) FROM review_queue") == a.needs_review == b.needs_review
    assert scalar(db, "SELECT COUNT(*) FROM runs") == 1
def test_without_run_key_each_execution_is_a_new_run(ds, tmp_path):
    db = tmp_path / "n.db"
    a = run_pipeline(ds[0], db, "regex")
    b = run_pipeline(ds[0], db, "regex")
    assert a.run_id != b.run_id
    assert scalar(db, "SELECT COUNT(*) FROM invoices") == 200
# ------------------------------------------------------------- failures
def test_corrupt_pdf_is_failed_not_queued_and_batch_continues(ds, tmp_path):
    d, victim = damaged_copy(ds, tmp_path)
    db = tmp_path / "f.db"
    s = run_pipeline(d, db, "regex")
    assert s.processed == 99 and len(s.failed) == 1
    assert (s.failed[0].doc_id, s.failed[0].code) == (victim, "PDF_READ_ERROR")
    row = q(db, "SELECT processing_status, status, error_message, invoice_row_id FROM invoices "
                "WHERE doc_id=?", victim)[0]
    assert row["processing_status"] == "failed" and row["status"] is None
    assert row["error_message"].startswith("PDF_READ_ERROR")
    assert scalar(db, "SELECT COUNT(*) FROM review_queue WHERE invoice_row_id=?",
                  row["invoice_row_id"]) == 0
    assert scalar(db, "SELECT COUNT(*) FROM invoices WHERE status='auto_approved' "
                      "AND processing_status='failed'") == 0
def test_unexpected_exception_has_its_own_code_and_batch_continues(ds, tmp_path):
    db = tmp_path / "u.db"
    s = run_pipeline(ds[0], db, extractor=Flaky(raise_on=3))
    assert s.processed == 99 and len(s.failed) == 1
    assert (s.failed[0].doc_id, s.failed[0].code) == ("doc_0003", "UNEXPECTED_ERROR")
    assert "boom" in s.failed[0].message
def test_failure_during_save_rolls_back_the_whole_document(ds, tmp_path):
    db = tmp_path / "a.db"
    s = run_pipeline(ds[0], db, extractor=Flaky(bad_money_on=1))
    assert [f.doc_id for f in s.failed] == ["doc_0001"] and s.failed[0].code == "UNEXPECTED_ERROR"
    row = q(db, "SELECT invoice_row_id, processing_status, status FROM invoices "
                "WHERE doc_id='doc_0001'")[0]
    assert row["processing_status"] == "failed" and row["status"] is None
    rid = row["invoice_row_id"]
    for table in ("line_items", "field_confidence", "validation_results", "review_queue"):
        assert scalar(db, f"SELECT COUNT(*) FROM {table} WHERE invoice_row_id=?", rid) == 0, table
    assert scalar(db, "SELECT COUNT(*) FROM invoices WHERE doc_id='doc_0001'") == 1
def test_resume_retries_failed_documents(ds, tmp_path):
    db = tmp_path / "r.db"
    first = run_pipeline(ds[0], db, extractor=Flaky(raise_on=3), run_key="x")
    assert len(first.failed) == 1
    second = run_pipeline(ds[0], db, "regex", run_key="x")
    assert second.run_id == first.run_id and second.resumed_skipped == 99
    assert second.failed == [] and second.processed == 100
    assert scalar(db, "SELECT COUNT(*) FROM invoices") == 100
    assert scalar(db, "SELECT COUNT(*) FROM review_queue") == second.needs_review
def test_failure_that_cannot_be_recorded_is_fatal(ds, tmp_path, monkeypatch):
    d, _ = damaged_copy(ds, tmp_path)
    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(pipeline_mod, "save_failed", broken)
    with pytest.raises(PipelineFatalError):
        run_pipeline(d, tmp_path / "x.db", "regex")
# --------------------------------------------------------- ground truth
def test_ground_truth_is_untouched_by_a_run(ds, tmp_path):
    db = tmp_path / "g.db"
    c = connect(db)
    init_db(c)
    load_dataset(c, ds[0])
    snap = lambda: ([tuple(r) for r in c.execute("SELECT * FROM gt_documents ORDER BY doc_id")],
                    [tuple(r) for r in c.execute("SELECT * FROM gt_defects ORDER BY doc_id, defect_code")])
    before = snap()
    run_pipeline(ds[0], db, "regex")
    assert snap() == before
    c.close()
# ------------------------------------------------------------------ CLI
def test_cli_generate_run_and_review_queue(tmp_path, capsys):
    data, db = tmp_path / "d", tmp_path / "c.db"
    assert cli.main(["generate", "--out", str(data)]) == 0
    assert cli.main(["run", "--data", str(data), "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "processed     100" in out and "failed        0" in out
    assert cli.main(["review-queue", "--db", str(db), "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "run 1: 5 shown" in out and "doc_" in out
def test_cli_db_default_comes_from_environment(tmp_path, monkeypatch):
    target = tmp_path / "env.db"
    monkeypatch.setenv("DB_PATH", str(target))
    assert cli.main(["init-db"]) == 0
    assert target.exists()
