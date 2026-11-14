"""SQLite: schema, seed, the reads a check needs, and save_check.

One adapter, so no interface: tests use a temporary file. Money and hours are stored as text
and read back as Decimal.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    contact_name TEXT,
    po_required INTEGER,              -- structured requirement set by a person; NULL when not given
    po_format TEXT,
    decision_table TEXT,              -- file in config/ with a stricter table; NULL = default
    requirements_text TEXT,           -- free-text invoice requirements; NULL when there are none
    requirements_status TEXT,         -- not_analysed | ai_extracted | verified; NULL without text
    requirements_reason TEXT,
    requirements_verified_by TEXT,
    requirements_verified_at TEXT,
    requirements_rejected TEXT NOT NULL DEFAULT '[]',
    requirements_model TEXT,
    requirements_mode TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    country TEXT NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS contracts (
    order_id TEXT PRIMARY KEY REFERENCES orders(id),
    text TEXT NOT NULL,
    status TEXT NOT NULL,             -- not_analysed | ai_extracted | verified
    reason TEXT,
    verified_by TEXT,
    verified_at TEXT,
    rejected TEXT NOT NULL DEFAULT '[]',  -- rule candidates that failed validation, with the reason
    model TEXT,
    mode TEXT,
    analysed_at TEXT
);
CREATE TABLE IF NOT EXISTS rate_cards (
    order_id TEXT NOT NULL REFERENCES orders(id),
    service_code TEXT NOT NULL,
    description TEXT NOT NULL,
    unit TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    category TEXT NOT NULL,
    PRIMARY KEY (order_id, service_code)
);
CREATE TABLE IF NOT EXISTS time_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    service_code TEXT NOT NULL,
    work_date TEXT NOT NULL,
    hours TEXT NOT NULL,
    inspector TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contract_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,              -- contract | requirements
    order_id TEXT REFERENCES orders(id),
    customer_id TEXT REFERENCES customers(id),
    rule_type TEXT NOT NULL,
    category TEXT,
    regex TEXT,
    quote TEXT NOT NULL,
    explanation_nl TEXT NOT NULL,
    model TEXT,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    invoice_number TEXT,
    order_id TEXT,
    customer_id TEXT,
    outcome TEXT NOT NULL,            -- a verdict, or geen_oordeel
    error TEXT,
    input TEXT NOT NULL,
    findings TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    config_hash TEXT,
    rules TEXT NOT NULL DEFAULT '{}',
    ai_mode TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS checks_by_customer ON checks (customer_id, received_at);
CREATE INDEX IF NOT EXISTS checks_by_invoice ON checks (invoice_number);
"""

NOT_ANALYSED = "not_analysed"
AI_EXTRACTED = "ai_extracted"
VERIFIED = "verified"
NO_VERDICT = "geen_oordeel"


