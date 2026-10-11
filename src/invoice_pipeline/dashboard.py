"""Static HTML summary page built from reports/comparison.json (and optionally the DB).
No JavaScript, no libraries, no network. Every number comes from the report or the
database; nothing is typed in. Charts are CSS bars.
"""
from __future__ import annotations
import html
import json
from pathlib import Path
from .compare import EXACT, REVIEW
from .db import connect, from_minor
from .evaluate import pct
e = html.escape
LABELS = {
    "document_type": "Document type", "vendor": "Vendor", "invoice_no": "Invoice number",
    "invoice_date": "Invoice date", "due_date": "Due date", "currency": "Currency",
    "subtotal_minor": "Subtotal", "discount_minor": "Discount", "tax_minor": "Tax",
    "total_minor": "Total", "line_items": "Line items",
}
def nice(name):
    return LABELS.get(name, name)
def readable(text):
    for raw, label in LABELS.items():
        text = text.replace(raw, label)
    return text
CSS = """
:root{--bg:#0f172a;--card:#1e293b;--tx:#e2e8f0;--mut:#94a3b8;--g:#22c55e;--y:#eab308;--r:#ef4444;--b:#38bdf8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,Segoe UI,sans-serif;padding:24px;max-width:1100px;margin:auto}
h1{margin:0 0 4px}h2{margin:32px 0 12px;font-size:18px;border-bottom:1px solid #334155;padding-bottom:6px}
.sub{color:var(--mut)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.card{background:var(--card);border-radius:10px;padding:14px}.big{font-size:30px;font-weight:700}.lab{color:var(--mut);font-size:13px}
.row{display:grid;grid-template-columns:230px 1fr;gap:10px;align-items:center;margin:8px 0}
.bar{background:#334155;border-radius:6px;height:22px;position:relative;overflow:hidden}
.fill{height:100%;border-radius:6px}.val{position:absolute;right:8px;top:0;font-size:12px;line-height:22px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #334155}
.tag{padding:1px 7px;border-radius:9px;font-size:12px}.hard{background:#7f1d1d}.soft{background:#713f12}.uncertain{background:#1e3a5f}
.banner{background:#422006;border-left:4px solid var(--y);padding:10px 14px;border-radius:6px;margin:10px 0}
.good{color:var(--g)}.bad{color:var(--r)}.warn{color:var(--y)}code{background:#0b1220;padding:1px 5px;border-radius:4px}
"""
def _bar(label, ratio, color):
    rate = ratio["rate"]
    w = 0 if rate is None else rate * 100
    return (f'<div class="row"><div>{e(label)}</div><div class="bar">'
            f'<div class="fill" style="width:{w:.1f}%;background:{color}"></div>'
            f'<div class="val">{e(pct(ratio))}</div></div></div>')
def _card(value, label, cls=""):
    return f'<div class="card"><div class="big {cls}">{e(str(value))}</div><div class="lab">{e(label)}</div></div>'
def _db_sections(db, run_id):
    conn = connect(db)
    try:
        run_id = run_id or conn.execute("SELECT MAX(run_id) FROM runs").fetchone()[0]
        queue = conn.execute(
            "SELECT i.invoice_row_id, i.doc_id, i.invoice_no FROM review_queue q "
            "JOIN invoices i ON i.invoice_row_id = q.invoice_row_id WHERE i.run_id = ? "
            "ORDER BY i.processing_order", (run_id,)).fetchall()
        rows, seen = [], set()
        for r in queue:
            reasons = conn.execute(
                "SELECT check_code, severity FROM validation_results WHERE invoice_row_id = ? "
                "AND passed = 0 ORDER BY check_code", (r["invoice_row_id"],)).fetchall()
            if reasons and reasons[0]["check_code"] not in seen and len(rows) < 10:
                seen.add(reasons[0]["check_code"])
                tags = " ".join(f'<span class="tag {x["severity"]}">{e(x["check_code"])}</span>' for x in reasons)
                rows.append(f"<tr><td>{e(r['doc_id'])}</td><td>{e(r['invoice_no'] or '-')}</td><td>{tags}</td></tr>")
        out = [f"<h2>Human review queue ({len(queue)} documents, one example per first reason)</h2>",
               "<table><tr><th>Document</th><th>Invoice no</th><th>Failed checks</th></tr>" + "".join(rows) + "</table>"]
        row = conn.execute(
            "SELECT i.invoice_row_id, i.doc_id, i.status, i.subtotal_minor, i.discount_minor, "
            "i.tax_minor, i.total_minor FROM invoices i JOIN validation_results v "
            "ON v.invoice_row_id = i.invoice_row_id WHERE i.run_id = ? AND v.check_code = 'TOTAL_MISMATCH' "
            "AND v.passed = 0 ORDER BY i.doc_id LIMIT 1", (run_id,)).fetchone()
        if row:
            calc = from_minor(row["subtotal_minor"]) - from_minor(row["discount_minor"] or 0) + from_minor(row["tax_minor"])
            printed = from_minor(row["total_minor"])
            out += ["<h2>Failure walkthrough: a total that does not add up</h2>", '<div class="card">',
                    f"<p><b>{e(row['doc_id'])}</b></p>",
                    f"<p>Calculated (subtotal - discount + tax): <b>INR {calc:,.2f}</b><br>"
                    f"Printed total: <b>INR {printed:,.2f}</b><br>"
                    f"Difference: <b class='bad'>INR {printed - calc:,.2f}</b></p>",
                    f"<p>Validation: <b class='bad'>FAILED (TOTAL_MISMATCH)</b> &middot; Routing: "
                    f"<b class='warn'>{e(row['status'].upper())}</b> &middot; Auto-approved: <b>NO</b></p></div>"]
        return "".join(out)
    finally:
        conn.close()
