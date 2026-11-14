"""Command line: init, seed, ingest-contract, verify-contract, verify-requirements.

Contract intake happens here, once per contract, not per invoice (ADR 0001). `verify-contract`
and `verify-requirements` are the human control point: a person confirms that the rules the
model wrote are correct and complete.

    python -m app.cli init
    python -m app.cli seed
    python -m app.cli ingest-contract ORD-DE-0077 contract.txt
    python -m app.cli verify-contract ORD-DE-0077 --by "J. Jansen"
    python -m app.cli verify-requirements CUST-ROTH --by "J. Jansen"
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app.ai.client import ModelClient, client_from_env
from app.ai.rules import ExtractionResult, extract_rules
from app.config import Settings, configure_logging, load_config, load_env_file, log
from app.store import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_args(result: ExtractionResult) -> dict:
    return {
        "status": result.status, "reason": result.reason,
        "rules": [r.as_dict() for r in result.rules], "rejected": list(result.rejected),
        "model": result.model, "mode": result.mode, "at": _now(),
    }


def analyse_pending(store: Store, client: ModelClient) -> None:
    """Extract rules for every contract and requirement text that is still not_analysed."""
    names = store.known_names()
    contracts, requirements = store.pending_contracts(), store.pending_requirements()
    jobs = [("contract", c["order_id"], c["text"]) for c in contracts]
    jobs += [("requirements", r["customer_id"], r["text"]) for r in requirements]
    if not jobs:
        return
    # The texts are independent, so the model calls run side by side; the writes do not.
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        results = list(pool.map(lambda job: extract_rules(job[2], job[0], client, names), jobs))
    for (kind, key, _), result in zip(jobs, results):
        if kind == "contract":
            store.save_contract_analysis(key, **_as_args(result))
        else:
            store.save_requirements_analysis(key, **_as_args(result))
        log.info("intake", extra={"fields": {
            "kind": kind, "id": key, "status": result.status, "rules": len(result.rules),
            "rejected": len(result.rejected), "ai_mode": result.mode,
        }})


def seed(store: Store, seed_dir: Path, client: ModelClient) -> None:
    """Load the synthetic data when the database is empty, then run intake on what is pending."""
    store.init_schema()
    if store.is_empty():
        store.load_seed(seed_dir)
        log.info("seed_loaded", extra={"fields": {"seed_dir": seed_dir.as_posix()}})
    analyse_pending(store, client)


def ingest_contract(store: Store, order_id: str, text: str, client: ModelClient) -> ExtractionResult:
    """Store a new contract text for an order and extract its rules. A person must confirm again."""
    if store.get_order(order_id) is None:
        raise SystemExit(f"Opdracht {order_id} bestaat niet.")
    store.set_contract_text(order_id, text)
    result = extract_rules(text, "contract", client, store.known_names())
    store.save_contract_analysis(order_id, **_as_args(result))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the schema, load the seed when empty, run pending intake")
    sub.add_parser("seed", help="the same as init; run it again after adding a token")
    p = sub.add_parser("ingest-contract", help="store a contract text and extract its rules")
    p.add_argument("order_id")
    p.add_argument("file", type=Path)
    p = sub.add_parser("verify-contract", help="a person confirms the rule set of a contract")
    p.add_argument("order_id")
    p.add_argument("--by", required=True, help="who confirms")
    p = sub.add_parser("verify-requirements", help="a person confirms the rules from a customer's requirements")
    p.add_argument("customer_id")
    p.add_argument("--by", required=True, help="who confirms")
    args = parser.parse_args(argv)

    load_env_file()
    configure_logging()
    settings = Settings.from_env()
    load_config()  # a bad config file stops here, before anything is written
    store = Store(settings.database_path)
    store.init_schema()

    if args.command in ("init", "seed"):
        seed(store, settings.seed_dir, client_from_env())
    elif args.command == "ingest-contract":
        result = ingest_contract(store, args.order_id, args.file.read_text(encoding="utf-8"), client_from_env())
        print(f"{args.order_id}: {result.status}, {len(result.rules)} regels, {len(result.rejected)} geweigerd"
              + (f" ({result.reason})" if result.reason else ""))
        print(f"Bekijk ze met GET /v1/orders/{args.order_id}/rules en bevestig met verify-contract.")
    elif args.command == "verify-contract":
        if not store.verify_contract(args.order_id, args.by, _now()):
            print(f"{args.order_id}: niets te bevestigen (onbekende opdracht, of het contract is niet geanalyseerd).")
            return 1
        print(f"{args.order_id}: contractregels bevestigd door {args.by}.")
    elif args.command == "verify-requirements":
        if not store.verify_requirements(args.customer_id, args.by, _now()):
            print(f"{args.customer_id}: niets te bevestigen (onbekende klant, of de eisen zijn niet geanalyseerd).")
            return 1
        print(f"{args.customer_id}: factuureisen bevestigd door {args.by}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
