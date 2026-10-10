-- Named queries used by evaluate.py. Each starts with a "-- name:" marker.
-- Parameter :run_id selects the pipeline run. Ground-truth tables are only read.
-- name: documents
SELECT i.invoice_row_id, i.doc_id, i.processing_status, i.status, i.confidence, i.error_message,
       i.document_type, i.vendor, i.invoice_no, i.invoice_date, i.due_date, i.currency,
       i.subtotal_minor, i.discount_minor, i.tax_minor, i.total_minor,
       g.document_type AS g_document_type, g.vendor AS g_vendor, g.invoice_no AS g_invoice_no,
       g.invoice_date AS g_invoice_date, g.due_date AS g_due_date, g.currency AS g_currency,
       g.subtotal_minor AS g_subtotal_minor, g.discount_minor AS g_discount_minor,
       g.tax_minor AS g_tax_minor, g.total_minor AS g_total_minor,
       g.expected_review, g.fields_readable
FROM invoices i
JOIN gt_documents g ON g.doc_id = i.doc_id
WHERE i.run_id = :run_id
ORDER BY i.doc_id;
-- name: gt_defects_all
SELECT doc_id, defect_code FROM gt_defects;
-- name: failed_checks
SELECT v.invoice_row_id, v.check_code
FROM validation_results v
JOIN invoices i ON i.invoice_row_id = v.invoice_row_id
WHERE i.run_id = :run_id AND v.passed = 0;
-- name: defect_detection
-- For every ground-truth defect: did the validator fail that same check?
SELECT g.defect_code AS code,
       gd.fields_readable AS readable,
       COUNT(*) AS n,
       SUM(CASE WHEN v.passed = 0 THEN 1 ELSE 0 END) AS detected
FROM gt_defects g
JOIN gt_documents gd ON gd.doc_id = g.doc_id
JOIN invoices i ON i.doc_id = g.doc_id AND i.run_id = :run_id AND i.processing_status = 'processed'
LEFT JOIN validation_results v ON v.invoice_row_id = i.invoice_row_id AND v.check_code = g.defect_code
GROUP BY g.defect_code, gd.fields_readable
ORDER BY g.defect_code;
-- name: extra_detections
-- Checks that failed although the ground truth did not list that defect.
SELECT v.check_code AS code, gd.fields_readable AS readable, COUNT(*) AS n
FROM validation_results v
JOIN invoices i ON i.invoice_row_id = v.invoice_row_id
JOIN gt_documents gd ON gd.doc_id = i.doc_id
LEFT JOIN gt_defects g ON g.doc_id = i.doc_id AND g.defect_code = v.check_code
WHERE i.run_id = :run_id AND v.passed = 0 AND g.doc_id IS NULL
GROUP BY v.check_code, gd.fields_readable
ORDER BY n DESC, v.check_code;
-- name: review_by_reason
SELECT v.check_code AS code, v.severity AS severity, COUNT(*) AS n
FROM validation_results v
JOIN invoices i ON i.invoice_row_id = v.invoice_row_id
WHERE i.run_id = :run_id AND v.passed = 0
GROUP BY v.check_code, v.severity
ORDER BY n DESC, v.check_code;
-- name: low_confidence_only
-- Reviews whose ONLY failed check is LOW_CONFIDENCE.
SELECT COUNT(*) AS n
FROM invoices i
WHERE i.run_id = :run_id AND i.status = 'needs_review'
  AND NOT EXISTS (SELECT 1 FROM validation_results v
                  WHERE v.invoice_row_id = i.invoice_row_id AND v.passed = 0
                    AND v.check_code <> 'LOW_CONFIDENCE');
