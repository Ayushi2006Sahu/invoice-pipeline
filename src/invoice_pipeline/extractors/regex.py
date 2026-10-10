"""Baseline regex extractor.
Rules it lives by:
- Never raises. Bad text gives fewer fields and lower confidence.
- Does not know the layout of the document (no layout hint is accepted).
- Liberal about WHAT it captures (an invoice number is taken exactly as
  printed), strict about numbers and dates (unparseable -> None, confidence 0).
- Judges nothing. Signs, totals and duplicates are validator business.
- Number style (1,234.50 vs 1.234,50) comes from evidence inside the document.
"""
from __future__ import annotations
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from ..rules import DEFAULT_POLICY, Policy, is_ambiguous_date_text
from .base import ExtractedItem, ExtractionResult
SHAKY = 0.7          # parsed, but a human should look (ambiguous date, mixed number styles)
VENDOR_CONF = 0.9    # positional guess, not a labelled value
JUNK_CHARS = frozenset("*~^?|<>{}[]=_;")
HEADER_LABELS = {
    "invoice_no": re.compile(r"invoice no|inv ?#|bill number|invoice number"),
    "invoice_date": re.compile(r"invoice date|date|issued"),
    "due_date": re.compile(r"due date|payment due|due by"),
    "currency": re.compile(r"currency"),
}
TOTAL_LABELS = {
    "subtotal": re.compile(r"sub ?total|net amount"),
    "discount": re.compile(r"discount|less discount|rebate"),
    "tax": re.compile(r"tax(?: \(\d+%\))?|gst @ ?\d+%|vat \d+%"),
    "total": re.compile(r"total|total due|grand total|total amount"),
}
LABEL_LINE = re.compile(r"^\s*(?P<label>[^:\n]{1,40}?)\s*:\s*(?P<value>.*?)\s*$")
DASH_LINE = re.compile(r"^\s*-{5,}\s*$")
_MONEY = r"-?(?:(?:INR|Rs\.?)\s?)?-?\d[\d.,]*"
ROW = re.compile(
    rf"^\s*(?P<desc>.+?)\s+(?P<qty>\d+)\s+(?P<unit>{_MONEY})\s+(?P<amount>{_MONEY})\s*$",
    re.I,
)
MONEY_FULL = re.compile(
    r"^\s*(?P<n1>-)?\s*(?P<pre>INR|Rs\.?)?\s*(?P<n2>-)?\s*(?P<num>\d[\d.,]*)\s*$", re.I
)
NUMERIC_DATE = re.compile(r"^(\d{1,2})([/.-])(\d{1,2})\2(\d{4})$")
NAMED_DATE_FORMATS = ("%d %b %Y", "%d-%b-%Y", "%B %d, %Y")
# ---------------------------------------------------------------- numbers
def _classify(num: str):
    """Return (kind, style, decimal_mark).
    kind: 'ok' | 'ambiguous' | 'invalid'. style: 'en' | 'eu' | None (no evidence).
    decimal_mark: the decimal separator, or None when there is none.
    A 2-digit tail marks the decimal separator; a 3-digit tail is a thousands
    separator. A single '1,234' or '1.234' therefore carries no style evidence.
    """
    if num.isdigit():
        return "ok", None, None
    idx = max(num.rfind("."), num.rfind(","))
    sep, head, tail = num[idx], num[:idx], num[idx + 1:]
    other = "," if sep == "." else "."
    if not tail.isdigit():
        return "invalid", None, None
    if len(tail) == 2:
        if sep in head:
            return "invalid", None, None
        if other in head and not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(other)}\d{{3}})+", head):
            return "invalid", None, None
        return "ok", ("en" if sep == "." else "eu"), sep
    if len(tail) == 3:
        if other in num:
            return "invalid", None, None
        if not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(sep)}\d{{3}})+", num):
            return "invalid", None, None
        if num.count(sep) >= 2:   # repeated separator can only be thousands
            return "ok", ("en" if sep == "," else "eu"), None
        return "ambiguous", None, None
    return "invalid", None, None
