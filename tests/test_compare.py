import json
import os
import shutil
import pytest
from invoice_pipeline import cli
from invoice_pipeline.compare import CompareError, build_comparison, render_markdown, write_comparison
from invoice_pipeline.dataset import write_dataset
from invoice_pipeline.evaluate import LIMITATIONS
@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    root = tmp_path_factory.mktemp("cmp")
    sample, held, out = root / "sample", root / "heldout", root / "reports"
    assert cli.main(["generate", "--out", str(sample)]) == 0
    write_dataset(held, 7, layouts=["E"])
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("LLM_BACKEND", raising=False)
        mp.delenv("EXTRACTOR", raising=False)
        for data, db, ext in ((sample, "dev.db", "regex"), (held, "held.db", "regex"),
                              (sample, "llm.db", "llm")):
            assert cli.main(["run", "--data", str(data), "--db", str(root / db), "--extractor", ext]) == 0
            assert cli.main(["evaluate", "--data", str(data), "--db", str(root / db),
                             "--out", str(out)]) == 0
        assert cli.main(["fault-demo", "--data", str(sample), "--out", str(out)]) == 0
    return out
@pytest.fixture
def work(reports, tmp_path):
    d = tmp_path / "reports"
    shutil.copytree(reports, d)
    return d
def edit(path, fn):
    rep = json.loads(path.read_text(encoding="utf-8"))
    fn(rep)
    path.write_text(json.dumps(rep), encoding="utf-8")
def test_report_has_all_sections_and_the_plumbing_banner(work):
    md, js = write_comparison(build_comparison(work), work)
    text = md.read_text(encoding="utf-8")
    for h in ("## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## Inputs and integrity"):
        assert h in text, h
    assert "Plumbing check, not a model result" in text
    assert len(json.loads(js.read_text(encoding="utf-8"))["inputs"]) == 5
def test_regex_and_scripted_client_are_never_in_the_same_section(work):
    text = render_markdown(build_comparison(work))
    assert "mock" not in text.split("## 3.")[0].lower()
def test_numbers_come_from_the_json_reports(work):
    edit(work / "eval_heldout_regex.json", lambda r: r["extraction"]["exact_match"].update(n=7))
    assert "(7/94)" in render_markdown(build_comparison(work))
def test_inconsistent_report_is_flagged_not_hidden(work):
    edit(work / "eval_heldout_regex.json", lambda r: r["counts"].update(needs_review=r["counts"]["needs_review"] + 1))
    cmp = build_comparison(work)
    assert cmp["integrity"]["heldout"]
    assert "DISCREPANCY in heldout" in render_markdown(cmp)
def test_real_reports_have_no_discrepancies(work):
    assert all(v == [] for v in build_comparison(work)["integrity"].values())
def test_wrong_values_are_called_out_when_the_report_has_them(work):
    edit(work / "eval_heldout_regex.json", lambda r: r["extraction"]["fields"]["vendor"].update(wrong=3))
    assert "Wrong values were produced" in render_markdown(build_comparison(work))
def test_missing_report_is_a_clear_error_and_writes_nothing(work, capsys):
    (work / "eval_heldout_regex.json").unlink()
    with pytest.raises(CompareError, match="eval_heldout_regex.json"):
        build_comparison(work)
    assert cli.main(["compare", "--reports", str(work)]) == 1
    assert "create with" in capsys.readouterr().err
    assert not (work / "comparison.md").exists()
def test_report_from_the_wrong_extractor_is_rejected(work):
    shutil.copy(work / "eval_sample_regex.json", work / "eval_sample_llm-mock.json")
    with pytest.raises(CompareError, match="llm-mock"):
        build_comparison(work)
def test_confidently_wrong_cases_use_the_real_routing_outcome(work):
    f = build_comparison(work)["faults"]
    v, t = f["wrong_vendor"], f["wrong_total"]
    assert v["wrong"] == 94 and v["undetected"] == v["auto_approved"] > 0
    assert t["wrong"] == 94 and t["undetected"] == 0 and t["caught"] == 94
def test_zero_denominators_show_na_and_limitations_are_shared(work):
    text = render_markdown(build_comparison(work))
    assert "n/a" in text
    for line in LIMITATIONS:
        assert line in text
def test_compare_adds_only_its_two_files_and_the_cli_reports_success(work, capsys):
    before = set(os.listdir(work))
    assert cli.main(["compare", "--reports", str(work)]) == 0
    assert set(os.listdir(work)) - before == {"comparison.md", "comparison.json"}
    assert "comparison written" in capsys.readouterr().out
