"""Load and validate config/*.yaml and the settings from the environment.

A bad config file stops the service; the error names the file and the field.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.schemas import Category, Certainty, Finding, Severity, Verdict

ROOT = Path(__file__).resolve().parent.parent
DEMO_API_KEY = "demo-key"
DEFAULT_TABLE = "decision_table.yaml"

# Row 0 of the verdict, part one: only these certainties can give afgewezen.
REJECTING_CERTAINTIES = frozenset({"code", "verified_rule"})


class ConfigError(Exception):
    """A config file is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VatConfig(_Strict):
    standard: Decimal = Field(ge=0, le=100)
    by_category: dict[Category, Decimal] = {}


class CountryConfig(_Strict):
    country: str = Field(pattern=r"^[A-Z]{2}$")
    name_nl: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    vat: VatConfig

    def vat_rate(self, category: str | None) -> Decimal:
        return self.vat.by_category.get(category, self.vat.standard)  # type: ignore[arg-type]


def _as_list(value):
    return [value] if isinstance(value, str) else value


class When(_Strict):
    severity: list[Severity] = Field(min_length=1)
    certainty: list[Certainty] | None = None
    codes: list[str] | None = None

    wrap_single_values = field_validator("severity", "certainty", "codes", mode="before")(_as_list)


class Row(_Strict):
    name: str
    when: When
    verdict: Verdict

    def matches(self, finding: Finding) -> bool:
        when = self.when
        if finding.severity not in when.severity:
            return False
        if when.certainty is not None and finding.certainty not in when.certainty:
            return False
        if when.codes is not None and not any(_code_matches(c, finding.code) for c in when.codes):
            return False
        return True


def _code_matches(pattern: str, code: str) -> bool:
    if pattern.endswith(".*"):
        return code.startswith(pattern[:-1])
    return pattern == code


class DecisionTable(_Strict):
    """First match wins. Row 0 (in app/verdict.py) comes before any table."""

    rows: list[Row]
    default: Literal["goedgekeurd", "controle_nodig"]

    @model_validator(mode="after")
    def _within_row_zero(self) -> "DecisionTable":
        for row in self.rows:
            if row.verdict != "afgewezen":
                continue
            allowed = row.when.certainty
            if not allowed or not set(allowed) <= REJECTING_CERTAINTIES:
                raise ValueError(
                    f"row '{row.name}' gives afgewezen for findings that rest on AI or on a check that "
                    "did not run; a rejecting row must list certainty code and/or verified_rule only"
                )
        return self


class DuplicateWeights(_Strict):
    lines: Decimal = Field(ge=0, le=1)
    total: Decimal = Field(ge=0, le=1)
    order: Decimal = Field(ge=0, le=1)
    period: Decimal = Field(ge=0, le=1)


class DuplicatesConfig(_Strict):
    weights: DuplicateWeights
    threshold: Decimal = Field(gt=0, le=1)
    window_days: int = Field(gt=0)
    total_tolerance_pct: Decimal = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "DuplicatesConfig":
        w = self.weights
        if w.lines + w.total + w.order + w.period != Decimal("1"):
            raise ValueError("weights must add up to 1")
        return self


class LineCategories(_Strict):
    keywords: dict[Category, list[str]]


@dataclass(frozen=True)
class AppConfig:
    directory: Path
    countries: dict[str, CountryConfig]
    keywords: dict[str, list[str]]
    duplicates: DuplicatesConfig
    config_hash: str
    _tables: dict[str, DecisionTable] = field(default_factory=dict, compare=False)

    def table(self, name: str | None = None) -> DecisionTable:
        """The decision table of a customer; the default one when the customer names none."""
        name = name or DEFAULT_TABLE
        if name not in self._tables:
            self._tables[name] = _load(self.directory / name, DecisionTable)
        return self._tables[name]


def _load(path: Path, model: type[BaseModel]):
    shown = path.as_posix()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{shown}: file not found") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{shown}: not valid YAML: {exc}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(file)'}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"{shown}: {problems}") from exc


def load_config(directory: Path | None = None) -> AppConfig:
    directory = Path(directory or os.environ.get("CONFIG_DIR") or ROOT / "config")
    country_files = sorted((directory / "countries").glob("*.yaml"))
    if not country_files:
        raise ConfigError(f"{(directory / 'countries').as_posix()}: no country files found")
    countries: dict[str, CountryConfig] = {}
    for path in country_files:
        country = _load(path, CountryConfig)
        if country.country != path.stem:
            raise ConfigError(f"{path.as_posix()}: country: must equal the file name ({path.stem})")
        countries[country.country] = country
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*.yaml")):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes())
    config = AppConfig(
        directory=directory,
        countries=countries,
        keywords=_load(directory / "line_categories.yaml", LineCategories).keywords,
        duplicates=_load(directory / "duplicates.yaml", DuplicatesConfig),
        config_hash=digest.hexdigest()[:16],
    )
    config.table()  # the default table must load at start-up
    return config


# --- settings from the environment -------------------------------------------------------------


def load_env_file(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from .env into the environment. Variables that exist win.

    Docker Compose does this itself; this is for a start without Docker. Values are never logged.
    """
    path = path or ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    api_key: str
    database_path: Path
    seed_dir: Path
    cassette_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_key=os.environ.get("API_KEY") or DEMO_API_KEY,
            database_path=Path(os.environ.get("DATABASE_PATH") or ROOT / "data" / "app.db"),
            seed_dir=Path(os.environ.get("SEED_DIR") or ROOT / "data" / "seed"),
            cassette_dir=ROOT / "data" / "cassettes",
        )


# --- logging -----------------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """JSON lines on stdout. Callers log ids, codes and durations; never text, names or amounts."""
    logger = logging.getLogger("factuurcontrole")
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


log = logging.getLogger("factuurcontrole")
