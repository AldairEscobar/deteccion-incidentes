"""Controlador del cliente: une la vista Tkinter con el caso de uso.

También implementa ``ProgressListener`` para traducir los eventos del caso de
uso (progreso, incidente, acuse de la central) en actualizaciones de la vista
y en la alarma sonora.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from tkinter import filedialog
from typing import Any

from incidentes.application.detect_incidents import DetectIncidentsUseCase
from incidentes.domain.entities import AlertAck, ProcessingProgress
from incidentes.domain.ports import ProgressListener
from incidentes.infrastructure.audio.alarm import sound_alarm
from incidentes.presentation.detector_view import DetectorView

log = logging.getLogger(__name__)


class TkProgressListener(ProgressListener):
    """Traduce eventos del caso de uso a la vista y a la alarma sonora.

    La alarma solo se activa una vez por corrida (modo binario); las
    confirmaciones posteriores se registran en el log.
    """

    def __init__(
        self,
        view: DetectorView,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self._view = view
        self._on_done = on_done
        self._alarmed = False

    def on_progress(self, progress: ProcessingProgress, preview_frame: Any) -> None:
        try:
            if progress.current_frame == 1:
                self._view.set_progress_max(progress.total_frames)
            self._view.show_progress(progress.current_frame, progress.total_frames)
            self._view.show_frame(preview_frame)
        except Exception:
            log.exception(
                "fallo al actualizar el progreso del frame %d",
                progress.current_frame,
            )

    def on_incident_alert(self, incident: Any, timestamp_sec: float) -> None:
        try:
            if not self._alarmed:
                self._alarmed = True
                log.warning(
                    "VIOLENCIA DETECTADA @ %.1fs :: %s",
                    timestamp_sec,
                    incident.label,
                )
                self._view.set_alarm(f"{incident.type} @ {timestamp_sec:.1f}s")
                sound_alarm(times=3)
            else:
                log.info(
                    "violencia persistente @ %.1fs :: %s",
                    timestamp_sec,
                    incident.label,
                )
        except Exception:
            log.exception("fallo al procesar la alerta @ %.1fs", timestamp_sec)

    def on_alert_dispatched(self, ack: AlertAck, incident: Any) -> None:
        """Muestra en la GUI la respuesta de la central."""
        try:
            if ack.delivered:
                log.warning(
                    "la central respondió: ticket=%s eta=%s min (%s)",
                    ack.ticket,
                    ack.eta_min,
                    ack.status,
                )
                self._view.show_central_response(
                    ack.message or f"Ayuda en camino (ticket {ack.ticket})"
                )
            else:
                log.error("la central no confirmó la alerta: %s", ack.detail)
                self._view.show_central_response(
                    f"no se pudo entregar ({ack.detail})", error=True
                )
        except Exception:
            log.exception("fallo al mostrar la respuesta de la central")

    def on_completed(self, output_path: str) -> None:
        try:
            self._view.show_completed(output_path)
        except Exception:
            log.exception("fallo al mostrar el estado completado")
        finally:
            if self._on_done:
                self._on_done()

    def on_summary(self, summary: dict[str, Any]) -> None:
        try:
            detected = bool(summary.get("events"))
            log.warning(
                "resultado final: VIOLENCIA=%s, eventos=%d, reporte=%s",
                "SI" if detected else "NO",
                len(summary.get("events", [])),
                summary.get("report"),
            )
            self._view.show_summary(summary)
        except Exception:
            log.exception("fallo al mostrar el resumen")

    def on_error(self, message: str) -> None:
        try:
            log.error("error del caso de uso: %s", message)
            self._view.show_error(message)
        except Exception:
            log.exception("fallo al mostrar el error")
        finally:
            if self._on_done:
                self._on_done()


class DetectorController:
    """Coordina la vista con el caso de uso en un hilo de fondo."""

    def __init__(
        self,
        view: DetectorView,
        use_case: DetectIncidentsUseCase,
        output_filename: str = "resultado_incidentes.mp4",
    ) -> None:
        self._view = view
        self._use_case = use_case
        self._output_filename = output_filename
        self._video_path = ""
        self._is_processing = False
        self._view.bind(self.select_video, self.start_processing)

    def select_video(self) -> None:
        """Abre el diálogo de selección y valida que se eligió un archivo."""
        try:
            file_path = filedialog.askopenfilename(
                title="Selecciona un archivo de video",
                filetypes=[("Archivos MP4", "*.mp4"), ("Todos los archivos", "*.*")],
            )
        except Exception:
            log.exception("fallo al abrir el diálogo de archivo")
            return

        if file_path:
            self._video_path = file_path
            log.info("video seleccionado: %s", file_path)
            self._view.show_selected_file(os.path.basename(file_path))

    def start_processing(self) -> None:
        """Lanza el procesamiento en un hilo para no congelar la ventana."""
        if not self._video_path or self._is_processing:
            log.warning("inicio ignorado (sin video o ya procesando)")
            return
        log.info("iniciando procesamiento: %s", self._video_path)
        threading.Thread(
            target=self._run, daemon=True, name="detector-worker"
        ).start()

    def _run(self) -> None:
        self._is_processing = True
        self._view.set_busy(True)
        self._view.reset_state()
        output_path = DetectIncidentsUseCase.resolve_output_path(
            self._video_path, self._output_filename
        )

        def on_done() -> None:
            self._is_processing = False
            self._view.set_busy(False)

        listener = TkProgressListener(self._view, on_done=on_done)
        try:
            self._use_case.execute(self._video_path, output_path, listener)
        except Exception:
            log.exception("fallo el procesamiento de %s", self._video_path)
            on_done()
