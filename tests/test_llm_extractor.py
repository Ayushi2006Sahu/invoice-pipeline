import inspect
import json
from datetime import date
from decimal import Decimal as D
import pytest
from invoice_pipeline import cli
from invoice_pipeline.dataset import write_dataset
from invoice_pipeline.db import connect, init_db, load_dataset
from invoice_pipeline.evaluate import evaluate_run, ratio, render_markdown
from invoice_pipeline.extractors import get_extractor
from invoice_pipeline.extractors.llm import LLMExtractor, build_prompt, document_from_prompt
from invoice_pipeline.extractors.mock_llm import (
    FaultyClient, MockLLMClient, oracle_client_from_dataset,
)
from invoice_pipeline.ingest import ingest
from invoice_pipeline.pipeline import run_pipeline
from invoice_pipeline.route import decide
from invoice_pipeline.validate import failed_codes, validate
DOC = "INVOICE\nAcme Pvt Ltd\nInvoice No: ACM-2026-0001\n"
GOOD = {
    "document_type": "invoice", "vendor": "Acme Pvt Ltd", "invoice_no": "ACM-2026-0001",
    "invoice_date": "12 Mar 2026", "due_date": "11 Apr 2026", "currency": "INR",
    "subtotal": "100.00", "discount": "0.00", "tax": "18.00", "total": "118.00",
    "line_items": [{"description": "Chair", "qty": 1, "unit_price": "100.00", "amount": "100.00"}],
}
SCALARS = ("document_type", "vendor", "invoice_no", "invoice_date", "due_date",
           "currency", "subtotal", "discount", "tax", "total")
class Fixed:
    name = "fixed"
    def __init__(self, reply):
        self.reply = reply
    def complete(self, prompt):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply
def extract(reply):
    if not isinstance(reply, (str, Exception)):
        reply = json.dumps(reply)
    return LLMExtractor(Fixed(reply)).extract(DOC)
# ------------------------------------------------------------ parsing
def test_valid_json_becomes_canonical_types_and_passes_validation():
    res = extract(GOOD)
    assert isinstance(res.total, D) and res.total == D("118.00")
    assert res.invoice_date == date(2026, 3, 12) and res.due_date == date(2026, 4, 11)
    assert res.line_items[0].unit_price == D("100.00") and res.line_items[0].qty == 1
    assert {c for _, c in res.field_conf.values()} <= {0.0, 0.9}
    assert failed_codes(validate(res)) == []
def test_fenced_and_prose_wrapped_json_is_accepted():
    assert extract("```json\n" + json.dumps(GOOD) + "\n```").total == D("118.00")
    assert extract("Here you go: " + json.dumps(GOOD) + " Thanks!").total == D("118.00")
@pytest.mark.parametrize("reply", [
    "not json at all", json.dumps(GOOD)[:60], "", "[1, 2]", TimeoutError("slow"),
])
def test_bad_replies_never_raise_and_force_review(reply):
    res = extract(reply)
    assert "llm_error" in res.signals and "extractor_error" not in res.signals
    assert res.total is None and res.vendor is None and res.line_items == []
    results = validate(res)
    assert "MISSING_FIELD" in failed_codes(results)
    assert decide(results).needs_review is True
    if isinstance(reply, Exception):
        assert res.signals["llm_error"].startswith("CLIENT_ERROR")
def test_empty_text_does_not_call_the_model():
    class Boom:
        name = "boom"
        def complete(self, prompt):
            raise AssertionError("must not be called")
    res = LLMExtractor(Boom()).extract("   \n")
    assert res.signals["text_empty"] is True and "llm_error" not in res.signals
    assert "GARBLED_TEXT" in failed_codes(validate(res))
@pytest.mark.parametrize("bad", [1234.5, 118, "1,234.50", "1234.567", "INR 118.00", "abc", True])
def test_money_must_be_a_plain_decimal_string(bad):
    res = extract(dict(GOOD, total=bad))
    assert res.total is None and res.field_conf["total"][1] == 0.0
