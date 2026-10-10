# Evaluation: heldout / regex

- Run 1, extractor `regex`
- Policy: as-of 2026-10-10, rounding tolerance 0.01, confidence threshold 0.8
- Documents 100: processed 100 (auto-approved 0, needs review 100), failed 0; unreadable (garbled) 6
- Comparison policy: canonical values, exact equality. Strings are not normalised.
- `n/a` means no eligible documents, which is not the same as 0%.

## Headline (separate metrics, never combined)

| Metric | Result |
|---|---|
| Extraction: exact-match documents (readable only) | 0.0% (0/94) |
| Auto-approved: every extracted field correct | n/a (0/0) |
| Auto-approved: ground truth defect-free | n/a (0/0) |
| Review recall (expected_review=1 sent to review) | 100.0% (49/49) |
| False review rate (expected_review=0 sent to review) | 100.0% (51/51) |
| Error catch rate (docs with a wrong/missing field sent to review) | 100.0% (100/100) |
| Review rate (of processed) | 100.0% (100/100) |

## 1. Extraction accuracy (readable documents only)

| Field | Correct | Missing | Wrong | Accuracy |
|---|---|---|---|---|
| document_type | 94 | 0 | 0 | 100.0% (94/94) |
| vendor | 0 | 94 | 0 | 0.0% (0/94) |
| invoice_no | 2 | 92 | 0 | 2.1% (2/94) |
| invoice_date | 1 | 93 | 0 | 1.1% (1/94) |
| due_date | 5 | 89 | 0 | 5.3% (5/94) |
| currency | 94 | 0 | 0 | 100.0% (94/94) |
| subtotal_minor | 0 | 94 | 0 | 0.0% (0/94) |
| discount_minor | 0 | 94 | 0 | 0.0% (0/94) |
| tax_minor | 0 | 94 | 0 | 0.0% (0/94) |
| total_minor | 94 | 0 | 0 | 100.0% (94/94) |
| line_items | 94 | 0 | 0 | 100.0% (94/94) |

Line item cells (matched by line number):

| Cell | Correct | Missing | Wrong | Accuracy |
|---|---|---|---|---|
| description | 257 | 0 | 0 | 100.0% (257/257) |
| qty | 257 | 0 | 0 | 100.0% (257/257) |
| unit_price_minor | 257 | 0 | 0 | 100.0% (257/257) |
| amount_minor | 257 | 0 | 0 | 100.0% (257/257) |

- mean_required_field_confidence: 0.3750
- required_field_coverage (mean): 0.3750

## 2. Auto-approved quality

- Auto-approved documents: 0
- All extracted fields correct: n/a (0/0)
- Ground truth defect-free: n/a (0/0)

### Bad auto-approvals

None.

## 3. Defect catch

- Review recall: 100.0% (49/49)
- False review rate: 100.0% (51/51)
- Error catch rate: 100.0% (100/100)

Defect-level detection, readable documents (did the validator fail the same code?):

| Defect | Detected |
|---|---|
| AMBIGUOUS_DATE | 0.0% (0/5) |
| CREDIT_NOTE_SIGN_MISMATCH | 100.0% (3/3) |
| DUE_BEFORE_INVOICE | 0.0% (0/5) |
| DUPLICATE_ID_CONFLICT | 0.0% (0/3) |
| DUPLICATE_INVOICE | 0.0% (0/3) |
| FUTURE_INVOICE_DATE | 0.0% (0/3) |
| INVALID_INVOICE_ID | 0.0% (0/4) |
| MISSING_FIELD | 100.0% (3/3) |
| NEGATIVE_AMOUNT_ON_INVOICE | 100.0% (3/3) |
| NO_LINE_ITEMS | 100.0% (3/3) |
| SUBTOTAL_MISMATCH | 0.0% (0/5) |
| TOTAL_MISMATCH | 0.0% (0/5) |

Defect-level detection, garbled documents:

| Defect | Detected |
|---|---|
| GARBLED_TEXT | 100.0% (6/6) |

Extra detections (validator failed a check the ground truth did not list; not counted as errors):

| Check | Readable | Documents |
|---|---|---|
| LOW_CONFIDENCE | yes | 94 |
| MISSING_FIELD | yes | 91 |
| GARBLED_TEXT | yes | 28 |
| LOW_CONFIDENCE | no | 6 |
| MISSING_FIELD | no | 6 |
| NO_LINE_ITEMS | no | 1 |

## Review breakdown

- Review rate: 100.0% (100/100)
- Reviews triggered only by LOW_CONFIDENCE: 0

| Reason | Severity | Documents |
|---|---|---|
| LOW_CONFIDENCE | uncertain | 100 |
| MISSING_FIELD | uncertain | 100 |
| GARBLED_TEXT | uncertain | 34 |
| NO_LINE_ITEMS | hard | 4 |
| CREDIT_NOTE_SIGN_MISMATCH | soft | 3 |
| NEGATIVE_AMOUNT_ON_INVOICE | soft | 3 |

## Garbled-input stress test

| Mode | Documents | Sent to review | GARBLED_TEXT fired |
|---|---|---|---|
| char_swap | 2 | 2 | 2 |
| empty | 1 | 1 | 1 |
| noise | 1 | 1 | 1 |
| truncated | 2 | 2 | 2 |

GARBLED_TEXT not firing for some modes is a finding, not a bug: thresholds were fixed in advance and other checks may still route the document to review.

## Failed documents (not auto-approved, not queued)

None.

## Limitations

- The data is synthetic. Real invoices will behave worse; nothing here is a real-world accuracy claim.
- The generator and the regex extractor have the same author and share label vocabulary, so regex accuracy on layouts A-D is circular. The held-out layout E is the honest generalisation check.
- Each defect type has only a handful of examples, so every percentage is shown with n/N and strong claims from small samples are not supported.
- Zero false positives on clean documents is a result on this dataset, not a guarantee.
- Two errors that cancel each other (a consistently wrong subtotal and total) cannot be caught by arithmetic validation.
- Confidence is a review signal, not proof of correctness; an extractor can be confidently wrong.
