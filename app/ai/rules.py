"""extract_rules(text, kind, client) -> ExtractionResult.

The model reads a masked contract (kind "contract") or a customer's masked invoice
requirements (kind "requirements") and proposes typed rules. Code validates every candidate:
known rule type, category from the closed list, a safe PO regex, and a quote that exists
verbatim in the original text. What fails is kept with its reason, not used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.client import TASKS, ModelClient, ModelError, ModelUnavailable
from app.ai.masking import mask, unmask
from app.ai.prompts import contract_rules_prompt, requirement_rules_prompt
from app.schemas import EXPENSE_CATEGORIES

MAX_CANDIDATES = 20
MIN_QUOTE = 12
MAX_QUOTE = 600
MAX_EXPLANATION = 300
MAX_REGEX = 64

# The allowed subset of a PO regex: anchors, literal letters/digits/hyphen/slash, \d, \w,
# simple character classes, and fixed counts. No groups, alternation, wildcards, + or *.
_REGEX_SUBSET = re.compile(r"\^?(?:(?:\\d|\\w|\[[A-Za-z0-9\-_]+\]|[A-Za-z0-9\-/_ ])(?:\{\d{1,2}(?:,\d{1,2})?\})?)+\$?")

_KINDS = {
    "contract": ("contract_rules", contract_rules_prompt, ("expense_not_billable",)),
    "requirements": ("requirement_rules", requirement_rules_prompt, ("po_required", "po_format")),
}


@dataclass(frozen=True)
class ContractRule:
    type: str
    quote: str
    explanation_nl: str
    category: str | None = None
    regex: str | None = None

    def as_dict(self) -> dict:
        return {
            "type": self.type, "category": self.category, "regex": self.regex,
            "quote": self.quote, "explanation_nl": self.explanation_nl,
        }


@dataclass(frozen=True)
class ExtractionResult:
    status: str  # ai_extracted | not_analysed
    reason: str | None
    rules: tuple[ContractRule, ...]
    rejected: tuple[dict, ...]
    model: str | None
    mode: str


def _squash(text: str) -> str:
    return " ".join(text.split())


def po_regex_problem(regex: object) -> str | None:
    """Why a PO regex from the model is refused; None when it is fine."""
    if not isinstance(regex, str) or not regex:
        return "geen regex"
    if len(regex) > MAX_REGEX:
        return f"regex langer dan {MAX_REGEX} tekens"
    if not _REGEX_SUBSET.fullmatch(regex):
        return "regex gebruikt meer dan de toegestane bouwstenen"
    try:
        re.compile(regex)
    except re.error:
        return "regex compileert niet"
    return None


def _validate(candidate: object, allowed_types: tuple[str, ...], original: str, token_map: dict) -> ContractRule | str:
    """A valid rule, or the reason the candidate is refused."""
    if not isinstance(candidate, dict):
        return "geen object"
    rule_type = candidate.get("type")
    if rule_type not in allowed_types:
        return "onbekend regeltype"

    quote, explanation = candidate.get("quote"), candidate.get("explanation_nl")
    if not isinstance(quote, str) or not isinstance(explanation, str) or not explanation.strip():
        return "citaat of uitleg ontbreekt"
    quote, explanation = _squash(unmask(quote, token_map)), _squash(unmask(explanation, token_map))
    if not MIN_QUOTE <= len(quote) <= MAX_QUOTE:
        return "citaat te kort of te lang"
    if quote not in _squash(original):
        return "citaat staat niet letterlijk in de tekst"
    if len(explanation) > MAX_EXPLANATION:
        return "uitleg te lang"

    category = regex = None
    if rule_type == "expense_not_billable":
        category = candidate.get("category")
        if category not in EXPENSE_CATEGORIES:
            return "categorie staat niet in de lijst"
    if rule_type == "po_format":
        regex = candidate.get("regex")
        problem = po_regex_problem(regex)
        if problem:
            return problem
    return ContractRule(type=rule_type, quote=quote, explanation_nl=explanation, category=category, regex=regex)


def extract_rules(
    text: str, kind: str, client: ModelClient, known_names: list[str] | tuple[str, ...] = ()
) -> ExtractionResult:
    task, build_prompt, allowed_types = _KINDS[kind]
    masked, token_map = mask(text, known_names)

    def not_analysed(reason: str) -> ExtractionResult:
        return ExtractionResult("not_analysed", reason, (), (), None, client.mode)

    try:
        reply = client.complete_json(task, build_prompt(masked))
    except ModelUnavailable as exc:
        return not_analysed(f"AI-check niet uitgevoerd: {exc}")
    except ModelError as exc:
        return not_analysed(f"AI-check mislukt: {exc}")

    candidates = reply.get("rules")
    if not isinstance(candidates, list):
        return not_analysed("AI-check mislukt: het antwoord had niet de afgesproken vorm")

    rules: list[ContractRule] = []
    rejected: list[dict] = []
    for candidate in candidates[:MAX_CANDIDATES]:
        result = _validate(candidate, allowed_types, text, token_map)
        if isinstance(result, str):
            # Keep only the type and the reason: the rest of a refused candidate is untrusted text.
            shown = candidate.get("type") if isinstance(candidate, dict) else None
            rejected.append({"type": shown if isinstance(shown, str) else None, "reason": result})
        elif any((r.type, r.category, r.regex) == (result.type, result.category, result.regex) for r in rules):
            rejected.append({"type": result.type, "reason": "dubbele regel"})
        else:
            rules.append(result)
    if len(candidates) > MAX_CANDIDATES:
        rejected.append({"type": None, "reason": f"meer dan {MAX_CANDIDATES} kandidaten; de rest is genegeerd"})
    return ExtractionResult("ai_extracted", None, tuple(rules), tuple(rejected), TASKS[task].model, client.mode)