def _to_decimal(num: str, doc_style: Optional[str]) -> Optional[Decimal]:
    kind, _style, dec = _classify(num)
    if kind == "invalid":
        return None
    if kind == "ambiguous":
        sep = num[-4]
        if (doc_style == "en" and sep == ",") or (doc_style == "eu" and sep == "."):
            return Decimal(re.sub(r"[.,]", "", num))
        return None   # no usable evidence: refuse to guess
    if dec is None:
        return Decimal(re.sub(r"[.,]", "", num))
    whole, frac = num.rsplit(dec, 1)
    return Decimal(re.sub(r"[.,]", "", whole) + "." + frac)
def document_number_style(nums) -> str:
    """'en' | 'eu' | 'conflict' | 'unknown', from unambiguous tokens only."""
    styles = set()
    for n in nums:
        kind, style, _ = _classify(n)
        if kind == "ok" and style:
            styles.add(style)
    if styles == {"en"}:
        return "en"
    if styles == {"eu"}:
        return "eu"
    if styles:
        return "conflict"
    return "unknown"
def parse_money(raw: str, style: Optional[str]) -> Optional[Decimal]:
    m = MONEY_FULL.match(raw or "")
    if not m:
        return None
    if m.group("n1") and m.group("n2"):
        return None   # "--5" or "-INR -5": two minus signs is garbage, not a number
    val = _to_decimal(m.group("num"), style)
    if val is None:
        return None
    if (m.group("n1") or m.group("n2")) and val != 0:
        val = -val
    return val
