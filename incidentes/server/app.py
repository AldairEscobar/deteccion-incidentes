"""API REST de la central de alertas.

Ejecutar en solitario (sin dashboard)::

    python -m uvicorn incidentes.server.app:app --host 0.0.0.0 --port 8000

Endpoints principales::

    GET    /health                       estado del servicio
    POST   /api/v1/alertas               recibe una alerta y despacha la ayuda
    GET    /api/v1/alertas               historial (más recientes primero)
    GET    /api/v1/alertas/{ticket}      detalle de una alerta
    GET    /api/v1/alertas/{ticket}/json     JSON del incidente (descarga)
    GET    /api/v1/alertas/{ticket}/captura  captura JPEG de evidencia
    PATCH  /api/v1/alertas/{ticket}      actualiza el estado (p. ej. "atendida")
    POST   /api/v1/clientes/registro     alta de un cliente
    POST   /api/v1/clientes/heartbeat    latido de vida
    GET    /api/v1/clientes              clientes conectados
    DELETE /api/v1/clientes/{client_id}  baja de un cliente

Autenticación: si ``CENTRAL_API_KEY`` está definida, exige la cabecera
``X-API-Key``. Aviso a unidades: si ``DISPATCH_WEBHOOK_URL`` está definida,
hace POST a ese webhook.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse

from incidentes import __version__
from incidentes.server.dispatch import DispatchPlan, notify_units, plan_dispatch
from incidentes.server.models import (
    AlertAck,
    AlertIn,
    AlertRecord,
    ClientInfo,
    ClientRegister,
    HeartbeatIn,
    StatusUpdate,
)
from incidentes.server.registry import ClientRegistry
from incidentes.server.store import DEFAULT_DB_PATH, AlertStore

log = logging.getLogger(__name__)

API_KEY = os.getenv("CENTRAL_API_KEY", "")
CLIENT_ALIVE_SEC = float(os.getenv("CLIENT_ALIVE_SEC", "30"))
DB_PATH = os.getenv("CENTRAL_DB_PATH", DEFAULT_DB_PATH)

app = FastAPI(title="Central de Alertas", version=__version__)

# Persistencia compartida con el dashboard de PyQt5: alertas + capturas.
store = AlertStore(DB_PATH)
registry = ClientRegistry(alive_seconds=CLIENT_ALIVE_SEC)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Valida ``X-API-Key`` solo si la central tiene una clave configurada."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida o ausente",
        )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@app.get("/", include_in_schema=False)
def index() -> dict:
    """Descubrimiento mínimo del servicio."""
    return {
        "service": "central-alertas",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict:
    """Estado del servicio, número de alertas y clientes en línea."""
    return {
        "status": "ok",
        "service": "central-alertas",
        "alertas": store.count(),
        "clientes_en_linea": registry.count(),
    }


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
@app.post(
    "/api/v1/alertas",
    response_model=AlertAck,
    status_code=status.HTTP_201_CREATED,
)
def receive_alert(alert: AlertIn, _: None = Depends(require_api_key)) -> AlertAck:
    """Registra la alerta, calcula el despacho y avisa a las unidades."""
    plan: DispatchPlan = plan_dispatch(alert)
    record = AlertRecord(
        ticket="",
        status="registrada",
        priority=plan["priority"],
        eta_min=plan["eta_min"],
        units=plan["units"],
        received_at=datetime.now(UTC).isoformat(timespec="seconds"),
        alert=alert,
    )
    saved = store.add(record)  # asigna el ticket
    if notify_units(saved.ticket, alert, plan):
        saved = store.update_status(saved.ticket, "despachada") or saved
    log.warning("ALERTA %s (%s): %s", saved.ticket, saved.status, plan["message"])
    return AlertAck(
        ticket=saved.ticket,
        status=saved.status,
        priority=saved.priority,
        eta_min=saved.eta_min,
        units=saved.units,
        message=f"{plan['notify']} Ticket {saved.ticket}.",
        received_at=saved.received_at,
    )


@app.get("/api/v1/alertas", response_model=list[AlertRecord])
def list_alerts(
    limit: int = Query(default=100, ge=1, le=1000),
    _: None = Depends(require_api_key),
) -> list[dict]:
    """Historial de alertas, de la más reciente a la más antigua."""
    return store.list(limit)


@app.get("/api/v1/alertas/{ticket}", response_model=AlertRecord)
def get_alert(ticket: str, _: None = Depends(require_api_key)) -> dict:
    record = store.get(ticket)
    if record is None:
        raise HTTPException(status_code=404, detail=f"ticket {ticket} no existe")
    return record


@app.get("/api/v1/alertas/{ticket}/json")
def get_alert_json(ticket: str, _: None = Depends(require_api_key)) -> JSONResponse:
    """Descarga el JSON persistido del incidente, con metadatos de evidencia."""
    record = store.get(ticket)
    if record is None:
        raise HTTPException(status_code=404, detail=f"ticket {ticket} no existe")
    return JSONResponse(
        content=record,
        headers={"Content-Disposition": f'attachment; filename="{ticket}.json"'},
    )


@app.get("/api/v1/alertas/{ticket}/captura")
def get_alert_snapshot(ticket: str, _: None = Depends(require_api_key)) -> Response:
    """Devuelve la captura JPEG que el cliente adjuntó a la alerta."""
    evidence = store.get_snapshot(ticket)
    if evidence is None:
        raise HTTPException(
            status_code=404, detail=f"ticket {ticket} no tiene captura"
        )
    data, mime = evidence
    return Response(
        content=data,
        media_type=mime or "image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.patch("/api/v1/alertas/{ticket}", response_model=AlertRecord)
def update_alert(
    ticket: str,
    update: StatusUpdate,
    _: None = Depends(require_api_key),
) -> dict:
    record = store.update_status(ticket, update.status)
    if record is None:
        raise HTTPException(status_code=404, detail=f"ticket {ticket} no existe")
    log.info("estado de %s -> %s", ticket, update.status)
    return record


# ---------------------------------------------------------------------------
# Clientes (multi-cliente): alta + heartbeat + listado
# ---------------------------------------------------------------------------
@app.post("/api/v1/clientes/registro", response_model=ClientInfo)
def register_client(
    info: ClientRegister,
    request: Request,
    _: None = Depends(require_api_key),
) -> dict:
    record = registry.register(
        info.client_id,
        name=info.name,
        address=info.address,
        lat=info.lat,
        lon=info.lon,
        ip=_client_ip(request),
        source_state=info.source_state,
    )
    if record.pop("nuevo", False):
        log.warning(
            "CLIENTE conectado: %s (%s) desde %s",
            record["client_id"],
            record["address"] or "sin dirección",
            record["ip"],
        )
    else:
        log.info("cliente %s re-registrado", info.client_id)
    return record


@app.post("/api/v1/clientes/heartbeat", response_model=ClientInfo)
def client_heartbeat(
    info: HeartbeatIn,
    request: Request,
    _: None = Depends(require_api_key),
) -> dict:
    record = registry.heartbeat(
        info.client_id, ip=_client_ip(request), source_state=info.source_state
    )
    if record is None:
        # El cliente debe volver a registrarse (la central se reinició).
        raise HTTPException(
            status_code=404,
            detail=f"cliente {info.client_id} no registrado",
        )
    return record


@app.get("/api/v1/clientes", response_model=list[ClientInfo])
def list_clients(
    only_alive: bool = Query(default=True),
    _: None = Depends(require_api_key),
) -> list[dict]:
    return registry.list(only_alive=only_alive)


@app.delete("/api/v1/clientes/{client_id}")
def remove_client(client_id: str, _: None = Depends(require_api_key)) -> dict:
    if not registry.remove(client_id):
        raise HTTPException(status_code=404, detail=f"cliente {client_id} no existe")
    log.info("cliente %s se desconectó", client_id)
    return {"removed": client_id}
