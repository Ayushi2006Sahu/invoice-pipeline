"""SQLite access layer.
Money is Decimal in Python and integer paise in SQLite. Conversion never
rounds silently. Ground-truth tables are written only by load_ground_truth().
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional
from .rules import DEFAULT_POLICY, SEVERITY, Policy, expected_review
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
# ------------------------------------------------------------------ money
def to_minor(value) -> int:
    """Decimal/str/int rupees -> integer paise. Rejects extra fractional paise."""
    if isinstance(value, (float, bool)):
        raise TypeError("money must be Decimal, str or int, never float/bool")
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not d.is_finite():
        raise ValueError(f"not a finite amount: {value!r}")
    minor = d.scaleb(2)
    if minor != minor.to_integral_value():
        raise ValueError(f"{value!r} is not an exact multiple of 0.01")
    return int(minor)
def from_minor(minor: int) -> Decimal:
    return Decimal(int(minor)).scaleb(-2)
def _m(value) -> Optional[int]:
    return None if value is None else to_minor(value)
def _iso(d) -> Optional[str]:
    return None if d is None else d.isoformat()
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
# ------------------------------------------------------------- connection
def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")
# ------------------------------------------------------------------- runs
def get_or_create_run(
    conn: sqlite3.Connection,
    extractor: str,
    model: Optional[str] = None,
    policy: Policy = DEFAULT_POLICY,
    run_key: Optional[str] = None,
) -> int:
    with conn:
        if run_key is not None:
            row = conn.execute("SELECT run_id FROM runs WHERE run_key = ?", (run_key,)).fetchone()
            if row:
                return row["run_id"]
        cur = conn.execute(
            "INSERT INTO runs (run_key, extractor, model, policy_json, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_key, extractor, model, json.dumps(asdict(policy), default=str), _now()),
        )
        return cur.lastrowid
# -------------------------------------------------------------- documents
def register_document(conn: sqlite3.Connection, path: Path) -> str:
    path = Path(path)
    doc_id = path.stem
    fmt = path.suffix.lstrip(".").lower()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with conn:
        row = conn.execute("SELECT sha256 FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        if row:
            if row["sha256"] != sha:
                raise ValueError(f"{doc_id} already registered with different content")
            return doc_id
        conn.execute(
            "INSERT INTO documents (doc_id, source_file, file_format, sha256) VALUES (?, ?, ?, ?)",
            (doc_id, path.as_posix(), fmt, sha),
        )
    return doc_id
# ----------------------------------------------------------- ground truth
def load_ground_truth(conn: sqlite3.Connection, gt: dict, policy: Policy = DEFAULT_POLICY) -> None:
    """The only writer of gt_* tables. Refuses internally inconsistent truth."""
    doc_id = gt["doc_id"]
    defects = list(gt["expected_defects"])
    for d in defects:
        if d not in SEVERITY:
            raise ValueError(f"{doc_id}: unknown defect code {d}")
    if bool(gt["expected_review"]) != expected_review(defects, policy):
        raise ValueError(f"{doc_id}: expected_review disagrees with rules.expected_review()")
    f = gt["fields"]
    with conn:
        conn.execute("DELETE FROM gt_line_items WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM gt_defects WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM gt_documents WHERE doc_id = ?", (doc_id,))
        conn.execute(
            "INSERT INTO gt_documents (doc_id, document_type, layout, vendor, invoice_no, "
            "invoice_date, due_date, currency, subtotal_minor, discount_minor, tax_minor, "
            "total_minor, expected_review, fields_readable, duplicate_of, conflicts_with) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id, gt["document_type"], gt.get("layout"), f["vendor"], f["invoice_no"],
                f["invoice_date"], f["due_date"], f["currency"],
                _m(f["subtotal"]), _m(f["discount"]), _m(f["tax"]), _m(f["total"]),
                int(gt["expected_review"]), int(gt.get("fields_readable", True)),
                gt.get("duplicate_of"), gt.get("conflicts_with"),
            ),
        )
        conn.executemany(
            "INSERT INTO gt_defects (doc_id, defect_code) VALUES (?, ?)",
            [(doc_id, d) for d in defects],
        )
        conn.executemany(
            "INSERT INTO gt_line_items (doc_id, line_no, description, qty, unit_price_minor, "
            "amount_minor) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (doc_id, n, it["description"], it["qty"], _m(it["unit_price"]), _m(it["amount"]))
                for n, it in enumerate(f.get("line_items", []), 1)
            ],
        )
def load_dataset(conn: sqlite3.Connection, data_dir, policy: Policy = DEFAULT_POLICY) -> int:
    """Register every document (PDF preferred, else TXT) and load its ground truth."""
    data_dir = Path(data_dir)
    inv_dir = data_dir / "invoices"
    ids = sorted({p.stem for p in inv_dir.glob("doc_*")})
    for doc_id in ids:
        pdf, txt = inv_dir / f"{doc_id}.pdf", inv_dir / f"{doc_id}.txt"
        register_document(conn, pdf if pdf.exists() else txt)
    for gt_path in sorted((data_dir / "ground_truth").glob("doc_*.json")):
        load_ground_truth(conn, json.loads(gt_path.read_text(encoding="utf-8")), policy)
    return len(ids)
# --------------------------------------------------------- pipeline output
def save_invoice(
    conn: sqlite3.Connection,
    run_id: int,
    doc_id: str,
    processing_order: int,
    inv,                                   # models.Invoice-like; any field may be None
    confidence: Optional[float] = None,
    field_conf: Optional[dict] = None,     # {"total": (raw_text, 0.95), ...}
) -> int:
    """Insert extraction output atomically. Status stays pending until finalize."""
    with conn:
        cur = conn.execute(
            "INSERT INTO invoices (run_id, doc_id, processing_order, document_type, vendor, "
            "invoice_no, invoice_date, due_date, currency, subtotal_minor, discount_minor, "
            "tax_minor, total_minor, confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, doc_id, processing_order, inv.document_type, inv.vendor, inv.invoice_no,
                _iso(inv.invoice_date), _iso(inv.due_date), inv.currency,
                _m(inv.subtotal), _m(inv.discount), _m(inv.tax), _m(inv.total), confidence,
            ),
        )
        row_id = cur.lastrowid
        for n, it in enumerate(inv.line_items or [], 1):
            conn.execute(
                "INSERT INTO line_items (invoice_row_id, line_no, description, qty, "
                "unit_price_minor, amount_minor) VALUES (?,?,?,?,?,?)",
                (row_id, n, it.description, it.qty, _m(it.unit_price), _m(it.amount)),
            )
        for name, (raw, conf) in (field_conf or {}).items():
            conn.execute(
                "INSERT INTO field_confidence (invoice_row_id, field_name, raw_text, confidence) "
                "VALUES (?,?,?,?)",
                (row_id, name, raw, conf),
            )
    return row_id
def record_validation(
    conn: sqlite3.Connection,
    invoice_row_id: int,
    results: Iterable[tuple[str, bool, Optional[str]]],   # (check_code, passed, detail)
) -> None:
    results = list(results)
    for code, _, _ in results:
        if code not in SEVERITY:
            raise ValueError(f"unknown check code: {code}")
    with conn:
        conn.executemany(
            "INSERT INTO validation_results (invoice_row_id, check_code, severity, passed, detail) "
            "VALUES (?,?,?,?,?)",
            [(invoice_row_id, c, SEVERITY[c].value, int(p), d) for c, p, d in results],
        )
def finalize_invoice(conn: sqlite3.Connection, invoice_row_id: int, needs_review: bool) -> None:
    with conn:
        conn.execute(
            "UPDATE invoices SET status = ?, processing_status = 'processed' WHERE invoice_row_id = ?",
            ("needs_review" if needs_review else "auto_approved", invoice_row_id),
        )
        if needs_review:
            conn.execute(
                "INSERT OR IGNORE INTO review_queue (invoice_row_id, created_at) VALUES (?, ?)",
                (invoice_row_id, _now()),
            )
def mark_failed(conn: sqlite3.Connection, invoice_row_id: int, message: str) -> None:
    with conn:
        conn.execute(
            "UPDATE invoices SET processing_status = 'failed', status = NULL, error_message = ? "
            "WHERE invoice_row_id = ?",
            (message, invoice_row_id),
        )
# ------------------------------------------------- atomic per-document writers
def save_document(
    conn: sqlite3.Connection,
    run_id: int,
    doc_id: str,
    processing_order: int,
    inv,
    confidence: Optional[float],
    field_conf: Optional[dict],
    check_rows: Iterable[tuple],        # (check_code, passed, detail); skipped checks excluded
    needs_review: bool,
) -> int:
    """Write one fully processed document in ONE transaction.
    Invoice, line items, field confidence, validation rows and the review queue
    entry all commit together or not at all. On any error nothing is kept.
    """
    check_rows = list(check_rows)
    for code, _, _ in check_rows:
        if code not in SEVERITY:
            raise ValueError(f"unknown check code: {code}")
    status = "needs_review" if needs_review else "auto_approved"
    with conn:
        cur = conn.execute(
            "INSERT INTO invoices (run_id, doc_id, processing_order, processing_status, status, "
            "document_type, vendor, invoice_no, invoice_date, due_date, currency, subtotal_minor, "
            "discount_minor, tax_minor, total_minor, confidence) "
            "VALUES (?,?,?,'processed',?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, doc_id, processing_order, status, inv.document_type, inv.vendor,
                inv.invoice_no, _iso(inv.invoice_date), _iso(inv.due_date), inv.currency,
                _m(inv.subtotal), _m(inv.discount), _m(inv.tax), _m(inv.total), confidence,
            ),
        )
        row_id = cur.lastrowid
        for n, it in enumerate(inv.line_items or [], 1):
            conn.execute(
                "INSERT INTO line_items (invoice_row_id, line_no, description, qty, "
                "unit_price_minor, amount_minor) VALUES (?,?,?,?,?,?)",
                (row_id, n, it.description, it.qty, _m(it.unit_price), _m(it.amount)),
            )
        for name, (raw, conf) in (field_conf or {}).items():
            conn.execute(
                "INSERT INTO field_confidence (invoice_row_id, field_name, raw_text, confidence) "
                "VALUES (?,?,?,?)",
                (row_id, name, raw, conf),
            )
        conn.executemany(
            "INSERT INTO validation_results (invoice_row_id, check_code, severity, passed, detail) "
            "VALUES (?,?,?,?,?)",
            [(row_id, c, SEVERITY[c].value, int(ok), d) for c, ok, d in check_rows],
        )
        if needs_review:
            conn.execute(
                "INSERT INTO review_queue (invoice_row_id, created_at) VALUES (?, ?)",
                (row_id, _now()),
            )
    return row_id
def save_failed(
    conn: sqlite3.Connection,
    run_id: int,
    doc_id: str,
    processing_order: int,
    error_code: str,
    message: str,
) -> int:
    """Record a document that could not be processed. Never queued, never approved."""
    with conn:
        cur = conn.execute(
            "INSERT INTO invoices (run_id, doc_id, processing_order, processing_status, status, "
            "error_message) VALUES (?, ?, ?, 'failed', NULL, ?)",
            (run_id, doc_id, processing_order, f"{error_code}: {message}"),
        )
        return cur.lastrowid

