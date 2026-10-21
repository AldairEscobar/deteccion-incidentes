"""Puertos del dominio (interfaces que implementa la infraestructura).

La regla de dependencia es unidireccional: ``application`` depende de estos
puertos y ``infrastructure`` los implementa. Así el caso de uso se puede
probar sin YOLO, sin cámara y sin red.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from incidentes.domain.entities import (
    AlertAck,
    AlertPayload,
    ProcessingProgress,
    Suspicion,
    Verdict,
    VideoInfo,
)


class IncidentDetector(ABC):
    """Analiza un frame y devuelve posibles incidentes (etapa 1)."""

    @abstractmethod
    def analyze(self, frame: Any) -> Any:
        """Devuelve un ``FrameAnalysis`` con el frame anotado y las sospechas."""
        raise NotImplementedError


class ViolenceVerifier(ABC):
    """Confirma o descarta una sospecha con un modelo de visión (etapa 2).

    El contrato exige **fail-closed**: ante error, timeout o respuesta
    inválida debe devolver ``Verdict(violent=False)``. La alarma solo suena
    con una confirmación positiva.
    """

    @property
    def name(self) -> str:
        """Identificador legible del verificador (ej. ``ollama/qwen2.5vl``)."""
        return "verifier"

    @abstractmethod
    def ping(self) -> bool:
        """Indica si el verificador está disponible. No debe lanzar."""
        raise NotImplementedError

    @abstractmethod
    def verify(self, jpeg_bytes: bytes, suspicion: Suspicion) -> Verdict:
        """Verifica una sospecha. No debe lanzar excepciones."""
        raise NotImplementedError


class VideoReader(ABC):
    """Puerto de lectura de video."""

    @abstractmethod
    def open(self, path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def read(self) -> tuple[bool, Any]:
        """Devuelve ``(ok, frame)``; ``ok=False`` marca el fin del video."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class VideoWriter(ABC):
    """Puerto de escritura del video anotado."""

    @abstractmethod
    def open(self, path: str, fps: int, width: int, height: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def write(self, frame: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class ProgressListener(ABC):
    """Puerto de notificación hacia la interfaz.

    La implementación de presentación es responsable de saltar al hilo de la
    GUI. El caso de uso puede invocar estos métodos desde un hilo worker.
    """

    @abstractmethod
    def on_progress(self, progress: ProcessingProgress, preview_frame: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_completed(self, output_path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_error(self, message: str) -> None:
        raise NotImplementedError

    # Hooks opcionales: se dejan con implementación vacía para que un listener
    # que solo necesite progreso no tenga que implementarlos todos.
    def on_incident_alert(self, incident: Any, timestamp_sec: float) -> None:  # noqa: B027
        """Se invoca por cada incidente confirmado (respetando el cooldown)."""

    def on_summary(self, summary: dict[str, Any]) -> None:  # noqa: B027
        """Resumen final con conteos y ruta del reporte JSON."""

    def on_alert_dispatched(self, ack: AlertAck, incident: Any) -> None:  # noqa: B027
        """La central respondió a una alerta (ticket, ETA, mensaje)."""


class AlertDispatcher(ABC):
    """Envía una alerta confirmada a la central.

    Contrato **fail-open**: nunca lanza. Si la red falla, devuelve
    ``AlertAck(delivered=False)`` y la detección local continúa.
    """

    @abstractmethod
    def dispatch(self, alert: AlertPayload) -> AlertAck:
        raise NotImplementedError


__all__ = [
    "AlertDispatcher",
    "IncidentDetector",
    "ProgressListener",
    "VideoInfo",
    "VideoReader",
    "VideoWriter",
    "ViolenceVerifier",
]
