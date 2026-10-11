"""Self-contained demonstration of the whole pipeline, in six stages.
Everything runs in a temporary folder with freshly generated data, so the
caller's database and reports are untouched (pass --out to keep the reports).
Every number printed comes from the database or the evaluation reports; nothing
is typed in. Output is plain ASCII. Colour is off unless asked for.
"""
from __future__ import annotations
import os
import tempfile
from pathlib import Path
from .compare import EXACT, REVIEW, build_comparison, write_comparison
from .dataset import write_dataset
from .db import connect, from_minor, init_db, load_dataset
from .evaluate import evaluate_run, headline, pct, write_reports
from .extractors.llm import LLMExtractor
from .extractors.mock_llm import FaultyClient, oracle_client_from_dataset
from .extractors.regex import RegexExtractor
from .pipeline import run_pipeline
from .rules import SEVERITY
RUN_KEY = "demo"
WIDTH = 72
def use_color(flag: bool) -> bool:
    return bool(flag) and not os.environ.get("NO_COLOR")
class Style:
    CODES = {"green": "32", "red": "31", "yellow": "33", "bold": "1"}
    def __init__(self, color: bool = False):
        self.color = color
    def paint(self, text: str, name: str) -> str:
        return f"\x1b[{self.CODES[name]}m{text}\x1b[0m" if self.color else text
    def ok(self, msg: str) -> str:
        return f"{self.paint('[ OK ]', 'green')} {msg}"
    def warn(self, msg: str) -> str:
        return f"{self.paint('[WARN]', 'yellow')} {msg}"
    def fail(self, msg: str) -> str:
        return f"{self.paint('[FAIL]', 'red')} {msg}"
    def head(self, msg: str) -> str:
        return self.paint(msg, "bold")
class _CrashOnCall:
    """Regex extractor that raises on one chosen call, to show the batch carrying on."""
    name = "regex-crash-demo"
    def __init__(self, inner, n: int):
        self.inner, self.n, self.calls = inner, n, 0
    def extract(self, text):
        self.calls += 1
        if self.calls == self.n:
            raise RuntimeError("simulated extractor crash")
        return self.inner.extract(text)
def _evaluate(data, db, run_id, reports):
    conn = connect(db)
    try:
        init_db(conn)
        load_dataset(conn, data)
        rep = evaluate_run(conn, run_id, data)
    finally:
        conn.close()
    write_reports(rep, reports, Path(data).name)
    return rep
def _money(minor) -> str:
    return "n/a" if minor is None else f"INR {from_minor(minor):,.2f}"
