"""Masking: what must never reach a model is replaced, reversibly."""

import pytest

from app.ai.masking import mask, unmask
from app.ai.prompts import contract_rules_prompt, requirement_rules_prompt
from tests.conftest import SEED

NAMES = ["Rothmann Anlagenbau GmbH", "Katrin Vogel", "Van der Vliet Procestechniek B.V.", "Sanne de Groot"]


def test_known_names_are_masked_also_in_another_case_or_over_a_line_break():
    masked, tokens = mask("Kontakt: katrin vogel bei Rothmann Anlagenbau\nGmbH.", NAMES)
    assert masked == "Kontakt: [NAAM_2] bei [NAAM_1]."
    assert tokens == {"[NAAM_1]": "Rothmann Anlagenbau\nGmbH", "[NAAM_2]": "katrin vogel"}


@pytest.mark.parametrize("text, token", [
    ("Mail an rechnungseingang@rothmann-anlagenbau.example bitte", "[EMAIL_1]"),
    ("IBAN NL91 ABNA 0417 1643 00 ten name van", "[IBAN_1]"),
    ("IBAN DE89370400440532013000.", "[IBAN_1]"),
    ("Telefon +49 30 5550 1234.", "[TEL_1]"),
    ("Bel 020-5550123 voor vragen", "[TEL_1]"),
    ("Der Stundensatz beträgt 95,00 EUR.", "[BEDRAG_1]"),
    ("Facturen boven EUR 10.000,00 worden afgestemd", "[BEDRAG_1]"),
    ("een bedrag van € 1.250,00 per maand", "[BEDRAG_1]"),
    ("totaal 1250.00 exclusief btw", "[BEDRAG_1]"),
    ("Bestellnummer 4500012345 angeben", "[NUMMER_1]"),
    ("onder PO-204518 besteld", "[NUMMER_1]"),
    ("Auftrag ORD-DE-0077", "[NUMMER_1]"),
])
def test_sensitive_values_become_tokens(text, token):
    masked, tokens = mask(text)
    assert token in masked
    assert list(tokens) == [token]
    assert tokens[token] not in masked
    assert unmask(masked, tokens) == text


@pytest.mark.parametrize("text", [
    "Die Bestellnummer beginnt mit 45 und hat genau 10 Ziffern.",
    "Rechnungen sind innerhalb von 30 Tagen ohne Abzug zahlbar.",
    "§ 3 Nebenkosten",
    "Inspektionen der Stufe 2 im Jahr 2026",
])
def test_small_numbers_that_carry_the_meaning_of_a_clause_stay(text):
    assert mask(text) == (text, {})


def test_the_same_value_gets_the_same_token_and_unmask_restores_the_text():
    text = "Katrin Vogel zahlt 95,00 EUR. Rückfragen an Katrin Vogel, nochmals 95,00 EUR, dann 120,00 EUR."
    masked, tokens = mask(text, NAMES)
    assert masked.count("[NAAM_1]") == 2 and masked.count("[BEDRAG_1]") == 2 and "[BEDRAG_2]" in masked
    assert unmask(masked, tokens) == text


def test_a_token_that_was_already_in_the_text_cannot_be_used_to_inject_a_value():
    masked, tokens = mask("Zahlung [BEDRAG_1] an Katrin Vogel über 500,00 EUR", NAMES)
    assert masked == "Zahlung (BEDRAG_1) an [NAAM_1] über [BEDRAG_1]"
    assert unmask(masked, tokens) == "Zahlung (BEDRAG_1) an Katrin Vogel über 500,00 EUR"


@pytest.mark.parametrize("path, build", [
    ("contracts/ORD-DE-0077.txt", contract_rules_prompt),
    ("contracts/ORD-NL-0031.txt", contract_rules_prompt),
    ("requirements/CUST-ROTH.txt", requirement_rules_prompt),
])
def test_the_prompt_for_a_seed_text_holds_no_name_amount_number_or_contact_detail(path, build):
    text = (SEED / path).read_text(encoding="utf-8")
    masked, _ = mask(text, NAMES)
    prompt = build(masked)
    for secret in NAMES + ["95,00", "10.000,00", "ORD-DE-0077", "ORD-NL-0031", "rothmann-anlagenbau.example", "5550 1234"]:
        assert secret not in prompt
    # the clause the model has to read is still there
    assert ("Reisekosten werden nicht gesondert berechnet." in prompt or "niet apart gefactureerd" in prompt
            or "beginnt mit 45" in prompt)
