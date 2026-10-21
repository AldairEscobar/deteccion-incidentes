"""Vista Tkinter del cliente detector.

La vista es deliberadamente "tonta": solo construye widgets y expone métodos
para actualizarlos. No conoce YOLO, OpenCV de negocio ni el caso de uso. Todos
los métodos públicos se ejecutan en el hilo de la GUI mediante ``after``,
porque el caso de uso notifica desde hilos worker.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

import cv2
from PIL import Image, ImageTk


class DetectorView:
    """Ventana del detector: identidad, estado, progreso y previsualización."""

    PREVIEW_SIZE = (640, 360)

    def __init__(self, root: tk.Tk, client_id: str = "", address: str = "") -> None:
        self.root = root
        self.client_id = client_id
        self.address = address

        suffix = f" - {client_id}" if client_id else ""
        self.root.title(f"Detección de Incidentes Violentos (YOLO){suffix}")
        self.root.geometry("880x740")
        self.root.minsize(700, 620)

        self._on_select: Callable[[], None] | None = None
        self._on_process: Callable[[], None] | None = None

        tk.Label(
            root,
            text="Detector de Peleas / Robos / Violencia (YOLO)",
            font=("Arial", 15, "bold"),
        ).pack(pady=(10, 2))

        location = f"{client_id}  ·  {address}" if client_id else address
        self.lbl_identity = tk.Label(
            root,
            text=location or "Cámara sin identificar",
            font=("Arial", 10, "italic"),
            fg="#0B5394",
        )
        self.lbl_identity.pack(pady=(0, 6))

        # Banner de alarma: oculto hasta que la IA confirma un incidente.
        self.lbl_alert = tk.Label(
            root, text="", font=("Arial", 12, "bold"),
            bg="SystemButtonFace", fg="white", wraplength=820, justify=tk.CENTER,
        )
        self.lbl_alert.pack(pady=2, fill=tk.X, padx=20)

        self.lbl_state = tk.Label(
            root, text="● SIN VIOLENCIA", font=("Arial", 11, "bold"), fg="#27AE60"
        )
        self.lbl_state.pack(pady=2)

        # Respuesta de la central ("ayuda en camino ...").
        self.lbl_central = tk.Label(
            root, text="", font=("Arial", 12, "bold"),
            bg="SystemButtonFace", fg="white", wraplength=820, justify=tk.CENTER,
        )
        self.lbl_central.pack(pady=2, fill=tk.X, padx=20)

        file_frame = tk.Frame(root)
        file_frame.pack(pady=5, fill=tk.X, padx=20)

        self.btn_select = tk.Button(
            file_frame,
            text="Seleccionar Video MP4",
            command=lambda: self._on_select and self._on_select(),
            font=("Arial", 10),
            bg="#007ACC",
            fg="white",
            padx=10,
            pady=5,
        )
        self.btn_select.pack(side=tk.LEFT)

        self.lbl_file = tk.Label(
            file_frame, text="Ningún video seleccionado", font=("Arial", 9, "italic")
        )
        self.lbl_file.pack(side=tk.LEFT, padx=10)

        self.btn_process = tk.Button(
            root,
            text="Iniciar Detección",
            command=lambda: self._on_process and self._on_process(),
            font=("Arial", 11, "bold"),
            bg="#28A745",
            fg="white",
            state=tk.DISABLED,
            padx=15,
            pady=8,
        )
        self.btn_process.pack(pady=10)

        self.progress = ttk.Progressbar(
            root, orient="horizontal", length=750, mode="determinate"
        )
        self.progress.pack(pady=5)

        self.lbl_status = tk.Label(
            root, text="Estado: Esperando video...", font=("Arial", 10)
        )
        self.lbl_status.pack(pady=5)

        self.canvas_frame = tk.Frame(root, bg="black", width=640, height=360)
        self.canvas_frame.pack(pady=10, fill=tk.BOTH, expand=True, padx=20)

        self.video_label = tk.Label(self.canvas_frame, bg="black")
        self.video_label.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------- integración
    def bind(
        self,
        on_select: Callable[[], None],
        on_process: Callable[[], None],
    ) -> None:
        """Conecta los botones con el controlador (la vista no lo importa)."""
        self._on_select = on_select
        self._on_process = on_process

    def _run_on_ui(self, callback: Callable[[], None]) -> None:
        """Programa una actualización en el hilo de la GUI."""
        self.root.after(0, callback)

    # ------------------------------------------------------------------ estado
    def show_selected_file(self, file_name: str) -> None:
        def apply() -> None:
            self.lbl_file.config(text=f"Archivo: {file_name}")
            self.btn_process.config(state=tk.NORMAL)
            self.lbl_status.config(text="Estado: Listo para procesar.")

        self._run_on_ui(apply)

    def set_busy(self, busy: bool) -> None:
        def apply() -> None:
            state = tk.DISABLED if busy else tk.NORMAL
            self.btn_select.config(state=state)
            self.btn_process.config(state=state)

        self._run_on_ui(apply)

    def set_progress_max(self, total: int) -> None:
        self._run_on_ui(lambda: self.progress.config(maximum=max(total, 1)))

    def show_progress(self, current: int, total: int) -> None:
        def apply() -> None:
            self.progress["value"] = current
            percentage = int(current / total * 100) if total else 0
            self.lbl_status.config(
                text=f"Procesando frame {current} de {total} ({percentage}%)..."
            )

        self._run_on_ui(apply)

    def show_frame(self, bgr_frame: Any) -> None:
        """Convierte el frame a RGB en el hilo worker y publica el PhotoImage."""
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        rgb = cv2.resize(rgb, self.PREVIEW_SIZE)
        image = ImageTk.PhotoImage(image=Image.fromarray(rgb))

        def apply() -> None:
            self.video_label.imgtk = image  # referencia para evitar el GC
            self.video_label.config(image=image)

        self._run_on_ui(apply)

    def set_alarm(self, text: str) -> None:
        """Activa la alarma visual con latch hasta el próximo procesamiento."""

        def apply() -> None:
            self.lbl_alert.config(text=f"ALARMA: {text}", bg="#C0392B")
            self.lbl_state.config(text="● VIOLENCIA DETECTADA", fg="#C0392B")

        self._run_on_ui(apply)

    def show_central_response(self, text: str, error: bool = False) -> None:
        """Muestra el acuse de la central o el fallo de entrega."""
        background = "#C0392B" if error else "#0B6623"
        message = f"SIN RESPUESTA: {text}" if error else text

        def apply() -> None:
            self.lbl_central.config(text=message, bg=background, fg="white")

        self._run_on_ui(apply)

    def reset_state(self) -> None:
        def apply() -> None:
            self.lbl_alert.config(text="", bg="SystemButtonFace")
            self.lbl_state.config(text="● SIN VIOLENCIA", fg="#27AE60")
            self.lbl_central.config(text="", bg="SystemButtonFace")

        self._run_on_ui(apply)

    def show_completed(self, output_path: str) -> None:
        self._run_on_ui(
            lambda: self.lbl_status.config(
                text=f"¡Proceso finalizado! Guardado en: {output_path}"
            )
        )

    def show_summary(self, summary: dict[str, Any]) -> None:
        """Muestra el resultado final y la ruta del reporte JSON."""
        events = summary.get("events", [])
        report = summary.get("report", "")
        if events:
            first = events[0]
            body = (
                "VIOLENCIA DETECTADA: SÍ\n\n"
                f"Primera detección: {first.get('type')} @ {first.get('time_sec')}s\n"
                f"Momentos con violencia: {len(events)}\n\n"
                f"Reporte JSON:\n{report}"
            )
        else:
            body = (
                "VIOLENCIA DETECTADA: NO\n\n"
                "Sin incidentes confirmados por la IA.\n\n"
                f"Reporte JSON:\n{report}"
            )

        def apply() -> None:
            messagebox.showinfo(
                "Proceso Completado",
                f"Video guardado en:\n{summary.get('output', '')}\n\n{body}",
            )

        self._run_on_ui(apply)

    def show_error(self, message: str) -> None:
        self._run_on_ui(lambda: messagebox.showerror("Error", message))
