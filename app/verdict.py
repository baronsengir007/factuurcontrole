"""decide(findings, table) -> Verdict.

The table comes from config and is evaluated top to bottom; the first row that matches wins.
Row 0 is fixed here, in code, and no table can change it:

  a. Only a finding with certainty `code` or `verified_rule` can give afgewezen.
  b. A finding with certainty `ai` or `not_run` (an unconfirmed rule, a model line category,
     a check that could not run) never gives goedgekeurd.

So a finding that rests on AI, or on a check that did not run, always ends as controle_nodig
unless another finding rejects the invoice.
"""

from __future__ import annotations

from app.config import REJECTING_CERTAINTIES, DecisionTable
from app.schemas import Finding, Verdict


def decide(findings: list[Finding], table: DecisionTable) -> Verdict:
    verdict: Verdict = table.default
    for row in table.rows:
        candidates = findings
        if row.verdict == "afgewezen":  # Row 0a
            candidates = [f for f in findings if f.certainty in REJECTING_CERTAINTIES]
        if any(row.matches(f) for f in candidates):
            verdict = row.verdict
            break
    if verdict == "goedgekeurd" and any(  # Row 0b
        f.severity != "info" and f.certainty not in REJECTING_CERTAINTIES for f in findings
    ):
        return "controle_nodig"
    return verdict
