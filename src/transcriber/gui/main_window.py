"""Main window: the queue and Library on the left, the Rendering of the selected Transcript on the right."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QDragEnterEvent, QDropEvent
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
from ..core.queue import QueueItem, QueueState
from ..core.rendering import format_duration, render

AUDIO_FILTER = (
    "Audio and video (*.mp3 *.wav *.m4a *.mp4 *.flac *.ogg *.webm *.aac *.wma *.opus *.mkv *.mov);;"
    "All files (*)"
)
ID_ROLE = Qt.ItemDataRole.UserRole
STATE_LABEL = {
    QueueState.WAITING: "waiting",
    QueueState.TRANSCRIBING: "transcribing…",
    QueueState.DONE: "done",
    QueueState.FAILED: "failed",
}


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


class QueueWorker(QThread):
    """Drains the File queue one item at a time until nothing is waiting."""

    changed = Signal(object)  # QueueItem

    def __init__(self, core: TranscriberCore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._core = core

    def run(self) -> None:
        while (item := self._core.process_next()) is not None:
            self.changed.emit(item)


class MainWindow(QMainWindow):
    def __init__(self, core: TranscriberCore) -> None:
        super().__init__()
        self._core = core
        self._worker: CoreWorker | None = None
        self._queue_worker: QueueWorker | None = None
        self._entries: dict[str, LibraryEntry] = {}
        self.setWindowTitle("Transcriber")
        self.resize(1000, 680)
        self.setAcceptDrops(True)

        # Top bar
        self.open_button = QPushButton("Open files…")
        self.open_button.clicked.connect(self._choose_files)
        self.status = QLabel("Open audio files, or drop them onto this window.")
        self.status.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.open_button)
        top.addWidget(self.status, stretch=1)

        # Queue
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(140)
        self.queue_list.currentItemChanged.connect(self._on_queue_selection_changed)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self._retry)
        queue_header = QHBoxLayout()
        queue_header.addWidget(QLabel("Queue"))
        queue_header.addStretch(1)
        queue_header.addWidget(self.retry_button)

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

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(queue_header)
        left_layout.addWidget(self.queue_list)
        left_layout.addWidget(QLabel("Library"))
        left_layout.addWidget(self.library_list, stretch=1)
        left_layout.addLayout(actions)

        # Viewer
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setPlaceholderText("Select a Transcript to read its Rendering.")

        splitter = QSplitter()
        splitter.addWidget(left)
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
        self.refresh_queue()

    # ---- Drag and drop -------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt override)
        if any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt override)
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        files = [p for p in paths if p.is_file()]
        if files:
            self.enqueue(files)
            event.acceptProposedAction()

    # ---- Queue ---------------------------------------------------------------------------

    @Slot()
    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open audio files", "", AUDIO_FILTER)
        if paths:
            self.enqueue([Path(p) for p in paths])

    def enqueue(self, paths: list[Path]) -> None:
        """Queue Files for transcription and make sure the worker is draining the queue."""
        self._core.enqueue(paths)
        self.refresh_queue()
        self._ensure_queue_running()

    def _ensure_queue_running(self) -> None:
        if self._queue_worker is not None and self._queue_worker.isRunning():
            return
        self._queue_worker = QueueWorker(self._core, parent=self)
        self._queue_worker.changed.connect(self._on_queue_item_changed)
        self._queue_worker.finished.connect(self._on_queue_drained)
        self._queue_worker.start()

    @Slot(object)
    def _on_queue_item_changed(self, item: QueueItem) -> None:
        self.refresh_queue()
        if item.state is QueueState.DONE and item.transcript_id is not None:
            self.refresh_library(select_id=item.transcript_id)
            self.status.setText(f"Transcribed {item.path.name}")
        elif item.state is QueueState.FAILED:
            self.status.setText(f"{item.path.name}: {item.error}")

    @Slot()
    def _on_queue_drained(self) -> None:
        # An item added while the worker was finishing would otherwise wait forever.
        if any(i.state is QueueState.WAITING for i in self._core.queue()):
            self._ensure_queue_running()

    def refresh_queue(self) -> None:
        selected = self._selected_queue_id()
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        for item in self._core.queue():
            text = f"{item.path.name} — {STATE_LABEL[item.state]}"
            if item.state is QueueState.FAILED and item.error:
                text += f": {item.error}"
            row = QListWidgetItem(text)
            row.setData(ID_ROLE, item.id)
            if item.state is QueueState.FAILED:
                row.setForeground(QColor("firebrick"))
            elif item.state is QueueState.DONE:
                row.setForeground(QColor("gray"))
            self.queue_list.addItem(row)
            if item.id == selected:
                self.queue_list.setCurrentItem(row)
        self.queue_list.blockSignals(False)
        self._update_retry_button()

    def _selected_queue_id(self) -> str | None:
        row = self.queue_list.currentItem()
        return row.data(ID_ROLE) if row is not None else None

    @Slot(QListWidgetItem, QListWidgetItem)
    def _on_queue_selection_changed(self, _current, _previous) -> None:
        self._update_retry_button()

    def _update_retry_button(self) -> None:
        selected = self._selected_queue_id()
        failed = any(i.id == selected and i.state is QueueState.FAILED for i in self._core.queue())
        self.retry_button.setEnabled(failed)

    @Slot()
    def _retry(self) -> None:
        selected = self._selected_queue_id()
        if selected is not None:
            self._core.retry(selected)
            self.refresh_queue()
            self._ensure_queue_running()

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
            row = QListWidgetItem(f"{t.title}\n{summary}")
            row.setData(ID_ROLE, t.id)
            if entry.missing_files:
                row.setForeground(QColor("gray"))
            self.library_list.addItem(row)
        self.library_list.blockSignals(False)
        for i in range(self.library_list.count()):
            if self.library_list.item(i).data(ID_ROLE) == select_id:
                self.library_list.setCurrentRow(i)
                break
        else:
            self.library_list.setCurrentRow(-1)
            self._show_entry(None)

    def selected_id(self) -> str | None:
        row = self.library_list.currentItem()
        return row.data(ID_ROLE) if row is not None else None

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

    # ---- Library actions -----------------------------------------------------------------

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
        if entry is None or not entry.rendering_found or entry.transcript.rendering_path is None:
            return
        if sys.platform == "win32":
            os.startfile(entry.transcript.rendering_path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.transcript.rendering_path)))

    # ---- Single-operation worker (re-transcribe) ----------------------------------------

    def _run(self, status: str, operation: Callable[[], Transcript]) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.retranscribe_button.setEnabled(False)
        self.status.setText(status)
        self._worker = CoreWorker(operation, parent=self)
        self._worker.ready.connect(self._on_ready)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    @Slot(object)
    def _on_ready(self, transcript: Transcript) -> None:
        self.refresh_library(select_id=transcript.id)
        self.status.setText(f"Saved to {transcript.rendering_path}")

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self._show_entry(self.selected_entry())
