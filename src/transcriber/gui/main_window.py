"""Main window: open a File, watch it transcribe, read the Rendering."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.app import TranscriberCore
from ..core.gateway import TranscriptionError
from ..core.model import Transcript
from ..core.rendering import render

AUDIO_FILTER = "Audio and video (*.mp3 *.wav *.m4a *.mp4 *.flac *.ogg *.webm *.aac *.wma *.opus *.mkv *.mov);;All files (*)"


class TranscribeFileWorker(QThread):
    """Runs one File transcription on its own thread and reports back through signals."""

    ready = Signal(object)  # Transcript
    failed = Signal(str)

    def __init__(self, core: TranscriberCore, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._core = core
        self._path = path

    def run(self) -> None:
        try:
            self.ready.emit(self._core.transcribe_file(self._path))
        except TranscriptionError as e:
            self.failed.emit(e.message)
        except OSError as e:
            self.failed.emit(f"Could not read the file: {e.strerror or e}")


class MainWindow(QMainWindow):
    def __init__(self, core: TranscriberCore) -> None:
        super().__init__()
        self._core = core
        self._worker: TranscribeFileWorker | None = None
        self.setWindowTitle("Transcriber")
        self.resize(820, 600)

        self.open_button = QPushButton("Open file…")
        self.open_button.clicked.connect(self._choose_file)
        self.status = QLabel("Open an audio file to transcribe it.")
        self.status.setWordWrap(True)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setPlaceholderText("The Rendering will appear here.")

        top = QHBoxLayout()
        top.addWidget(self.open_button)
        top.addWidget(self.status, stretch=1)
        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self.viewer, stretch=1)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    @Slot()
    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open audio file", "", AUDIO_FILTER)
        if path:
            self.transcribe(Path(path))

    def transcribe(self, path: Path) -> None:
        """Start transcribing a File. Public so the app can be driven without the dialog."""
        if self._worker is not None and self._worker.isRunning():
            return
        self.open_button.setEnabled(False)
        self.status.setText(f"Transcribing {path.name}…")
        self.viewer.clear()
        self._worker = TranscribeFileWorker(self._core, path, parent=self)
        self._worker.ready.connect(self._on_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(object)
    def _on_ready(self, transcript: Transcript) -> None:
        self.open_button.setEnabled(True)
        self.status.setText(f"Saved to {transcript.rendering_path}")
        self.viewer.setPlainText(render(transcript))

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.open_button.setEnabled(True)
        self.status.setText(message)
