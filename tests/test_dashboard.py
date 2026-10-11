from pathlib import Path
import pytest
from invoice_pipeline import cli
REPORTS = Path(__file__).resolve().parents[1] / "reports"
@pytest.mark.skipif(not (REPORTS / "comparison.json").exists(), reason="run compare first")
def test_dashboard_builds_from_comparison_json(tmp_path):
    out = tmp_path / "d.html"
    assert cli.main(["dashboard", "--reports", str(REPORTS), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Plumbing check, not a model result" in text and "<script" not in text
    assert "wrong_vendor" in text and "Limitations" in text
def test_dashboard_without_comparison_is_a_clear_error(tmp_path, capsys):
    assert cli.main(["dashboard", "--reports", str(tmp_path)]) == 1
    assert "run 'compare' first" in capsys.readouterr().err
