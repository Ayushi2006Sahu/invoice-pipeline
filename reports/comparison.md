# Extractor comparison

Composed from existing evaluation reports. Nothing was re-run or re-scored here; every number comes from the input files listed at the end.

Metrics stay separate and are never combined into one accuracy figure. `n/a` means no eligible documents, which is not the same as 0%.

## 1. Regex baseline: development layouts vs held-out layout

- Development (layouts A-D): run 1 (`regex`), 100 documents, 100 processed, 0 failed
- Held-out (layout E, unseen): run 1 (`regex`), 100 documents, 100 processed, 0 failed

| Metric | Development | Held-out |
|---|---|---|
| Extraction: exact-match documents (readable only) | 100.0% (94/94) | 0.0% (0/94) |
| Auto-approved: every extracted field correct | 100.0% (51/51) | n/a (0/0) |
| Auto-approved: ground truth defect-free | 100.0% (51/51) | n/a (0/0) |
| Bad auto-approvals (auto-approved but wrong or defective) | 0.0% (0/51) | n/a (0/0) |
| Review recall (expected_review=1 sent to review) | 100.0% (49/49) | 100.0% (49/49) |
| False review rate (expected_review=0 sent to review) | 0.0% (0/51) | 100.0% (51/51) |
| Error catch rate (docs with a wrong/missing field sent to review) | 100.0% (6/6) | 100.0% (100/100) |
| Review rate (of processed) | 49.0% (49/100) | 100.0% (100/100) |

Per field (held-out columns are document counts):

| Field | Dev accuracy | Held-out correct | Held-out missing | Held-out wrong |
|---|---|---|---|---|
| document_type | 100.0% (94/94) | 94 | 0 | 0 |
| vendor | 100.0% (94/94) | 0 | 94 | 0 |
| invoice_no | 100.0% (94/94) | 2 | 92 | 0 |
| invoice_date | 100.0% (94/94) | 1 | 93 | 0 |
| due_date | 100.0% (94/94) | 5 | 89 | 0 |
| currency | 100.0% (94/94) | 94 | 0 | 0 |
| subtotal_minor | 100.0% (94/94) | 0 | 94 | 0 |
| discount_minor | 100.0% (94/94) | 0 | 94 | 0 |
| tax_minor | 100.0% (94/94) | 0 | 94 | 0 |
| total_minor | 100.0% (94/94) | 94 | 0 | 0 |
| line_items | 100.0% (94/94) | 94 | 0 | 0 |

Development numbers are circular: the generator and the regex extractor share an author and a label vocabulary. The held-out layout uses labels and an order the extractor was never tuned on.

## 2. Held-out finding

- Exact-match documents: 0.0% (0/94).
- Fields correct in every readable document: document_type, currency, total_minor, line_items.
- Fields with missing values: vendor (94 missing), invoice_no (92 missing), invoice_date (93 missing), due_date (89 missing), subtotal_minor (94 missing), discount_minor (94 missing), tax_minor (94 missing).
- Wrong (non-missing) values: 0 field cell(s).
- Sent to review: 100.0% (100/100). Auto-approved: 0. Bad auto-approvals: 0.

It produced no wrong values here, only missing ones; missing values are what the MISSING_FIELD check sends to review.
Every document went to review. That is safe but not useful: a human would have to redo all of them, and the error catch rate is trivially high.

## 3. LLM extractor plumbing check (scripted client)

> **Plumbing check, not a model result.** These results verify the extractor interface and pipeline integration; they do not measure real LLM extraction accuracy.

Run 1 (`llm-mock`) on the development dataset only. The scripted client answers from the ground truth, so its numbers are not placed next to the regex numbers. The held-out layout is excluded because the scripted answers exist only for the dataset they were built from.

| Metric | Result |
|---|---|
| Extraction: exact-match documents (readable only) | 100.0% (94/94) |
| Auto-approved: every extracted field correct | 100.0% (51/51) |
| Auto-approved: ground truth defect-free | 100.0% (51/51) |
| Bad auto-approvals (auto-approved but wrong or defective) | 0.0% (0/51) |
| Review recall (expected_review=1 sent to review) | 100.0% (49/49) |
| False review rate (expected_review=0 sent to review) | 0.0% (0/51) |
| Error catch rate (docs with a wrong/missing field sent to review) | 100.0% (6/6) |
| Review rate (of processed) | 49.0% (49/100) |

## 4. Confidently-wrong extractor demo

Two scripted extractors corrupt one field in every answer while reporting normal confidence. 'Auto-approved despite the error' means the routing decision was auto_approved although the field was wrong.

| Fault | Corrupted field | Documents with wrong value | Sent to review | Auto-approved despite the error | Most frequent failed checks | Outcome |
|---|---|---|---|---|---|---|
| wrong_vendor | vendor | 94 | 43 | 51 | LOW_CONFIDENCE (9), MISSING_FIELD (9), AMBIGUOUS_DATE (5) | 51 of 94 undetected |
| wrong_total | total_minor | 94 | 94 | 0 | TOTAL_MISMATCH (94), LOW_CONFIDENCE (9), MISSING_FIELD (9) | every wrong document was sent to review |

- wrong_vendor: 51 of 94 documents with a wrong vendor were auto-approved, so a human would never see them.
- wrong_total: every document with a wrong total_minor was sent to review.

## 5. Limitations

- The data is synthetic. Real invoices will behave worse; nothing here is a real-world accuracy claim.
- The generator and the regex extractor have the same author and share label vocabulary, so regex accuracy on layouts A-D is circular. The held-out layout E is the honest generalisation check.
- Each defect type has only a handful of examples, so every percentage is shown with n/N and strong claims from small samples are not supported.
- Zero false positives on clean documents is a result on this dataset, not a guarantee.
- Two errors that cancel each other (a consistently wrong subtotal and total) cannot be caught by arithmetic validation.
- Confidence is a review signal, not proof of correctness; an extractor can be confidently wrong.
- The scripted LLM client is not a model. No real LLM was evaluated; a real local model client is future work.
- The held-out result comes from one unseen layout and one seed. It shows that the regex label dictionary does not generalise, not how any extractor would do on arbitrary invoices.

## Inputs and integrity

| Role | File | Run | Extractor | Dataset |
|---|---|---|---|---|
| dev | reports\eval_sample_regex.json | 1 | regex | sample |
| heldout | reports\eval_heldout_regex.json | 1 | regex | heldout |
| mock | reports\eval_sample_llm-mock.json | 1 | llm-mock | sample |
| wrong_vendor | reports\eval_sample_llm-mock-wrong_vendor.json | 1 | llm-mock-wrong_vendor | sample |
| wrong_total | reports\eval_sample_llm-mock-wrong_total.json | 1 | llm-mock-wrong_total | sample |

- dev: counts are internally consistent
- heldout: counts are internally consistent
- mock: counts are internally consistent
- wrong_vendor: counts are internally consistent
- wrong_total: counts are internally consistent
