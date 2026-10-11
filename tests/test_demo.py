import contextlib
import io
import os
import pytest
from invoice_pipeline import cli
from invoice_pipeline.demo import Style, use_color
@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    cwd, out = root / "cwd", root / "kept"
    cwd.mkdir()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["demo", "--out", str(out)])
    finally:
        os.chdir(old)
    return code, buf.getvalue(), cwd, out
def test_demo_succeeds_and_shows_all_six_stages(demo_run):
    code, text, _, _ = demo_run
    assert code == 0
    for stage in ("01 / DATA GENERATION", "02 / PIPELINE EXECUTION", "03 / HUMAN REVIEW QUEUE",
                  "04 / EVALUATION", "05 / COMPARISON", "06 / FAILURE WALKTHROUGH"):
        assert stage in text, stage
def test_demo_shows_idempotency_and_the_real_failure_cases(demo_run):
    _, text, _, _ = demo_run
    assert "0 new," in text and "already done and skipped" in text
    assert "TOTAL_MISMATCH" in text and "in review queue: yes" in text
    assert "were auto-approved" in text and "no check examines the vendor" in text
    assert "0 auto-approved" in text                      # wrong total: nothing slips through
    assert "simulated" not in text or "UNEXPECTED_ERROR" in text
    assert "UNEXPECTED_ERROR" in text
def test_demo_labels_the_scripted_client_and_stays_plain_by_default(demo_run):
    _, text, _, _ = demo_run
    assert "Plumbing check, not a model result" in text
    assert "\x1b[" not in text
    assert text.isascii()
def test_demo_leaves_the_working_folder_alone_and_keeps_reports_on_request(demo_run):
    _, _, cwd, out = demo_run
    assert list(cwd.iterdir()) == []
    assert (out / "comparison.md").exists() and (out / "comparison.json").exists()
def test_color_is_off_by_default_and_respects_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert "\x1b[" not in Style(False).ok("x")
    assert "\x1b[" in Style(use_color(True)).ok("x")
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(True) is False
    assert "\x1b[" not in Style(use_color(True)).fail("x")