def build_dashboard(reports_dir, db=None, run_id=None) -> str:
    cmp = json.loads((Path(reports_dir) / "comparison.json").read_text(encoding="utf-8"))
    m, runs, f = cmp["metrics"], cmp["runs"], cmp["finding"]
    d, h = runs["dev"], runs["heldout"]
    bad = sum(len(v) for v in cmp["integrity"].values())
    p = ["<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Invoice pipeline summary</title><style>{CSS}</style></head><body>",
         "<h1>Invoice Information Pipeline</h1>",
         "<div class='sub'>Synthetic data &middot; Python &middot; SQLite &middot; extract, validate, route to review, report accuracy</div>",
         "<h2>Headline</h2><div class='grid'>",
         _card(d["documents"], "documents (development set)"),
         _card(d["auto_approved"], "auto-approved", "good"),
         _card(d["needs_review"], "sent to human review", "warn"),
         _card("{} of {}".format(*(lambda r: (r["n"], r["d"]))(
               m["dev"]["Bad auto-approvals (auto-approved but wrong or defective)"])),
               "bad auto-approvals (dev)", "good"),
         _card(pct(m["heldout"][EXACT]), "held-out exact match", "bad"),
         "</div>",
         "<p class='sub'>Bad auto-approval is judged against the ground truth (a wrong extracted field or "
         "an expected defect), not by the validator's own decision.</p>",
         "<h2>Regex: development layouts vs unseen layout</h2>"]
    for key, label in ((EXACT, "Exact-match documents"), (REVIEW, "Review rate")):
        p.append(_bar(label + " (development)", m["dev"][key], "var(--g)"))
        p.append(_bar(label + " (held-out)", m["heldout"][key], "var(--r)" if key == EXACT else "var(--y)"))
    p.append("<p class='sub'>Development numbers are circular (generator and regex share an author). "
             "The held-out layout is the honest generalisation check.</p>")
    hf = [x for x in cmp["fields"]]
    ok = [nice(x["field"]) for x in hf if x["heldout"]["missing"] == 0 and x["heldout"]["wrong"] == 0]
    gone = [nice(x["field"]) for x in hf if x["heldout"]["missing"] > 0]
    wrong_total = sum(x["heldout"]["wrong"] for x in hf)
    p.append("<div class='banner'><b>{} wrong values does not mean the extraction was right.</b> "
             "Exact match on the unseen layout is {}. Read correctly in every document: {}. "
             "Left empty and sent to review: {}.</div>".format(
                 wrong_total, e(pct(m["heldout"][EXACT])), e(", ".join(ok) or "none"),
                 e(", ".join(gone) or "none")))
    p.append("<h2>What happened on the unseen layout</h2><ul>" + "".join(f"<li>{e(readable(x))}</li>" for x in f["facts"]) + "</ul>")
    p.append("".join(f"<p>{e(x)}</p>" for x in f["reading"]))
    p.append("<h2>Confidently wrong extractors</h2><p class='sub'>One field is corrupted in every answer while "
             "confidence stays normal. Auto-approved means no check noticed.</p>")
    for mode, x in cmp["faults"].items():
        p.append(_bar(f"{mode}: auto-approved despite error",
                      {"rate": x["undetected"] / x["wrong"] if x["wrong"] else None,
                       "n": x["undetected"], "d": x["wrong"]},
                      "var(--r)" if x["undetected"] else "var(--g)"))
    p.append("<p class='sub'>A wrong total is caught by arithmetic. A wrong vendor is not: no check examines it.</p>")
    p.append("<div class='banner'><b>Plumbing check, not a model result.</b> The scripted LLM client answers from the "
             f"ground truth (exact match {e(pct(m['mock'][EXACT]))}). It verifies the interface, not LLM accuracy.</div>")
    if db:
        p.append(_db_sections(db, run_id))
    p.append("<details><summary>Technical details: raw field names</summary><table><tr><th>Shown as</th>"
             "<th>Stored as</th></tr>" + "".join(
                 f"<tr><td>{e(v)}</td><td><code>{e(k)}</code></td></tr>" for k, v in LABELS.items())
             + "</table></details>")
    p.append("<h2>Limitations</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in cmp["limitations"]) + "</ul>")
    p.append("<h2>Integrity</h2><p>" + ("<span class='good'>All input reports are internally consistent.</span>"
             if not bad else f"<span class='bad'>{bad} discrepancies, see comparison.md</span>") + "</p>")
    p.append("<h2>Sources</h2><table><tr><th>Role</th><th>File</th><th>Run</th></tr>" + "".join(
        f"<tr><td>{e(i['role'])}</td><td><code>{e(i['file'])}</code></td><td>{i['run_id']}</td></tr>"
        for i in cmp["inputs"]) + "</table></body></html>")
    return "".join(p)
def write_dashboard(reports_dir, out=None, db=None, run_id=None) -> Path:
    path = Path(out) if out else Path(reports_dir) / "dashboard.html"
    path.write_text(build_dashboard(reports_dir, db, run_id), encoding="utf-8", newline="\n")
    return path
