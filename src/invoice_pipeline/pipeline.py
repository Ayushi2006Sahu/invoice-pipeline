"""Batch pipeline: ingest -> extract -> validate -> route -> save.
Documents run in sorted doc_id order, which is also the processing order that
duplicate detection depends on. Each document is saved in one transaction.
A document that fails is recorded as failed and the batch carries on. If even
the failure cannot be recorded, the run stops with PipelineFatalError.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional
from .db import (
    connect, from_minor, get_or_create_run, init_db, register_document,
    save_document, save_failed,
)
from .extractors import get_extractor
from .ingest import ingest
from .route import decide
from .rules import DEFAULT_POLICY, DocKey, Policy
from .validate import db_rows, validate
UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
class PipelineFatalError(RuntimeError):
    """The database could not record a result or a failure. The run cannot continue."""
@dataclass
class FailedDoc:
    doc_id: str
    code: str
    message: str
@dataclass
class RunSummary:
    run_id: int
    total_documents: int = 0
    resumed_skipped: int = 0
    processed: int = 0
    auto_approved: int = 0
    needs_review: int = 0
    failed: list = field(default_factory=list)
def _primary(inv_dir: Path, doc_id: str) -> Path:
    pdf = inv_dir / f"{doc_id}.pdf"
    return pdf if pdf.exists() else inv_dir / f"{doc_id}.txt"
def _key(res) -> Optional[DocKey]:
    parts = (res.vendor, res.invoice_no, res.invoice_date, res.total)
    return None if any(x is None for x in parts) else DocKey(*parts)
def _key_from_row(row) -> Optional[DocKey]:
    if any(row[k] is None for k in ("vendor", "invoice_no", "invoice_date", "total_minor")):
        return None
    return DocKey(row["vendor"], row["invoice_no"],
                  date.fromisoformat(row["invoice_date"]), from_minor(row["total_minor"]))
def _process_one(conn, run_id, doc_id, order, path, extractor, prior, policy):
    ing = ingest(path)
    if not ing.ok:
        save_failed(conn, run_id, doc_id, order, ing.error_code, ing.error_message)
        return None
    res = extractor.extract(ing.text)
    results = validate(res, prior, policy)
    decision = decide(results)
    save_document(conn, run_id, doc_id, order, res, res.mean_required_field_confidence,
                  res.field_conf, db_rows(results), decision.needs_review)
    return _key(res)
def run_pipeline(
    data_dir,
    db_path,
    extractor_name: Optional[str] = None,
    run_key: Optional[str] = None,
    policy: Policy = DEFAULT_POLICY,
    extractor=None,
) -> RunSummary:
    inv_dir = Path(data_dir) / "invoices"
    ids = sorted({p.stem for p in inv_dir.glob("doc_*")})
    extractor = extractor or get_extractor(extractor_name, data_dir=data_dir)
    conn = connect(db_path)
    try:
        init_db(conn)
        for doc_id in ids:
            register_document(conn, _primary(inv_dir, doc_id))
        run_id = get_or_create_run(conn, extractor.name, getattr(extractor, "model", None),
                                   policy, run_key)
        done = {r["doc_id"]: r for r in conn.execute(
            "SELECT doc_id, vendor, invoice_no, invoice_date, total_minor FROM invoices "
            "WHERE run_id = ? AND processing_status = 'processed'", (run_id,))}
        summary = RunSummary(run_id, total_documents=len(ids), resumed_skipped=len(done))
        prior: list[DocKey] = []
        for order, doc_id in enumerate(ids, 1):
            if doc_id in done:                      # resume: keep it, still count it as prior
                key = _key_from_row(done[doc_id])
                if key:
                    prior.append(key)
                continue
            with conn:                              # a failed attempt from an earlier try is retried
                conn.execute("DELETE FROM invoices WHERE run_id = ? AND doc_id = ? "
                             "AND processing_status = 'failed'", (run_id, doc_id))
            try:
                key = _process_one(conn, run_id, doc_id, order, _primary(inv_dir, doc_id),
                                   extractor, prior, policy)
            except Exception as exc:
                try:
                    save_failed(conn, run_id, doc_id, order, UNEXPECTED_ERROR,
                                f"{type(exc).__name__}: {exc}")
                except Exception as exc2:
                    raise PipelineFatalError(
                        f"could not record failure for {doc_id}: {exc2!r} (original: {exc!r})"
                    ) from exc2
                continue
            if key:
                prior.append(key)
        counts = {r["status"]: r["c"] for r in conn.execute(
            "SELECT status, COUNT(*) c FROM invoices WHERE run_id = ? "
            "AND processing_status = 'processed' GROUP BY status", (run_id,))}
        summary.auto_approved = counts.get("auto_approved", 0)
        summary.needs_review = counts.get("needs_review", 0)
        summary.processed = summary.auto_approved + summary.needs_review
        for r in conn.execute("SELECT doc_id, error_message FROM invoices WHERE run_id = ? "
                              "AND processing_status = 'failed' ORDER BY processing_order",
                              (run_id,)):
            code, _, msg = (r["error_message"] or "").partition(": ")
            summary.failed.append(FailedDoc(r["doc_id"], code, msg))
        return summary
    finally:
        conn.close()
