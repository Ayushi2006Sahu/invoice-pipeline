"""Synthetic invoice generator, part 1: data model, clean builder, ground truth.
Money is Decimal everywhere. All randomness comes from one seeded Random,
so the same seed always produces the same data.
"""
from __future__ import annotations
import json
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional
from .models import Invoice, LineItem
from .rules import DEFAULT_POLICY, Policy, expected_review, review_reasons
SEED = 42
CENT = Decimal("0.01")
TAX_RATE = Decimal("0.18")
PERIOD_START = date(2026, 1, 1)
VENDORS = [
    ("Acme Pvt Ltd", "ACM"),
    ("Zenith Corp", "ZEN"),
    ("Bharat Office Supplies Pvt Ltd", "BOS"),
    ("Nova Logistics Ltd", "NVL"),
    ("Sundar Electronics", "SUN"),
    ("Kaveri Foods Pvt Ltd", "KAV"),
    ("Orion Software Services", "ORS"),
    ("Himalaya Stationers", "HIM"),
]
ITEMS = [
    "Office chair", "A4 paper ream", "Laptop stand", "USB-C cable",
    "Printer toner", "Consulting hours", "Cloud hosting", "Courier charges",
    "Whiteboard", "Desk lamp", "Network switch", "Annual maintenance",
]
def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)
class InvoiceFactory:
    def __init__(self, seed: int = SEED, policy: Policy = DEFAULT_POLICY):
        self.rng = random.Random(seed)
        self.policy = policy
        self._counters: dict[str, int] = {}
    def _next_invoice_no(self, prefix: str, year: int) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{year}-{n:04d}"
    def _random_past_date(self) -> date:
        latest = self.policy.as_of_date - timedelta(days=1)
        span = (latest - PERIOD_START).days
        return PERIOD_START + timedelta(days=self.rng.randint(0, span))
    def clean(
        self,
        document_type: str = "invoice",
        discount: Optional[Decimal] = None,
        with_due_date: bool = True,
        vendor: Optional[tuple[str, str]] = None,
    ) -> Invoice:
        """A fully consistent document. Defects are injected later (part 2)."""
        rng = self.rng
        name, prefix = vendor or rng.choice(VENDORS)
        sign = Decimal(-1) if document_type == "credit_note" else Decimal(1)
        items: list[LineItem] = []
        for desc in rng.sample(ITEMS, rng.randint(1, 5)):
            qty = rng.randint(1, 10)
            unit = money(Decimal(rng.randint(5000, 500000)) / 100)
            items.append(LineItem(desc, qty, unit, sign * money(unit * qty)))
        subtotal = sum((i.amount for i in items), Decimal("0"))
        magnitude = abs(subtotal)
        if discount is None:
            if rng.random() < 0.5:
                disc_mag = Decimal("0.00")
            else:
                pct = Decimal(rng.randint(5, 15)) / 100
                disc_mag = money(magnitude * pct)
        else:
            disc_mag = abs(discount)
        tax_mag = money((magnitude - disc_mag) * TAX_RATE)
        total_mag = magnitude - disc_mag + tax_mag
        invoice_date = self._random_past_date()
        due_date = None
        if with_due_date:
            due_date = invoice_date + timedelta(days=rng.choice([15, 30, 45]))
        return Invoice(
            vendor=name,
            invoice_no=self._next_invoice_no(prefix, invoice_date.year),
            document_type=document_type,
            invoice_date=invoice_date,
            due_date=due_date,
            currency="INR",
            line_items=items,
            subtotal=subtotal,
            discount=sign * disc_mag,
            tax=sign * tax_mag,
            total=sign * total_mag,
        )
def _s(x: Decimal) -> str:
    """Decimal to string; avoids printing negative zero as -0.00."""
    return str(x + 0)
def invoice_fields(inv: Invoice) -> dict:
    """Printed values as plain JSON types. Money stays a string (no float)."""
    return {
        "vendor": inv.vendor,
        "invoice_no": inv.invoice_no,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "currency": inv.currency,
        "subtotal": _s(inv.subtotal),
        "discount": _s(inv.discount),
        "tax": _s(inv.tax),
        "total": _s(inv.total),
        "line_items": [
            {
                "description": i.description,
                "qty": i.qty,
                "unit_price": _s(i.unit_price),
                "amount": _s(i.amount),
            }
            for i in inv.line_items
        ],
    }
def ground_truth(
    doc_id: str,
    inv: Invoice,
    defects: list[str] | None = None,
    duplicate_of: Optional[str] = None,
    layout: Optional[str] = None,
    policy: Policy = DEFAULT_POLICY,
) -> dict:
    """Ground truth for one document.
    `fields` hold what is PRINTED (even if wrong). `expected_review` is never
    set by hand: it comes from the shared rules module.
    """
    defects = sorted(defects or [])
    return {
        "doc_id": doc_id,
        "document_type": inv.document_type,
        "layout": layout,
        "fields": invoice_fields(inv),
        "duplicate_of": duplicate_of,
        "expected_defects": defects,
        "expected_review": expected_review(defects, policy),
        "expected_review_reasons": review_reasons(defects, policy),
    }
def write_ground_truth(path: Path, gt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gt, indent=2, ensure_ascii=False), encoding="utf-8")

