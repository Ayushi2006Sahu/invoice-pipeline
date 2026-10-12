import json
import pytest
from invoice_pipeline import cli
from invoice_pipeline.dataset import write_dataset
@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    d = tmp_path_factory.mktemp("chk") / "data"
    write_dataset(d)
    gts = {p.stem: json.loads(p.read_text(encoding="utf-8"))
           for p in sorted((d / "ground_truth").glob("doc_*.json"))}
    return d, gts
def txt(ds, doc):
    return ds[0] / "invoices" / f"{doc}.txt"
def pick(ds, defect=None):
    for k, g in ds[1].items():
        if defect is None and not g["expected_defects"]:
            return k
        if defect and g["expected_defects"] == [defect]:
            return k
def test_clean_invoice_is_approved(ds, capsys):
    assert cli.main(["check", str(txt(ds, pick(ds)))]) == 0
    assert "DECISION: APPROVED" in capsys.readouterr().out
def test_total_mismatch_needs_review_with_the_reason(ds, capsys):
    assert cli.main(["check", str(txt(ds, pick(ds, "TOTAL_MISMATCH")))]) == 1
    out = capsys.readouterr().out
    assert "NEEDS REVIEW" in out and "[FAIL] TOTAL_MISMATCH" in out
def test_editing_a_clean_invoice_is_caught(ds, tmp_path, capsys):
    lines = txt(ds, pick(ds)).read_text(encoding="utf-8").rstrip("\n").split("\n")
    last = lines[-1].rstrip()                       # the total line ends with a digit
    lines[-1] = last[:-1] + str((int(last[-1]) + 3) % 10)
    edited = tmp_path / "edited.txt"
    edited.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(["check", str(edited)]) == 1
    assert "TOTAL_MISMATCH" in capsys.readouterr().out
def test_corrupt_pdf_is_never_approved(tmp_path, capsys):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert cli.main(["check", str(bad)]) == 2
    out = capsys.readouterr().out
    assert "COULD NOT READ" in out and "APPROVED" not in out
def test_missing_file_and_empty_file(tmp_path, capsys):
    assert cli.main(["check", str(tmp_path / "nope.txt")]) == 2
    empty = tmp_path / "e.txt"
    empty.write_text("", encoding="utf-8")
    assert cli.main(["check", str(empty)]) == 1     # readable but empty: flagged, not approved
    assert "GARBLED_TEXT" in capsys.readouterr().out
