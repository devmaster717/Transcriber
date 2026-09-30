"""Main window: the Library on the left, the Rendering of the selected Transcript on the right."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.app import LibraryEntry, TranscriberCore
from ..core.gateway import TranscriptionError
from ..core.model import Transcript
from ..core.rendering import format_duration, render

AUDIO_FILTER = (
    "Audio and video (*.mp3 *.wav *.m4a *.mp4 *.flac *.ogg *.webm *.aac *.wma *.opus *.mkv *.mov);;"
    "All files (*)"
)
ID_ROLE = Qt.ItemDataRole.UserRole


class CoreWorker(QThread):
    """Runs one core operation on its own thread and reports back through signals."""

    ready = Signal(object)  # Transcript
    failed = Signal(str)

    def __init__(self, operation: Callable[[], Transcript], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._operation = operation

    def run(self) -> None:
        try:
            self.ready.emit(self._operation())
        except TranscriptionError as e:
            self.failed.emit(e.message)
        except OSError as e:
            self.failed.emit(f"Could not read the file: {e.strerror or e}")


class MainWindow(QMainWindow):
    def __init__(self, core: TranscriberCore) -> None:
        super().__init__()
        self._core = core
        self._worker: CoreWorker | None = None
        self._entries: dict[str, LibraryEntry] = {}
        self.setWindowTitle("Transcriber")
        self.resize(1000, 640)

        # Top bar
        self.open_button = QPushButton("Open file…")
        self.open_button.clicked.connect(self._choose_file)
        self.status = QLabel("Open an audio file to transcribe it.")
        self.status.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.open_button)
        top.addWidget(self.status, stretch=1)

        # Library
        self.library_list = QListWidget()
        self.library_list.currentItemChanged.connect(self._on_selection_changed)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_file_button = QPushButton("Open file")
        self.open_file_button.clicked.connect(self._open_rendering)
        self.retranscribe_button = QPushButton("Re-transcribe")
        self.retranscribe_button.clicked.connect(self._retranscribe)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove)
        actions = QHBoxLayout()
        for b in (self.open_folder_button, self.open_file_button, self.retranscribe_button, self.remove_button):
            actions.addWidget(b)
        library_panel = QWidget()
        library_layout = QVBoxLayout(library_panel)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.addWidget(QLabel("Library"))
        library_layout.addWidget(self.library_list, stretch=1)
        library_layout.addLayout(actions)

        # Viewer
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setPlaceholderText("Select a Transcript to read its Rendering.")

        splitter = QSplitter()
        splitter.addWidget(library_panel)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(splitter, stretch=1)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.refresh_library()

    # ---- Library -------------------------------------------------------------------------

    def refresh_library(self, select_id: str | None = None) -> None:
        """Reload the Library from the core and keep (or set) the selection."""
        if select_id is None:
            select_id = self.selected_id()
        self.library_list.blockSignals(True)
        self.library_list.clear()
        self._entries = {}
        for entry in self._core.library():
            t = entry.transcript
            self._entries[t.id] = entry
            summary = " · ".join(
                [t.created.strftime("%Y-%m-%d %H:%M"), format_duration(t.duration), t.source_kind.label]
            )
            if entry.missing_files:
                summary += " · not found"
            item = QListWidgetItem(f"{t.title}\n{summary}")
            item.setData(ID_ROLE, t.id)
            if entry.missing_files:
                item.setForeground(QColor("gray"))
            self.library_list.addItem(item)
        self.library_list.blockSignals(False)
        for row in range(self.library_list.count()):
            if self.library_list.item(row).data(ID_ROLE) == select_id:
                self.library_list.setCurrentRow(row)
                break
        else:
            self.library_list.setCurrentRow(-1)
            self._show_entry(None)

    def selected_id(self) -> str | None:
        item = self.library_list.currentItem()
        return item.data(ID_ROLE) if item is not None else None

    def selected_entry(self) -> LibraryEntry | None:
        selected = self.selected_id()
        return self._entries.get(selected) if selected else None

    @Slot(QListWidgetItem, QListWidgetItem)
    def _on_selection_changed(self, current: QListWidgetItem | None, _previous) -> None:
        self._show_entry(self._entries.get(current.data(ID_ROLE)) if current is not None else None)

    def _show_entry(self, entry: LibraryEntry | None) -> None:
        has_entry = entry is not None
        self.open_folder_button.setEnabled(has_entry)
        self.open_file_button.setEnabled(has_entry and entry.rendering_found)
        self.retranscribe_button.setEnabled(has_entry and entry.source_found)
        self.remove_button.setEnabled(has_entry)
        if entry is None:
            self.viewer.clear()
            return
        self.viewer.setPlainText(render(entry.transcript))
        missing = []
        if not entry.rendering_found:
            missing.append("Rendering file not found")
        if not entry.source_found:
            missing.append("source file not found")
        if missing:
            self.status.setText(", ".join(missing).capitalize() + ". The Transcript is still kept by the app.")
        else:
            self.status.setText(f"{entry.transcript.rendering_path}")

    # ---- Actions -------------------------------------------------------------------------

    @Slot()
    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open audio file", "", AUDIO_FILTER)
        if path:
            self.transcribe(Path(path))

    def transcribe(self, path: Path) -> None:
        """Start transcribing a File. Public so the app can be driven without the dialog."""
        self._run(f"Transcribing {path.name}…", lambda: self._core.transcribe_file(path))

    @Slot()
    def _retranscribe(self) -> None:
        entry = self.selected_entry()
        if entry is not None:
            self._run(f"Re-transcribing {entry.transcript.title}…", lambda: self._core.retranscribe(entry.transcript.id))

    @Slot()
    def _remove(self) -> None:
        entry = self.selected_entry()
        if entry is not None:
            self._core.remove(entry.transcript.id)
            self.refresh_library(select_id="")
            self.status.setText(f"Removed {entry.transcript.title} from the Library. Its files were not touched.")

    @Slot()
    def _open_folder(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        target = entry.transcript.rendering_path if entry.rendering_found else entry.transcript.source_path
        if target is None:
            return
        if target.exists() and sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

    @Slot()
    def _open_rendering(self) -> None:
        entry = self.selected_entry()
        if entry is not None and entry.rendering_found and entry.transcript.rendering_path is not None:
            os.startfile(entry.transcript.rendering_path) if sys.platform == "win32" else QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(entry.transcript.rendering_path))
            )

    # ---- Worker plumbing --------------------------------------------------------------------

    def _run(self, status: str, operation: Callable[[], Transcript]) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_busy(True)
        self.status.setText(status)
        self._worker = CoreWorker(operation, parent=self)
        self._worker.ready.connect(self._on_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.retranscribe_button.setEnabled(not busy and self.retranscribe_button.isEnabled())

    @Slot(object)
    def _on_ready(self, transcript: Transcript) -> None:
        self._set_busy(False)
        self.refresh_library(select_id=transcript.id)
        self.status.setText(f"Saved to {transcript.rendering_path}")

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status.setText(message)
        self._show_entry(self.selected_entry())
