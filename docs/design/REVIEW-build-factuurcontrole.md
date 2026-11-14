# Build review: factuurcontrole

Log of the review of the built code. Round 0 is the builder's own comparison of the code with ARCHITECTURE.md and the brief. The Codex rounds follow below it. A P1 names its anchor: the DONE MEANS line the code fails, a deviation from ARCHITECTURE.md or an accepted ADR, or a bug with the scenario that triggers it.

Reviewer, from round 2 on: before new findings, report per P1 of the previous round: resolved or not resolved.

## Round 0 (builder, 2026-11-14, before Codex was reachable)

Codex was at its usage limit until 17:26, so the builder first compared the code with ARCHITECTURE.md line by line.

- found:
  1. P1 - bug: with a customer table in which a warning rejects (the stricter table ARCHITECTURE.md allows), the summary line crashed with an IndexError, so the call returned 500. Scenario: customer table with `hours.below_records -> afgewezen`, invoice with fewer hours than recorded. Found by a new test.
  2. P1 - deviation from ARCHITECTURE.md, Verdict, Row 0 ("a finding that rests on AI gives at most controle nodig"): `vat.wrong_rate` had certainty `code` also when the expected rate came from a line category the model gave. Scenario: a country config with a rate per category, an unclear line the model classifies.
  3. P1 - same anchor: `rates.no_service_code` had certainty `code` also when the model gave the category "labour". Scenario: a customer table that rejects on that warning.
  4. minor - the real CLI call (`_run_cli`) had no test: tools off, empty folder, token only in the environment, every failure mapped to a ModelError.
  5. minor - the start-up check of customer tables and the `.env` reader had no test.
  6. minor - `invoice_number`, `order_id` and `customer_id` of an invalid request were stored unbounded in the index columns.
- changed:
  1. `app/messages.py` `summary`: the first action comes from the first certain finding, error or warning. Test: `test_a_customer_with_a_stricter_table_gets_a_stricter_verdict_and_other_customers_do_not`.
  2. `app/checks/vat.py`: certainty `ai` (model category) or `not_run` (no category) when the country's rate depends on the category. Tests: `test_a_vat_finding_that_rests_on_a_model_category_is_not_certain`, `test_with_one_rate_for_every_category_the_category_does_not_matter`. ARCHITECTURE.md updated.
  3. `app/checks/rates_hours.py`: certainty `ai` when the category came from the model. Test: `test_a_missing_service_code_on_a_line_the_model_called_labour_is_not_certain`. ARCHITECTURE.md updated.
  4. Tests added in `tests/test_rules.py` with the subprocess replaced.
  5. Tests added in `tests/test_api.py` and `tests/test_verdict.py`.
  6. The three index columns keep the first 64 characters; the whole input stays in `input`.
- differences between the code and the Codebase section that remain: none after the update of ARCHITECTURE.md. What the build changed in the shape is marked "Build:" in that file.
- state after round 0: `uv run pytest -q` 213 passed; `uv run python evals/run_eval.py` 43 of 43 in both modes.

## Round 1 (Codex, `codex review --uncommitted`, 2026-11-14 17:51)

- found:
  1. P1 - bug with scenario (`app/checker.py`, line categories): a line with a service code of the rate card got the category of the rate card, also when the line itself said something else. Scenario: on the verified order `ORD-DE-0077` (contract excludes travel) a line "Reisekosten" billed under `INSP-L2`, with the card price and with hours that match the time entries, was goedgekeurd. Anchor: wrong approval of a cost the contract excludes (the contract check, brief check 5).
  2. P2 - `app/checks/rates_hours.py`: the unit of a line is not compared with the unit of the rate card.
  3. P2 - `app/store.py` `earlier_checks`: every earlier check of the customer is loaded for the duplicate check, not only those inside the date window.
