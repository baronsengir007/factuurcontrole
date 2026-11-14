# Evalresultaten factuurcontrole

Gedraaid op 2026-11-14 18:02. 46 gelabelde, synthetische cases, elk op een eigen lege database, via de HTTP-API. Config-hash `d9ca341cac5d7f42`. Opnieuw draaien: `uv run python evals/run_eval.py`.

- **zonder-ai**: geen model (geen token). De AI-controles melden dat ze niet zijn uitgevoerd.
- **met-ai**: opgenomen antwoorden van Claude, daarna zijn alle regelsets bevestigd zoals een mens dat doet.

## Samenvatting

| Modus | Cases goed | Codecontroles: gemist | Codecontroles: vals alarm | AI-controles: gemist | AI-controles: vals alarm |
|---|---|---|---|---|---|
| zonder-ai | 46 van 46 | 0 | 0 | 0 | 0 |
| met-ai | 46 van 46 | 0 | 0 | 0 | 0 |

Een case is goed als het oordeel en de set bevindingcodes precies kloppen met het label (bij een invoerfout: de statuscode en de foutcode).

## zonder-ai: per bevindingcode

| Bevindingcode | Soort | Gevangen | Gemist | Vals alarm |
|---|---|---|---|---|
| `totals.line_amount` | code | 2 | 0 | 0 |
| `totals.subtotal` | code | 1 | 0 | 0 |
| `totals.vat_total` | code | 1 | 0 | 0 |
| `totals.total` | code | 1 | 0 | 0 |
| `vat.wrong_rate` | code | 2 | 0 | 0 |
| `po.missing` | code | 1 | 0 | 0 |
| `po.bad_format` | code | 1 | 0 | 0 |
| `po.requirements_not_analysed` | AI | 22 | 0 | 0 |
| `rates.mismatch` | code | 2 | 0 | 0 |
| `rates.unknown_service` | code | 1 | 0 | 0 |
| `rates.no_service_code` | code | 1 | 0 | 0 |
| `rates.unit_mismatch` | code | 1 | 0 | 0 |
| `hours.exceeds_records` | code | 3 | 0 | 0 |
| `hours.below_records` | code | 1 | 0 | 0 |
| `contract.not_analysed` | AI | 42 | 0 | 0 |
| `lines.unclassified` | AI | 3 | 0 | 0 |
| `lines.category_conflict` | code | 2 | 0 | 0 |
| `duplicate.suspected` | code | 2 | 0 | 0 |
| `duplicate.resubmitted` | code | 1 | 0 | 0 |

### zonder-ai: oordelen (rij = verwacht, kolom = gegeven)

| | goedgekeurd | controle_nodig | afgewezen | geen oordeel (4xx) |
|---|---|---|---|---|
| **goedgekeurd** | 0 | 0 | 0 | 0 |
| **controle_nodig** | 0 | 27 | 0 | 0 |
| **afgewezen** | 0 | 0 | 15 | 0 |
| **geen oordeel (4xx)** | 0 | 0 | 0 | 4 |

## met-ai: per bevindingcode

| Bevindingcode | Soort | Gevangen | Gemist | Vals alarm |
|---|---|---|---|---|
| `totals.line_amount` | code | 2 | 0 | 0 |
| `totals.subtotal` | code | 1 | 0 | 0 |
| `totals.vat_total` | code | 1 | 0 | 0 |
| `totals.total` | code | 1 | 0 | 0 |
| `vat.wrong_rate` | code | 2 | 0 | 0 |
| `po.missing` | code | 2 | 0 | 0 |
| `po.bad_format` | code | 3 | 0 | 0 |
| `po.requirements_unverified` | AI | 1 | 0 | 0 |
| `rates.mismatch` | code | 2 | 0 | 0 |
| `rates.unknown_service` | code | 1 | 0 | 0 |
| `rates.no_service_code` | code | 1 | 0 | 0 |
| `rates.unit_mismatch` | code | 1 | 0 | 0 |
| `hours.exceeds_records` | code | 3 | 0 | 0 |
| `hours.below_records` | code | 1 | 0 | 0 |
| `contract.expense_not_billable` | AI | 6 | 0 | 0 |
| `contract.rules_unverified` | AI | 1 | 0 | 0 |
| `lines.ai_classified` | AI | 2 | 0 | 0 |
| `lines.unclassified` | AI | 1 | 0 | 0 |
| `lines.category_conflict` | code | 2 | 0 | 0 |
| `duplicate.suspected` | code | 2 | 0 | 0 |
| `duplicate.resubmitted` | code | 1 | 0 | 0 |

### met-ai: oordelen (rij = verwacht, kolom = gegeven)

| | goedgekeurd | controle_nodig | afgewezen | geen oordeel (4xx) |
|---|---|---|---|---|
| **goedgekeurd** | 9 | 0 | 0 | 0 |
| **controle_nodig** | 0 | 11 | 0 | 0 |
| **afgewezen** | 0 | 0 | 22 | 0 |
| **geen oordeel (4xx)** | 0 | 0 | 0 | 4 |

## Live Claude tegenover de opgenomen antwoorden

Live run van 2026-11-14 18:01 (modus met-ai, model via de Claude CLI). Dit was een losse live run, na het opnemen: elke aanroep ging opnieuw naar Claude.

- Zelfde oordeel als met de opgenomen antwoorden: **46 van 46** cases.
- Ook dezelfde bevindingcodes: 46 van 46 cases.
- Live tegenover de labels: 46 van 46 cases goed.