# ------------------------------------------------------------------ dates
def parse_date(raw: str, policy: Policy = DEFAULT_POLICY):
    """Return (date or None, confidence)."""
    s = (raw or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date(), 1.0
        except ValueError:
            return None, 0.0
    m = NUMERIC_DATE.match(s)
    if m:
        a, b, year = int(m.group(1)), int(m.group(3)), int(m.group(4))
        day, month = (a, b) if policy.date_order == "DMY" else (b, a)
        try:
            d = date(year, month, day)
        except ValueError:
            return None, 0.0
        return d, (SHAKY if is_ambiguous_date_text(s) else 1.0)
    for fmt in NAMED_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date(), 1.0
        except ValueError:
            pass
    return None, 0.0
# -------------------------------------------------------------- extractor
class RegexExtractor:
    name = "regex"
    def __init__(self, policy: Policy = DEFAULT_POLICY):
        self.policy = policy
    def extract(self, text: str) -> ExtractionResult:
        try:
            return self._extract(text or "")
        except Exception as exc:  # contract: never raise, say why instead
            res = ExtractionResult()
            res.signals = {
                "extractor_error": f"{type(exc).__name__}: {exc}",
                "text_empty": not (text or "").strip(),
            }
            return res
    def _extract(self, text: str) -> ExtractionResult:
        res = ExtractionResult()
        sig = res.signals
        conf = res.field_conf
        lines = text.replace("\r\n", "\n").split("\n")
        nonempty = [(i, ln) for i, ln in enumerate(lines) if ln.strip()]
        nonspace = [c for c in text if not c.isspace()]
        sig.update(
            text_empty=not nonempty,
            junk_ratio=(sum(c in JUNK_CHARS for c in nonspace) / len(nonspace)) if nonspace else 0.0,
            recognized_line_ratio=0.0,
            number_style="unknown",
            date_order=self.policy.date_order,
            table_found=False,
        )
        if not nonempty:
            return res
        recognized: set[int] = set()
        # 1. title -> document type; next line -> vendor (unless it looks labelled)
        first_i, first_line = nonempty[0]
        if re.search(r"\bcredit note\b", first_line, re.I):
            res.document_type = "credit_note"
        elif re.search(r"\binvoice\b", first_line, re.I):
            res.document_type = "invoice"
        if res.document_type:
            conf["document_type"] = (first_line.strip(), 1.0)
            recognized.add(first_i)
        if len(nonempty) > 1:
            vi, vline = nonempty[1]
            if not LABEL_LINE.match(vline) and not DASH_LINE.match(vline):
                res.vendor = vline.strip()
                conf["vendor"] = (res.vendor, VENDOR_CONF)
                recognized.add(vi)
        # 2. labelled lines (anchored, exact label match, first occurrence wins)
        header_raw: dict[str, str] = {}
        money_raw: dict[str, str] = {}
        for i, ln in nonempty:
            m = LABEL_LINE.match(ln)
            if not m:
                continue
            label = " ".join(m.group("label").lower().split())
            value = m.group("value")
            for fld, pat in HEADER_LABELS.items():
                if pat.fullmatch(label):
                    header_raw.setdefault(fld, value)
                    recognized.add(i)
                    break
            else:
                for fld, pat in TOTAL_LABELS.items():
                    if pat.fullmatch(label):
                        money_raw.setdefault(fld, value)
                        recognized.add(i)
                        break
        # 3. line item table: rows between the first two dashed lines
        dashes = [i for i, ln in enumerate(lines) if DASH_LINE.match(ln)]
        recognized.update(dashes)
        parsed_rows = []
        if len(dashes) >= 2:
            sig["table_found"] = True
            for j in range(dashes[0] - 1, -1, -1):   # header row above the first dash
                if lines[j].strip():
                    recognized.add(j)
                    break
            for i in range(dashes[0] + 1, dashes[1]):
                if lines[i].strip():
                    parsed_rows.append((i, ROW.match(lines[i])))
        # 4. document-level number style, from unambiguous tokens only
        tokens = []
        for raw in money_raw.values():
            mm = MONEY_FULL.match(raw)
            if mm:
                tokens.append(mm.group("num"))
        for _i, m in parsed_rows:
            if m:
                for g in ("unit", "amount"):
                    mm = MONEY_FULL.match(m.group(g))
                    if mm:
                        tokens.append(mm.group("num"))
        style = document_number_style(tokens)
        sig["number_style"] = style
        cap = SHAKY if style == "conflict" else 1.0
        # 5. money fields
        for fld in ("subtotal", "discount", "tax", "total"):
            if fld in money_raw:
                raw = money_raw[fld]
                val = parse_money(raw, style)
                setattr(res, fld, val)
                conf[fld] = (raw, 0.0 if val is None else cap)
        # 6. line items
        if sig["table_found"]:
            bad = 0
            for i, m in parsed_rows:
                if m is None:
                    bad += 1
                    continue
                recognized.add(i)
                unit = parse_money(m.group("unit"), style)
                amount = parse_money(m.group("amount"), style)
                if unit is None or amount is None:
                    bad += 1
                res.line_items.append(
                    ExtractedItem(m.group("desc").strip(), int(m.group("qty")), unit, amount)
                )
            conf["line_items"] = (
                f"{len(parsed_rows)} row(s)",
                SHAKY if (bad or style == "conflict") else 1.0,
            )
        # 7. header fields
        if "invoice_no" in header_raw:
            v = header_raw["invoice_no"].strip()
            res.invoice_no = v or None          # liberal: taken exactly as printed
            conf["invoice_no"] = (v, 1.0 if v else 0.0)
        for fld in ("invoice_date", "due_date"):
            if fld in header_raw:
                raw = header_raw[fld].strip()
                d, c = parse_date(raw, self.policy)
                setattr(res, fld, d)
                conf[fld] = (raw, c)
        # 8. currency: labelled line, else the prefix on a money value
        if "currency" in header_raw:
            v = header_raw["currency"].strip().upper()
            res.currency = v or None
            conf["currency"] = (v, 1.0 if v else 0.0)
        else:
            candidates = list(money_raw.values()) + [m.group("unit") for _i, m in parsed_rows if m]
            for raw in candidates:
                mm = MONEY_FULL.match(raw)
                if mm and mm.group("pre"):
                    res.currency = "INR"       # INR and Rs. are both rupees
                    conf["currency"] = (mm.group("pre"), 0.9)
                    break
        seen = {i for i, _ in nonempty}
        sig["recognized_line_ratio"] = len(recognized & seen) / len(nonempty)
        return res
