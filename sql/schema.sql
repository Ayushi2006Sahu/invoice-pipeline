PRAGMA foreign_keys = ON;
-- One row per pipeline execution. run_key (optional) lets a retry/resume
-- reuse the same logical run; without it every execution is a new run.
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY,
    run_key     TEXT UNIQUE,
    extractor   TEXT NOT NULL,            -- 'regex', 'llm', 'mock'
    model       TEXT,                     -- NULL for regex
    policy_json TEXT NOT NULL,            -- snapshot of tolerance, threshold, as_of_date
    started_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,         -- doc_0001
    source_file TEXT NOT NULL,            -- primary file: pdf if present, else txt
    file_format TEXT NOT NULL CHECK (file_format IN ('txt', 'pdf')),
    sha256      TEXT NOT NULL
);
-- What the extractor produced. Extracted fields are all nullable.
-- processing_status = did the pipeline finish; status = the routing decision.
CREATE TABLE IF NOT EXISTS invoices (
    invoice_row_id    INTEGER PRIMARY KEY,
    run_id            INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    doc_id            TEXT    NOT NULL REFERENCES documents(doc_id),
    processing_order  INTEGER NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'pending'
                      CHECK (processing_status IN ('pending', 'processed', 'failed')),
    error_message     TEXT,
    status            TEXT CHECK (status IN ('auto_approved', 'needs_review')),
    document_type     TEXT CHECK (document_type IN ('invoice', 'credit_note')),
    vendor            TEXT,
    invoice_no        TEXT,
    invoice_date      TEXT,               -- ISO yyyy-mm-dd
    due_date          TEXT,
    currency          TEXT,
    subtotal_minor    INTEGER,            -- integer paise
    discount_minor    INTEGER,
    tax_minor         INTEGER,
    total_minor       INTEGER,
    confidence        REAL,
    UNIQUE (run_id, doc_id),
    -- a routing decision exists only for processed invoices
    CHECK ((processing_status = 'processed') = (status IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS line_items (
    invoice_row_id   INTEGER NOT NULL REFERENCES invoices(invoice_row_id) ON DELETE CASCADE,
    line_no          INTEGER NOT NULL,
    description      TEXT,
    qty              INTEGER,
    unit_price_minor INTEGER,
    amount_minor     INTEGER,
    PRIMARY KEY (invoice_row_id, line_no)
);
CREATE TABLE IF NOT EXISTS field_confidence (
    invoice_row_id INTEGER NOT NULL REFERENCES invoices(invoice_row_id) ON DELETE CASCADE,
    field_name     TEXT NOT NULL,
    raw_text       TEXT,                  -- exactly what was matched
    confidence     REAL NOT NULL,
    PRIMARY KEY (invoice_row_id, field_name)
);
-- DETECTED problems (never ground truth).
-- One row per check that actually EXECUTED:
--   passed = 1  check ran, condition held
--   passed = 0  check ran, condition failed
--   no row      check did not run (e.g. optional due date missing => skipped)
CREATE TABLE IF NOT EXISTS validation_results (
    invoice_row_id INTEGER NOT NULL REFERENCES invoices(invoice_row_id) ON DELETE CASCADE,
    check_code     TEXT NOT NULL,         -- same codes as rules.SEVERITY
    severity       TEXT NOT NULL CHECK (severity IN ('hard', 'soft', 'uncertain')),
    passed         INTEGER NOT NULL CHECK (passed IN (0, 1)),
    detail         TEXT,
    PRIMARY KEY (invoice_row_id, check_code)
);
-- Queue state only. The reasons live in validation_results (passed = 0).
CREATE TABLE IF NOT EXISTS review_queue (
    invoice_row_id INTEGER PRIMARY KEY REFERENCES invoices(invoice_row_id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    resolved       INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    resolved_by    TEXT,
    resolution     TEXT CHECK (resolution IN ('approved', 'corrected', 'rejected')),
    corrected_json TEXT
);
-- GROUND TRUTH: loaded from the generator's JSON, never written by the pipeline.
-- fields_readable = 0 means the document text was deliberately corrupted
-- (GARBLED_TEXT stress test), so field-level accuracy is not expected there.
CREATE TABLE IF NOT EXISTS gt_documents (
    doc_id          TEXT PRIMARY KEY REFERENCES documents(doc_id),
    document_type   TEXT NOT NULL,
    layout          TEXT,
    vendor          TEXT,
    invoice_no      TEXT,
    invoice_date    TEXT,
    due_date        TEXT,
    currency        TEXT,
    subtotal_minor  INTEGER,
    discount_minor  INTEGER,
    tax_minor       INTEGER,
    total_minor     INTEGER,
    expected_review INTEGER NOT NULL CHECK (expected_review IN (0, 1)),
    fields_readable INTEGER NOT NULL CHECK (fields_readable IN (0, 1)),
    duplicate_of    TEXT,
    conflicts_with  TEXT
);
CREATE TABLE IF NOT EXISTS gt_defects (
    doc_id      TEXT NOT NULL REFERENCES gt_documents(doc_id),
    defect_code TEXT NOT NULL,
    PRIMARY KEY (doc_id, defect_code)
);
CREATE INDEX IF NOT EXISTS idx_invoices_run    ON invoices(run_id, processing_order);
CREATE INDEX IF NOT EXISTS idx_validation_fail ON validation_results(check_code) WHERE passed = 0;
-- GROUND TRUTH line items (printed values), loaded from JSON. One row per printed line.
CREATE TABLE IF NOT EXISTS gt_line_items (
    doc_id           TEXT NOT NULL REFERENCES gt_documents(doc_id),
    line_no          INTEGER NOT NULL,
    description      TEXT,
    qty              INTEGER,
    unit_price_minor INTEGER,
    amount_minor     INTEGER,
    PRIMARY KEY (doc_id, line_no)
);
