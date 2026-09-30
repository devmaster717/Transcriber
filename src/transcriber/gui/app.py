"""Composition root: builds the core with real adapters and shows the main window."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_dir
from PySide6.QtWidgets import QApplication

from ..core.app import TranscriberCore
from ..core.gateway import RejectedKeyError, TranscriptionResult
from ..deepgram_gateway import DeepgramGateway
from .main_window import MainWindow

APP_NAME = "Transcriber"


class MissingKeyGateway:
    """Stands in for Deepgram when no key is configured, so the window can say so inline."""

    def transcribe(self, audio: Path) -> TranscriptionResult:
        raise RejectedKeyError("No Deepgram API key. Set DEEPGRAM_API_KEY and restart.")


def build_core() -> TranscriberCore:
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    gateway = DeepgramGateway(key) if key else MissingKeyGateway()
    return TranscriberCore(gateway=gateway, data_dir=Path(user_data_dir(APP_NAME, appauthor=False)))


def run(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow(build_core())
    window.show()
    return app.exec()
