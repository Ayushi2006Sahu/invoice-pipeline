"""LLM extractor behind a one-method client interface.
Only LLMClient.complete(prompt) -> str talks to a model. Everything else
(prompt, parsing, canonical types, confidence) is plain code, so a real backend
can be swapped in without touching the pipeline, validators or evaluation.
Contract (same as every extractor): extract() never raises. A client exception,
invalid JSON or an empty reply gives an empty result plus signals["llm_error"].
Confidence is a fixed placeholder, NOT a model score: 0.9 when a value was
returned and parsed, 0.0 when it is absent or unusable.
Signals: text_empty, junk_ratio, table_found, date_order, llm_error. There is no
recognized_line_ratio (an LLM has no such notion), so GARBLED_TEXT can fire for
this extractor only on empty text or junk characters.
"""
from __future__ import annotations
import json
import re
from decimal import Decimal
from typing import Optional, Protocol
from ..rules import DEFAULT_POLICY, Policy
from .base import ExtractedItem, ExtractionResult
from .regex import JUNK_CHARS, parse_date
FIELD_CONFIDENCE = 0.9
DOC_OPEN, DOC_CLOSE = "<document>\n", "\n</document>"
PROMPT_HEAD = (
    "You read one invoice or credit note and return its fields as JSON.\n"
    "Return ONLY one JSON object, no explanation, with these keys:\n"
    "document_type (\"invoice\" or \"credit_note\"), vendor, invoice_no, invoice_date,\n"
    "due_date, currency, subtotal, discount, tax, total, line_items.\n"
    "line_items is a list of objects with description, qty, unit_price, amount.\n"
    "Rules:\n"
    "- Copy invoice_no, invoice_date and due_date exactly as printed. Do not reformat dates.\n"
    "- Money values are strings with a dot as decimal mark, no thousands separators and no\n"
    "  currency symbol, for example \"1234.50\". Keep a minus sign if one is printed.\n"
    "- qty is an integer.\n"
    "- Use null for anything that is not printed. Do not guess, calculate or correct anything.\n"
)
_MONEY = re.compile(r"-?\d+(?:\.\d{1,2})?")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
class LLMClient(Protocol):
    name: str
    def complete(self, prompt: str) -> str: ...
def build_prompt(text: str) -> str:
    return PROMPT_HEAD + DOC_OPEN + text + DOC_CLOSE
def document_from_prompt(prompt: str) -> str:
    i = prompt.index(DOC_OPEN) + len(DOC_OPEN)
    return prompt[i:prompt.rindex(DOC_CLOSE)]
def parse_response(raw) -> tuple:
    """Return (dict, None) or (None, error_text)."""
    if not isinstance(raw, str):
        return None, "INVALID_RESPONSE: client did not return text"
    s = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        data = None
        if i != -1 and j > i:
            try:
                data = json.loads(s[i:j + 1])
            except ValueError:
                data = None
        if data is None:
            return None, "INVALID_JSON: reply is not a complete JSON object"
    if not isinstance(data, dict):
        return None, "INVALID_JSON: top-level value is not an object"
    return data, None
def _text(v) -> Optional[str]:
    return (v.strip() or None) if isinstance(v, str) else None
def _doc_type(v) -> Optional[str]:
    s = _text(v)
    return s.lower() if s and s.lower() in ("invoice", "credit_note") else None
def _currency(v) -> Optional[str]:
    s = _text(v)
    return s.upper() if s else None
def _money(v) -> Optional[Decimal]:
    """Strict: JSON string with a dot decimal and at most 2 decimals. Never a float."""
    if not isinstance(v, str) or not _MONEY.fullmatch(v.strip()):
        return None
    d = Decimal(v.strip())
    return d + 0 if d == 0 else d       # no negative zero
def _field(data: dict, key: str, parse):
    """-> (value, raw_text, confidence). Absent -> (None, None, 0). Unusable -> (None, raw, 0)."""
    v = data.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None, 0.0
    raw = v.strip() if isinstance(v, str) else json.dumps(v)
    val = parse(v)
    return (val, raw, FIELD_CONFIDENCE) if val is not None else (None, raw, 0.0)
class LLMExtractor:
    def __init__(self, client, policy: Policy = DEFAULT_POLICY):
        self.client = client
        self.policy = policy
        self.name = f"llm-{getattr(client, 'name', 'unknown')}"
        self.model = getattr(client, "name", None)
    def extract(self, text: str) -> ExtractionResult:
        try:
            return self._extract(text or "")
        except Exception as exc:  # contract: never raise
            res = ExtractionResult()
            res.signals = {"extractor_error": f"{type(exc).__name__}: {exc}",
                           "text_empty": not (text or "").strip(), "table_found": False}
            return res
    def _date(self, v) -> Optional[object]:
        return parse_date(v.strip(), self.policy)[0] if isinstance(v, str) else None
    def _extract(self, text: str) -> ExtractionResult:
        res = ExtractionResult()
        sig = res.signals
        nonspace = [c for c in text if not c.isspace()]
        sig.update(
            text_empty=not text.strip(),
            junk_ratio=(sum(c in JUNK_CHARS for c in nonspace) / len(nonspace)) if nonspace else 0.0,
            table_found=False,
            date_order=self.policy.date_order,
        )
        if sig["text_empty"]:
            return res                          # nothing to ask the model about
        try:
            raw = self.client.complete(build_prompt(text))
        except Exception as exc:
            sig["llm_error"] = f"CLIENT_ERROR: {type(exc).__name__}: {exc}"
            return res
        data, err = parse_response(raw)
        if err:
            sig["llm_error"] = err
            return res
        parsers = {
            "document_type": _doc_type, "vendor": _text, "invoice_no": _text,
            "invoice_date": self._date, "due_date": self._date, "currency": _currency,
            "subtotal": _money, "discount": _money, "tax": _money, "total": _money,
        }
        for name, parse in parsers.items():
            val, rawtxt, conf = _field(data, name, parse)
            setattr(res, name, val)
            res.field_conf[name] = (rawtxt, conf)
        items = data.get("line_items")
        if isinstance(items, list):
            sig["table_found"] = True
            for row in items:
                if not isinstance(row, dict):
                    res.line_items.append(ExtractedItem(None, None, None, None))
                    continue
                qty = row.get("qty")
                qty = qty if isinstance(qty, int) and not isinstance(qty, bool) else None
                res.line_items.append(ExtractedItem(
                    _text(row.get("description")), qty,
                    _money(row.get("unit_price")), _money(row.get("amount"))))
            res.field_conf["line_items"] = (f"{len(items)} row(s)", FIELD_CONFIDENCE)
        return res
