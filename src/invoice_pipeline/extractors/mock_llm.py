"""Mock LLM clients for tests and demos. Nothing here is a model.
MockLLMClient answers from a fixed table. oracle_client_from_dataset() builds
that table from the ground truth, so it only proves the plumbing works.
FaultyClient wraps a client and misbehaves on purpose (bad JSON, timeouts,
confidently wrong values) so validators and evaluation can be tested.
"""
from __future__ import annotations
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from ..ingest import ingest
from .llm import document_from_prompt
MODES = ("invalid_json", "truncated", "empty", "timeout", "wrong_total", "wrong_vendor")
class MockLLMClient:
    name = "mock"
    def __init__(self, responses: dict):
        self.responses = responses          # document text -> JSON string
        self.calls = 0
    def complete(self, prompt: str) -> str:
        self.calls += 1
        try:
            return self.responses[document_from_prompt(prompt)]
        except KeyError:
            raise LookupError("mock fixture has no scripted answer for this document") from None
def _answer(gt: dict) -> dict:
    f, fmt = gt["fields"], gt["render"]["date_format"]
    show = lambda iso: date.fromisoformat(iso).strftime(fmt) if iso else None   # date as printed
    return {
        "document_type": gt["document_type"], "vendor": f["vendor"],
        "invoice_no": f["invoice_no"], "invoice_date": show(f["invoice_date"]),
        "due_date": show(f["due_date"]), "currency": f["currency"],
        "subtotal": f["subtotal"], "discount": f["discount"], "tax": f["tax"], "total": f["total"],
        "line_items": [{"description": i["description"], "qty": i["qty"],
                        "unit_price": i["unit_price"], "amount": i["amount"]}
                       for i in f["line_items"]],
    }
def oracle_client_from_dataset(data_dir) -> MockLLMClient:
    """Perfect answers for every readable document. Garbled ones get no answer."""
    data_dir = Path(data_dir)
    inv_dir = data_dir / "invoices"
    responses: dict = {}
    for gt_path in sorted((data_dir / "ground_truth").glob("doc_*.json")):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        if not gt.get("fields_readable", True):
            continue
        pdf = inv_dir / f"{gt['doc_id']}.pdf"
        ing = ingest(pdf if pdf.exists() else inv_dir / f"{gt['doc_id']}.txt")
        if ing.ok:
            responses[ing.text] = json.dumps(_answer(gt))
    return MockLLMClient(responses)
class FaultyClient:
    """Wraps a client; `on(n)` says whether call number n (1-based) misbehaves."""
    def __init__(self, inner, mode: str, on=None):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; choose from {MODES}")
        self.inner, self.mode, self.on = inner, mode, on or (lambda n: True)
        self.name = f"mock-{mode}"
        self.calls = 0
    def complete(self, prompt: str) -> str:
        self.calls += 1
        if not self.on(self.calls):
            return self.inner.complete(prompt)
        if self.mode == "timeout":
            raise TimeoutError("simulated timeout")
        raw = self.inner.complete(prompt)
        if self.mode == "invalid_json":
            return "Sure! Here is the invoice you asked for."
        if self.mode == "truncated":
            return raw[: len(raw) // 2]
        if self.mode == "empty":
            return ""
        data = json.loads(raw)
        if self.mode == "wrong_total" and data.get("total") is not None:
            data["total"] = str(Decimal(data["total"]) + Decimal("12345.67"))
        if self.mode == "wrong_vendor" and data.get("vendor") is not None:
            data["vendor"] += " X"
        return json.dumps(data)
