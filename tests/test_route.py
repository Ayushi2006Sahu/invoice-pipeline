from invoice_pipeline.route import decide
from invoice_pipeline.validate import CheckResult
def r(code, status):
    return CheckResult(code, status, None)
def test_no_failures_means_auto_approve():
    d = decide([r("TOTAL_MISMATCH", "passed"), r("AMBIGUOUS_DATE", "passed")])
    assert d.needs_review is False and d.reasons == ()
def test_skipped_checks_do_not_trigger_review_by_themselves():
    d = decide([r("DUE_BEFORE_INVOICE", "skipped"), r("TOTAL_MISMATCH", "passed")])
    assert d.needs_review is False
def test_any_failed_check_means_review_with_sorted_reasons():
    d = decide([r("TOTAL_MISMATCH", "failed"), r("AMBIGUOUS_DATE", "failed"),
                r("LOW_CONFIDENCE", "passed")])
    assert d.needs_review is True
    assert d.reasons == ("AMBIGUOUS_DATE", "TOTAL_MISMATCH")
def test_reasons_are_grouped_by_severity():
    d = decide([r("TOTAL_MISMATCH", "failed"), r("AMBIGUOUS_DATE", "failed"),
                r("MISSING_FIELD", "failed")])
    assert d.by_severity == {"hard": ["TOTAL_MISMATCH"], "soft": ["AMBIGUOUS_DATE"],
                             "uncertain": ["MISSING_FIELD"]}
def test_empty_results_is_not_review():
    assert decide([]).needs_review is False
