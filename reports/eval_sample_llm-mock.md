# Evaluation: sample / llm-mock

> **Plumbing check, not a model result.** These numbers come from a scripted mock client that answers from the ground truth. They verify the extractor interface and pipeline integration; they do not measure real LLM extraction accuracy.

- Run 1, extractor `llm-mock`, model `mock`
- Policy: as-of 2026-10-10, rounding tolerance 0.01, confidence threshold 0.8
- Documents 100: processed 100 (auto-approved 51, needs review 49), failed 0; unreadable (garbled) 6
- Comparison policy: canonical values, exact equality. Strings are not normalised.
- `n/a` means no eligible documents, which is not the same as 0%.

## Headline (separate metrics, never combined)

| Metric | Result |
|---|---|
| Extraction: exact-match documents (readable only) | 100.0% (94/94) |
| Auto-approved: every extracted field correct | 100.0% (51/51) |
| Auto-approved: ground truth defect-free | 100.0% (51/51) |
| Review recall (expected_review=1 sent to review) | 100.0% (49/49) |
| False review rate (expected_review=0 sent to review) | 0.0% (0/51) |
| Error catch rate (docs with a wrong/missing field sent to review) | 100.0% (6/6) |
| Review rate (of processed) | 49.0% (49/100) |

## 1. Extraction accuracy (readable documents only)

| Field | Correct | Missing | Wrong | Accuracy |
|---|---|---|---|---|
| document_type | 94 | 0 | 0 | 100.0% (94/94) |
| vendor | 94 | 0 | 0 | 100.0% (94/94) |
| invoice_no | 94 | 0 | 0 | 100.0% (94/94) |
| invoice_date | 94 | 0 | 0 | 100.0% (94/94) |
| due_date | 94 | 0 | 0 | 100.0% (94/94) |
| currency | 94 | 0 | 0 | 100.0% (94/94) |
| subtotal_minor | 94 | 0 | 0 | 100.0% (94/94) |
| discount_minor | 94 | 0 | 0 | 100.0% (94/94) |
| tax_minor | 94 | 0 | 0 | 100.0% (94/94) |
| total_minor | 94 | 0 | 0 | 100.0% (94/94) |
| line_items | 94 | 0 | 0 | 100.0% (94/94) |

Line item cells (matched by line number):

| Cell | Correct | Missing | Wrong | Accuracy |
|---|---|---|---|---|
| description | 293 | 0 | 0 | 100.0% (293/293) |
| qty | 293 | 0 | 0 | 100.0% (293/293) |
| unit_price_minor | 293 | 0 | 0 | 100.0% (293/293) |
| amount_minor | 293 | 0 | 0 | 100.0% (293/293) |

- mean_required_field_confidence: 0.8964
- required_field_coverage (mean): 0.9960

## 2. Auto-approved quality

- Auto-approved documents: 51
- All extracted fields correct: 100.0% (51/51)
- Ground truth defect-free: 100.0% (51/51)

### Bad auto-approvals

None.

## 3. Defect catch

- Review recall: 100.0% (49/49)
- False review rate: 0.0% (0/51)
- Error catch rate: 100.0% (6/6)

Defect-level detection, readable documents (did the validator fail the same code?):

| Defect | Detected |
|---|---|
| AMBIGUOUS_DATE | 100.0% (5/5) |
| CREDIT_NOTE_SIGN_MISMATCH | 100.0% (3/3) |
| DUE_BEFORE_INVOICE | 100.0% (5/5) |
| DUPLICATE_ID_CONFLICT | 100.0% (3/3) |
| DUPLICATE_INVOICE | 100.0% (3/3) |
| FUTURE_INVOICE_DATE | 100.0% (3/3) |
| INVALID_INVOICE_ID | 100.0% (4/4) |
| MISSING_FIELD | 100.0% (3/3) |
| NEGATIVE_AMOUNT_ON_INVOICE | 100.0% (3/3) |
| NO_LINE_ITEMS | 100.0% (3/3) |
| SUBTOTAL_MISMATCH | 100.0% (5/5) |
| TOTAL_MISMATCH | 100.0% (5/5) |

Defect-level detection, garbled documents:

| Defect | Detected |
|---|---|
| GARBLED_TEXT | 33.3% (2/6) |

Extra detections (validator failed a check the ground truth did not list; not counted as errors):

| Check | Readable | Documents |
|---|---|---|
| LOW_CONFIDENCE | no | 6 |
| MISSING_FIELD | no | 6 |
| LOW_CONFIDENCE | yes | 3 |

## Review breakdown

- Review rate: 49.0% (49/100)
- Reviews triggered only by LOW_CONFIDENCE: 0

| Reason | Severity | Documents |
|---|---|---|
| LOW_CONFIDENCE | uncertain | 9 |
| MISSING_FIELD | uncertain | 9 |
| AMBIGUOUS_DATE | soft | 5 |
| DUE_BEFORE_INVOICE | hard | 5 |
| SUBTOTAL_MISMATCH | hard | 5 |
| TOTAL_MISMATCH | hard | 5 |
| INVALID_INVOICE_ID | hard | 4 |
| CREDIT_NOTE_SIGN_MISMATCH | soft | 3 |
| DUPLICATE_ID_CONFLICT | soft | 3 |
| DUPLICATE_INVOICE | hard | 3 |
| FUTURE_INVOICE_DATE | soft | 3 |
| NEGATIVE_AMOUNT_ON_INVOICE | soft | 3 |
| NO_LINE_ITEMS | hard | 3 |
| GARBLED_TEXT | uncertain | 2 |

## Garbled-input stress test

| Mode | Documents | Sent to review | GARBLED_TEXT fired |
|---|---|---|---|
| char_swap | 2 | 2 | 0 |
| empty | 1 | 1 | 1 |
| noise | 1 | 1 | 1 |
| truncated | 2 | 2 | 0 |

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
