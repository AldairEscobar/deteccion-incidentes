"""Latido del cliente hacia la central.

Mantiene registrado al detector mientras la aplicación está abierta: se
registra una vez y luego envía heartbeats periódicos en un hilo daemon. Así la
central puede mostrar "clientes conectados" y detectar clientes caídos.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

log = logging.getLogger(__name__)


class ClientChannel(Protocol):
    """Operaciones del canal necesarias para mantener la presencia."""

    def register(self) -> bool: ...

    def heartbeat(self) -> bool: ...

    def unregister(self) -> bool: ...


class ClientBeacon:
    """Registra el cliente y mantiene su latido en segundo plano."""

    def __init__(self, channel: ClientChannel, interval_sec: float = 10.0) -> None:
        self._channel = channel
        self._interval = interval_sec
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Registra al cliente y arranca el hilo de latidos (idempotente)."""
        if self._thread is not None:
            return
        self._channel.register()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="client-beacon"
        )
        self._thread.start()

    def _loop(self) -> None:
        # wait() devuelve True cuando se pide parar; False al cumplirse el intervalo.
        while not self._stop_event.wait(self._interval):
            try:
                self._channel.heartbeat()
            except Exception:
                log.exception("fallo el latido hacia la central")

    def stop(self) -> None:
        """Detiene los latidos y da de baja al cliente. Nunca lanza."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            self._channel.unregister()
        except Exception:
            log.debug("no se pudo dar de baja al cliente", exc_info=True)
