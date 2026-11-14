"""Reversible masking: names, amounts, long numbers, emails, IBANs and phone numbers become tokens.

mask(text, known_names) -> (masked, token_map); unmask(text, token_map) puts the values back.
The token map lives in memory only. It is never stored or logged.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b")
_PHONE = re.compile(r"(?<![\w.,])(?:\+\d{1,3}|00\d{1,3}|0\d{1,3})[ /-]?(?:\(0\)[ ]?)?\d[\d /-]{5,12}\d(?!\w)")
_CURRENCY = r"(?:€|EUR\b|Euro\b|euro\b)"
_AMOUNT = re.compile(
    rf"{_CURRENCY}\s?\d[\d.,]*\d|{_CURRENCY}\s?\d"  # EUR 1.250,00
    rf"|(?<![\d.,])\d[\d.,]*\s?{_CURRENCY}"  # 1.250,00 EUR
    r"|(?<![\d.,])\d{1,3}(?:\.\d{3})*,\d{2}(?![\d.,]*\d)"  # 1.250,00
    r"|(?<![\d.,])\d+\.\d{2}(?![\d.,]*\d)"  # 1250.00
)
_NUMBER = re.compile(r"\b(?:[A-Z]{2,5}[-/])?\d{6,}\b|\b[A-Z]{2,5}[-/]\d{4,}\b")  # PO-like numbers

_KINDS = (("EMAIL", _EMAIL), ("IBAN", _IBAN), ("TEL", _PHONE), ("BEDRAG", _AMOUNT), ("NUMMER", _NUMBER))
_TOKEN = re.compile(r"\[(?:NAAM|EMAIL|IBAN|TEL|BEDRAG|NUMMER)_\d+\]")


def mask(text: str, known_names: list[str] | tuple[str, ...] = ()) -> tuple[str, dict[str, str]]:
    """Replace every sensitive value by a token. The same value always gets the same token."""
    token_map: dict[str, str] = {}
    by_value: dict[tuple[str, str], str] = {}

    def token_for(kind: str, value: str) -> str:
        key = (kind, value.casefold())
        if key not in by_value:
            count = sum(1 for k in by_value if k[0] == kind) + 1
            by_value[key] = f"[{kind}_{count}]"
            token_map[by_value[key]] = value
        return by_value[key]

    # A token that was already in the text would be unmasked wrongly later: break it up first.
    masked = _TOKEN.sub(lambda m: m.group(0).replace("[", "(").replace("]", ")"), text)

    for name in sorted({n for n in known_names if n and n.strip()}, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, name.split())) + r"(?!\w)", re.IGNORECASE)
        masked = pattern.sub(lambda m: token_for("NAAM", m.group(0)), masked)
    for kind, pattern in _KINDS:
        masked = pattern.sub(lambda m, kind=kind: token_for(kind, m.group(0)), masked)
    return masked, token_map


def unmask(text: str, token_map: dict[str, str]) -> str:
    return _TOKEN.sub(lambda m: token_map.get(m.group(0), m.group(0)), text)