def run_demo(out=None, color: bool = False, verbose: bool = False, seed: int = 42) -> int:
    st = Style(use_color(color))
    problems = 0
    p = print
    p(st.head("INVOICE INFORMATION PIPELINE"))
    p("Synthetic data | Python | SQLite | regex extraction, scripted LLM client")
    p("Runs in a temporary folder; your database and reports are not touched.")
    p("It runs the pipeline several times, so allow a minute or two.")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        reports = Path(out) if out else root / "reports"
        # ------------------------------------------------ 1 data generation
        p("\n" + st.head("01 / DATA GENERATION") + "\n" + "-" * WIDTH)
        sample = root / "sample"
        manifest = write_dataset(sample, seed)
        dev_db = root / "dev.db"
        conn = connect(dev_db)
        init_db(conn)
        load_dataset(conn, sample)
        gt_n = conn.execute("SELECT COUNT(*) FROM gt_documents").fetchone()[0]
        conn.close()
        p(st.ok(f"Dataset generated (seed {seed}): {manifest['total_documents']} documents, "
                "text and PDF"))
        p(st.ok(f"Ground truth stored separately: {gt_n} documents"))
        p(f"       clean {manifest['clean_documents']}, review-worthy "
          f"{manifest['expected_in_review']}")
        expected = set(SEVERITY) - {"LOW_CONFIDENCE"}   # LOW_CONFIDENCE is detected, never injected
        missing = sorted(expected - set(manifest["defect_counts"]))
        if missing:
            p(st.warn("Defect types with no example: " + ", ".join(missing)))
        else:
            p(st.ok(f"Every injected defect type is present ({len(manifest['defect_counts'])} types)"))
        # ------------------------------------------------ 2 pipeline run
        p("\n" + st.head("02 / PIPELINE EXECUTION (regex extractor)") + "\n" + "-" * WIDTH)
        s1 = run_pipeline(sample, dev_db, extractor_name="regex", run_key=RUN_KEY)
        p(st.ok(f"Processed {s1.processed} of {s1.total_documents} documents"))
        p(f"       auto-approved {s1.auto_approved}, needs review {s1.needs_review}, "
          f"failed {len(s1.failed)}")
        if s1.failed:
            problems += 1
            for f in s1.failed:
                p(st.fail(f"{f.doc_id} {f.code} {f.message}"))
        s2 = run_pipeline(sample, dev_db, extractor_name="regex", run_key=RUN_KEY)
        new = s2.total_documents - s2.resumed_skipped
        conn = connect(dev_db)
        rows = conn.execute("SELECT COUNT(*) FROM invoices WHERE run_id = ?", (s1.run_id,)).fetchone()[0]
        conn.close()
        p(st.ok(f"Re-run with the same run key: {new} new, {s2.resumed_skipped} already done and "
                f"skipped; invoice rows still {rows}"))
        # ------------------------------------------------ 3 review queue
        p("\n" + st.head("03 / HUMAN REVIEW QUEUE") + "\n" + "-" * WIDTH)
        conn = connect(dev_db)
        queue = conn.execute(
            "SELECT i.invoice_row_id, i.doc_id, i.invoice_no FROM review_queue q "
            "JOIN invoices i ON i.invoice_row_id = q.invoice_row_id "
            "WHERE i.run_id = ? ORDER BY i.processing_order", (s1.run_id,)).fetchall()
        sev = {r["severity"]: r["c"] for r in conn.execute(
            "SELECT v.severity, COUNT(DISTINCT v.invoice_row_id) c FROM validation_results v "
            "JOIN invoices i ON i.invoice_row_id = v.invoice_row_id "
            "WHERE i.run_id = ? AND v.passed = 0 GROUP BY v.severity", (s1.run_id,))}
        entries = []
        for r in queue:
            reasons = [(x["check_code"], x["severity"]) for x in conn.execute(
                "SELECT check_code, severity FROM validation_results "
                "WHERE invoice_row_id = ? AND passed = 0 ORDER BY check_code", (r["invoice_row_id"],))]
            entries.append((r["doc_id"], r["invoice_no"] or "-", reasons))
        conn.close()
        p(st.warn(f"{len(queue)} documents waiting for review"))
        p("       documents with at least one failed check, by severity: "
          + (", ".join(f"{k} {sev[k]}" for k in ("hard", "soft", "uncertain") if k in sev) or "none"))
        shown, seen = [], set()
        for e in entries:
            if verbose or (e[2] and e[2][0][0] not in seen and len(shown) < 8):
                shown.append(e)
                if e[2]:
                    seen.add(e[2][0][0])
        p("       " + ("all documents:" if verbose else "a sample, one per first reason:"))
        for doc, no, reasons in shown:
            p(f"       {doc}  {no:<16} " + ", ".join(f"{c} [{s}]" for c, s in reasons))
        # ------------------------------------------------ 4 evaluation
        p("\n" + st.head("04 / EVALUATION (separate metrics, never one accuracy number)")
          + "\n" + "-" * WIDTH)
        dev = _evaluate(sample, dev_db, s1.run_id, reports)
        p(headline(dev))
        bad = dev["auto_approved"]["bad"]
        p((st.ok if not bad else st.warn)(f"Bad auto-approvals: {len(bad)}"))
        modes = dev["garbled"]["by_mode"]
        if modes:
            p("       garbled-input stress test (documents / sent to review / GARBLED_TEXT fired):")
            for m, v in sorted(modes.items()):
                p(f"         {m:<10} {v['n']} / {v['flagged']} / {v['garbled_text_fired']}")
        p("       dev numbers are circular (same author for generator and regex); see held-out below")
        # ------------------------------------------------ 5 comparison
        p("\n" + st.head("05 / COMPARISON: development vs held-out layout") + "\n" + "-" * WIDTH)
        held = root / "heldout"
        write_dataset(held, 7, layouts=["E"])
        hs = run_pipeline(held, root / "held.db", extractor_name="regex")
        _evaluate(held, root / "held.db", hs.run_id, reports)
        oracle = oracle_client_from_dataset(sample)
        ms = run_pipeline(sample, root / "mock.db", extractor=LLMExtractor(oracle))
        _evaluate(sample, root / "mock.db", ms.run_id, reports)
        fault = {}
        for mode in ("wrong_vendor", "wrong_total"):
            db = root / f"{mode}.db"
            fs = run_pipeline(sample, db, extractor=LLMExtractor(FaultyClient(oracle, mode)))
            fault[mode] = (fs, db, _evaluate(sample, db, fs.run_id, reports))
        cmp = build_comparison(reports)
        md, js = write_comparison(cmp, reports)
        m = cmp["metrics"]
        p(f"       {'regex':<20}{'development':>18}{'held-out':>18}")
        for key, label in ((EXACT, "exact-match documents"), (REVIEW, "review rate")):
            p(f"       {label:<20}{pct(m['dev'][key]):>18}{pct(m['heldout'][key]):>18}")
        hf = cmp["fields"]
        gaps = [f["field"] for f in hf if f["heldout"]["missing"] > 0]
        wrong = sum(f["heldout"]["wrong"] for f in hf)
        p(f"       held-out: fields with missing values: {', '.join(gaps) or 'none'}; "
          f"wrong values: {wrong}")
        for line in cmp["finding"]["reading"][:2]:
            p("       " + line)
        mr = cmp["metrics"]["mock"][EXACT]
        p(st.warn(f"Plumbing check, not a model result: scripted client exact match {pct(mr)} "
                  "(it answers from the ground truth)"))
        n_disc = sum(len(v) for v in cmp["integrity"].values())
        if n_disc:
            problems += 1
            p(st.fail(f"{n_disc} report integrity discrepancies"))
        else:
            p(st.ok("Report integrity: counts are internally consistent"))
        p(f"       comparison report: {md}")
        p(f"                          {js}")
        if not out:
            p("       (temporary files; use --out DIR to keep them)")
        # ------------------------------------------------ 6 failures
        p("\n" + st.head("06 / FAILURE WALKTHROUGH") + "\n" + "-" * WIDTH)
        conn = connect(dev_db)
        row = conn.execute(
            "SELECT i.invoice_row_id, i.doc_id, i.status, i.subtotal_minor, i.discount_minor, "
            "i.tax_minor, i.total_minor, v.detail FROM invoices i JOIN validation_results v "
            "ON v.invoice_row_id = i.invoice_row_id WHERE i.run_id = ? "
            "AND v.check_code = 'TOTAL_MISMATCH' AND v.passed = 0 ORDER BY i.doc_id LIMIT 1",
            (s1.run_id,)).fetchone()
        p(st.head("Case 1: a printed total that does not add up (caught)"))
        if row is None:
            p(st.warn("no TOTAL_MISMATCH document found in this run"))
        else:
            calc = (from_minor(row["subtotal_minor"]) - from_minor(row["discount_minor"] or 0)
                    + from_minor(row["tax_minor"]))
            printed = from_minor(row["total_minor"])
            queued = conn.execute("SELECT COUNT(*) FROM review_queue WHERE invoice_row_id = ?",
                                  (row["invoice_row_id"],)).fetchone()[0]
            truth = [r[0] for r in conn.execute(
                "SELECT defect_code FROM gt_defects WHERE doc_id = ?", (row["doc_id"],))]
            p(f"       document:                     {row['doc_id']}")
            p(f"       subtotal - discount + tax:    INR {calc:,.2f}")
            p(f"       total printed on invoice:     INR {printed:,.2f}")
            p(f"       difference:                   INR {printed - calc:,.2f}")
            p(f"       validation:                   FAILED (TOTAL_MISMATCH)")
            p(f"       routing:                      {row['status'].upper()}, "
              f"in review queue: {'yes' if queued else 'NO'}")
            p(f"       ground truth defects:         {', '.join(truth) or 'none'}")
        conn.close()
        fs, db, rep = fault["wrong_vendor"]
        p("\n" + st.head("Case 2: a confidently wrong vendor (NOT caught)"))
        ex = next((b for b in rep["auto_approved"]["bad"]
                   if b["decision"] == "auto_approved" and "vendor=wrong" in b["field_errors"]), None)
        c2 = connect(db)
        if ex:
            r = c2.execute("SELECT i.vendor, g.vendor AS gv, i.confidence, i.status FROM invoices i "
                           "JOIN gt_documents g ON g.doc_id = i.doc_id WHERE i.run_id = ? "
                           "AND i.doc_id = ?", (fs.run_id, ex["doc_id"])).fetchone()
            p(f"       document:                     {ex['doc_id']}")
            p(f"       vendor extracted:             {r['vendor']}")
            p(f"       vendor on the invoice:        {r['gv']}")
            p(f"       mean field confidence:        {r['confidence']:.2f}")
            p(f"       routing:                      {r['status'].upper()}")
        c2.close()
        wr = rep["extraction"]["fields"]["vendor"]["wrong"]
        p(st.warn(f"{fs.auto_approved} of {wr} documents with a wrong vendor were auto-approved: "
                  "no check examines the vendor, so a human never sees them"))
        fs, db, rep = fault["wrong_total"]
        p("\n" + st.head("Case 3: a confidently wrong total (caught by arithmetic)"))
        wr = rep["extraction"]["fields"]["total_minor"]["wrong"]
        p((st.ok if fs.auto_approved == 0 else st.warn)(
            f"{wr} documents with a wrong total, {fs.auto_approved} auto-approved, "
            f"{fs.needs_review} sent to review"))
        p("\n" + st.head("Case 4: the extractor crashes on one document"))
        cs = run_pipeline(sample, root / "crash.db", extractor=_CrashOnCall(RegexExtractor(), 3))
        f_ids = ", ".join(f"{f.doc_id} ({f.code})" for f in cs.failed)
        p((st.ok if len(cs.failed) == 1 else st.warn)(
            f"batch finished: {cs.processed} processed, {len(cs.failed)} failed: {f_ids}"))
        p("       the failed document is not auto-approved and not queued; it is listed separately")
        p("\n" + st.head("SUMMARY"))
        p("Validators catch what they are written to check (totals, dates, ids, duplicates, "
          "missing fields).")
        p("They do not check every field: a wrong vendor passes. Real data will do worse than "
          "this synthetic set.")
        p("No real LLM was evaluated; the LLM client is scripted and behind a swappable interface.")
    return 1 if problems else 0
