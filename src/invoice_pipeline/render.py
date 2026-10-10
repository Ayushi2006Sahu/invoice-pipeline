"""Render an Invoice to plain text (and a text-based PDF).
LAYOUTS (A-D) are the development layouts. HELDOUT_LAYOUTS (E) exist only for a
generalisation check: the extractor is never told about them and is never tuned
on them.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional
from .models import Invoice
@dataclass(frozen=True)
class Layout:
    name: str
    title: str
    no_label: str
    date_label: str
    due_label: str
    subtotal_label: str
    discount_label: str
    tax_label: str
    total_label: str
    date_fmt: str          # clean layouts never use an ambiguous numeric format
    number_style: str      # "en" = 1,234.50   "eu" = 1.234,50
    currency_prefix: str
    show_currency_line: bool
    vendor_label: str = ""   # "" = vendor on its own line under the title
    header_order: tuple = ("no", "date", "due", "currency")
    items_header: tuple = ("Description", "Qty", "Unit Price", "Amount")
LAYOUTS: dict[str, Layout] = {
    l.name: l
    for l in [
        Layout("A", "INVOICE", "Invoice No", "Invoice Date", "Due Date",
               "Subtotal", "Discount", "Tax (18%)", "Total",
               "%d %b %Y", "en", "Rs. ", False),
        Layout("B", "TAX INVOICE", "Inv #", "Date", "Payment Due",
               "Sub Total", "Less Discount", "GST @18%", "Total Due",
               "%Y-%m-%d", "en", "", True),
        Layout("C", "INVOICE", "Bill Number", "Issued", "Due By",
               "Net Amount", "Rebate", "Tax", "Grand Total",
               "%d-%b-%Y", "en", "INR ", False),
        Layout("D", "INVOICE", "Invoice Number", "Invoice Date", "Due Date",
               "Subtotal", "Discount", "VAT 18%", "Total Amount",
               "%B %d, %Y", "eu", "INR ", False),
    ]
}
HELDOUT_LAYOUTS: dict[str, Layout] = {
    "E": Layout(
        "E", "SALES INVOICE", "Ref No", "Dated", "Pay By",
        "Gross Amount", "Concession", "IGST 18%", "Total",
        "%d %b %Y", "en", "INR ", False,
        vendor_label="Supplier",
        header_order=("due", "date", "no", "vendor"),
        items_header=("Item", "Units", "Rate", "Value"),
    ),
}
ALL_LAYOUTS: dict[str, Layout] = {**LAYOUTS, **HELDOUT_LAYOUTS}
WIDTH = 59
def fmt_money(value: Decimal, layout: Layout) -> str:
    s = format(abs(value), ",.2f")
    if layout.number_style == "eu":
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    sign = "-" if value < 0 else ""
    return f"{sign}{layout.currency_prefix}{s}"
def render_text(inv: Invoice, layout: Layout, date_fmt: Optional[str] = None) -> str:
    dfmt = date_fmt or layout.date_fmt
    title = "CREDIT NOTE" if inv.document_type == "credit_note" else layout.title
    lines = [title]
    if not layout.vendor_label:
        lines.append(inv.vendor)
    lines.append("")
    parts = {
        "no": f"{layout.no_label}: {inv.invoice_no}" if inv.invoice_no else None,
        "date": f"{layout.date_label}: {inv.invoice_date.strftime(dfmt)}" if inv.invoice_date else None,
        "due": f"{layout.due_label}: {inv.due_date.strftime(dfmt)}" if inv.due_date else None,
        "currency": f"Currency: {inv.currency}" if layout.show_currency_line else None,
        "vendor": f"{layout.vendor_label}: {inv.vendor}" if layout.vendor_label else None,
    }
    for key in layout.header_order:
        if parts.get(key):
            lines.append(parts[key])
    lines.append("")
    h = layout.items_header
    lines.append(f"{h[0]:<22}{h[1]:>5}{h[2]:>16}{h[3]:>16}")
    lines.append("-" * WIDTH)
    for it in inv.line_items:
        lines.append(
            f"{it.description:<22}{it.qty:>5}"
            f"{fmt_money(it.unit_price, layout):>16}{fmt_money(it.amount, layout):>16}"
        )
    lines.append("-" * WIDTH)
    for label, value in [
        (layout.subtotal_label, inv.subtotal),
        (layout.discount_label, inv.discount),
        (layout.tax_label, inv.tax),
        (layout.total_label, inv.total),
    ]:
        lines.append(f"{label + ':':<30}{fmt_money(value, layout):>29}")
    return "\n".join(lines) + "\n"
def render_pdf(text: str, path: Path) -> None:
    """Text-based PDF (real text layer, so pdfplumber can read it)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setFont("Courier", 9)
    y = A4[1] - 50
    for line in text.split("\n"):
        if y < 50:
            c.showPage()
            c.setFont("Courier", 9)
            y = A4[1] - 50
        c.drawString(40, y, line)
        y -= 12
    c.save()
