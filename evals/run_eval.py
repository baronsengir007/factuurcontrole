"""Eval: run the labelled cases through the HTTP API and print the scoreboard.

    uv run python evals/run_eval.py                  # both modes with recorded answers
    uv run python evals/run_eval.py --live           # also one run with live Claude, compared with recorded
    uv run python evals/run_eval.py --live --record  # the live run also rewrites data/cassettes/

Every case runs on its own fresh database, through the same code path as the service
(FastAPI TestClient -> check_invoice). Two modes:

- zonder-ai: no model at all (the Unavailable client), as on a machine without a token.
- met-ai: recorded model answers (the Recorded client), then verify-contract and
  verify-requirements for every rule set, as a person would do after reading the rules.

Per finding code the report counts caught, missed and false alarms, and it shows the verdicts
in a confusion matrix. The result is written to evals/results/latest.md.
The exit code is 1 when a code check missed something or raised a false alarm.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.client import ClaudeCLI, ModelClient, Recorded, Unavailable  # noqa: E402
from app.checker import Deps  # noqa: E402
from app.cli import seed  # noqa: E402
from app.config import AppConfig, load_config, load_env_file  # noqa: E402
from app.main import create_app  # noqa: E402
from app.messages import CODES  # noqa: E402
from app.store import Store  # noqa: E402

CASES = ROOT / "evals" / "cases"
RESULTS = ROOT / "evals" / "results"
SEED = ROOT / "data" / "seed"
CASSETTES = ROOT / "data" / "cassettes"
KEY = {"X-API-Key": "eval-key"}
MODES = ("zonder-ai", "met-ai")
NO_VERDICT = "geen oordeel (4xx)"
VERDICTS = ("goedgekeurd", "controle_nodig", "afgewezen", NO_VERDICT)
# Findings that exist because a model read a text, or could not. Everything else is code.
AI_PREFIXES = ("contract.", "lines.ai_classified", "lines.unclassified", "po.requirements_")


def is_ai_code(code: str) -> bool:
    return code.startswith(AI_PREFIXES)


class _Recording:
    """Wraps the live client during `--live --record`: every answer is also written as a cassette."""

    mode = "live"

    def __init__(self, inner: ModelClient, cassettes: Recorded):
        self.inner, self.cassettes = inner, cassettes

    def complete_json(self, task: str, prompt: str) -> dict:
        response = self.inner.complete_json(task, prompt)
        self.cassettes.write(task, prompt, response)
        return response


def load_cases() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES.glob("*.json"))]


def build_template(path: Path, client: ModelClient, verify: bool) -> Path:
    """A seeded database: intake with the given client, then (optionally) a person confirms."""
    store = Store(path)
    seed(store, SEED, client)
    if verify:
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for order in json.loads((SEED / "orders.json").read_text(encoding="utf-8")):
            store.verify_contract(order["id"], "eval", at)
        for customer in json.loads((SEED / "customers.json").read_text(encoding="utf-8")):
            store.verify_requirements(customer["id"], "eval", at)
    return path


def run_case(case: dict, database: Path, client: ModelClient, config: AppConfig) -> dict:
    app = create_app(Deps(store=Store(database), config=config, client=client), api_key=KEY["X-API-Key"])
    with TestClient(app) as http:
        for earlier in case["setup"]:
            http.post("/v1/checks", json=earlier, headers=KEY)
        response = http.post("/v1/checks", json=case["invoice"], headers=KEY)
    body = response.json()
    if response.status_code != 200:
        return {"status": response.status_code, "error": body.get("error")}
    return {"status": 200, "verdict": body["verdict"], "codes": sorted({f["code"] for f in body["findings"]})}


def run_mode(mode: str, client: ModelClient, cases: list[dict], config: AppConfig, tmp: Path, tag: str) -> dict:
    """All cases in one mode. Returns {case id: outcome}."""
    with_ai = mode == "met-ai"
    confirmed = build_template(tmp / f"{tag}-confirmed.db", client, verify=with_ai)
    unconfirmed = build_template(tmp / f"{tag}-unconfirmed.db", client, verify=False) if with_ai else confirmed
    outcomes = {}
    for case in cases:
        database = tmp / f"{tag}-{case['id']}.db"
        shutil.copy(confirmed if case["verified"] else unconfirmed, database)
        outcomes[case["id"]] = run_case(case, database, client, config)
    return outcomes


def passed(expected: dict, actual: dict) -> bool:
    if expected["status"] != 200:
        return (actual["status"], actual.get("error")) == (expected["status"], expected["error"])
    return actual["status"] == 200 and (actual["verdict"], actual["codes"]) == (expected["verdict"], expected["codes"])


def score(cases: list[dict], outcomes: dict, mode: str) -> dict:
    """Per finding code: caught, missed, false alarms. Plus the verdict confusion matrix."""
    per_code = {code: Counter() for code in CODES}
    matrix: Counter = Counter()
    failed = []
    for case in cases:
        expected, actual = case["expect"][mode], outcomes[case["id"]]
        want = set(expected.get("codes", []))
        got = set(actual.get("codes", []))
        for code in want | got:
            per_code[code]["caught" if code in want and code in got else "missed" if code in want else "false_alarm"] += 1
        matrix[(expected.get("verdict", NO_VERDICT), actual.get("verdict", NO_VERDICT))] += 1
        if not passed(expected, actual):
            failed.append((case, expected, actual))

    def total(kind: str, ai: bool) -> int:
        return sum(c[kind] for code, c in per_code.items() if is_ai_code(code) == ai)

    return {
        "per_code": per_code, "matrix": matrix, "failed": failed, "cases": len(cases),
        "code_missed": total("missed", False), "code_false": total("false_alarm", False),
        "ai_missed": total("missed", True), "ai_false": total("false_alarm", True),
    }


# --- the report ----------------------------------------------------------------------------------


def _describe(outcome: dict) -> str:
    if outcome["status"] != 200:
        return f"{outcome['status']} {outcome.get('error')}"
    return f"{outcome['verdict']} [{', '.join(outcome['codes']) or 'geen bevindingen'}]"


def report(cases: list[dict], scores: dict, recorded: dict, live: dict | None, config: AppConfig) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Evalresultaten factuurcontrole",
        "",
        f"Gedraaid op {now}. {len(cases)} gelabelde, synthetische cases, elk op een eigen lege database, "
        f"via de HTTP-API. Config-hash `{config.config_hash}`. Opnieuw draaien: `uv run python evals/run_eval.py`.",
        "",
        "- **zonder-ai**: geen model (geen token). De AI-controles melden dat ze niet zijn uitgevoerd.",
        "- **met-ai**: opgenomen antwoorden van Claude, daarna zijn alle regelsets bevestigd zoals een mens dat doet.",
        "",
        "## Samenvatting",
        "",
        "| Modus | Cases goed | Codecontroles: gemist | Codecontroles: vals alarm | AI-controles: gemist | AI-controles: vals alarm |",
        "|---|---|---|---|---|---|",
    ]
    for mode in MODES:
        s = scores[mode]
        lines.append(
            f"| {mode} | {s['cases'] - len(s['failed'])} van {s['cases']} | {s['code_missed']} | {s['code_false']} "
            f"| {s['ai_missed']} | {s['ai_false']} |"
        )
    lines += ["", "Een case is goed als het oordeel en de set bevindingcodes precies kloppen met het label "
              "(bij een invoerfout: de statuscode en de foutcode)."]

    for mode in MODES:
        s = scores[mode]
        lines += ["", f"## {mode}: per bevindingcode", "",
                  "| Bevindingcode | Soort | Gevangen | Gemist | Vals alarm |", "|---|---|---|---|---|"]
        for code, counts in s["per_code"].items():
            if sum(counts.values()) == 0:
                continue
            kind = "AI" if is_ai_code(code) else "code"
            lines.append(f"| `{code}` | {kind} | {counts['caught']} | {counts['missed']} | {counts['false_alarm']} |")
        lines += ["", f"### {mode}: oordelen (rij = verwacht, kolom = gegeven)", "",
                  "| | " + " | ".join(VERDICTS) + " |", "|---|" + "---|" * len(VERDICTS)]
        for want in VERDICTS:
            lines.append(f"| **{want}** | " + " | ".join(str(s["matrix"][(want, got)]) for got in VERDICTS) + " |")
        if s["failed"]:
            lines += ["", f"### {mode}: afwijkende cases", ""]
            for case, expected, actual in s["failed"]:
                lines.append(f"- `{case['id']}` ({case['title']}): verwacht {_describe(expected)}, kreeg {_describe(actual)}")

    lines += ["", "## Live Claude tegenover de opgenomen antwoorden", ""]
    if live is None:
        lines.append("Nog geen live run vastgelegd. Draai `uv run python evals/run_eval.py --live` met een token in `.env`.")
    else:
        same_verdict = [c for c in cases if _verdict(live["cases"].get(c["id"])) == _verdict(recorded[c["id"]])]
        same_all = [c for c in cases if live["cases"].get(c["id"]) == recorded[c["id"]]]
        how = (
            "Deze live run heeft de opnames zelf geschreven, dus gelijkheid zegt hier weinig; draai `--live` nog eens zonder `--record`."
            if live.get("recorded_in_this_run") else
            "Dit was een losse live run, na het opnemen: elke aanroep ging opnieuw naar Claude."
        )
        lines += [
            f"Live run van {live['ran_at']} (modus met-ai, model via de Claude CLI). {how}",
            "",
            f"- Zelfde oordeel als met de opgenomen antwoorden: **{len(same_verdict)} van {len(cases)}** cases.",
            f"- Ook dezelfde bevindingcodes: {len(same_all)} van {len(cases)} cases.",
            f"- Live tegenover de labels: {live['passed']} van {len(cases)} cases goed.",
        ]
        different = [c for c in cases if c not in same_all]
        if different:
            lines += ["", "Verschillen:"]
            for case in different:
                got = live["cases"].get(case["id"])
                lines.append(f"- `{case['id']}`: live {_describe(got) if got else 'niet gedraaid'}, "
                             f"opgenomen {_describe(recorded[case['id']])}")
    return "\n".join(lines) + "\n"


def _verdict(outcome: dict | None):
    if outcome is None:
        return None
    return outcome.get("verdict", f"{outcome['status']} {outcome.get('error')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true", help="also run met-ai once with live Claude (needs a token)")
    parser.add_argument("--record", action="store_true", help="with --live: rewrite data/cassettes/ from this run")
    args = parser.parse_args()
    if args.record and not args.live:
        parser.error("--record needs --live")
    sys.stdout.reconfigure(encoding="utf-8")

    config = load_config()
    cases = load_cases()
    RESULTS.mkdir(parents=True, exist_ok=True)
    live_file = RESULTS / "live.json"

    with tempfile.TemporaryDirectory(prefix="factuurcontrole-eval-") as tmp_name:
        tmp = Path(tmp_name)
        if args.live:
            load_env_file()
            client: ModelClient = ClaudeCLI()
            if args.record:
                shutil.rmtree(CASSETTES, ignore_errors=True)
                client = _Recording(client, Recorded(CASSETTES))
            print("Live run met Claude ...", flush=True)
            outcomes = run_mode("met-ai", client, cases, config, tmp, "live")
            live_score = score(cases, outcomes, "met-ai")
            live_file.write_text(json.dumps({
                "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "recorded_in_this_run": args.record,
                "passed": len(cases) - len(live_score["failed"]),
                "cases": outcomes,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        clients = {"zonder-ai": Unavailable(), "met-ai": Recorded(CASSETTES)}
        outcomes = {mode: run_mode(mode, clients[mode], cases, config, tmp, mode) for mode in MODES}

    scores = {mode: score(cases, outcomes[mode], mode) for mode in MODES}
    live = json.loads(live_file.read_text(encoding="utf-8")) if live_file.is_file() else None
    text = report(cases, scores, outcomes["met-ai"], live, config)
    (RESULTS / "latest.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"Geschreven: {(RESULTS / 'latest.md').relative_to(ROOT).as_posix()}")
    code_clean = all(s["code_missed"] == 0 and s["code_false"] == 0 for s in scores.values())
    return 0 if code_clean else 1


if __name__ == "__main__":
    sys.exit(main())
