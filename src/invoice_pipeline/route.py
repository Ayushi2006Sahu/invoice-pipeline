"""Routing decision. Deliberately tiny: any failed check means human review.
Severity and policy live in rules.py. Ingestion failures are not routed here,
they never reach the validators.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
from .rules import SEVERITY
from .validate import FAILED, CheckResult
@dataclass(frozen=True)
class Decision:
    needs_review: bool
    reasons: tuple
    by_severity: dict
def decide(results: Sequence[CheckResult]) -> Decision:
    failed = sorted(r.code for r in results if r.status == FAILED)
    by_sev = {"hard": [], "soft": [], "uncertain": []}
    for code in failed:
        by_sev[SEVERITY[code].value].append(code)
    return Decision(bool(failed), tuple(failed), by_sev)
