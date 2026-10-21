"""Lectura y metadatos de video con OpenCV."""

from __future__ import annotations

from typing import Any

import cv2

from incidentes.domain.entities import VideoInfo
from incidentes.domain.ports import VideoReader


def get_video_info(path: str) -> VideoInfo:
    """Lee los metadatos del video sin dejar el archivo abierto."""
    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise OSError(f"No se pudo abrir el video: {path}")
        return VideoInfo(
            path=path,
            total_frames=int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
            fps=int(capture.get(cv2.CAP_PROP_FPS)) or 30,
            width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
    finally:
        capture.release()


class OpenCVVideoReader(VideoReader):
    """Implementación de ``VideoReader`` sobre ``cv2.VideoCapture``."""

    def __init__(self) -> None:
        self._capture: Any = None

    def open(self, path: str) -> None:
        self._capture = cv2.VideoCapture(path)
        if not self._capture.isOpened():
            raise OSError(f"No se pudo abrir el video: {path}")

    def read(self) -> tuple[bool, Any]:
        if self._capture is None:
            raise RuntimeError("El lector no está abierto; llama a open() primero.")
        return self._capture.read()

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
