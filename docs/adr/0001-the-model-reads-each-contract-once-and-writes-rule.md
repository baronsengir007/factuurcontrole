# 0001 - The model reads each contract once and writes rules; code applies them per invoice

Status: accepted (user decision, 2026-11-14)

Context: contract clauses are free text in any language; invoices arrive by the thousand per month.

Decision: at contract intake the model turns clauses and customer requirement text into typed rules with a verbatim quote and a Dutch explanation, stored in SQLite; every invoice is checked by code against those rules. Per invoice the model is called only for a line whose category neither the line nor the keyword table gives, with the masked description alone, and its answer always leads to controle nodig.

Why: cheap, fast and repeatable per invoice, and the rules are readable and reviewable by a person. Reversing to per-invoice judgement costs a model call per invoice and makes verdicts vary between runs.

Rejected: a model call per invoice with clause text and lines (cost, latency, non-repeatable, harder to audit).
