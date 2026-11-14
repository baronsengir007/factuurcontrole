# Glossary: factuurcontrole

Terms only. Dutch word for the README and messages, English word in code.

- **Conceptfactuur / draft invoice** (`Invoice`): the invoice before it is sent. Not: bill, factuurvoorstel.
- **Opdracht / order** (`Order`): the job a customer gave, with its rate card, contract and requirements. Not: project, assignment.
- **Klant / customer** (`Customer`): the company invoiced.
- **Tariefkaart / rate card** (`RateCard`): agreed unit price per service code for one order.
- **Urenregistratie / time entries** (`TimeEntry`): hours an inspector booked on an order, per day and service code.
- **Clausule / clause**: free text in a contract or in the customer's invoice requirements.
- **Contractregel / rule** (`ContractRule`): a typed rule derived from a clause, with quote, Dutch explanation and status `ai_extracted` or `verified`.
- **Controle / check** (`Check`): one run of the service on one invoice, stored. Not: validation, audit.
- **Bevinding / finding** (`Finding`): one problem found, with code, severity, certainty, message and source.
- **Oordeel / verdict** (`Verdict`): goedgekeurd, controle nodig or afgewezen. Not: status, result. In the API: `goedgekeurd`, `controle_nodig`, `afgewezen`; a stored call without a verdict has outcome `geen_oordeel`.
- **Bron / source** (`Source`): what a finding rests on: calculation, VAT rule, rate card, time entries, clause, customer requirement, earlier check, line category.
- **Zekerheid / certainty**: how sure a finding is: `code` (computed from structured data), `verified_rule` (a rule a person confirmed or configured), `ai` (rests on a model answer no person confirmed), `not_run` (the check could not run).
- **Regelcategorie / line category**: what an invoice line bills for, from a closed list: labour, lab_test, travel, accommodation, materials, subcontracting.
- **Opname / cassette**: a recorded model answer, used only by the eval script.
