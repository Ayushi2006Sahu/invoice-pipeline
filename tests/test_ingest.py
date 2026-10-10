from invoice_pipeline.ingest import ingest
from invoice_pipeline.render import render_pdf
def test_txt_is_read_and_newlines_normalised(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"INVOICE\r\nAcme\r\n")
    r = ingest(p)
    assert r.ok and r.file_format == "txt" and r.text == "INVOICE\nAcme\n"
def test_empty_txt_is_readable_not_failed(tmp_path):
    p = tmp_path / "e.txt"
    p.write_text("", encoding="utf-8")
    r = ingest(p)
    assert r.ok and r.text == "" and r.error_code is None
def test_undecodable_txt_fails(tmp_path):
    p = tmp_path / "bad.txt"
    p.write_bytes(b"\xff\xfe\x00bad\x80")
    r = ingest(p)
    assert not r.ok and r.error_code == "DECODE_ERROR"
def test_corrupt_pdf_is_a_read_failure(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"this is not a pdf")
    r = ingest(p)
    assert not r.ok and r.error_code == "PDF_READ_ERROR"
    assert r.error_message
def test_pdf_without_text_is_no_text_layer(tmp_path):
    from reportlab.pdfgen import canvas
    p = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(p))
    c.rect(50, 50, 100, 100)     # a drawing, no text
    c.save()
    r = ingest(p)
    assert not r.ok and r.error_code == "NO_TEXT_LAYER"
def test_text_pdf_roundtrip(tmp_path):
    p = tmp_path / "ok.pdf"
    render_pdf("INVOICE\nAcme Pvt Ltd\n", p)
    r = ingest(p)
    assert r.ok and r.file_format == "pdf" and "Acme Pvt Ltd" in r.text
def test_missing_file(tmp_path):
    assert ingest(tmp_path / "nope.txt").error_code == "FILE_NOT_FOUND"
def test_unsupported_format(tmp_path):
    p = tmp_path / "x.docx"
    p.write_bytes(b"x")
    assert ingest(p).error_code == "UNSUPPORTED_FORMAT"