- changed:
  1. `app/checker.py`: `_conflict` compares the category that the service code or the `category` field gives with the line's own evidence: the `category` field (against the rate card) and the keywords in the description (against both). Keywords that the rate card's own description of the service holds do not count. `LineCategory` got the field `conflict`. `app/checks/contract.py` gives `lines.category_conflict` (warning, certainty `code`), so the verdict is controle_nodig. The same hole existed one step lower (a travel line labelled `materials` in the `category` field); the same comparison closes it for descriptions with a known keyword. Tests: `test_a_line_that_points_to_another_category_than_its_service_code_is_not_approved` (2 variants), `test_a_category_field_that_contradicts_the_description_is_not_approved`, `test_a_line_that_points_to_another_category_gives_a_certain_warning`, `test_words_from_the_rate_cards_own_description_are_not_a_conflict`. Eval cases `contract-hidden-01` and `contract-hidden-02`. ARCHITECTURE.md (contract check, verdict row 3) and README updated.
- rejected (minor, not changed):
  2. Unit against the rate card: no anchor in DONE MEANS or ARCHITECTURE.md (the rates check is defined on the unit price), and the money is covered: `quantity x unit_price` is checked by totals, and hours billed under another unit still count against the time entries. Listed in the report as a minor finding.
  3. Duplicate candidates: a cost that grows with the history of one customer, not a wrong verdict. The prototype measures 19 ms per check on its data. Listed in the report as a minor finding; a production store filters on invoice date in SQL.
- remaining limit, stated in the README: a line with a wrong label and a description without a known keyword is not caught.
- state after round 1: `uv run pytest -q` 218 passed; `uv run python evals/run_eval.py` 45 of 45 in both modes; `--live` run of 17:56: same verdict on 45 of 45, same codes on 44 of 45 (`injection-line-02`: the live model gave the injected line a category this time; verdict stayed controle_nodig).

## Round 2 (Codex, 2026-11-14 17:59)

- check of round 1 first (`codex exec`, read-only): P1 1 resolved. Codex read the code; it could not run the tests in its read-only sandbox.
- found:
  1. P1 - bug with scenario (`app/checks/rates_hours.py`): a line with a valid service code and the card price but another unit (`day` in place of `hour`) was goedgekeurd. This is finding 2 of round 1, which the builder rejected as minor; Codex now gives the scenario of a wrong approval. Anchor: the rates check of the brief (rate against the rate card); 95.00 per day is not the agreed 95.00 per hour.
  2. P2 - `app/store.py` `earlier_checks`: unchanged from round 1.
- changed:
  1. `app/checks/rates_hours.py`: the unit of the line is compared with the unit of the rate card, without regard to capitals and spaces. A difference gives the new code `rates.unit_mismatch` (warning, certainty `code`), so the verdict is controle_nodig. A warning and not an error: `unit` is free text, and a billing system that spells the unit differently must not get every invoice rejected. Test: `test_a_unit_that_differs_from_the_rate_card_is_a_warning_and_spelling_in_capitals_is_not`. Eval case `rate-unit-01`. ARCHITECTURE.md (rates_hours) and README updated. The rejection of this finding in round 1 was wrong on one point: the totals check proves the arithmetic, not that the unit is the agreed one.
- rejected (minor, not changed):
  2. Duplicate candidates: same reason as in round 1.
- state after round 2: `uv run pytest -q` 219 passed; `uv run python evals/run_eval.py` 46 of 46 in both modes; `--live` run of 18:01: same verdict and same codes on 46 of 46. The three new cases got their own invoice numbers after that run; no model call reads an invoice number.

## Round 3 (Codex, 2026-11-14 18:03)

- check of round 2 first (`codex exec`, read-only): P1 1 resolved, by reading the code.
- found: nothing. Codex: "I found no actionable bugs in the staged, unstaged, or untracked changes." It did not run the tests; the builder did (state after round 2).
- changed: nothing.
- open minor finding from rounds 1 and 2, not changed: the duplicate check loads every earlier check of the customer (`app/store.py` `earlier_checks`).
- state after round 3: cold run on a copy without `.env`, `--no-cache`: build 70 s, `/health` at 77 s, example POST 200 `controle_nodig`; in that container `pytest` 219 passed and the eval 46 of 46 in both modes.
