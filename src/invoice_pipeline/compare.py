"""Compose one comparison report from EXISTING evaluation JSON reports.
This module scores nothing and runs nothing. It reads the reports written by
`evaluate` (and `fault-demo`), checks they are what they claim to be, and lays
the numbers out. Every figure shown comes from those files; nothing is
hard-coded. A missing or mislabelled input is an error, never a silent fallback.
Regex results and scripted-client results are kept in separate sections on
purpose: the scripted client answers from the ground truth and is not a model.
"""
from __future__ import annotations
import json
from pathlib import Path
from .evaluate import LIMITATIONS, _table, pct, ratio
CLI = "python -m invoice_pipeline.cli"
FAULTS = {"wrong_vendor": "vendor", "wrong_total": "total_minor"}
EXACT = "Extraction: exact-match documents (readable only)"
REVIEW = "Review rate (of processed)"
# role -> (dataset name, extractor name, commands that create the report)
INPUTS = {
    "dev": ("sample", "regex", [
        f"{CLI} run --data data/sample --db invoices.db --extractor regex",
        f"{CLI} evaluate --data data/sample --db invoices.db"]),
    "heldout": ("heldout", "regex", [
        f"{CLI} run --data data/heldout --db heldout.db --extractor regex",
        f"{CLI} evaluate --data data/heldout --db heldout.db"]),
    "mock": ("sample", "llm-mock", [
        f"{CLI} run --data data/sample --db llm.db --extractor llm",
        f"{CLI} evaluate --data data/sample --db llm.db"]),
    "wrong_vendor": ("sample", "llm-mock-wrong_vendor", [f"{CLI} fault-demo --data data/sample"]),
    "wrong_total": ("sample", "llm-mock-wrong_total", [f"{CLI} fault-demo --data data/sample"]),
}
REQUIRED_KEYS = ("run", "counts", "extraction", "auto_approved", "catch", "review")
EXTRA_LIMITATIONS = [
    "The scripted LLM client is not a model. No real LLM was evaluated; a real local model client "
    "is future work.",
    "The held-out result comes from one unseen layout and one seed. It shows that the regex label "
    "dictionary does not generalise, not how any extractor would do on arbitrary invoices.",
]
class CompareError(Exception):
    pass
# --------------------------------------------------------------- loading
def load_reports(reports_dir):
    d = Path(reports_dir)
    reports, paths, missing = {}, {}, []
    for role, (dataset, extractor, hints) in INPUTS.items():
        p = d / f"eval_{dataset}_{extractor}.json"
        paths[role] = p
        if not p.is_file():
            missing.append((p, hints))
            continue
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CompareError(f"{p} is not valid JSON: {exc}") from exc
        absent = [k for k in REQUIRED_KEYS if k not in rep]
        if absent:
            raise CompareError(f"{p} is not an evaluation report (missing keys: {', '.join(absent)})")
        if rep["run"].get("extractor") != extractor or rep.get("dataset") != dataset:
            raise CompareError(
                f"{p} holds extractor {rep['run'].get('extractor')!r} on dataset "
                f"{rep.get('dataset')!r}, expected {extractor!r} on {dataset!r}")
        reports[role] = rep
    if missing:
        lines = ["missing evaluation report(s), nothing was compared:"]
        for p, hints in missing:
            lines.append(f"  {p}")
            lines += [f"      create with: {h}" for h in hints]
        raise CompareError("\n".join(lines))
    return reports, paths
def integrity(rep) -> list:
    """Internal consistency of one report. Empty list = nothing wrong found."""
    c, e, a, r = rep["counts"], rep["extraction"], rep["auto_approved"], rep["review"]
    issues = []
    if c["documents"] != c["processed"] + c["failed"]:
        issues.append(f"documents {c['documents']} != processed {c['processed']} + failed {c['failed']}")
    if c["processed"] != c["auto_approved"] + c["needs_review"]:
        issues.append(f"processed {c['processed']} != auto-approved {c['auto_approved']} + "
                      f"needs review {c['needs_review']}")
    if a["count"] != c["auto_approved"]:
        issues.append(f"auto-approved block says {a['count']}, counts say {c['auto_approved']}")
    if r["rate"]["n"] != c["needs_review"] or r["rate"]["d"] != c["processed"]:
        issues.append(f"review rate {r['rate']['n']}/{r['rate']['d']} disagrees with counts "
                      f"{c['needs_review']}/{c['processed']}")
    if e["readable_documents"] != c["readable_processed"]:
        issues.append(f"readable documents {e['readable_documents']} != {c['readable_processed']}")
    for name, f in e["fields"].items():
        total = f["correct"] + f["missing"] + f["wrong"]
        if total != c["readable_processed"]:
            issues.append(f"field {name}: {total} judged, {c['readable_processed']} readable documents")
    return issues
