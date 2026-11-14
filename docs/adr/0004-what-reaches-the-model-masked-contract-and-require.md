# 0004 - What reaches the model: masked contract and requirement text at intake, and the masked description of an unclear invoice line

Status: accepted (user decision, 2026-11-14)

Context: invoices and contracts are confidential; the case asks what is sent to an external model and how manipulation is prevented.

Decision: of an invoice only the description of a line the code cannot classify reaches a model, masked, and the answer is one category from a closed list. Contract and requirement text is masked reversibly (names, amounts, PO numbers, emails, IBAN, phone), fenced as data, sent with tools off; output is validated against a closed schema and every rule's quote must exist verbatim in the original text.

Why: the smallest data footprint that still lets the model do the one job code cannot. Reversing (sending invoices) widens the data exposure and the injection surface for no gain in the required checks.

Rejected: sending full invoices and contracts unmasked; a local model (quality on German legal text unproven, noted as an option in the drawing).
