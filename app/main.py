"""The API: routes, the key check and the mapping of errors to 401 / 404 / 422. Thin on purpose.

The work is in app/checker.py. This file never decides anything about an invoice.
"""

import hmac
import json
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.ai.client import client_from_env
from app.checker import Deps, DomainError, check_invoice, record_invalid_input
from app.config import DEMO_API_KEY, ROOT, Settings, configure_logging, load_config, load_env_file, log
from app.schemas import CheckOut, ErrorOut, InvoiceIn
from app.store import Store

DESCRIPTION = """
Controleert een conceptfactuur voordat die naar de klant gaat. Het antwoord is een oordeel
(`goedgekeurd`, `controle_nodig` of `afgewezen`) met per bevinding een reden en een bron.
De service verstuurt of wijzigt nooit een factuur: een mens beslist.

**Proberen:** klik op *Authorize* en vul de sleutel in. Zonder `.env` is dat `demo-key`.
Open daarna `POST /v1/checks`, kies een voorbeeld en klik op *Execute*.
Alle gegevens zijn synthetisch.
"""

_FIELD_PROBLEMS = {
    "missing": "verplicht veld ontbreekt",
    "json_invalid": "de body is geen geldige JSON",
    "too_short": "er moet minstens een waarde staan",
    "string_too_short": "mag niet leeg zijn",
    "string_too_long": "te lang",
    "string_pattern_mismatch": "heeft niet het juiste formaat",
    "decimal_parsing": "is geen getal",
    "decimal_type": "is geen getal",
    "decimal_max_places": "heeft te veel decimalen",
    "greater_than": "moet groter dan nul zijn",
    "date_from_datetime_parsing": "is geen datum (JJJJ-MM-DD)",
    "date_parsing": "is geen datum (JJJJ-MM-DD)",
    "date_type": "is geen datum (JJJJ-MM-DD)",
    "literal_error": "is geen toegestane waarde",
}

_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="Zonder .env: demo-key")


def _examples() -> dict:
    """The seed invoices, one valid one per order, shown in /docs."""
    examples = {}
    for path in sorted((ROOT / "data" / "seed" / "examples").glob("*.json")):
        examples[path.stem] = {"summary": path.stem, "value": json.loads(path.read_text(encoding="utf-8"))}
    return examples


