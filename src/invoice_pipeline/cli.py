"""Command line entry point.
  python -m invoice_pipeline.cli init-db
  python -m invoice_pipeline.cli generate
  python -m invoice_pipeline.cli load-truth
  python -m invoice_pipeline.cli run --extractor regex
  python -m invoice_pipeline.cli review-queue
Defaults come from environment variables (see .env.example): EXTRACTOR, DB_PATH.
No secrets are read or needed.
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from .db import connect, init_db, load_dataset
from .pipeline import run_pipeline
DEFAULT_DATA = "data/sample"
def _db_default() -> str:
    return os.environ.get("DB_PATH", "invoices.db")
def cmd_init_db(args) -> int:
    conn = connect(args.db)
    init_db(conn)
    conn.close()
    print(f"database ready: {args.db}")
    return 0
def cmd_generate(args) -> int:
    from .dataset import write_dataset
    manifest = write_dataset(Path(args.out), args.seed)
    print(f"wrote {manifest['total_documents']} documents to {args.out} (seed {args.seed})")
    return 0
def cmd_load_truth(args) -> int:
    conn = connect(args.db)
    init_db(conn)
    n = load_dataset(conn, args.data)
    conn.close()
    print(f"registered {n} documents and loaded their ground truth into {args.db}")
    return 0
def cmd_run(args) -> int:
    s = run_pipeline(args.data, args.db, args.extractor, args.run_key)
    print(f"run {s.run_id}: {s.total_documents} documents "
          f"({s.resumed_skipped} already done and skipped)")
    print(f"  processed     {s.processed}")
    print(f"  auto-approved {s.auto_approved}")
    print(f"  needs review  {s.needs_review}")
    print(f"  failed        {len(s.failed)}")
    for f in s.failed:
        print(f"    {f.doc_id}  {f.code}  {f.message}")
    return 0
def cmd_review_queue(args) -> int:
    conn = connect(args.db)
    run_id = args.run_id or conn.execute("SELECT MAX(run_id) FROM runs").fetchone()[0]
    if run_id is None:
        print("no runs yet")
        conn.close()
        return 0
    rows = conn.execute(
        "SELECT i.doc_id, i.invoice_no, GROUP_CONCAT(v.check_code, ', ') AS reasons "
        "FROM review_queue q JOIN invoices i ON i.invoice_row_id = q.invoice_row_id "
        "LEFT JOIN validation_results v ON v.invoice_row_id = i.invoice_row_id AND v.passed = 0 "
        "WHERE i.run_id = ? AND q.resolved = 0 GROUP BY i.invoice_row_id "
        "ORDER BY i.processing_order LIMIT ?", (run_id, args.limit)).fetchall()
    print(f"run {run_id}: {len(rows)} shown")
    for r in rows:
        print(f"{r['doc_id']}  {r['invoice_no'] or '-':<16}  {r['reasons']}")
    conn.close()
    return 0
def cmd_evaluate(args) -> int:
    from .evaluate import evaluate_run, headline, write_reports
    conn = connect(args.db)
    init_db(conn)
    load_dataset(conn, args.data)      # idempotent; ground truth is only ever read by evaluation
    run_id = args.run_id or conn.execute("SELECT MAX(run_id) FROM runs").fetchone()[0]
    if run_id is None:
        print("no runs yet: run the pipeline first")
        conn.close()
        return 1
    report = evaluate_run(conn, run_id, args.data)
    conn.close()
    md, js = write_reports(report, args.out, Path(args.data).name)
    print(headline(report))
    print(f"report written: {md}")
    print(f"                {js}")
    return 0
def cmd_fault_demo(args) -> int:
    import tempfile
    from .evaluate import evaluate_run, write_reports
    from .extractors.llm import LLMExtractor
    from .extractors.mock_llm import FaultyClient, oracle_client_from_dataset
    oracle = oracle_client_from_dataset(args.data)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        for mode in ("wrong_vendor", "wrong_total"):
            db = Path(tmp) / f"{mode}.db"
            summary = run_pipeline(args.data, db, extractor=LLMExtractor(FaultyClient(oracle, mode)))
            conn = connect(db)
            init_db(conn)
            load_dataset(conn, args.data)
            report = evaluate_run(conn, summary.run_id, args.data)
            conn.close()
            md, js = write_reports(report, args.out, Path(args.data).name)
            print(f"{mode}: {summary.auto_approved} auto-approved, {summary.needs_review} needs review")
            print(f"  report written: {md}")
    return 0
def cmd_compare(args) -> int:
    from .compare import EXACT, REVIEW, CompareError, build_comparison, write_comparison
    from .evaluate import pct
    try:
        cmp = build_comparison(args.reports)
    except CompareError as exc:
        print(f"cannot compare: {exc}", file=sys.stderr)
        return 1
    md, js = write_comparison(cmp, args.reports)
    for key in ("dev", "heldout"):
        print(f"regex {key}: exact match {pct(cmp['metrics'][key][EXACT])}, "
              f"review rate {pct(cmp['metrics'][key][REVIEW])}")
    problems = sum(len(v) for v in cmp["integrity"].values())
    print(f"integrity discrepancies: {problems}")
    print(f"comparison written: {md}")
    print(f"                    {js}")
    return 0
def cmd_demo(args) -> int:
    from .demo import run_demo
    return run_demo(out=args.out, color=args.color, verbose=args.verbose, seed=args.seed)
def cmd_dashboard(args) -> int:
    from .dashboard import write_dashboard
    try:
        path = write_dashboard(args.reports, args.out, args.db, args.run_id)
    except FileNotFoundError:
        print(f"cannot build: {args.reports}/comparison.json not found; run 'compare' first", file=sys.stderr)
        return 1
    print(f"dashboard written: {path}")
    return 0
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="invoice_pipeline")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init-db", help="create the SQLite schema")
    p.add_argument("--db", default=_db_default())
    p.set_defaults(fn=cmd_init_db)
    p = sub.add_parser("generate", help="generate the synthetic dataset")
    p.add_argument("--out", default=DEFAULT_DATA)
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("load-truth", help="load documents and ground truth (for evaluation)")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--db", default=_db_default())
    p.set_defaults(fn=cmd_load_truth)
    p = sub.add_parser("run", help="process every document in a data folder")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--db", default=_db_default())
    p.add_argument("--extractor", default=None, help="regex (default, or $EXTRACTOR)")
    p.add_argument("--run-key", default=None, help="reuse the same run on retry")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("review-queue", help="list documents waiting for human review")
    p.add_argument("--db", default=_db_default())
    p.add_argument("--run-id", type=int, default=None)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_review_queue)
    p = sub.add_parser("evaluate", help="score a run against ground truth, write a report")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--db", default=_db_default())
    p.add_argument("--run-id", type=int, default=None, help="default: latest run")
    p.add_argument("--out", default="reports")
    p.set_defaults(fn=cmd_evaluate)
    p = sub.add_parser("fault-demo", help="run the confidently-wrong scripted extractors, write reports")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--out", default="reports")
    p.set_defaults(fn=cmd_fault_demo)
    p = sub.add_parser("compare", help="compose reports/comparison.md from existing evaluation reports")
    p.add_argument("--reports", default="reports")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("demo", help="run the whole pipeline in six stages in a temporary folder")
    p.add_argument("--out", default=None, help="keep the evaluation and comparison reports here")
    p.add_argument("--color", action="store_true", help="colour the status tags (NO_COLOR is respected)")
    p.add_argument("--verbose", action="store_true", help="list every review-queue document")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(fn=cmd_demo)
    p = sub.add_parser("dashboard", help="build a self-contained HTML summary page from comparison.json")
    p.add_argument("--reports", default="reports")
    p.add_argument("--db", default=None, help="optional: adds the review queue and failure card")
    p.add_argument("--run-id", type=int, default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_dashboard)
    return ap
def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
if __name__ == "__main__":
    sys.exit(main())