def _rule(row: sqlite3.Row) -> dict:
    rule = dict(row)
    rule["id"] = f"cr_{row['id']}"
    return rule


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        """One connection per call; commits on success, rolls back on an error."""
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn

    # --- schema and seed ---------------------------------------------------------------------

    def init_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    def is_empty(self) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM orders LIMIT 1").fetchone() is None

    def load_seed(self, seed_dir: Path) -> None:
        """Insert the synthetic customers, orders, contracts, rate cards and time entries."""

        def read(name: str):
            return json.loads((seed_dir / name).read_text(encoding="utf-8"))

        with self._db() as db:
            for c in read("customers.json"):
                text = None
                if c.get("requirements_file"):
                    text = (seed_dir / c["requirements_file"]).read_text(encoding="utf-8")
                po = c.get("po") or {}
                db.execute(
                    "INSERT INTO customers (id, name, country, contact_name, po_required, po_format,"
                    " decision_table, requirements_text, requirements_status, requirements_reason)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        c["id"], c["name"], c["country"], c.get("contact_name"),
                        None if "required" not in po else int(po["required"]), po.get("format"),
                        c.get("decision_table"), text,
                        NOT_ANALYSED if text else None,
                        "nog niet geanalyseerd" if text else None,
                    ),
                )
            for o in read("orders.json"):
                db.execute(
                    "INSERT INTO orders (id, customer_id, country, description) VALUES (?, ?, ?, ?)",
                    (o["id"], o["customer_id"], o["country"], o.get("description")),
                )
                db.execute(
                    "INSERT INTO contracts (order_id, text, status, reason) VALUES (?, ?, ?, ?)",
                    (
                        o["id"], (seed_dir / o["contract_file"]).read_text(encoding="utf-8"),
                        NOT_ANALYSED, "nog niet geanalyseerd",
                    ),
                )
            for r in read("rate_cards.json"):
                db.execute(
                    "INSERT INTO rate_cards VALUES (?, ?, ?, ?, ?, ?)",
                    (r["order_id"], r["service_code"], r["description"], r["unit"],
                     str(r["unit_price"]), r["category"]),
                )
            for t in read("time_entries.json"):
                db.execute(
                    "INSERT INTO time_entries (order_id, service_code, work_date, hours, inspector)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (t["order_id"], t["service_code"], t["date"], str(t["hours"]), t["inspector"]),
                )

    # --- reads for a check -------------------------------------------------------------------

    def get_order(self, order_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            return dict(row) if row else None

    def get_customer(self, customer_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if row is None:
                return None
            customer = dict(row)
            customer["requirements_rejected"] = json.loads(customer["requirements_rejected"])
            return customer

    def rate_card(self, order_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM rate_cards WHERE order_id = ? ORDER BY service_code", (order_id,)
            ).fetchall()
        return [{**dict(r), "unit_price": Decimal(r["unit_price"])} for r in rows]

    def recorded_hours(self, order_id: str, date_from: str, date_to: str) -> dict[str, Decimal]:
        """Hours booked on an order per service code, for work dates inside the period."""
        with self._db() as db:
            rows = db.execute(
                "SELECT service_code, hours FROM time_entries"
                " WHERE order_id = ? AND work_date BETWEEN ? AND ?",
                (order_id, date_from, date_to),
            ).fetchall()
        totals: dict[str, Decimal] = {}
        for r in rows:
            totals[r["service_code"]] = totals.get(r["service_code"], Decimal("0")) + Decimal(r["hours"])
        return totals

    def get_contract(self, order_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM contracts WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                return None
            contract = dict(row)
            contract["rejected"] = json.loads(contract["rejected"])
            return contract

    def contract_rules(self, order_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM contract_rules WHERE scope = 'contract' AND order_id = ? ORDER BY id",
                (order_id,),
            ).fetchall()
        return [_rule(r) for r in rows]

    def requirement_rules(self, customer_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM contract_rules WHERE scope = 'requirements' AND customer_id = ? ORDER BY id",
                (customer_id,),
            ).fetchall()
        return [_rule(r) for r in rows]

    def known_names(self) -> list[str]:
        """Names of customers, contacts and inspectors: what masking must hide."""
        with self._db() as db:
            names = [r[0] for r in db.execute("SELECT name FROM customers")]
            names += [r[0] for r in db.execute("SELECT contact_name FROM customers WHERE contact_name IS NOT NULL")]
            names += [r[0] for r in db.execute("SELECT DISTINCT inspector FROM time_entries")]
        return names

    def decision_tables(self) -> list[str]:
        with self._db() as db:
            rows = db.execute(
                "SELECT DISTINCT decision_table FROM customers WHERE decision_table IS NOT NULL"
            ).fetchall()
        return [r[0] for r in rows]

    # --- contract intake ---------------------------------------------------------------------

    def pending_contracts(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT order_id, text FROM contracts WHERE status = ? ORDER BY order_id", (NOT_ANALYSED,)
            ).fetchall()
        return [dict(r) for r in rows]

    def pending_requirements(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id AS customer_id, requirements_text AS text FROM customers"
                " WHERE requirements_status = ? ORDER BY id",
                (NOT_ANALYSED,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_contract_text(self, order_id: str, text: str) -> None:
        """Replace the contract text. The old rules and the old confirmation no longer hold."""
        with self._db() as db:
            db.execute("DELETE FROM contract_rules WHERE scope = 'contract' AND order_id = ?", (order_id,))
            db.execute(
                "INSERT INTO contracts (order_id, text, status, reason) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (order_id) DO UPDATE SET text = excluded.text, status = excluded.status,"
                " reason = excluded.reason, verified_by = NULL, verified_at = NULL, rejected = '[]',"
                " model = NULL, mode = NULL, analysed_at = NULL",
                (order_id, text, NOT_ANALYSED, "nog niet geanalyseerd"),
            )

    def save_contract_analysis(
        self, order_id: str, *, status: str, reason: str | None, rules: list[dict],
        rejected: list[dict], model: str | None, mode: str, at: str,
    ) -> None:
        with self._db() as db:
            db.execute("DELETE FROM contract_rules WHERE scope = 'contract' AND order_id = ?", (order_id,))
            db.execute(
                "UPDATE contracts SET status = ?, reason = ?, rejected = ?, model = ?, mode = ?,"
                " analysed_at = ?, verified_by = NULL, verified_at = NULL WHERE order_id = ?",
                (status, reason, json.dumps(rejected, ensure_ascii=False), model, mode, at, order_id),
            )
            self._insert_rules(db, "contract", order_id, None, rules, model, mode, at)

    def save_requirements_analysis(
        self, customer_id: str, *, status: str, reason: str | None, rules: list[dict],
        rejected: list[dict], model: str | None, mode: str, at: str,
    ) -> None:
        with self._db() as db:
            db.execute(
                "DELETE FROM contract_rules WHERE scope = 'requirements' AND customer_id = ?", (customer_id,)
            )
            db.execute(
                "UPDATE customers SET requirements_status = ?, requirements_reason = ?,"
                " requirements_rejected = ?, requirements_model = ?, requirements_mode = ?,"
                " requirements_verified_by = NULL, requirements_verified_at = NULL WHERE id = ?",
                (status, reason, json.dumps(rejected, ensure_ascii=False), model, mode, customer_id),
            )
            self._insert_rules(db, "requirements", None, customer_id, rules, model, mode, at)

    @staticmethod
    def _insert_rules(db, scope, order_id, customer_id, rules, model, mode, at) -> None:
        for rule in rules:
            db.execute(
                "INSERT INTO contract_rules (scope, order_id, customer_id, rule_type, category, regex,"
                " quote, explanation_nl, model, mode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (scope, order_id, customer_id, rule["type"], rule.get("category"), rule.get("regex"),
                 rule["quote"], rule["explanation_nl"], model, mode, at),
            )

    def verify_contract(self, order_id: str, by: str, at: str) -> bool:
        """A person confirms the rule set. Only a set the model produced can be confirmed."""
        with self._db() as db:
            cursor = db.execute(
                "UPDATE contracts SET status = ?, verified_by = ?, verified_at = ?"
                " WHERE order_id = ? AND status IN (?, ?)",
                (VERIFIED, by, at, order_id, AI_EXTRACTED, VERIFIED),
            )
            return cursor.rowcount == 1

    def verify_requirements(self, customer_id: str, by: str, at: str) -> bool:
        with self._db() as db:
            cursor = db.execute(
                "UPDATE customers SET requirements_status = ?, requirements_verified_by = ?,"
                " requirements_verified_at = ? WHERE id = ? AND requirements_status IN (?, ?)",
                (VERIFIED, by, at, customer_id, AI_EXTRACTED, VERIFIED),
            )
            return cursor.rowcount == 1

    # --- checks ------------------------------------------------------------------------------

    def save_check(self, record: dict) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO checks (id, received_at, invoice_number, order_id, customer_id, outcome, error,"
                " input, findings, summary, config_hash, rules, ai_mode, duration_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["id"], record["received_at"], record.get("invoice_number"),
                    record.get("order_id"), record.get("customer_id"), record["outcome"],
                    json.dumps(record["error"], ensure_ascii=False) if record.get("error") else None,
                    json.dumps(record["input"], ensure_ascii=False),
                    json.dumps(record.get("findings", []), ensure_ascii=False),
                    record.get("summary"), record.get("config_hash"),
                    json.dumps(record.get("rules", {}), ensure_ascii=False),
                    record.get("ai_mode"), record.get("duration_ms"),
                ),
            )

    @staticmethod
    def _check(row: sqlite3.Row) -> dict:
        check = dict(row)
        for key in ("error", "input", "findings", "rules"):
            check[key] = json.loads(check[key]) if check[key] else None
        return check

    def get_check(self, check_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
        return self._check(row) if row else None

    def find_checks(self, invoice_number: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM checks WHERE invoice_number = ? ORDER BY received_at, rowid", (invoice_number,)
            ).fetchall()
        return [self._check(r) for r in rows]

    def earlier_checks(self, customer_id: str) -> list[dict]:
        """Stored checks of a customer that got a verdict: the candidates for the duplicate check."""
        with self._db() as db:
            rows = db.execute(
                "SELECT id, input FROM checks WHERE customer_id = ? AND outcome != ? ORDER BY received_at, rowid",
                (customer_id, NO_VERDICT),
            ).fetchall()
        return [{"check_id": r["id"], "invoice": json.loads(r["input"])} for r in rows]
