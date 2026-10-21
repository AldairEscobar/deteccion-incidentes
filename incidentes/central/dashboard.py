"""Dashboard de mando de la central.

Muestra en tiempo real las alertas recibidas, los clientes conectados y dos
gráficas (por tipo de incidente y por cliente). El servidor REST se levanta en
un hilo aparte; la GUI solo **lee** el estado compartido con un ``QTimer``,
respetando la regla de Qt de no tocar widgets desde otros hilos.
"""

from __future__ import annotations

import logging
import time

import matplotlib

matplotlib.use("Qt5Agg")  # debe fijarse antes de importar pyplot

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from incidentes.central.server_thread import RestServerThread
from incidentes.server.app import registry, store

log = logging.getLogger(__name__)

MAX_VISIBLE_ALERTS = 500
POLL_INTERVAL_MS = 800
START_CHECK_MS = 200
START_MAX_ATTEMPTS = 25

# Estilos reutilizados por los botones de arranque/parada.
BUTTON_START = (
    "background-color:#28A745;color:white;font-weight:bold;"
    "padding:6px 14px;border-radius:4px;"
)
BUTTON_STOP = (
    "background-color:#C0392B;color:white;font-weight:bold;"
    "padding:6px 14px;border-radius:4px;"
)

plt.style.use("dark_background")


