"""Contrato REST de la central (modelos Pydantic).

Los modelos son la frontera de validación de la API: cualquier cliente que
envíe datos fuera de rango recibe un 422 antes de tocar la lógica de despacho.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Location(BaseModel):
    """Ubicación reportada por el cliente."""

    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    address: str = ""


class AlertIn(BaseModel):
    """Alerta que envía el cliente (espejo de ``AlertPayload``)."""

    client_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    label: str = ""
    frame: int = Field(default=0, ge=0)
    time_sec: float = Field(default=0.0, ge=0.0)
    video: str = ""
    verified_by: str = "yolo"
    reason: str = ""
    timestamp: str = ""
    location: Location = Field(default_factory=Location)
    snapshot_b64: str | None = None  # evidencia JPEG en base64 (opcional)


class AlertAck(BaseModel):
    """Respuesta al cliente: acuse + plan de ayuda despachado."""

    ticket: str
    status: str
    priority: str
    eta_min: int | None = None
    units: list[str] = Field(default_factory=list)
    message: str = ""
    received_at: str


class AlertRecord(BaseModel):
    """Alerta almacenada en la central, con su plan de despacho."""

    ticket: str
    status: str
    priority: str
    eta_min: int | None = None
    units: list[str] = Field(default_factory=list)
    received_at: str
    alert: AlertIn


class StatusUpdate(BaseModel):
    """Cambio de estado de una alerta (p. ej. ``atendida``)."""

    status: str = Field(min_length=1)


class ClientRegister(BaseModel):
    """Datos de alta de un cliente (detector) en la central."""

    client_id: str = Field(min_length=1)
    name: str = ""
    address: str = ""
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class HeartbeatIn(BaseModel):
    """Latido de vida de un cliente."""

    client_id: str = Field(min_length=1)


class ClientInfo(BaseModel):
    """Ficha de un cliente conectado que expone la central."""

    client_id: str
    name: str = ""
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    ip: str = ""
    connected_at: str = ""
    last_seen: str = ""
    online: bool = True
