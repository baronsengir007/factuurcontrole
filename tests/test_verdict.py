"""The verdict: the decision table from config, behind Row 0 that is fixed in code."""

import os
import shutil

import pytest

from app.config import ROOT, ConfigError, DecisionTable, Settings, load_config, load_env_file
from app.messages import finding, summary
from app.schemas import Source
from app.verdict import decide

SRC = Source(type="calculation", ref="test")


def f(code: str, certainty: str):
    return finding(code, certainty, SRC, line=1, quantity="1", unit_price="1", expected="1", stated="2",
                   reason="geen token", count=1, description="x", category_nl="reiskosten", amount="1",
                   service_code="X", invoiced="1", recorded="2", invoice_number="INV-1")


def table(rows, default="goedgekeurd") -> DecisionTable:
    return DecisionTable.model_validate({"rows": rows, "default": default})


def test_no_findings_is_goedgekeurd(config):
    assert decide([], config.table()) == "goedgekeurd"


def test_an_error_from_code_is_afgewezen(config):
    assert decide([f("totals.line_amount", "code")], config.table()) == "afgewezen"


def test_an_error_from_a_confirmed_rule_is_afgewezen(config):
    assert decide([f("contract.expense_not_billable", "verified_rule")], config.table()) == "afgewezen"


def test_an_error_that_rests_on_ai_is_controle_nodig(config):
    assert decide([f("contract.expense_not_billable", "ai")], config.table()) == "controle_nodig"


def test_any_warning_is_controle_nodig(config):
    assert decide([f("hours.below_records", "code")], config.table()) == "controle_nodig"
    assert decide([f("contract.not_analysed", "not_run")], config.table()) == "controle_nodig"
    assert decide([f("lines.ai_classified", "ai")], config.table()) == "controle_nodig"


def test_information_does_not_change_the_verdict(config):
    assert decide([f("duplicate.resubmitted", "code")], config.table()) == "goedgekeurd"


def test_a_certain_error_wins_over_warnings(config):
    findings = [f("contract.not_analysed", "not_run"), f("totals.line_amount", "code")]
    assert decide(findings, config.table()) == "afgewezen"


def test_a_customer_can_get_a_stricter_table(config):
    """Under-billing warnings from code also reject, for a customer whose table says so."""
    strict = table([
        {"name": "under_billing_rejects", "verdict": "afgewezen",
         "when": {"severity": "warning", "certainty": "code", "codes": ["hours.below_records"]}},
        *[row.model_dump() for row in config.table().rows],
    ])
    assert decide([f("hours.below_records", "code")], strict) == "afgewezen"
    assert decide([f("hours.below_records", "code")], config.table()) == "controle_nodig"


# --- Row 0: fixed in code, whatever a table says ---------------------------------------------


@pytest.mark.parametrize("when", [
    {"severity": "error"},  # no certainty: would also match AI findings
    {"severity": "error", "certainty": ["code", "ai"]},
    {"severity": "warning", "certainty": "not_run", "codes": ["contract.not_analysed"]},
    {"severity": "warning", "codes": ["lines.*"]},
])
def test_row_zero_refuses_a_table_that_lets_ai_or_not_run_findings_reject(when):
    with pytest.raises(ValueError, match="afgewezen"):
        table([{"name": "too_strict", "when": when, "verdict": "afgewezen"}])


def test_row_zero_refuses_a_table_whose_default_is_afgewezen():
    with pytest.raises(ValueError):
        table([], default="afgewezen")


def test_a_bad_customer_table_stops_config_loading_and_names_file_and_row(tmp_path):
    directory = tmp_path / "config"
    shutil.copytree(ROOT / "config", directory)
    (directory / "strict.yaml").write_text(
        "rows:\n  - name: reject_ai\n    when: {severity: error, certainty: [ai]}\n    verdict: afgewezen\n"
        "default: goedgekeurd\n", encoding="utf-8")
    config = load_config(directory)
    with pytest.raises(ConfigError, match=r"strict\.yaml.*reject_ai"):
        config.table("strict.yaml")


