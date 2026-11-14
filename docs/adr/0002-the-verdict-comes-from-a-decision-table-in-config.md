# 0002 - The verdict comes from a decision table in config; AI-extracted rules give "controle nodig" until a person verified them

Status: accepted (user decision, 2026-11-14)

Context: the case wants "controle nodig" when the system is unsure, and traceable outcomes.

Decision: findings carry severity and certainty; a first-match decision table in `config/decision_table.yaml` maps them to the verdict. An error resting on AI (a rule of a contract no person has confirmed, or a line category from the model) gives controle nodig; from a confirmed rule or from code, afgewezen. A check that could not run gives controle nodig. A contract whose rule set no person has confirmed keeps every invoice on that order at controle nodig, so a rule the model missed cannot approve an invoice.

Why: a billing employee reads the row that fired in one sentence, and a misread clause can never reject on its own. A weighted score can list its features too, but its threshold has to be tuned and explained, and the case gives no data to tune it. Reversing changes every test expectation.

Rejected: weighted score with threshold (opaque); the model deciding the verdict (not repeatable, not allowed by the brief).
