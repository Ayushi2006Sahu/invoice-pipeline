"""Synthetic invoice generator, part 2: defect injection and dataset files.
Run:  python -m invoice_pipeline.dataset --out data/sample
"""
from __future__ import annotations
import argparse
import copy
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional
from .generate import SEED, VENDORS, InvoiceFactory, ground_truth, money, write_ground_truth
from .models import Invoice
from .render import ALL_LAYOUTS, LAYOUTS, render_pdf, render_text
from .rules import DEFAULT_POLICY, DocKey, Policy, classify_duplicate
LAYOUT_NAMES = list(LAYOUTS)
@dataclass
class Spec:
    doc_id: str = ""
    inv: Optional[Invoice] = None
    defects: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    render: dict = field(default_factory=dict)   # date_fmt override, garble mode
    formats: tuple = ("txt", "pdf")
    layout: str = "A"
    duplicate_of: Optional[str] = None
    conflicts_with: Optional[str] = None
    plain: bool = False
def doc_key(inv: Invoice) -> DocKey:
    return DocKey(inv.vendor, inv.invoice_no, inv.invoice_date, inv.total)
# ---------------------------------------------------------------- injectors
def _neg(x: Decimal) -> Decimal:
    return -x if x else x   # never produce Decimal("-0.00")
def inj_total_mismatch(inv, rng, policy):
    delta = money(Decimal(rng.randint(100, 5000)) / 100)
    if rng.random() < 0.5 or inv.total - delta <= 0:
        inv.total += delta
    else:
        inv.total -= delta
def inj_subtotal_mismatch(inv, rng, policy):
    # Printed subtotal is wrong, total stays consistent with the printed subtotal.
    inv.subtotal += money(Decimal(rng.randint(100, 5000)) / 100)
    inv.total = inv.subtotal - inv.discount + inv.tax
def inj_due_before_invoice(inv, rng, policy):
    inv.due_date = inv.invoice_date - timedelta(days=rng.randint(1, 20))
def inj_no_line_items(inv, rng, policy):
    zero = Decimal("0.00")
    inv.line_items = []
    inv.subtotal = inv.discount = inv.tax = inv.total = zero
def inj_invalid_id(inv, rng, policy):
    pre, year, num = inv.invoice_no.split("-")
    inv.invoice_no = rng.choice([
        f"{pre} {year}/{int(num)}",
        f"{pre}-{year[2:]}-{int(num)}",
        inv.invoice_no.lower(),
        f"{pre}{year}-{num}",
    ])
def inj_ambiguous_date(inv, rng, policy):
    while True:
        month, day = rng.randint(1, 9), rng.randint(1, 12)
        if day != month:
            break
    inv.invoice_date = date(2026, month, day)
    if inv.due_date:
        inv.due_date = inv.invoice_date + timedelta(days=30)
    return {"date_fmt": "%d/%m/%Y"}
def inj_future_date(inv, rng, policy):
    inv.invoice_date = policy.as_of_date + timedelta(days=rng.randint(10, 70))
    inv.due_date = inv.invoice_date + timedelta(days=30)
def inj_negative_on_invoice(inv, rng, policy):
    for it in inv.line_items:
        it.amount = _neg(it.amount)
    inv.subtotal, inv.discount, inv.tax, inv.total = (
        _neg(inv.subtotal), _neg(inv.discount), _neg(inv.tax), _neg(inv.total))
def inj_credit_note_positive(inv, rng, policy):
    inv.document_type = "credit_note"    # amounts stay positive: wrong sign
def inj_missing_invoice_no(inv, rng, policy):
    inv.invoice_no = None
def inj_missing_date(inv, rng, policy):
    inv.invoice_date = None
    inv.due_date = None
def garble(text: str, mode: str, rng: random.Random) -> str:
    """Corrupted-input stress test (NOT real OCR)."""
    if mode == "empty":
        return ""
    if mode == "truncated":
        return text[: int(len(text) * rng.uniform(0.3, 0.5))]
    if mode == "char_swap":
        swap = {"O": "0", "0": "O", "l": "1", "1": "l", "5": "S", "8": "B"}
        return "".join(swap[c] if c in swap and rng.random() < 0.5 else c for c in text)
    if mode == "noise":
        return "".join(
            rng.choice("#@%&*~^?") if (not c.isspace() and rng.random() < 0.3) else c
            for c in text)
    raise ValueError(mode)