def test_row_zero_a_holds_in_decide_even_for_a_table_object_built_around_validation():
    row = {"name": "reject_errors", "when": {"severity": "error", "certainty": ["code"]}, "verdict": "afgewezen"}
    built = table([row])
    built.rows[0].when.certainty = None  # what a bug or a hand-built object could do
    assert decide([f("contract.expense_not_billable", "ai")], built) == "controle_nodig"
    assert decide([f("totals.line_amount", "code")], built) == "afgewezen"


def test_row_zero_b_a_lax_table_cannot_approve_a_check_that_did_not_run():
    lax = table([])  # no rows: everything would be goedgekeurd
    assert decide([f("contract.not_analysed", "not_run")], lax) == "controle_nodig"
    assert decide([f("lines.ai_classified", "ai")], lax) == "controle_nodig"
    assert decide([f("contract.expense_not_billable", "ai")], lax) == "controle_nodig"
    assert decide([], lax) == "goedgekeurd"


# --- config: a bad file stops the service and names the file and the field --------------------


@pytest.mark.parametrize("file, content, expected", [
    ("countries/DE.yaml", "country: DE\nname_nl: Duitsland\ncurrency: EUR\nvat: {standard: 190}\n", r"DE\.yaml: vat\.standard"),
    ("countries/DE.yaml", "country: FR\nname_nl: x\ncurrency: EUR\nvat: {standard: 19}\n", r"DE\.yaml: country"),
    ("decision_table.yaml", "rows: []\ndefault: misschien\n", r"decision_table\.yaml: default"),
    ("duplicates.yaml", "weights: {lines: 0.9, total: 0.3, order: 0.2, period: 0.1}\nthreshold: 0.8\n"
                        "window_days: 120\ntotal_tolerance_pct: 1\n", r"duplicates\.yaml.*add up to 1"),
    ("line_categories.yaml", "keywords: {vervoer: [taxi]}\n", r"line_categories\.yaml: keywords"),
    ("duplicates.yaml", "weights: [\n", r"duplicates\.yaml: not valid YAML"),
])
def test_an_invalid_config_file_is_refused_with_file_and_field(tmp_path, file, content, expected):
    directory = tmp_path / "config"
    shutil.copytree(ROOT / "config", directory)
    (directory / file).write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=expected):
        load_config(directory)


def test_a_new_country_is_a_new_file_not_new_code(tmp_path):
    directory = tmp_path / "config"
    shutil.copytree(ROOT / "config", directory)
    (directory / "countries" / "BE.yaml").write_text(
        "country: BE\nname_nl: Belgie\ncurrency: EUR\nvat: {standard: 21}\n", encoding="utf-8")
    config = load_config(directory)
    assert sorted(config.countries) == ["BE", "DE", "NL"]
    assert config.config_hash != load_config().config_hash  # a stored check shows which config it used


# --- the summary line -------------------------------------------------------------------------


def test_the_summary_counts_and_names_the_first_action():
    findings = [f("contract.not_analysed", "not_run"), f("totals.line_amount", "code")]
    assert summary("afgewezen", findings) == "1 fout, 1 twijfelpunt. Niet versturen. Eerst: reken regel 1 na."
    assert summary("goedgekeurd", []) == "Geen fouten of twijfelpunten gevonden."
    review = summary("controle_nodig", [f("hours.below_records", "code")])
    assert review.startswith("0 fouten, 1 twijfelpunt. Laat iemand meekijken")


# --- settings ---------------------------------------------------------------------------------


def test_the_env_file_fills_missing_variables_and_never_overrides_the_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nAPI_KEY=from-file\nCLAUDE_CODE_OAUTH_TOKEN=\nFC_TEST_OTHER="quoted"\n', encoding="utf-8")
    monkeypatch.setenv("API_KEY", "from-environment")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    try:
        load_env_file(env_file)
        assert os.environ["API_KEY"] == "from-environment"
        assert os.environ["FC_TEST_OTHER"] == "quoted"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ  # an empty value is no token
    finally:
        os.environ.pop("FC_TEST_OTHER", None)
    load_env_file(tmp_path / "missing.env")  # no file is fine


def test_without_settings_the_service_uses_the_demo_key(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    assert Settings.from_env().api_key == "demo-key"
    monkeypatch.setenv("API_KEY", "eigen-sleutel")
    assert Settings.from_env().api_key == "eigen-sleutel"
