"""Read a TXT or text-based PDF into plain text.
Failure is reported, never raised, and is NOT the same thing as garbled content:
  - a readable file whose content is junk or empty -> ok=True (validator decides later)
  - a file that cannot be read at all              -> ok=False with an error_code
NO_TEXT_LAYER means only "no extractable text was found on at least one page".
It does not prove that the PDF is image-based or scanned.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
FILE_NOT_FOUND = "FILE_NOT_FOUND"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
FILE_READ_ERROR = "FILE_READ_ERROR"
DECODE_ERROR = "DECODE_ERROR"
PDF_READ_ERROR = "PDF_READ_ERROR"
NO_TEXT_LAYER = "NO_TEXT_LAYER"
@dataclass(frozen=True)
class IngestResult:
    ok: bool
    text: str = ""
    file_format: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
def _fail(code: str, message: str, fmt: Optional[str] = None) -> IngestResult:
    return IngestResult(False, "", fmt, code, message)
def _read_txt(p: Path) -> IngestResult:
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return _fail(FILE_READ_ERROR, f"{type(exc).__name__}: {exc}", "txt")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return _fail(DECODE_ERROR, str(exc), "txt")
    return IngestResult(True, text.replace("\r\n", "\n").replace("\r", "\n"), "txt")
def _read_pdf(p: Path) -> IngestResult:
    try:
        import pdfplumber
        with pdfplumber.open(p) as pdf:
            pages = [(page.extract_text() or "") for page in pdf.pages]
    except Exception as exc:  # corrupt, encrypted, truncated ...
        return _fail(PDF_READ_ERROR, f"{type(exc).__name__}: {exc}", "pdf")
    empty = [n for n, t in enumerate(pages, 1) if not t.strip()]
    if not pages or empty:
        where = f"page(s) {empty}" if pages else "any page"
        return _fail(NO_TEXT_LAYER, f"no extractable text on {where}", "pdf")
    return IngestResult(True, "\n".join(pages), "pdf")
def ingest(path) -> IngestResult:
    p = Path(path)
    if not p.is_file():
        return _fail(FILE_NOT_FOUND, f"no such file: {p}")
    suffix = p.suffix.lower()
    if suffix == ".txt":
        return _read_txt(p)
    if suffix == ".pdf":
        return _read_pdf(p)
    return _fail(UNSUPPORTED_FORMAT, f"unsupported file type: {p.suffix!r}")