def create_app(deps: Deps | None = None, api_key: str | None = None) -> FastAPI:
    """Build the app. Tests and the eval pass their own deps; the service reads the environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        if deps is None:
            load_env_file()
            settings = Settings.from_env()
            store = Store(settings.database_path)
            store.init_schema()
            app.state.deps = Deps(store=store, config=load_config(), client=client_from_env())
            app.state.api_key = settings.api_key
        for table in app.state.deps.store.decision_tables():
            app.state.deps.config.table(table)  # a bad customer table stops the start
        if app.state.api_key == DEMO_API_KEY:
            log.warning("demo_key_in_use", extra={"fields": {"hint": "set API_KEY in .env"}})
        log.info("started", extra={"fields": {
            "ai_mode": app.state.deps.client.mode, "config_hash": app.state.deps.config.config_hash,
        }})
        yield

    app = FastAPI(title="factuurcontrole", version="0.1.0", description=DESCRIPTION, lifespan=lifespan)
    if deps is not None:
        app.state.deps = deps
        app.state.api_key = api_key or DEMO_API_KEY

    def key_ok(request: Request) -> bool:
        given = request.headers.get("X-API-Key") or ""
        return hmac.compare_digest(given.encode(), app.state.api_key.encode())

    def require_key(request: Request, _: Annotated[str | None, Depends(_key_header)] = None) -> None:
        if not key_ok(request):
            raise HTTPException(status_code=401, detail="Ontbrekende of onjuiste X-API-Key.")

    def get_deps() -> Deps:
        return app.state.deps

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        names = {401: "unauthorized", 404: "not_found"}
        body = ErrorOut(error=names.get(exc.status_code, "error"), detail=str(exc.detail))
        return JSONResponse(body.model_dump(exclude_none=True), status_code=exc.status_code)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        body = ErrorOut(error=exc.error, detail=exc.detail, check_id=exc.check_id)
        return JSONResponse(body.model_dump(exclude_none=True), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Invalid JSON is found before the key dependency runs, so the key is checked here too.
        if not key_ok(request):
            return await http_error(request, HTTPException(401, "Ontbrekende of onjuiste X-API-Key."))
        fields = [
            {
                "field": "(body)" if e["type"] == "json_invalid"
                else ".".join(str(p) for p in e["loc"] if p != "body") or "(body)",
                "problem": _FIELD_PROBLEMS.get(e["type"], "ongeldige waarde"),
            }
            for e in exc.errors()
        ]
        body = ErrorOut(
            error="invalid_invoice",
            detail="De factuur is onvolledig of ongeldig. Geen oordeel gegeven.",
            fields=fields,
        )
        if request.method == "POST" and request.url.path == "/v1/checks":
            raw = exc.body
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            body.check_id = record_invalid_input(raw, {"error": body.error, "fields": fields}, app.state.deps)
        else:
            body.error, body.detail = "invalid_request", "Het verzoek is onvolledig of ongeldig."
        return JSONResponse(body.model_dump(exclude_none=True), status_code=422)

    no_verdict = {
        401: {"model": ErrorOut, "description": "Sleutel ontbreekt of is onjuist. Niets opgeslagen."},
        404: {"model": ErrorOut, "description": "Onbekende opdracht. Geen oordeel."},
        422: {"model": ErrorOut, "description": "Onvolledige of ongeldige factuur. Geen oordeel."},
    }

    @app.get("/health", tags=["service"])
    def health() -> dict:
        """Is the service up? No key needed."""
        return {"status": "ok", "ai_mode": app.state.deps.client.mode}

    @app.post(
        "/v1/checks", response_model=CheckOut, response_model_exclude_none=True,
        responses=no_verdict, tags=["checks"], dependencies=[Depends(require_key)],
    )
    def create_check(
        invoice: Annotated[InvoiceIn, Body(openapi_examples=_examples())],
        deps: Annotated[Deps, Depends(get_deps)],
    ) -> CheckOut:
        """Controleer een conceptfactuur. Elke aanroep wordt opgeslagen, ook een fout."""
        return check_invoice(invoice, deps)

    @app.get("/v1/checks/{check_id}", tags=["checks"], dependencies=[Depends(require_key)],
             responses={401: no_verdict[401], 404: {"model": ErrorOut}})
    def get_check(check_id: str, deps: Annotated[Deps, Depends(get_deps)]) -> dict:
        """De opgeslagen controle: invoer, bevindingen met bron, oordeel, config- en regelversies."""
        check = deps.store.get_check(check_id)
        if check is None:
            raise HTTPException(404, f"Controle {check_id} bestaat niet.")
        return check

    @app.get("/v1/checks", tags=["checks"], dependencies=[Depends(require_key)],
             responses={401: no_verdict[401]})
    def find_checks(
        invoice_number: Annotated[str, Query(min_length=1, max_length=64)],
        deps: Annotated[Deps, Depends(get_deps)],
    ) -> list[dict]:
        """Alle opgeslagen controles van een factuurnummer, oudste eerst."""
        return deps.store.find_checks(invoice_number)

    @app.get("/v1/orders/{order_id}/rules", tags=["rules"], dependencies=[Depends(require_key)],
             responses={401: no_verdict[401], 404: {"model": ErrorOut}})
    def order_rules(order_id: str, deps: Annotated[Deps, Depends(get_deps)]) -> dict:
        """De regels van een opdracht met citaat, uitleg en status, zodat een mens ze kan nalopen."""
        store = deps.store
        order = store.get_order(order_id)
        if order is None:
            raise HTTPException(404, f"Opdracht {order_id} bestaat niet.")
        contract = store.get_contract(order_id) or {}
        customer = store.get_customer(order["customer_id"])
        return {
            "order_id": order_id,
            "customer_id": customer["id"],
            "contract": {
                "status": contract.get("status"), "reason": contract.get("reason"),
                "verified_by": contract.get("verified_by"), "verified_at": contract.get("verified_at"),
                "model": contract.get("model"), "ai_mode": contract.get("mode"),
                "rules": store.contract_rules(order_id), "rejected": contract.get("rejected", []),
            },
            "customer_requirements": {
                "configured": {"po_required": customer["po_required"], "po_format": customer["po_format"]},
                "status": customer["requirements_status"], "reason": customer["requirements_reason"],
                "verified_by": customer["requirements_verified_by"],
                "verified_at": customer["requirements_verified_at"],
                "model": customer["requirements_model"], "ai_mode": customer["requirements_mode"],
                "rules": store.requirement_rules(customer["id"]),
                "rejected": customer["requirements_rejected"],
            },
        }

    return app


app = create_app()