# ------------------------------------------------------------- composing
def _metrics(rep) -> dict:
    e, a, k, r = rep["extraction"], rep["auto_approved"], rep["catch"], rep["review"]
    return {
        EXACT: e["exact_match"],
        "Auto-approved: every extracted field correct": a["field_correct"],
        "Auto-approved: ground truth defect-free": a["defect_free"],
        "Bad auto-approvals (auto-approved but wrong or defective)": ratio(len(a["bad"]), a["count"]),
        "Review recall (expected_review=1 sent to review)": k["review_recall"],
        "False review rate (expected_review=0 sent to review)": k["false_review_rate"],
        "Error catch rate (docs with a wrong/missing field sent to review)": k["error_catch_rate"],
        REVIEW: r["rate"],
    }
def _run_info(rep) -> dict:
    c = rep["counts"]
    return {"run_id": rep["run"]["run_id"], "extractor": rep["run"]["extractor"],
            "documents": c["documents"], "processed": c["processed"], "failed": c["failed"],
            "auto_approved": c["auto_approved"], "needs_review": c["needs_review"]}
def _finding(rep) -> dict:
    e, a = rep["extraction"], rep["auto_approved"]
    fields = e["fields"]
    wrong = sum(x["wrong"] for x in fields.values())
    full = [n for n, x in fields.items() if x["missing"] == 0 and x["wrong"] == 0]
    gaps = [f"{n} ({x['missing']} missing)" for n, x in fields.items() if x["missing"] > 0]
    bad = len(a["bad"])
    rate = rep["review"]["rate"]
    facts = [
        f"Exact-match documents: {pct(e['exact_match'])}.",
        "Fields correct in every readable document: " + (", ".join(full) or "none") + ".",
        "Fields with missing values: " + (", ".join(gaps) or "none") + ".",
        f"Wrong (non-missing) values: {wrong} field cell(s).",
        f"Sent to review: {pct(rate)}. Auto-approved: {rep['counts']['auto_approved']}. "
        f"Bad auto-approvals: {bad}.",
    ]
    reading = []
    if wrong == 0 and gaps:
        reading.append("It produced no wrong values here, only missing ones; missing values are what "
                       "the MISSING_FIELD check sends to review.")
    if wrong > 0:
        reading.append(f"Wrong values were produced in {wrong} field cell(s). Missing-field checks "
                       "cannot see these; only the arithmetic and format checks can.")
    if bad > 0:
        reading.append(f"WARNING: {bad} document(s) were auto-approved although wrong or defective "
                       "(listed in the held-out evaluation report).")
    if rate["rate"] == 1.0:
        reading.append("Every document went to review. That is safe but not useful: a human would "
                       "have to redo all of them, and the error catch rate is trivially high.")
    return {"facts": facts, "reading": reading}
def _fault(rep, field) -> dict:
    wrong = rep["extraction"]["fields"][field]["wrong"]
    und = [b for b in rep["auto_approved"]["bad"]
           if b["decision"] == "auto_approved" and f"{field}=wrong" in b["field_errors"]]
    top = [f"{r['code']} ({r['n']})" for r in rep["review"]["by_reason"][:3]]
    return {"field": field, "wrong": wrong, "undetected": len(und), "caught": wrong - len(und),
            "auto_approved": rep["counts"]["auto_approved"], "top_failed_checks": top,
            "field_accuracy": rep["extraction"]["fields"][field]["accuracy"]}
def build_comparison(reports_dir) -> dict:
    reps, paths = load_reports(reports_dir)
    dev, held = reps["dev"], reps["heldout"]
    rows = []
    for name, hf in held["extraction"]["fields"].items():
        df = dev["extraction"]["fields"].get(name)
        rows.append({"field": name, "dev": df["accuracy"] if df else None,
                     "heldout": {k: hf[k] for k in ("correct", "missing", "wrong")}})
    return {
        "inputs": [{"role": role, "file": str(paths[role]), "run_id": rep["run"]["run_id"],
                    "extractor": rep["run"]["extractor"], "dataset": rep["dataset"],
                    "started_at": rep["run"].get("started_at")} for role, rep in reps.items()],
        "integrity": {role: integrity(rep) for role, rep in reps.items()},
        "runs": {"dev": _run_info(dev), "heldout": _run_info(held), "mock": _run_info(reps["mock"])},
        "metrics": {"dev": _metrics(dev), "heldout": _metrics(held), "mock": _metrics(reps["mock"])},
        "fields": rows,
        "finding": _finding(held),
        "faults": {mode: _fault(reps[mode], field) for mode, field in FAULTS.items()},
        "limitations": list(LIMITATIONS) + EXTRA_LIMITATIONS,
    }