def test_one_decimal_string_is_accepted():
    assert extract(dict(GOOD, total="1234.5")).total == D("1234.50")
def test_unparseable_date_keeps_raw_text_and_fails_missing_field():
    res = extract(dict(GOOD, invoice_date="31/02/2026"))
    assert res.invoice_date is None and res.field_conf["invoice_date"] == ("31/02/2026", 0.0)
    assert "MISSING_FIELD" in failed_codes(validate(res))
def test_absent_due_date_is_valid():
    res = extract(dict(GOOD, due_date=None))
    assert res.field_conf["due_date"] == (None, 0.0)
    assert failed_codes(validate(res)) == []
def test_ambiguous_date_text_is_preserved_so_the_check_can_fire():
    res = extract(dict(GOOD, invoice_date="03/04/2026"))
    assert res.invoice_date == date(2026, 4, 3)
    assert failed_codes(validate(res)) == ["AMBIGUOUS_DATE"]
def test_confidence_zero_fails_and_nine_tenths_passes_the_threshold():
    assert extract(GOOD).mean_required_field_confidence == pytest.approx(0.9)
    empty = extract({})
    assert empty.mean_required_field_confidence == 0.0
    assert "LOW_CONFIDENCE" in failed_codes(validate(empty))
    assert "LOW_CONFIDENCE" not in failed_codes(validate(extract(GOOD)))
def test_prompt_asks_for_printed_dates_and_round_trips_the_document():
    p = build_prompt(DOC)
    assert "exactly as printed" in p and document_from_prompt(p) == DOC
def test_extractor_never_receives_a_layout():
    assert list(inspect.signature(LLMExtractor.extract).parameters) == ["self", "text"]
def test_faulty_client_rejects_unknown_mode():
    with pytest.raises(ValueError):
        FaultyClient(MockLLMClient({}), "nope")
# ---------------------------------------------------- whole dataset
def primary(d, doc_id):
    pdf = d / "invoices" / f"{doc_id}.pdf"
    return pdf if pdf.exists() else d / "invoices" / f"{doc_id}.txt"
def res_values(res):
    v = {k: getattr(res, k) for k in SCALARS}
    v["line_items"] = [(i.description, i.qty, i.unit_price, i.amount) for i in res.line_items]
    return v
def gt_values(gt):
    f = gt["fields"]
    dec = lambda x: None if x is None else D(x)
    day = lambda x: None if x is None else date.fromisoformat(x)
    return {
        "document_type": gt["document_type"], "vendor": f["vendor"], "invoice_no": f["invoice_no"],
        "invoice_date": day(f["invoice_date"]), "due_date": day(f["due_date"]),
        "currency": f["currency"], "subtotal": dec(f["subtotal"]), "discount": dec(f["discount"]),
        "tax": dec(f["tax"]), "total": dec(f["total"]),
        "line_items": [(i["description"], i["qty"], D(i["unit_price"]), D(i["amount"]))
                       for i in f["line_items"]],
    }
def run_and_eval(d, tmp_path, extractor):
    db = tmp_path / "x.db"
    s = run_pipeline(d, db, extractor=extractor)
    conn = connect(db)
    init_db(conn)
    load_dataset(conn, d)
    rep = evaluate_run(conn, s.run_id, d)
    conn.close()
    return s, rep, db
@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    d = tmp_path_factory.mktemp("llm") / "data"
    manifest = write_dataset(d)
    gts = {p.stem: json.loads(p.read_text(encoding="utf-8"))
           for p in sorted((d / "ground_truth").glob("doc_*.json"))}
    return d, manifest, gts
@pytest.fixture(scope="module")
def oracle(ds):
    return oracle_client_from_dataset(ds[0])
@pytest.fixture(scope="module")
def oracle_run(ds, oracle, tmp_path_factory):
    return run_and_eval(ds[0], tmp_path_factory.mktemp("odb"), LLMExtractor(oracle))