class DashboardWindow(QMainWindow):
    """Ventana principal de la central."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._server: RestServerThread | None = None
        self._start_attempts = 0
        self._seen_tickets: set[str] = set()
        self._seen_clients: set[str] = set()
        self._client_count = 0

        self.setWindowTitle("🚨 CENTRAL DE ALERTAS C5 - Dashboard de Mando")
        self.resize(1280, 720)
        self.setStyleSheet("background-color: #121212; color: #ffffff;")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        self._build_toolbar(root)
        self._build_body(root)

        self._draw_charts({}, {})
        self.table.setRowCount(0)

        # Sondeo periódico del estado compartido desde el hilo de la GUI.
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        self.append_log("Panel de la central listo. Pulsa 'Iniciar central'.")

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self, root: QVBoxLayout) -> None:
        bar = QHBoxLayout()

        self.btn_server = QPushButton("Iniciar central")
        self.btn_server.clicked.connect(self.toggle_server)
        self.btn_server.setStyleSheet(BUTTON_START)
        bar.addWidget(self.btn_server)

        self.lbl_status = QLabel("Central detenida")
        self.lbl_status.setStyleSheet("color:#ffa64d;font-weight:bold;")
        bar.addWidget(self.lbl_status)
        bar.addStretch(1)

        self.lbl_summary = QLabel("Clientes: 0   |   Alertas: 0")
        self.lbl_summary.setStyleSheet(
            "color:#39ff14;font-family:Consolas,monospace;"
        )
        bar.addWidget(self.lbl_summary)
        root.addLayout(bar)

    def _build_body(self, root: QVBoxLayout) -> None:
        body = QHBoxLayout()
        root.addLayout(body, stretch=1)

        left = QVBoxLayout()
        body.addLayout(left, stretch=2)
        left.addWidget(QLabel("Alertas recibidas"))

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Ticket", "Cliente", "Tipo", "Descripción", "Dirección", "Hora"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet(
            "QTableWidget{background:#0d1117;color:#e6e6e6;gridline-color:#30363d;}"
            "QHeaderView::section{background:#1e1e1e;color:#39ff14;}"
        )
        left.addWidget(self.table, stretch=2)

        left.addWidget(QLabel("Consola de eventos"))
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            "QTextEdit{background:#0d1117;color:#39ff14;"
            "font-family:Consolas,monospace;font-size:13px;"
            "border:1px solid #30363d;}"
        )
        left.addWidget(self.console, stretch=2)

        right = QVBoxLayout()
        body.addLayout(right, stretch=3)

        self.lbl_clients = QLabel("Clientes conectados: 0")
        right.addWidget(self.lbl_clients)

        self.client_list = QListWidget()
        self.client_list.setStyleSheet(
            "QListWidget{background:#11111b;color:#89b4fa;"
            "font-family:Consolas,monospace;border:1px solid #313244;}"
        )
        self.client_list.setMaximumHeight(160)
        right.addWidget(self.client_list)

        self.figure, (self.ax_types, self.ax_clients) = plt.subplots(
            2, 1, figsize=(6, 5)
        )
        self.figure.patch.set_facecolor("#121212")
        self.canvas = FigureCanvas(self.figure)
        right.addWidget(self.canvas, stretch=1)

    # --------------------------------------------------------------- servidor
    def toggle_server(self) -> None:
        if self._server is None:
            self.start_server()
        else:
            self.stop_server()

    def start_server(self) -> None:
        """Arranca uvicorn sin bloquear el hilo de la GUI."""
        self._server = RestServerThread(self._host, self._port)
        self._server.start()
        self._start_attempts = 0
        self.lbl_status.setText("Central iniciando...")
        self.lbl_status.setStyleSheet("color:#ffa64d;font-weight:bold;")
        # Sondeo no bloqueante: la GUI sigue respondiendo mientras uvicorn arranca.
        QTimer.singleShot(START_CHECK_MS, self._confirm_start)

    def _confirm_start(self) -> None:
        if self._server is None:
            return
        if self._server.running:
            self.btn_server.setText("Detener central")
            self.btn_server.setStyleSheet(BUTTON_STOP)
            self.lbl_status.setText(
                f"Central EN LÍNEA en http://{self._host}:{self._port}"
            )
            self.lbl_status.setStyleSheet("color:#39ff14;font-weight:bold;")
            self.append_log(
                f"[OK] Servidor REST escuchando en http://{self._host}:{self._port}"
            )
            return

        self._start_attempts += 1
        if self._start_attempts < START_MAX_ATTEMPTS:
            QTimer.singleShot(START_CHECK_MS, self._confirm_start)
            return

        self.append_log("[ERROR] No se pudo levantar el servidor (¿puerto ocupado?)")
        self.stop_server()

    def stop_server(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
        self.btn_server.setText("Iniciar central")
        self.btn_server.setStyleSheet(BUTTON_START)
        self.lbl_status.setText("Central detenida")
        self.lbl_status.setStyleSheet("color:#ffa64d;font-weight:bold;")
        self.append_log("[--] Servidor REST detenido.")

    # ---------------------------------------------------------------- sondeo
    def refresh(self) -> None:
        """Sincroniza la vista con el estado compartido (nunca lanza)."""
        try:
            self._refresh_clients()
            self._refresh_alerts()
        except Exception as exc:  # un fallo de la GUI no debe romper el bucle
            self.append_log(f"[ERROR GUI] {exc}")

    def _refresh_clients(self) -> None:
        clients = registry.list()
        self.client_list.clear()
        for client in clients:
            self.client_list.addItem(
                f"● {client['client_id']}  ({client['ip']})  "
                f"{client['address'] or 'sin dirección'}"
            )
            if client["client_id"] not in self._seen_clients:
                self._seen_clients.add(client["client_id"])
                self.append_log(
                    f"[REGISTRO] cliente conectado: {client['client_id']} "
                    f"desde {client['ip']} :: "
                    f"{client['address'] or 'sin dirección'}"
                )
        self._client_count = len(clients)
        self.lbl_clients.setText(f"Clientes conectados: {self._client_count}")

    def _refresh_alerts(self) -> None:
        alerts = store.list(limit=MAX_VISIBLE_ALERTS)
        new_alerts = [
            alert for alert in alerts if alert["ticket"] not in self._seen_tickets
        ]
        for alert in reversed(new_alerts):  # las más antiguas primero
            self._seen_tickets.add(alert["ticket"])
            self._append_alert(alert)

        self.lbl_summary.setText(
            f"Clientes: {self._client_count}   |   Alertas: {store.count()}"
        )
        if new_alerts:
            self._update_charts(alerts)

    def _append_alert(self, alert: dict) -> None:
        body = alert["alert"]
        location = body.get("location", {})
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            alert["ticket"],
            body["client_id"],
            body["type"],
            body.get("reason") or body.get("label", ""),
            location.get("address", "") or "sin dirección",
            alert["received_at"][11:19],
        ]
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.scrollToBottom()

        self.append_log(
            f"⚠️ ALERTA {alert['ticket']} [{alert['priority']}] "
            f"{body['type']} - {body['client_id']}"
        )
        self.append_log(
            f"    descripción: {body.get('reason') or body.get('label', '')}"
        )
        self.append_log(
            f"    dirección:   {location.get('address', '') or 'sin dirección'}"
        )
        self.append_log(
            f"    ► AYUDA EN CAMINO: {', '.join(alert['units'])} "
            f"(ETA ~{alert['eta_min']} min)"
        )

    # --------------------------------------------------------------- gráficas
    def _update_charts(self, alerts: list[dict]) -> None:
        by_type: dict[str, int] = {}
        by_client: dict[str, int] = {}
        for alert in alerts:
            incident_type = alert["alert"]["type"]
            client = alert["alert"]["client_id"]
            by_type[incident_type] = by_type.get(incident_type, 0) + 1
            by_client[client] = by_client.get(client, 0) + 1
        self._draw_charts(by_type, by_client)

    def _draw_charts(self, by_type: dict, by_client: dict) -> None:
        self.ax_types.clear()
        self.ax_types.set_facecolor("#1e1e1e")
        self.ax_types.bar(
            list(by_type.keys()) or ["-"],
            list(by_type.values()) or [0],
            color="#ff4d4d",
        )
        self.ax_types.set_title(
            "Alertas por Tipo de Incidente", color="white", pad=8
        )
        self.ax_types.tick_params(colors="white", labelsize=8)

        self.ax_clients.clear()
        self.ax_clients.set_facecolor("#1e1e1e")
        self.ax_clients.bar(
            list(by_client.keys()) or ["-"],
            list(by_client.values()) or [0],
            color="#9933ff",
        )
        self.ax_clients.set_title("Alertas por Cliente", color="white", pad=8)
        self.ax_clients.tick_params(colors="white", labelsize=8)

        self.figure.tight_layout(pad=2.5)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------ util
    def append_log(self, message: str) -> None:
        self.console.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def closeEvent(self, event) -> None:  # noqa: N802 (API de Qt)
        self.timer.stop()
        self.stop_server()
        event.accept()


def run(host: str = "127.0.0.1", port: int = 8000, auto_start: bool = True) -> int:
    """Crea la aplicación Qt y ejecuta el bucle de eventos."""
    application = QApplication.instance() or QApplication([])
    window = DashboardWindow(host, port)
    window.show()
    if auto_start:
        window.start_server()
    return application.exec_()
