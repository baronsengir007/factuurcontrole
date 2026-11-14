# Architecture: factuurcontrole

Updated: 2026-11-14 (after the build; what the build changed in the shape is marked "Build:"). Decisions with their reasons live in docs/adr/; this file describes the shape. The storyboard (one request and response per scenario) is in docs/design/DESIGN-factuurcontrole.md.

## Purpose
A billing employee at a local office or the shared service center makes a draft invoice. Before it is sent, the billing system calls this service. The service checks the draft against the order, the agreed rates, the time records and the customer's requirements and contract clauses, and returns goedgekeurd, controle nodig or afgewezen (in the API: `goedgekeurd`, `controle_nodig`, `afgewezen`) with a reason and a source per finding. A person decides; the service never sends or changes an invoice.

## Priorities and feel
1. Proof: every check is backed by a test and by the eval scoreboard (caught / missed / false alarms per check). Design consequence: checks are small pure functions over a context object, so each is tested in isolation and the eval can count per check code.
2. Runs the first time: one `docker compose up`, works without any key; the AI part degrades to "controle nodig", never to a crash or a silent pass.
Merely fine: no UI, no performance work beyond "a check returns in well under a second without the model".
Feel for the billing employee: every message is one Dutch sentence a non-engineer understands, plus its source.

