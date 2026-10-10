from pathlib import Path
import pytest
from invoice_pipeline.dataset import build_specs, document_text, write_dataset
from invoice_pipeline.extractors.regex import RegexExtractor
from invoice_pipeline.render import ALL_LAYOUTS, HELDOUT_LAYOUTS, LAYOUTS
SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample"
def test_heldout_layout_is_kept_separate():
    assert "E" not in LAYOUTS
    assert "E" in HELDOUT_LAYOUTS and "E" in ALL_LAYOUTS
def test_default_dataset_uses_only_development_layouts():
    assert {s.layout for s in build_specs()} <= set("ABCD")
def test_heldout_dataset_uses_layout_e_with_different_labels_and_order():
    specs = build_specs(seed=7, layouts=["E"])
    assert len(specs) == 100 and {s.layout for s in specs} == {"E"}
    s = next(x for x in specs if x.plain)
    text = document_text(s, seed=7)
    for label in ["Ref No:", "Dated:", "Pay By:", "Supplier:", "Gross Amount:", "Total:"]:
        assert label in text, label
    assert text.index("Pay By:") < text.index("Dated:") < text.index("Ref No:")
@pytest.mark.skipif(not (SAMPLE / "manifest.json").exists(), reason="sample dataset not generated")
def test_development_layouts_render_exactly_as_before(tmp_path):
    write_dataset(tmp_path)
    for p in sorted((SAMPLE / "invoices").glob("doc_*.txt")):
        regenerated = (tmp_path / "invoices" / p.name).read_text(encoding="utf-8")
        assert regenerated == p.read_text(encoding="utf-8"), p.name
def test_regex_survives_heldout_documents_without_errors():
    ex = RegexExtractor()
    for s in build_specs(seed=7, layouts=["E"]):
        res = ex.extract(document_text(s, 7))
        assert "extractor_error" not in res.signals
def test_cli_heldout_flag_writes_layout_e(tmp_path, monkeypatch):
    import json
    import sys
    from invoice_pipeline import dataset
    monkeypatch.setattr(sys, "argv", ["dataset", "--heldout", "--out", str(tmp_path), "--seed", "7"])
    dataset.main()
    gt = json.loads((tmp_path / "ground_truth" / "doc_0001.json").read_text(encoding="utf-8"))
    assert gt["layout"] == "E"