def test_oracle_matches_ground_truth_on_every_readable_document(ds, oracle):
    d, _, gts = ds
    ex, n = LLMExtractor(oracle), 0
    for doc_id, gt in gts.items():
        if not gt["fields_readable"]:
            continue
        res = ex.extract(ingest(primary(d, doc_id)).text)
        assert "llm_error" not in res.signals, doc_id
        assert res_values(res) == gt_values(gt), doc_id
        n += 1
    assert n == 94
def test_unreadable_documents_are_never_silently_approved(ds, oracle):
    d, _, gts = ds
    ex, n = LLMExtractor(oracle), 0
    for doc_id, gt in gts.items():
        if gt["fields_readable"]:
            continue
        res = ex.extract(ingest(primary(d, doc_id)).text)
        assert failed_codes(validate(res)), doc_id
        n += 1
    assert n == 6
def test_oracle_pipeline_run_counts_and_plumbing(ds, oracle_run):
    _, manifest, _ = ds
    s, rep, _ = oracle_run
    assert s.failed == [] and s.processed == 100
    assert (s.auto_approved, s.needs_review) == (manifest["clean_documents"], manifest["expected_in_review"])
    assert rep["run"]["extractor"] == "llm-mock"
    assert rep["extraction"]["exact_match"] == ratio(94, 94)
    assert rep["auto_approved"]["bad"] == []
def test_mock_report_carries_the_plumbing_banner(oracle_run):
    assert "Plumbing check, not a model result" in render_markdown(oracle_run[1])
def test_get_extractor_llm_uses_env_backend(ds, monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "mock")
    assert get_extractor("llm", data_dir=ds[0]).name == "llm-mock"
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    with pytest.raises(ValueError, match="future work"):
        get_extractor("llm", data_dir=ds[0])
def test_cli_runs_the_llm_extractor_with_the_data_folder(ds, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    db = tmp_path / "cli.db"
    assert cli.main(["run", "--data", str(ds[0]), "--db", str(db), "--extractor", "llm"]) == 0
    out = capsys.readouterr().out
    assert "processed     100" in out and "failed        0" in out
def test_client_timeout_on_one_document_does_not_stop_the_batch(ds, oracle, tmp_path):
    client = FaultyClient(oracle, "timeout", on=lambda n: n == 3)
    s, rep, db = run_and_eval(ds[0], tmp_path, LLMExtractor(client))
    assert s.failed == [] and s.processed == 100
    conn = connect(db)
    row = conn.execute(
        "SELECT i.status, v.passed FROM invoices i JOIN validation_results v USING (invoice_row_id) "
        "WHERE i.doc_id = 'doc_0003' AND v.check_code = 'MISSING_FIELD'").fetchone()
    conn.close()
    assert row["status"] == "needs_review" and row["passed"] == 0
def test_confidently_wrong_total_is_caught_by_the_arithmetic_check(ds, oracle, tmp_path):
    s, rep, _ = run_and_eval(ds[0], tmp_path, LLMExtractor(FaultyClient(oracle, "wrong_total")))
    assert rep["auto_approved"]["count"] == 0
    assert rep["extraction"]["fields"]["total_minor"]["wrong"] == 94
    assert rep["catch"]["defect_level"]["readable"]["TOTAL_MISMATCH"]["rate"] == 1.0
def test_confidently_wrong_vendor_is_auto_approved_and_exposed_by_evaluation(ds, oracle, tmp_path):
    s, rep, _ = run_and_eval(ds[0], tmp_path, LLMExtractor(FaultyClient(oracle, "wrong_vendor")))
    a = rep["auto_approved"]
    assert a["count"] > 0 and len(a["bad"]) == a["count"]     # no validator checks the vendor
    assert a["field_correct"]["n"] == 0
    assert rep["extraction"]["fields"]["vendor"]["correct"] == 0
