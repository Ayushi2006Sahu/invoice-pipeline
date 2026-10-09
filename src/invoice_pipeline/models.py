from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional
@dataclass
class LineItem:
    description: str
    qty: int
    unit_price: Decimal
    amount: Decimal  # signed: negative on credit notes
@dataclass
class Invoice:
    vendor: str
    invoice_no: str
    document_type: str  # "invoice" or "credit_note"
    invoice_date: date
    due_date: Optional[date]
    currency: str
    line_items: list[LineItem]
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
