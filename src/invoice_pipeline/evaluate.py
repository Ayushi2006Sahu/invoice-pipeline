"""Evaluate one pipeline run against ground truth. Read-only on the database.
Three metric families, deliberately never merged into one "accuracy":
  1. extraction accuracy   per field: correct / missing / wrong
  2. auto-approved quality how many auto-approved documents were actually fine
  3. defect catch          did the validator and router stop what should be stopped
Comparison policy: values are compared in their canonical stored form (ISO dates,
integer paise, strings exactly as extracted). No case, whitespace or punctuation
is ignored. Ground truth None and prediction None is correct (the field really is
absent); truth present and prediction None is missing; anything else that differs
is wrong. A ratio with no eligible documents is None (JSON null, "n/a" in
Markdown), never 0%.
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from typing import Optional
from .extractors.base import REQUIRED_FIELDS
QUERIES_PATH = Path(__file__).resolve().parents[2] / "sql" / "queries.sql"
SCALAR_FIELDS = (
    "document_type", "vendor", "invoice_no", "invoice_date", "due_date", "currency",
    "subtotal_minor", "discount_minor", "tax_minor", "total_minor",
)
LINE_FIELDS = ("description", "qty", "unit_price_minor", "amount_minor")
LIMITATIONS = [
    "The data is synthetic. Real invoices will behave worse; nothing here is a real-world accuracy claim.",
    "The generator and the regex extractor have the same author and share label vocabulary, so regex "
    "accuracy on layouts A-D is circular. The held-out layout E is the honest generalisation check.",
    "Each defect type has only a handful of examples, so every percentage is shown with n/N and "
    "strong claims from small samples are not supported.",
    "Zero false positives on clean documents is a result on this dataset, not a guarantee.",
    "Two errors that cancel each other (a consistently wrong subtotal and total) cannot be caught by "
    "arithmetic validation.",
    "Confidence is a review signal, not proof of correctness; an extractor can be confidently wrong.",
]
# ------------------------------------------------------------- helpers
def load_queries(path: Path = QUERIES_PATH) -> dict:
    queries: dict = {}
    name, buf = None, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("-- name:"):
            if name:
                queries[name] = "\n".join(buf).strip().rstrip(";")
            name, buf = line.split(":", 1)[1].strip(), []
        elif name is not None:
            buf.append(line)
    if name:
        queries[name] = "\n".join(buf).strip().rstrip(";")
    return queries
def ratio(n: int, d: int) -> dict:
    """rate is None (not 0.0) when nothing was eligible."""
    return {"n": n, "d": d, "rate": (n / d) if d else None}
def pct(r: Optional[dict]) -> str:
    if not r or r["rate"] is None:
        return f"n/a ({r['n']}/{r['d']})" if r else "n/a"
    return f"{r['rate'] * 100:.1f}% ({r['n']}/{r['d']})"
def outcome(truth, pred) -> str:
    if truth is None:
        return "correct" if pred is None else "wrong"
    if pred is None:
        return "missing"
    return "correct" if truth == pred else "wrong"
def compare_lines(gt: dict, pred: dict):
    """Match line items by line number; judge each cell on its own.
    Returns (cells, doc_outcome). cells = [(field, outcome), ...]. Doc outcome:
    any wrong cell -> wrong, else any missing -> missing, else correct.
    """
    cells = []
    for n in sorted(set(gt) | set(pred)):
        g, p = gt.get(n), pred.get(n)
        for f in LINE_FIELDS:
            cells.append((f, outcome(None if g is None else g[f], None if p is None else p[f])))
    outs = {o for _, o in cells}
    doc = "wrong" if "wrong" in outs else "missing" if "missing" in outs else "correct"
    return cells, doc
def _garble_mode(data_dir, doc_id) -> Optional[str]:
    if data_dir is None:
        return None
    try:
        gt = json.loads((Path(data_dir) / "ground_truth" / f"{doc_id}.json").read_text(encoding="utf-8"))
        return (gt.get("render") or {}).get("garble_mode")
    except (OSError, ValueError):
        return None
def _mean(xs):
    return (sum(xs) / len(xs)) if xs else None
def _stat(c: Counter) -> dict:
    return {"correct": c["correct"], "missing": c["missing"], "wrong": c["wrong"],
            "accuracy": ratio(c["correct"], c["correct"] + c["missing"] + c["wrong"])}
# ----------------------------------------------------------- evaluation
def evaluate_run(conn, run_id: int, data_dir=None) -> dict:
    q = load_queries()
    p = {"run_id": run_id}
    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"no such run: {run_id}")
    n_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE run_id = ?", (run_id,)).fetchone()[0]
    if n_inv == 0:
        raise ValueError(f"run {run_id} has no invoices")
    docs = conn.execute(q["documents"], p).fetchall()
    if len(docs) != n_inv:
        raise ValueError(f"ground truth missing for {n_inv - len(docs)} document(s); "
                         "load it first (the evaluate command does this)")
    exp_defects: dict = {}
    for r in conn.execute(q["gt_defects_all"]):
        exp_defects.setdefault(r["doc_id"], []).append(r["defect_code"])
    failed_by_row: dict = {}
    for r in conn.execute(q["failed_checks"], p):
        failed_by_row.setdefault(r["invoice_row_id"], []).append(r["check_code"])
    pred_lines: dict = {}
    for r in conn.execute(
            "SELECT l.invoice_row_id, l.line_no, l.description, l.qty, l.unit_price_minor, "
            "l.amount_minor FROM line_items l JOIN invoices i ON i.invoice_row_id = l.invoice_row_id "
            "WHERE i.run_id = ?", (run_id,)):
        pred_lines.setdefault(r["invoice_row_id"], {})[r["line_no"]] = r
    gt_lines: dict = {}
    for r in conn.execute("SELECT doc_id, line_no, description, qty, unit_price_minor, amount_minor "
                          "FROM gt_line_items"):
        gt_lines.setdefault(r["doc_id"], {})[r["line_no"]] = r
    required = set(REQUIRED_FIELDS)
    cov: dict = {}
    for r in conn.execute(
            "SELECT f.invoice_row_id, f.field_name, f.confidence FROM field_confidence f "
            "JOIN invoices i ON i.invoice_row_id = f.invoice_row_id WHERE i.run_id = ?", (run_id,)):
        if r["field_name"] in required and r["confidence"] > 0:
            cov[r["invoice_row_id"]] = cov.get(r["invoice_row_id"], 0) + 1
    processed = [d for d in docs if d["processing_status"] == "processed"]
    failed = [d for d in docs if d["processing_status"] != "processed"]
    readable = [d for d in processed if d["fields_readable"]]
    fstats = {f: Counter() for f in (*SCALAR_FIELDS, "line_items")}
    lstats = {f: Counter() for f in LINE_FIELDS}
    errors: dict = {}
    for d in processed:
        outs = {f: outcome(d["g_" + f], d[f]) for f in SCALAR_FIELDS}
        cells, outs["line_items"] = compare_lines(gt_lines.get(d["doc_id"], {}),
                                                  pred_lines.get(d["invoice_row_id"], {}))
        errors[d["doc_id"]] = [f"{f}={o}" for f, o in outs.items() if o != "correct"]
        if d["fields_readable"]:
            for f, o in outs.items():
                fstats[f][o] += 1
            for f, o in cells:
                lstats[f][o] += 1
    exact = sum(1 for d in readable if not errors[d["doc_id"]])
    extraction = {
        "readable_documents": len(readable),
        "exact_match": ratio(exact, len(readable)),
        "fields": {f: _stat(c) for f, c in fstats.items()},
        "line_cells": {f: _stat(c) for f, c in lstats.items()},
        "mean_required_field_confidence": _mean([d["confidence"] for d in readable
                                                 if d["confidence"] is not None]),
        "mean_required_field_coverage": _mean([cov.get(d["invoice_row_id"], 0) / len(required)
                                               for d in readable]),
    }
    auto = [d for d in processed if d["status"] == "auto_approved"]
    bad = []
    for d in auto:
        if errors[d["doc_id"]] or d["expected_review"]:
            bad.append({"doc_id": d["doc_id"], "decision": "auto_approved",
                        "expected_defects": sorted(exp_defects.get(d["doc_id"], [])),
                        "field_errors": errors[d["doc_id"]],
                        "failed_checks": sorted(failed_by_row.get(d["invoice_row_id"], []))})
    auto_block = {
        "count": len(auto),
        "field_correct": ratio(sum(1 for d in auto if not errors[d["doc_id"]]), len(auto)),
        "defect_free": ratio(sum(1 for d in auto if not d["expected_review"]), len(auto)),
        "bad": bad,
    }
    in_review = lambda d: d["status"] == "needs_review"
    exp1 = [d for d in processed if d["expected_review"]]
    exp0 = [d for d in processed if not d["expected_review"]]
    err_docs = [d for d in processed if errors[d["doc_id"]]]
    defect_level = {"readable": {}, "garbled": {}}
    for r in conn.execute(q["defect_detection"], p):
        defect_level["readable" if r["readable"] else "garbled"][r["code"]] = ratio(
            r["detected"] or 0, r["n"])
    extra = [{"code": r["code"], "readable": bool(r["readable"]), "n": r["n"]}
             for r in conn.execute(q["extra_detections"], p)]
    catch = {
        "review_recall": ratio(sum(1 for d in exp1 if in_review(d)), len(exp1)),
        "false_review_rate": ratio(sum(1 for d in exp0 if in_review(d)), len(exp0)),
        "error_catch_rate": ratio(sum(1 for d in err_docs if in_review(d)), len(err_docs)),
        "defect_level": defect_level,
        "extra_detections": extra,
    }
    n_review = sum(1 for d in processed if in_review(d))
    review = {
        "rate": ratio(n_review, len(processed)),
        "by_reason": [{"code": r["code"], "severity": r["severity"], "n": r["n"]}
                      for r in conn.execute(q["review_by_reason"], p)],
        "low_confidence_only": conn.execute(q["low_confidence_only"], p).fetchone()["n"],
    }
    g_docs, by_mode = [], {}
    for d in docs:
        if d["fields_readable"]:
            continue
        codes = sorted(failed_by_row.get(d["invoice_row_id"], []))
        mode = _garble_mode(data_dir, d["doc_id"]) or "unknown"
        entry = {"doc_id": d["doc_id"], "mode": mode,
                 "status": d["status"] or d["processing_status"],
                 "flagged": d["status"] == "needs_review", "failed_checks": codes,
                 "garbled_text_fired": "GARBLED_TEXT" in codes}
        g_docs.append(entry)
        m = by_mode.setdefault(mode, {"n": 0, "flagged": 0, "garbled_text_fired": 0})
        m["n"] += 1
        m["flagged"] += entry["flagged"]
        m["garbled_text_fired"] += entry["garbled_text_fired"]
    return {
        "dataset": Path(data_dir).name if data_dir else None,
        "run": {"run_id": run["run_id"], "extractor": run["extractor"], "model": run["model"],
                "started_at": run["started_at"], "policy": json.loads(run["policy_json"])},
        "counts": {"documents": n_inv, "processed": len(processed), "failed": len(failed),
                   "auto_approved": len(auto), "needs_review": n_review,
                   "readable_processed": len(readable), "garbled": len(g_docs)},
        "extraction": extraction,
        "auto_approved": auto_block,
        "catch": catch,
        "review": review,
        "garbled": {"documents": g_docs, "by_mode": by_mode},
        "failed": [{"doc_id": d["doc_id"], "error": d["error_message"]} for d in failed],
        "limitations": LIMITATIONS,
    }
# ------------------------------------------------------------ rendering
def _headline_rows(rep: dict):
    e, a, c = rep["extraction"], rep["auto_approved"], rep["catch"]
    return [
        ("Extraction: exact-match documents (readable only)", pct(e["exact_match"])),
        ("Auto-approved: every extracted field correct", pct(a["field_correct"])),
        ("Auto-approved: ground truth defect-free", pct(a["defect_free"])),
        ("Review recall (expected_review=1 sent to review)", pct(c["review_recall"])),
        ("False review rate (expected_review=0 sent to review)", pct(c["false_review_rate"])),
        ("Error catch rate (docs with a wrong/missing field sent to review)", pct(c["error_catch_rate"])),
        ("Review rate (of processed)", pct(rep["review"]["rate"])),
    ]
def headline(rep: dict) -> str:
    c = rep["counts"]
    lines = [f"run {rep['run']['run_id']} ({rep['run']['extractor']}) on {rep['dataset']}: "
             f"{c['documents']} documents, {c['processed']} processed, {c['failed']} failed"]
    lines += [f"  {k}: {v}" for k, v in _headline_rows(rep)]
    return "\n".join(lines)
def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return out
def render_markdown(rep: dict) -> str:
    run, c, e = rep["run"], rep["counts"], rep["extraction"]
    pol = run["policy"]
    L = [f"# Evaluation: {rep['dataset']} / {run['extractor']}", ""]
    L += [f"- Run {run['run_id']}, extractor `{run['extractor']}`"
          + (f", model `{run['model']}`" if run["model"] else ""),
          f"- Policy: as-of {pol.get('as_of_date')}, rounding tolerance {pol.get('rounding_tolerance')}, "
          f"confidence threshold {pol.get('confidence_threshold')}",
          f"- Documents {c['documents']}: processed {c['processed']} "
          f"(auto-approved {c['auto_approved']}, needs review {c['needs_review']}), "
          f"failed {c['failed']}; unreadable (garbled) {c['garbled']}",
          "- Comparison policy: canonical values, exact equality. Strings are not normalised.",
          "- `n/a` means no eligible documents, which is not the same as 0%.", ""]
    L += ["## Headline (separate metrics, never combined)", ""]
    L += _table(["Metric", "Result"], _headline_rows(rep)) + [""]
    L += ["## 1. Extraction accuracy (readable documents only)", ""]
    L += _table(["Field", "Correct", "Missing", "Wrong", "Accuracy"],
                [(f, s["correct"], s["missing"], s["wrong"], pct(s["accuracy"]))
                 for f, s in e["fields"].items()]) + [""]
    L += ["Line item cells (matched by line number):", ""]
    L += _table(["Cell", "Correct", "Missing", "Wrong", "Accuracy"],
                [(f, s["correct"], s["missing"], s["wrong"], pct(s["accuracy"]))
                 for f, s in e["line_cells"].items()]) + [""]
    mc, mv = e["mean_required_field_confidence"], e["mean_required_field_coverage"]
    L += [f"- mean_required_field_confidence: {'n/a' if mc is None else f'{mc:.4f}'}",
          f"- required_field_coverage (mean): {'n/a' if mv is None else f'{mv:.4f}'}", ""]
    a = rep["auto_approved"]
    L += ["## 2. Auto-approved quality", "",
          f"- Auto-approved documents: {a['count']}",
          f"- All extracted fields correct: {pct(a['field_correct'])}",
          f"- Ground truth defect-free: {pct(a['defect_free'])}", "",
          "### Bad auto-approvals", ""]
    if a["bad"]:
        L += _table(["Document", "Decision", "Expected defects", "Field errors", "Failed checks"],
                    [(b["doc_id"], b["decision"], ", ".join(b["expected_defects"]) or "-",
                      ", ".join(b["field_errors"]) or "-", ", ".join(b["failed_checks"]) or "-")
                     for b in a["bad"]])
    else:
        L += ["None."]
    L += [""]
    k = rep["catch"]
    L += ["## 3. Defect catch", "",
          f"- Review recall: {pct(k['review_recall'])}",
          f"- False review rate: {pct(k['false_review_rate'])}",
          f"- Error catch rate: {pct(k['error_catch_rate'])}", "",
          "Defect-level detection, readable documents (did the validator fail the same code?):", ""]
    L += _table(["Defect", "Detected"], [(code, pct(r)) for code, r in k["defect_level"]["readable"].items()]) \
        if k["defect_level"]["readable"] else ["None."]
    L += ["", "Defect-level detection, garbled documents:", ""]
    L += _table(["Defect", "Detected"], [(code, pct(r)) for code, r in k["defect_level"]["garbled"].items()]) \
        if k["defect_level"]["garbled"] else ["None."]
    L += ["", "Extra detections (validator failed a check the ground truth did not list; "
          "not counted as errors):", ""]
    L += _table(["Check", "Readable", "Documents"],
                [(x["code"], "yes" if x["readable"] else "no", x["n"]) for x in k["extra_detections"]]) \
        if k["extra_detections"] else ["None."]
    L += [""]
    r = rep["review"]
    L += ["## Review breakdown", "", f"- Review rate: {pct(r['rate'])}",
          f"- Reviews triggered only by LOW_CONFIDENCE: {r['low_confidence_only']}", ""]
    L += _table(["Reason", "Severity", "Documents"], [(x["code"], x["severity"], x["n"]) for x in r["by_reason"]]) \
        if r["by_reason"] else ["None."]
    L += [""]
    g = rep["garbled"]
    L += ["## Garbled-input stress test", ""]
    if g["documents"]:
        L += _table(["Mode", "Documents", "Sent to review", "GARBLED_TEXT fired"],
                    [(m, v["n"], v["flagged"], v["garbled_text_fired"]) for m, v in sorted(g["by_mode"].items())])
        L += ["", "GARBLED_TEXT not firing for some modes is a finding, not a bug: thresholds were fixed "
              "in advance and other checks may still route the document to review.", ""]
    else:
        L += ["No garbled documents in this dataset.", ""]
    L += ["## Failed documents (not auto-approved, not queued)", ""]
    L += _table(["Document", "Error"], [(f["doc_id"], f["error"]) for f in rep["failed"]]) \
        if rep["failed"] else ["None."]
    L += ["", "## Limitations", ""] + [f"- {x}" for x in rep["limitations"]] + [""]
    return "\n".join(L)
def write_reports(rep: dict, out_dir, label: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{label}_{rep['run']['extractor']}"
    md, js = out / f"{stem}.md", out / f"{stem}.json"
    md.write_text(render_markdown(rep), encoding="utf-8", newline="\n")
    js.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return md, js