# ---------------------------------------------------------------- planning
def build_specs(seed: int = SEED, policy: Policy = DEFAULT_POLICY,
                layouts: Optional[list[str]] = None) -> list[Spec]:
    names = layouts or LAYOUT_NAMES
    f = InvoiceFactory(seed, policy)
    rng = random.Random(seed + 1)
    specs: list[Spec] = []
    def add(inv, defects=(), tags=(), render=None, formats=("txt", "pdf"), plain=False):
        specs.append(Spec(inv=inv, defects=list(defects), tags=list(tags),
                          render=render or {}, formats=formats, plain=plain))
    def defective(n, defects, *injectors, **factory_kw):
        for _ in range(n):
            inv = f.clean(**factory_kw)
            render: dict = {}
            for fn in injectors:
                render.update(fn(inv, rng, policy) or {})
            add(inv, defects, render=render)
    # --- clean (must NOT be flagged)
    for _ in range(29):
        add(f.clean(), plain=True)
    for _ in range(4):
        add(f.clean(document_type="credit_note"), tags=["VALID_CREDIT_NOTE"])
    for _ in range(4):
        add(f.clean(with_due_date=False), tags=["NO_DUE_DATE"])
    for _ in range(4):
        add(f.clean(discount=Decimal("0.00")), tags=["ZERO_DISCOUNT"])
    for _ in range(4):
        inv = f.clean()
        inv.total += Decimal("0.01") * rng.choice([1, -1])
        add(inv, tags=["ROUNDING_WITHIN_TOLERANCE"])
    for _ in range(3):
        a = f.clean()
        other = next(v for v in VENDORS if v[0] != a.vendor)
        b = f.clean(vendor=other)
        b.invoice_no = a.invoice_no
        add(a)
        add(b, tags=["SAME_ID_DIFFERENT_VENDOR"])
    # --- defective
    defective(4, ["TOTAL_MISMATCH"], inj_total_mismatch)
    defective(4, ["SUBTOTAL_MISMATCH"], inj_subtotal_mismatch)
    defective(4, ["DUE_BEFORE_INVOICE"], inj_due_before_invoice)
    defective(3, ["NO_LINE_ITEMS"], inj_no_line_items)
    defective(4, ["INVALID_INVOICE_ID"], inj_invalid_id)
    defective(4, ["AMBIGUOUS_DATE"], inj_ambiguous_date)
    defective(3, ["FUTURE_INVOICE_DATE"], inj_future_date)
    defective(3, ["NEGATIVE_AMOUNT_ON_INVOICE"], inj_negative_on_invoice)
    defective(3, ["CREDIT_NOTE_SIGN_MISMATCH"], inj_credit_note_positive)
    defective(2, ["MISSING_FIELD"], inj_missing_invoice_no)
    defective(1, ["MISSING_FIELD"], inj_missing_date)
    defective(1, ["TOTAL_MISMATCH", "DUE_BEFORE_INVOICE"],
              inj_total_mismatch, inj_due_before_invoice)
    defective(1, ["SUBTOTAL_MISMATCH", "AMBIGUOUS_DATE"],
              inj_subtotal_mismatch, inj_ambiguous_date)
    for mode in ["char_swap", "char_swap", "truncated", "truncated", "empty", "noise"]:
        add(f.clean(), ["GARBLED_TEXT"], render={"garble": mode}, formats=("txt",))
    rng.shuffle(specs)
    for i, s in enumerate(specs, 1):
        s.doc_id = f"doc_{i:04d}"
        s.layout = rng.choice(names)
    # --- duplicates always come AFTER their originals
    originals = rng.sample([s for s in specs if s.plain], 6)
    for orig in originals[:3]:
        layout = rng.choice([n for n in names if n != orig.layout] or names)
        specs.append(Spec(
            doc_id=f"doc_{len(specs) + 1:04d}", inv=copy.deepcopy(orig.inv),
            defects=["DUPLICATE_INVOICE"], duplicate_of=orig.doc_id, layout=layout))
    prefixes = dict(VENDORS)
    for orig in originals[3:]:
        for _ in range(50):
            new = f.clean(vendor=(orig.inv.vendor, prefixes[orig.inv.vendor]))
            new.invoice_no = orig.inv.invoice_no
            if classify_duplicate(doc_key(orig.inv), doc_key(new)) == "DUPLICATE_ID_CONFLICT":
                break
        else:
            raise RuntimeError("could not build a conflicting duplicate")
        specs.append(Spec(
            doc_id=f"doc_{len(specs) + 1:04d}", inv=new,
            defects=["DUPLICATE_ID_CONFLICT"], conflicts_with=orig.doc_id,
            layout=rng.choice(names)))
    return specs
# ---------------------------------------------------------------- output
def document_text(s: Spec, seed: int = SEED) -> str:
    text = render_text(s.inv, ALL_LAYOUTS[s.layout], s.render.get("date_fmt"))
    mode = s.render.get("garble")
    if mode:
        text = garble(text, mode, random.Random(f"{seed}-{s.doc_id}"))
    return text
def write_dataset(out: Path, seed: int = SEED, policy: Policy = DEFAULT_POLICY,
                  layouts: Optional[list[str]] = None) -> dict:
    specs = build_specs(seed, policy, layouts)
    inv_dir, gt_dir = out / "invoices", out / "ground_truth"
    for d in (inv_dir, gt_dir):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("doc_*"):
            old.unlink()
    truths = []
    for s in specs:
        text = document_text(s, seed)
        (inv_dir / f"{s.doc_id}.txt").write_text(text, encoding="utf-8", newline="\n")
        if "pdf" in s.formats:
            render_pdf(text, inv_dir / f"{s.doc_id}.pdf")
        layout = ALL_LAYOUTS[s.layout]
        gt = ground_truth(s.doc_id, s.inv, s.defects, s.duplicate_of, layout.name, policy)
        gt["conflicts_with"] = s.conflicts_with
        gt["tags"] = s.tags
        gt["fields_readable"] = "garble" not in s.render
        gt["render"] = {
            "date_format": s.render.get("date_fmt") or layout.date_fmt,
            "number_style": layout.number_style,
            "formats": list(s.formats),
            "garble_mode": s.render.get("garble"),
        }
        write_ground_truth(gt_dir / f"{s.doc_id}.json", gt)
        truths.append(gt)
    manifest = {
        "seed": seed,
        "as_of_date": policy.as_of_date.isoformat(),
        "total_documents": len(truths),
        "clean_documents": sum(1 for t in truths if not t["expected_defects"]),
        "expected_in_review": sum(1 for t in truths if t["expected_review"]),
        "defect_counts": dict(sorted(Counter(
            d for t in truths for d in t["expected_defects"]).items())),
        "tag_counts": dict(sorted(Counter(g for t in truths for g in t["tags"]).items())),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic invoices + ground truth")
    ap.add_argument("--out", default="data/sample")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--heldout", action="store_true", help="layout E only")
    args = ap.parse_args()
    print(json.dumps(write_dataset(Path(args.out), args.seed, layouts=["E"] if args.heldout else None), indent=2))
if __name__ == "__main__":
    main()