# ------------------------------------------------------------- rendering
def render_markdown(cmp: dict) -> str:
    runs, m = cmp["runs"], cmp["metrics"]
    L = ["# Extractor comparison", "",
         "Composed from existing evaluation reports. Nothing was re-run or re-scored here; every "
         "number comes from the input files listed at the end.", "",
         "Metrics stay separate and are never combined into one accuracy figure. `n/a` means no "
         "eligible documents, which is not the same as 0%.", ""]
    L += ["## 1. Regex baseline: development layouts vs held-out layout", ""]
    for key, label in (("dev", "Development (layouts A-D)"), ("heldout", "Held-out (layout E, unseen)")):
        r = runs[key]
        L.append(f"- {label}: run {r['run_id']} (`{r['extractor']}`), {r['documents']} documents, "
                 f"{r['processed']} processed, {r['failed']} failed")
    L += [""]
    L += _table(["Metric", "Development", "Held-out"],
                [(k, pct(m["dev"][k]), pct(m["heldout"][k])) for k in m["dev"]]) + [""]
    L += ["Per field (held-out columns are document counts):", ""]
    L += _table(["Field", "Dev accuracy", "Held-out correct", "Held-out missing", "Held-out wrong"],
                [(r["field"], pct(r["dev"]), r["heldout"]["correct"], r["heldout"]["missing"],
                  r["heldout"]["wrong"]) for r in cmp["fields"]]) + [""]
    L += ["Development numbers are circular: the generator and the regex extractor share an author "
          "and a label vocabulary. The held-out layout uses labels and an order the extractor was "
          "never tuned on.", ""]
    L += ["## 2. Held-out finding", ""]
    L += [f"- {x}" for x in cmp["finding"]["facts"]] + [""]
    L += [f"{x}" for x in cmp["finding"]["reading"]]
    if cmp["finding"]["reading"]:
        L += [""]
    L += ["## 3. LLM extractor plumbing check (scripted client)", "",
          "> **Plumbing check, not a model result.** These results verify the extractor interface "
          "and pipeline integration; they do not measure real LLM extraction accuracy.", ""]
    r = runs["mock"]
    L += [f"Run {r['run_id']} (`{r['extractor']}`) on the development dataset only. The scripted "
          "client answers from the ground truth, so its numbers are not placed next to the regex "
          "numbers. The held-out layout is excluded because the scripted answers exist only for "
          "the dataset they were built from.", ""]
    L += _table(["Metric", "Result"], [(k, pct(v)) for k, v in m["mock"].items()]) + [""]
    L += ["## 4. Confidently-wrong extractor demo", "",
          "Two scripted extractors corrupt one field in every answer while reporting normal "
          "confidence. 'Auto-approved despite the error' means the routing decision was "
          "auto_approved although the field was wrong.", ""]
    rows = []
    for mode, f in cmp["faults"].items():
        outcome = ("every wrong document was sent to review" if f["undetected"] == 0 else
                   f"{f['undetected']} of {f['wrong']} undetected")
        rows.append((mode, f["field"], f["wrong"], f["caught"], f["undetected"],
                     ", ".join(f["top_failed_checks"]) or "-", outcome))
    L += _table(["Fault", "Corrupted field", "Documents with wrong value", "Sent to review",
                 "Auto-approved despite the error", "Most frequent failed checks", "Outcome"], rows) + [""]
    for mode, f in cmp["faults"].items():
        if f["undetected"]:
            L.append(f"- {mode}: {f['undetected']} of {f['wrong']} documents with a wrong {f['field']} "
                     "were auto-approved, so a human would never see them.")
        else:
            L.append(f"- {mode}: every document with a wrong {f['field']} was sent to review.")
    L += [""]
    L += ["## 5. Limitations", ""] + [f"- {x}" for x in cmp["limitations"]] + [""]
    L += ["## Inputs and integrity", ""]
    L += _table(["Role", "File", "Run", "Extractor", "Dataset"],
                [(i["role"], i["file"], i["run_id"], i["extractor"], i["dataset"])
                 for i in cmp["inputs"]]) + [""]
    for role, issues in cmp["integrity"].items():
        if issues:
            L += [f"- DISCREPANCY in {role}: {x}" for x in issues]
        else:
            L.append(f"- {role}: counts are internally consistent")
    return "\n".join(L) + "\n"
def write_comparison(cmp: dict, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / "comparison.md", out / "comparison.json"
    md.write_text(render_markdown(cmp), encoding="utf-8", newline="\n")
    js.write_text(json.dumps(cmp, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return md, js
