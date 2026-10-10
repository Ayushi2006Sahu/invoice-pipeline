"""Extractor interface and the result shape every extractor returns.
An extractor only reads. It never judges: totals, signs, duplicates and
routing belong to the validators.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Protocol
REQUIRED_FIELDS = (
    "document_type", "vendor", "invoice_no", "invoice_date",
    "subtotal", "tax", "total", "line_items",
)
OPTIONAL_FIELDS = ("due_date", "discount", "currency")
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
def _blank_conf() -> dict:
    return {name: (None, 0.0) for name in ALL_FIELDS}
@dataclass
class ExtractedItem:
    description: Optional[str]
    qty: Optional[int]
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
@dataclass
class ExtractionResult:
    document_type: Optional[str] = None
    vendor: Optional[str] = None
    invoice_no: Optional[str] = None
    invoice_date: Optional[date] = None
    due_date: Optional[date] = None
    currency: Optional[str] = None
    subtotal: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    total: Optional[Decimal] = None
    line_items: list = field(default_factory=list)
    # field -> (raw_text, confidence). raw_text None = nothing matched;
    # raw_text set but confidence 0 = matched text that could not be parsed.
    field_conf: dict = field(default_factory=_blank_conf)
    signals: dict = field(default_factory=dict)
    @property
    def mean_required_field_confidence(self) -> float:
        return sum(self.field_conf[f][1] for f in REQUIRED_FIELDS) / len(REQUIRED_FIELDS)
    @property
    def required_field_coverage(self) -> float:
        return sum(1 for f in REQUIRED_FIELDS if self.field_conf[f][1] > 0) / len(REQUIRED_FIELDS)
class Extractor(Protocol):
    """Anything that turns document text into an ExtractionResult.
    Contract: extract() never raises. Unreadable input means fewer fields and
    lower confidence, plus signals that explain why.
    """
    name: str
    def extract(self, text: str) -> ExtractionResult: ...
