"""Caso de uso principal: detección de incidentes en dos etapas.

Etapa 1 (rápida, síncrona)
    El detector analiza cada frame y propone **sospechas**. Se anota el video
    para que el operador vea qué está pasando, pero todavía no hay alarma.

Etapa 2 (lenta, asíncrona)
    Un hilo worker envía la sospecha a un verificador de visión local. Solo un
    veredicto ``violent=True`` produce un evento, dispara la alarma y envía la
    alerta a la central. El video ya escrito no se reescribe: la confirmación
    llega por el listener y por el reporte JSON.

El caso de uso no conoce YOLO, OpenCV, Ollama ni Tkinter. Las operaciones de
imagen (snapshot y overlay) se inyectan desde el *composition root*.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incidentes.domain.entities import (
    AlertPayload,
    DetectionConfig,
    Incident,
    IncidentEvent,
    ProcessingProgress,
    Suspicion,
    VideoInfo,
)
from incidentes.domain.ports import (
    AlertDispatcher,
    IncidentDetector,
    ProgressListener,
    VideoReader,
    VideoWriter,
    ViolenceVerifier,
)

log = logging.getLogger(__name__)

# Cola acotada: si la IA no da abasto, es mejor descartar una sospecha que
# acumular frames y consumir memoria sin límite.
JOB_QUEUE_SIZE = 2


@dataclass(frozen=True)
class _VerificationJob:
    """Sospecha empaquetada para el hilo de verificación."""

    jpeg: bytes
    suspicion: Suspicion


@dataclass
class _RunState:
    """Estado compartido entre el hilo principal y el worker (bajo ``lock``)."""

    pending: int = 0
    alert_sent: bool = False


class DetectIncidentsUseCase:
    """Orquesta detección, verificación, reporte y alerta a la central."""

    def __init__(
        self,
        suspicion_detector: IncidentDetector,
        verifier: ViolenceVerifier,
        reader_factory: Callable[[], VideoReader],
        writer_factory: Callable[[], VideoWriter],
        info_provider: Callable[[str], VideoInfo],
        snapshot_encoder: Callable[[Any], bytes],
        pending_marker: Callable[[Any, int], None],
        *,
        config: DetectionConfig | None = None,
        verify_cooldown_sec: float = 8.0,
        drain_timeout_sec: float = 120.0,
        alert_dispatcher: AlertDispatcher | None = None,
        alert_once: bool = True,
    ) -> None:
        self._detector = suspicion_detector
        self._verifier = verifier
        self._reader_factory = reader_factory
        self._writer_factory = writer_factory
        self._info_provider = info_provider
        self._snapshot_encoder = snapshot_encoder
        self._pending_marker = pending_marker
        self._config = config or DetectionConfig()
        self._verify_cooldown = verify_cooldown_sec
        self._drain_timeout = drain_timeout_sec
        self._dispatcher = alert_dispatcher  # None => solo alerta local
        self._alert_once = alert_once

    # ------------------------------------------------------------------ rutas
    @staticmethod
    def resolve_output_path(video_path: str, filename: str) -> str:
        """Coloca el video procesado junto al video de entrada."""
        return str(Path(video_path).with_name(filename))

    @staticmethod
    def resolve_report_path(output_path: str) -> str:
        """Deriva el nombre del reporte JSON a partir del video de salida."""
        path = Path(output_path)
        return str(path.with_name(f"{path.stem}_reporte.json"))

    # ------------------------------------------------------------------ alerta
    def _dispatch_alert(
        self,
        event: IncidentEvent,
        snapshot: bytes,
        video_path: str,
        listener: ProgressListener,
    ) -> None:
        """Envía la alerta confirmada a la central desde un hilo aparte.

        El puerto ``AlertDispatcher`` no lanza excepciones; el ``try`` es una
        red de seguridad para que un fallo inesperado nunca tumbe el worker.
        """
        if self._dispatcher is None:
            return

        payload = AlertPayload(
            type=event.type,
            confidence=event.confidence,
            label=event.label,
            frame=event.frame,
            time_sec=event.time_sec,
            video=video_path,
            verified_by=event.verified_by,
            reason=event.reason,
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
            snapshot_jpeg=snapshot,
        )
        try:
            ack = self._dispatcher.dispatch(payload)
        except Exception:
            log.exception("fallo inesperado al despachar la alerta a la central")
            return

        if ack.delivered:
            log.warning(
                "alerta enviada a la central: ticket=%s estado=%s eta=%s min",
                ack.ticket,
                ack.status,
                ack.eta_min,
            )
        else:
            log.error("alerta NO entregada a la central: %s", ack.detail)

        try:
            listener.on_alert_dispatched(
                ack, Incident(event.type, event.confidence, event.label)
            )
        except Exception:
            log.exception("fallo al notificar a la UI la respuesta de la central")

    # ------------------------------------------------------------------ worker
    def _verification_worker(
        self,
        jobs: queue.Queue[_VerificationJob | None],
        lock: threading.Lock,
        state: _RunState,
        events: list[IncidentEvent],
        counts: dict[str, int],
        last_event_frame: dict[str, int],
        listener: ProgressListener,
        video_path: str,
        verifier_name: str,
    ) -> None:
        """Consume sospechas y las verifica hasta recibir el centinela ``None``."""
        while True:
            job = jobs.get()
            if job is None:
                jobs.task_done()
                return
            try:
                self._verify_one(
                    job, lock, state, events, counts, last_event_frame,
                    listener, video_path, verifier_name,
                )
            except Exception:
                log.exception(
                    "fallo el worker de verificación (frame=%s)",
                    job.suspicion.frame,
                )
            finally:
                with lock:
                    state.pending -= 1
                jobs.task_done()

    def _verify_one(
        self,
        job: _VerificationJob,
        lock: threading.Lock,
        state: _RunState,
        events: list[IncidentEvent],
        counts: dict[str, int],
        last_event_frame: dict[str, int],
        listener: ProgressListener,
        video_path: str,
        verifier_name: str,
    ) -> None:
        """Verifica una sospecha y, si se confirma, genera evento y alerta."""
        suspicion = job.suspicion
        log.info(
            "enviando sospecha a IA: frame=%d t=%.1fs %s",
            suspicion.frame,
            suspicion.time_sec,
            suspicion.label,
        )
        verdict = self._verifier.verify(job.jpeg, suspicion)
        log.info(
            "veredicto IA: violent=%s tipo=%s conf=%.2f :: %s",
            verdict.violent,
            verdict.type,
            verdict.confidence,
            verdict.reason[:100],
        )

        confirmed: IncidentEvent | None = None
        with lock:
            previous = last_event_frame.get(verdict.type, -10**9)
            if verdict.violent and suspicion.frame - previous >= self._config.cooldown_frames:
                last_event_frame[verdict.type] = suspicion.frame
                counts[verdict.type] = counts.get(verdict.type, 0) + 1
                confirmed = IncidentEvent(
                    frame=suspicion.frame,
                    time_sec=round(suspicion.time_sec, 2),
                    type=verdict.type,
                    confidence=round(verdict.confidence, 3),
                    label=f"CONFIRMADO IA: {verdict.type} ({verdict.reason})",
                    verified_by=verifier_name,
                    reason=verdict.reason,
                )
                events.append(confirmed)

        if confirmed is None:
            return

        # La UI se notifica fuera del lock: un callback lento no bloquea al worker.
        try:
            listener.on_incident_alert(
                Incident(confirmed.type, confirmed.confidence, confirmed.label),
                confirmed.time_sec,
            )
        except Exception:
            log.exception("fallo al notificar el incidente a la UI")

        # Petición de ayuda a la central, sin bloquear la verificación.
        should_dispatch = True
        if self._alert_once:
            with lock:
                should_dispatch = not state.alert_sent
                if should_dispatch:
                    state.alert_sent = True
        if should_dispatch:
            threading.Thread(
                target=self._dispatch_alert,
                args=(confirmed, job.jpeg, video_path, listener),
                daemon=True,
                name="alert-dispatch",
            ).start()

    # ------------------------------------------------------------------ cierre
    @staticmethod
    def _stop_worker(
        jobs: queue.Queue[_VerificationJob | None],
        worker: threading.Thread,
    ) -> None:
        """Envía el centinela y espera al worker, descartando trabajos si hace falta."""
        if not worker.is_alive():
            return
        while True:
            try:
                jobs.put_nowait(None)
                break
            except queue.Full:
                # El worker está ocupado: libera un hueco descartando la cola.
                try:
                    jobs.get_nowait()
                    jobs.task_done()
                except queue.Empty:
                    break
        worker.join(timeout=5)

    def _build_summary(
        self,
        video_path: str,
        output_path: str,
        total_frames: int,
        suspicions: int,
        events: list[IncidentEvent],
        counts: dict[str, int],
        verifier_name: str,
        verifier_available: bool,
    ) -> dict[str, Any]:
        """Construye y persiste el reporte JSON de la corrida."""
        summary: dict[str, Any] = {
            "video": video_path,
            "output": output_path,
            "total_frames": total_frames,
            "suspicions_yolo": suspicions,
            "incident_frames": len(events),  # solo eventos confirmados por IA
            "counts": dict(counts),
            "verifier": verifier_name,
            "verifier_available": verifier_available,
            "events": [asdict(event) for event in events],
        }
        report_path = self.resolve_report_path(output_path)
        Path(report_path).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary["report"] = report_path
        return summary

    # ------------------------------------------------------------------ público
    def execute(
        self,
        video_path: str,
        output_path: str,
        listener: ProgressListener,
    ) -> dict[str, Any]:
        """Procesa un video completo y devuelve el resumen de la corrida."""
        reader = self._reader_factory()
        writer = self._writer_factory()
        verifier_available = self._verifier.ping()
        verifier_name = self._verifier.name

        jobs: queue.Queue[_VerificationJob | None] = queue.Queue(maxsize=JOB_QUEUE_SIZE)
        lock = threading.Lock()
        state = _RunState()
        events: list[IncidentEvent] = []
        counts: dict[str, int] = {}
        last_event_frame: dict[str, int] = {}
        worker = threading.Thread(
            target=self._verification_worker,
            args=(
                jobs, lock, state, events, counts, last_event_frame,
                listener, video_path, verifier_name,
            ),
            daemon=True,
            name="ai-verifier",
        )

        total = 0
        suspicions = 0
        frames_read = 0
        last_sent = float("-inf")

        try:
            info = self._info_provider(video_path)
            total = info.total_frames
            fps = info.fps or 30

            reader.open(video_path)
            writer.open(output_path, info.fps, info.width, info.height)
            worker.start()

            while True:
                ok, frame = reader.read()
                if not ok:
                    break
                frames_read += 1
                analysis = self._detector.analyze(frame)
                annotated = analysis.annotated_frame

                if analysis.incidents and verifier_available:
                    suspicions += 1
                    self._pending_marker(annotated, len(analysis.incidents))
                    now = time.monotonic()
                    if now - last_sent >= self._verify_cooldown:
                        best = max(analysis.incidents, key=lambda i: i.confidence)
                        snapshot = self._snapshot_encoder(frame)
                        if snapshot:
                            job = _VerificationJob(
                                jpeg=snapshot,
                                suspicion=Suspicion(
                                    frame=frames_read,
                                    time_sec=round(frames_read / fps, 2),
                                    type=best.type,
                                    label=best.label,
                                    heuristic_confidence=round(best.confidence, 3),
                                ),
                            )
                            # Incrementa antes de encolar para que el worker no
                            # decremente "pending" antes de que exista el trabajo.
                            with lock:
                                state.pending += 1
                            try:
                                jobs.put_nowait(job)
                                last_sent = now
                            except queue.Full:
                                with lock:
                                    state.pending -= 1
                                log.debug(
                                    "IA saturada: sospecha descartada (frame %d)",
                                    frames_read,
                                )

                writer.write(annotated)
                try:
                    listener.on_progress(
                        ProcessingProgress(frames_read, total), annotated
                    )
                except Exception:
                    log.exception("fallo on_progress en el frame %d", frames_read)

            log.info(
                "video leído: %d frames, %d sospechas; esperando veredictos IA...",
                frames_read,
                suspicions,
            )
            deadline = time.monotonic() + self._drain_timeout
            while time.monotonic() < deadline:
                with lock:
                    drained = state.pending <= 0
                if drained and jobs.empty():
                    break
                time.sleep(0.2)

            with lock:
                still_pending = state.pending
            if still_pending:
                log.warning(
                    "quedan %d verificaciones sin terminar; se cierra igual",
                    still_pending,
                )

            self._stop_worker(jobs, worker)

            summary = self._build_summary(
                video_path=video_path,
                output_path=output_path,
                total_frames=total,
                suspicions=suspicions,
                events=events,
                counts=counts,
                verifier_name=verifier_name,
                verifier_available=verifier_available,
            )
            log.warning(
                "fin: violencia=%s, eventos=%d, reporte=%s",
                "SI" if events else "NO",
                len(events),
                summary["report"],
            )
            listener.on_completed(output_path)
            listener.on_summary(summary)
            return summary

        except Exception as exc:
            log.exception("fallo execute video=%s", video_path)
            try:
                listener.on_error(str(exc))
            except Exception:
                log.exception("fallo al notificar el error a la UI")
            raise
        finally:
            self._stop_worker(jobs, worker)
            for resource in (reader, writer):
                try:
                    resource.close()
                except Exception:
                    log.exception("fallo al cerrar un recurso del pipeline")