## Parts
- API (`app/main.py`): FastAPI routes, API-key check, maps validation and domain errors to 401/404/422. Thin.
- Checker (`app/checker.py`): `check_invoice(invoice, deps) -> CheckResult`. Loads the order context from the store, runs every check, applies the decision table, stores the check. The deep module.
- Checks (`app/checks/*.py`): one module per check, each `run(ctx) -> list[Finding]`: totals, vat, po, rates_hours, contract, duplicates. Pure: no I/O; the context carries everything.
- Verdict (`app/verdict.py`): `decide(findings, table) -> Verdict`. Table from `config/decision_table.yaml`.
- Config (`app/config.py`): loads `config/countries/*.yaml` (VAT rates per category, currency), `config/decision_table.yaml`, `config/line_categories.yaml` (keywords per category, multilingual) and `config/duplicates.yaml`. Validated with Pydantic at start-up; a bad file stops the service with a clear message that names the file and the field. Build: the settings from the environment (API key, database path), the optional `.env` reader for a start without Docker and the JSON logging set-up also live here.
- Store (`app/store.py`): SQLite via the standard library. Schema, seed, reads for the context, `save_check`. One adapter, so no seam: tests use a temp file.
- AI (`app/ai/`): `rules.py` (`extract_rules(text, kind, client) -> list[ContractRule]` for contract clauses and customer requirement text), `classify.py` (`classify_line(description, client) -> Category | None` for unclear lines only), `masking.py` (reversible masking), `client.py` (the seam: `ModelClient.complete_json(task, prompt) -> dict` with three adapters: `ClaudeCLI` live, `Recorded` cassette replay used only by the eval, `Unavailable`). Build: the task decides the model and the patience: contract and requirement intake use `sonnet` with a 60 s timeout and one retry; a line category uses `haiku` with a 20 s timeout and no retry.
- CLI (`app/cli.py`): `seed` (load the synthetic data when the database is empty, then extract rules for every contract and requirement text that is still `not_analysed`, with the configured client; the model calls run side by side), `ingest-contract <order_id> <file>`, `verify-contract <order_id> --by <name>` (a person confirms the rule set of a contract is correct and complete; writes who and when), `verify-requirements <customer_id> --by <name>` (the same for the rules taken from a customer's free-text invoice requirements, such as the PO format; until then a PO finding from such a rule has certainty `ai`), `init` (schema, then the same as `seed`; the container runs it at start).

## Data flow
1. Intake (once per contract, CLI): contract text and customer requirement text -> masking (names, amounts, PO numbers, emails, IBAN, phone -> tokens) -> model with a fixed schema -> validation (known rule type, parameters in range, PO regex in an allowed subset and compiling, quote found verbatim in the unmasked source after unmasking) -> `contract_rules` table with model id, mode (live/recorded), time; the contract itself gets status `ai_extracted` (or `not_analysed` with the reason). Rejected candidates are kept with their reason in a JSON field of the contract row, so S14 can show them. A person reviews the set (S14) and runs `verify-contract`, which sets the contract to `verified`. Customers whose PO format is given as structured config need no model at all.
2. Check (per invoice, API): invoice JSON -> Pydantic validation (422) -> order lookup (404) -> context (order, customer, rate card, time entries for the period, rules, country config, earlier checks of the customer for duplicates) -> line categories (rate card when the line's service code is on it, else the line's field, else the keyword table, else the model on the masked description, else unclassified) -> checks -> findings -> decision table -> verdict -> stored in `checks` -> response.
3. Audit (API): `GET /v1/checks/{id}` and `GET /v1/orders/{id}/rules` read what was stored.
Source of truth: SQLite for orders, rates, time entries, contract text, rules and checks; YAML in `config/` for countries, categories and the decision table. Invoices are never stored outside `checks`. Of an invoice, only the masked description of an unclear line ever leaves the machine.

## Checks (the rules in code)
- totals: per line `quantity x unit_price = amount`; `sum(amount) = subtotal`; `sum(per-rate VAT) = vat_total`; `subtotal + vat_total = total`. Decimal arithmetic, half-up rounding to cents, tolerance 0.00 per line and 0.01 on aggregates. error.
- vat: each line's `vat_rate` is the rate the country config gives for the line's category (TIC services: standard; NL 21, DE 19). Unknown country -> 422 at validation. error. Build: while a country has one rate for every category the finding has certainty `code`. When the rate depends on the category, a finding on a line whose category came from the model has certainty `ai` (from nobody: `not_run`), so a model answer cannot reject through the VAT check either.
- po: if the customer requires a PO: present (error if missing), matches the customer's `po_format` rule (error if not). Certainty follows the rule status. Build: a format rule implies that a PO is required. While a customer's free-text requirements are `ai_extracted`, every invoice of that customer gets `po.requirements_unverified` (warning, certainty `ai`); while they are `not_analysed`, `po.requirements_not_analysed` (warning, certainty `not_run`). This mirrors the contract check: a PO rule the model missed cannot lead to goedgekeurd.
- rates_hours: each line with a `service_code` must be on the order's rate card (`rates.unknown_service`, error) with the same unit price (`rates.mismatch`, error); invoiced hours per service code in the period must not exceed the time entries for that order, code and period (error if more; warning if fewer than recorded, because under-billing is lost money but not a rejection). Build: the hours of a code are compared only when the code is on the invoice, so a separate invoice for lab work is not flagged for missing hours. A labour or lab line without a service code cannot be checked against the rate card and gets `rates.no_service_code` (warning; certainty `ai` when the model gave the category), so leaving the code off is not a way around the rate check. Build (review round 2): the unit of the line is compared with the unit of the rate card, without regard to capitals and spaces; a difference gives `rates.unit_mismatch` (warning, certainty `code`), because 16 days at the hourly price is not the agreed rate. It is a warning and not an error because the unit is free text and a billing system may spell it differently.
- contract: for each rule of the order: `expense_not_billable{category}` (category from the closed list: travel, accommodation, materials, subcontracting): any line in that category -> finding. The closed set of rule types is `expense_not_billable` (contracts) and `po_required` / `po_format{regex}` (customer requirements); rules with amounts are out of scope, because amounts are masked before the model sees the text. Line category comes from the rate card when the line's service code is on it, else the line's `category` field, else the keyword table (a description that hits no category, or more than one, has no keyword category), else the model (`lines.ai_classified`, warning, S7), else `unclassified` (warning). Certainty of a contract finding: `ai` when the rule is not verified or the line category came from the model; `verified_rule` only when both the contract is verified and the category came from the field or the keyword table. While the contract is `ai_extracted`, every invoice on the order also gets `contract.rules_unverified` (warning); while it is `not_analysed`, `contract.not_analysed` (warning). So an omitted rule can never produce goedgekeurd before a person has confirmed the rule set. Build (review round 1): the service code or the `category` field decides the category, but a line whose own field or description points to another category (the field differs from the rate card, or the description holds a keyword of another category that the rate card's own description of the service does not hold) gets `lines.category_conflict` (warning, certainty `code`). So travel billed under an inspection code, or labelled as materials, is not approved without a person.
- duplicates: candidates = earlier stored checks of the same customer that got a verdict, with a different invoice number and an invoice date within 120 days of this one. Score = 0.4 x line overlap (Jaccard on (service_code or category, quantity, unit_price)) + 0.3 x total within 1% + 0.2 x same order + 0.1 x overlapping period. Score >= 0.8 -> `duplicate.suspected` (warning). Same invoice number seen before with identical content -> `duplicate.resubmitted` (info, not a finding that changes the verdict). Weights and threshold live in config.

## Verdict (decision table, config/decision_table.yaml)
Evaluated top to bottom, first match wins. Row 0 is fixed in code (`app/verdict.py`) and comes before any table. It has two parts. (a) Only a finding with certainty `code` or `verified_rule` can give afgewezen. A finding that rests on AI (certainty `ai`: an unconfirmed rule, a model line category) or on a check that could not run (certainty `not_run`: `contract.not_analysed`, `po.requirements_not_analysed`, `lines.unclassified`) gives at most controle nodig, whatever a customer table says. Config loading refuses a table in which a rejecting row does not limit itself to certainty `code` / `verified_rule`. (b) Build: such a finding also never gives goedgekeurd, so a lax customer table cannot approve an invoice whose AI checks did not run (guardrail: "never goedgekeurd").
1. any finding with severity error and certainty `code` or `verified_rule` -> afgewezen
2. any finding with severity error and certainty `ai` -> controle nodig
3. any warning (this includes `contract.not_analysed`, `contract.rules_unverified`, `lines.ai_classified`, `lines.unclassified`, `lines.category_conflict`) -> controle nodig
4. otherwise -> goedgekeurd
A customer can point to a stricter table in its config (for example: under-billing warnings from code also reject), within Row 0.

## Failure and recovery
- Model down, timeout (60 s), invalid JSON: one retry with the parse error; then the contract is marked `not_analysed` with the reason, and every invoice on that order gets `contract.not_analysed` (controle nodig) until a rerun of `ingest-contract` succeeds.
- No token at intake: the contract is `not_analysed` ("AI-check niet uitgevoerd: geen token"), so every invoice on that order is controle nodig. Recorded answers are never used by the service, only by the eval script. A reviewer without a token sees every code check working and the contract part honestly marked as not run; the README shows how to add a token and rerun `seed`.
- No token at check time, or the line classification times out (20 s) or returns something outside the closed list: the line gets `lines.unclassified` (warning) and the check is stored as usual; no retry per invoice.
- Bad invoice: 422 with field list. Unknown order or customer/order mismatch: 404 / 422 with a Dutch message. Build: a country that is not configured, a country that differs from the country of the order, or a currency that differs from the currency of the country: 422 as well. All stored as checks with outcome `geen_oordeel`.
- Same invoice twice: both stored; see duplicates.
- Config file invalid: the service refuses to start and names the file and field.

## Integration points
- Auth: one API key in `X-API-Key`. Compose passes `API_KEY=${API_KEY:-demo-key}` and loads `.env` only when it exists (`env_file` with `required: false`), so a fresh clone works with `demo-key`; the service logs a warning when the demo key is in use. Constant-time compare.
- Configuration and secrets: optional `.env` for `API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`; `.env.example` documents both. The service picks the live client when the token is set, else unavailable; there is no mode switch in the service. The eval script picks the recorded client itself. Never baked into the image.
- Logging: structured JSON lines to stdout: check id, invoice number, order id, verdict, finding codes, durations, ai mode; at intake: the order or customer id, the status and the number of rules. Never invoice line text, contract text, names or amounts.
- Errors: one exception type per domain error, mapped in the API layer.
- Deployment: Docker image (python 3.12 slim + uv + node for the Claude CLI, pinned versions), compose file with a named volume for the database (mounted at `/data`, `DATABASE_PATH=/data/app.db`; without Docker the default is `data/app.db`), port 127.0.0.1:8000. `.dockerignore` keeps `.env` out of the image. The container command is `python -m app.cli init && uvicorn ...`: `init` creates the schema, loads the synthetic seed when the database is empty and runs contract intake for what is still `not_analysed` with whatever client is available (idempotent: a second start in the same situation changes nothing; a start after a token was added analyses what was pending). Build: the image also holds `tests/`, `evals/` and the dev packages, so a reviewer can run the tests and the eval inside the container. So `docker compose up` alone gives a database with orders, and the cold-start check measures exactly that. Without Docker: `uv sync && uv run python -m app.cli init && uv run uvicorn app.main:app`.

## Security
- Reachable: one port on localhost; every route except `/health` and `/docs` needs the key.
- Personal and confidential data: customer names, contact names, amounts, PO numbers. None leaves the machine except masked contract and requirement text at intake. Masking is reversible in memory only; the token map is never stored or logged.
- Prompt injection: of an invoice only the masked description of an unclear line reaches a model, and the answer is one value from a closed category list that always leads to controle nodig. Contract text is fenced as data in the prompt; the model has all tools off and runs in an empty temp folder; output must parse into a closed schema of rule types; every rule must carry a quote that exists verbatim in the original text; numbers are bounded; a PO regex must compile, be under 64 characters and use only an allowed subset (anchors, character classes, digits, fixed counts). The verdict is code, so the worst an injection can do is make the model add or omit a rule at intake. An added rule must quote real contract text and is ai_extracted, so at most it causes "controle nodig". An omitted rule cannot lead to goedgekeurd: until a person confirms the rule set with `verify-contract`, every invoice on the order is controle nodig (S11b). After confirmation the person owns completeness; the README names that as the human control point. Eval cases test both.
- The service has no write path to any invoice system.

## Codebase
```
factuurcontrole/
  app/
    main.py            API: routes, key check, error mapping
    schemas.py         Pydantic: InvoiceIn, CheckOut, Finding, Source
    checker.py         check_invoice(invoice, deps) -> CheckResult
    context.py         CheckContext: everything a check may read (built by checker from the store)
    checks/            totals.py vat.py po.py rates_hours.py contract.py duplicates.py
    verdict.py         decide(findings, table) -> Verdict
    messages.py        Dutch message templates per finding code
    config.py          load and validate config/*.yaml
    store.py           SQLite schema, seed, reads, save_check
    ai/
      client.py        ModelClient: ClaudeCLI | Recorded | Unavailable
      masking.py       mask(text, known_names) -> (masked, token_map); unmask
      rules.py         extract_rules(text, kind, client) -> ExtractionResult
      classify.py      classify_line(description, client) -> Category | None
      prompts.py       the three prompts (contract clauses, customer requirements, line category)
    cli.py             init, seed, ingest-contract, verify-contract, verify-requirements
  config/              countries/NL.yaml DE.yaml, decision_table.yaml, line_categories.yaml, duplicates.yaml
  data/seed/           customers.json orders.json rate_cards.json time_entries.json contracts/*.txt requirements/*.txt
                       examples/*.json (one valid invoice per order, shown in /docs)
  data/cassettes/      recorded model answers, keyed by sha256 of task + masked prompt; each file also holds the prompt
  evals/               cases/*.json, run_eval.py, results/latest.md, results/live.json
  tests/               one file per check + api, verdict, masking, rules, store; conftest.py (fake model, seeded store)
  docs/                architecture.md (Mermaid), architecture.png, adr/, design/ (design and review logs)
  build/               preflight.ps1 (the prerequisite check of the build)
  Dockerfile docker-compose.yml .dockerignore pyproject.toml uv.lock .env.example README.md
  ARCHITECTURE.md GLOSSARY.md CLAUDE.md .gitignore
```
Package marker files (`__init__.py` in `app/`, `app/ai/`, `app/checks/`, `tests/`) are not listed.
Seams: `ModelClient` (three adapters: real seam). Checks share one interface `run(ctx) -> list[Finding]` and a fixed registry list in `checker.py`; every check always runs, and config only gives each its country and customer parameters (VAT rates, whether a PO is required and its format, duplicate weights). Store: one adapter, no seam. Tests: checks are tested through `run(ctx)` with hand-built contexts; the checker through `check_invoice` with a temp SQLite and the `Recorded`/`Unavailable` client; the API through FastAPI's TestClient.

Alternatives considered: B, one `checks.py` and one `ai.py` (fewer files, but the eval counts per check and the tests read per check, so separate modules pay off); C, a declarative rule engine (rules as expressions in YAML evaluated generically): more configurable on paper, but it needs an expression language, makes sources harder to trace, and the case only needs per-country and per-customer parameters, which plain YAML and DB rows give.

## Evaluation (how the proof is built)
- `evals/cases/*.json`: a small fixed labelled set, written by hand: 6 clean invoices (NL and DE, three customers) and 2 per error type (totals line, totals aggregate, VAT, PO missing, PO format, rate, hours, travel vs clause, unclear line, near-duplicate), plus the edge cases (unknown order, missing field, injection in a line, injection in a contract). About 30 cases. Each carries its expected verdict and expected finding codes per mode.
- `evals/run_eval.py` runs every case through the HTTP API (TestClient) on a fresh database, in two modes: `zonder-ai` (Unavailable client) and `met-ai` (Recorded client, then `verify-contract` and `verify-requirements` as a person would). It prints per finding code: caught, missed, false alarms, and a verdict confusion matrix, and writes `evals/results/latest.md`. `--live --record` runs once with the token and writes the cassettes; the report compares the live verdicts with the recorded ones (DONE MEANS 5).
- No held-out set and no case generator: the time goes to the required proof first. The README names a blind held-out set as the next step.

## Agent decisions
- stdlib `sqlite3` instead of an ORM: seven tables, no migrations needed.
- Reasons are Dutch templates in code (`messages.py`); the model's part in the reasons is the Dutch explanation of each contract rule (`explanation_nl`), written once at intake and shown next to the original-language quote. Why: a template cannot drift from the finding; translating and explaining a German clause is where the model adds value.
- Duplicate scoring is code, not embeddings: invoices are structured, and a weighted feature score is explainable in the source field.
- Under-billing is a warning, not an error.
- Build: the API verdict values are `goedgekeurd`, `controle_nodig`, `afgewezen`; a stored call without a verdict has outcome `geen_oordeel`. `line` on a finding counts from 1, as the messages do.
- Build: a refused rule candidate is stored with its type and the reason only; the rest of it is untrusted model text.
- Build: the eval has 43 cases instead of about 30 (more cases per finding code), and `--live` without `--record` is a separate live run that is compared with the recorded answers.

## Open questions
- (2026-11-14) None known by Rienk.
