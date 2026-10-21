"""Almacén de alertas de la central.

Decisión de diseño: la central es un **receptor en vivo**, no una base de
datos. Las alertas viven solo en memoria mientras el proceso está en marcha:

- cada arranque empieza con el historial en blanco;
- el dashboard muestra los datos sin latencia de disco;
- cambiar a persistencia real solo requiere reemplazar esta clase por otra
  que implemente los mismos métodos, sin tocar los endpoints.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from incidentes.server.models import AlertRecord

log = logging.getLogger(__name__)


class AlertStore:
    """Repositorio en memoria, seguro para acceso concurrente."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_ticket: dict[str, dict] = {}
        self._order: list[str] = []
        self._daily_count: dict[str, int] = {}

    def _next_ticket(self) -> str:
        """Genera un ticket legible y único por día (``ALT-YYYYMMDD-0001``)."""
        day = datetime.now(UTC).strftime("%Y%m%d")
        self._daily_count[day] = self._daily_count.get(day, 0) + 1
        return f"ALT-{day}-{self._daily_count[day]:04d}"

    def add(self, record: AlertRecord) -> AlertRecord:
        """Guarda la alerta, le asigna ticket y devuelve el registro final."""
        with self._lock:
            ticket = self._next_ticket()
            record = record.model_copy(update={"ticket": ticket})
            self._by_ticket[ticket] = record.model_dump()
            self._order.append(ticket)
            return record

    def list(self, limit: int = 100) -> list[dict]:
        """Devuelve las alertas más recientes primero."""
        with self._lock:
            tickets = list(reversed(self._order))[:limit]
            return [self._by_ticket[ticket] for ticket in tickets]

    def get(self, ticket: str) -> dict | None:
        with self._lock:
            return self._by_ticket.get(ticket)

    def update_status(self, ticket: str, status: str) -> dict | None:
        """Actualiza el estado de una alerta; ``None`` si no existe."""
        with self._lock:
            record = self._by_ticket.get(ticket)
            if record is None:
                return None
            record["status"] = status
            return record

    def count(self) -> int:
        with self._lock:
            return len(self._order)
