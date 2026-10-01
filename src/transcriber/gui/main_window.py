"""Main window: the queue and Library on the left, the Rendering of the selected Transcript on the right."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.app import ApiKeyProblem, CaptionsChanged, LibraryEntry, TranscriberCore
from ..core.capture import CaptureError, CaptureState, LiveCaptions
from ..core.gateway import KeyProblem, TranscriptionError
from ..core.model import SourceKind, Transcript, TranscriptStatus
from ..core.queue import QueueItem, QueueState
from ..core.rendering import RenderingOptions, format_duration, format_timestamp, render
from .settings_dialog import SettingsDialog

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
    key_problem = Signal(str)

    def __init__(self, operation: Callable[[], Transcript], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._operation = operation

    def run(self) -> None:
        try:
            self.ready.emit(self._operation())
        except KeyProblem as e:
            self.failed.emit(e.message)
            self.key_problem.emit(e.message)
        except TranscriptionError as e:
            self.failed.emit(e.message)
        except OSError as e:
            self.failed.emit(f"Could not read the file: {e.strerror or e}")


class EventBridge(QObject):
    """Carries core events, which arrive on capture and network threads, onto the Qt thread."""

    event = Signal(object)


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
        self.settings_button = QPushButton("Settings…")
        self.settings_button.clicked.connect(lambda: self.open_settings())
        self.status = QLabel("Open audio files, or drop them onto this window.")
        self.status.setWordWrap(True)
        self._settings_dialog: SettingsDialog | None = None
        top = QHBoxLayout()
        top.addWidget(self.open_button)
        top.addWidget(self.settings_button)
        top.addWidget(self.status, stretch=1)

        # Capture controls
        self.source_kind = QComboBox()
        for kind in (SourceKind.MEETING, SourceKind.MICROPHONE, SourceKind.SYSTEM_AUDIO):
            self.source_kind.addItem(kind.label, kind)
        self.source_kind.setCurrentIndex(0)  # Meeting is the default kind of Capture
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Title (optional)")
        self.capture_button = QPushButton("Start capture")
        self.capture_button.clicked.connect(self._toggle_capture)
        self.capture_indicator = QLabel("")
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick)
        capture_row = QHBoxLayout()
        capture_row.addWidget(QLabel("Capture"))
        capture_row.addWidget(self.source_kind)
        capture_row.addWidget(self.title_edit, stretch=1)
        capture_row.addWidget(self.capture_button)
        capture_row.addWidget(self.capture_indicator)

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

        # Viewer with its Rendering toggles, and the Live Captions panel that replaces it during a Capture
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setPlaceholderText("Select a Transcript to read its Rendering.")
        defaults = self._core.settings()
        self.timestamps_toggle = QCheckBox("Timestamps")
        self.timestamps_toggle.setChecked(defaults.include_timestamps)
        self.speakers_toggle = QCheckBox("Speaker labels")
        self.speakers_toggle.setChecked(defaults.include_speaker_labels)
        for toggle in (self.timestamps_toggle, self.speakers_toggle):
            toggle.toggled.connect(self._rerender)
        self.make_default_button = QPushButton("Make default")
        self.make_default_button.setToolTip("Write new Rendering files with the current toggles")
        self.make_default_button.clicked.connect(self._make_default)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy)
        self.rename_button = QPushButton("Rename…")
        self.rename_button.clicked.connect(self._rename_transcript)
        self.rename_speaker_button = QPushButton("Rename speaker…")
        self.rename_speaker_button.clicked.connect(self._rename_speaker)
        viewer_bar = QHBoxLayout()
        viewer_bar.addWidget(self.timestamps_toggle)
        viewer_bar.addWidget(self.speakers_toggle)
        viewer_bar.addWidget(self.make_default_button)
        viewer_bar.addStretch(1)
        viewer_bar.addWidget(self.copy_button)
        viewer_bar.addWidget(self.rename_button)
        viewer_bar.addWidget(self.rename_speaker_button)
        viewer_panel = QWidget()
        viewer_layout = QVBoxLayout(viewer_panel)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.addLayout(viewer_bar)
        viewer_layout.addWidget(self.viewer, stretch=1)
        self.viewer_panel = viewer_panel
        self.captions_view = QTextEdit()
        self.captions_view.setReadOnly(True)
        self.captions_view.setPlaceholderText("Live Captions appear here as Deepgram returns them.")
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.viewer_panel)
        self.right_stack.addWidget(self.captions_view)

        self._bridge = EventBridge(self)
        self._bridge.event.connect(self._on_core_event)
        self._core.subscribe(self._bridge.event.emit)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.right_stack)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addLayout(capture_row)
        layout.addWidget(splitter, stretch=1)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.refresh_library()
        self.refresh_queue()

    # ---- Capture -------------------------------------------------------------------------

    @Slot()
    def _toggle_capture(self) -> None:
        if self._core.capture_status().state is CaptureState.CAPTURING:
            self._stop_capture()
        else:
            self._start_capture()

    def _start_capture(self) -> None:
        kind = SourceKind(self.source_kind.currentData())  # Qt hands a StrEnum back as a plain str
        try:
            status = self._core.start_capture(kind, self.title_edit.text())
        except KeyProblem as e:
            self.status.setText(e.message)
            self.open_settings(e.message)
            return
        except CaptureError as e:
            self.status.setText(e.message)
            return
        self.capture_button.setText("Stop")
        self.source_kind.setEnabled(False)
        self.title_edit.setEnabled(False)
        self.status.setText(f"Capturing {status.title} from the {kind.label}.")
        self.captions_view.clear()
        self.right_stack.setCurrentWidget(self.captions_view)
        self._tick()
        self._elapsed_timer.start()

    def _stop_capture(self) -> None:
        self._elapsed_timer.stop()
        self.capture_button.setEnabled(False)
        self.capture_indicator.setText("Finalising…")
        self.status.setText("Sending the Recording to Deepgram…")
        worker = CoreWorker(self._core.stop_capture, parent=self)
        worker.ready.connect(self._on_capture_ready)
        worker.failed.connect(self._on_capture_failed)
        worker.key_problem.connect(self.open_settings)
        self._capture_worker = worker
        worker.start()

    @Slot()
    def _tick(self) -> None:
        started = self._core.capture_status().started_at
        if started is None:
            return
        elapsed = (datetime.now() - started).total_seconds()
        text = f"● Capturing {format_timestamp(max(0.0, elapsed))}"
        if self._core.live_captions().reconnecting:
            text += " · reconnecting…"
        self.capture_indicator.setText(text)
        self.capture_indicator.setStyleSheet("color: firebrick; font-weight: bold;")

    @Slot(object)
    def _on_core_event(self, event: object) -> None:
        if isinstance(event, CaptionsChanged):
            self._show_captions(event.live)
            if self._core.capture_status().state is CaptureState.CAPTURING:
                self._tick()
        elif isinstance(event, ApiKeyProblem):
            self.open_settings(event.message)

    # ---- Settings ------------------------------------------------------------------------

    def open_settings(self, message: str | None = None) -> SettingsDialog:
        """Show the settings dialog, with a message when a key problem brought the user here."""
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            if message:
                self._settings_dialog.message.setText(message)
                self._settings_dialog.message.setVisible(True)
            self._settings_dialog.raise_()
            return self._settings_dialog
        dialog = SettingsDialog(self._core, self, message=message)
        dialog.accepted.connect(self._on_settings_saved)
        self._settings_dialog = dialog
        dialog.open()
        return dialog

    @Slot()
    def _on_settings_saved(self) -> None:
        settings = self._core.settings()
        self.timestamps_toggle.setChecked(settings.include_timestamps)
        self.speakers_toggle.setChecked(settings.include_speaker_labels)
        self.status.setText("Settings saved." + ("" if self._core.api_key() else " No API key is configured yet."))

    def _show_captions(self, live: LiveCaptions) -> None:
        meeting = self._core.capture_status().kind is SourceKind.MEETING
        lines = [f"<b>{self._caption_speaker(c, meeting)}:</b> {html_escape(c.text)}" for c in live.final]
        if live.provisional is not None:
            lines.append(
                f'<span style="color: gray;"><i>{self._caption_speaker(live.provisional, meeting)}: '
                f"{html_escape(live.provisional.text)}</i></span>"
            )
        if live.reconnecting:
            lines.append('<span style="color: firebrick;">Reconnecting to Deepgram… the Recording continues.</span>')
        self.captions_view.setHtml("<br>".join(lines))
        self.captions_view.verticalScrollBar().setValue(self.captions_view.verticalScrollBar().maximum())

    @staticmethod
    def _caption_speaker(caption, meeting: bool) -> str:
        """Live Captions label: in a Meeting the Microphone channel is You and System Audio speakers are numbered."""
        if meeting and caption.channel == 0:
            return "You"
        if caption.speaker is None:
            return "Speaker"
        return f"Speaker {caption.speaker + 1}"

    @Slot(object)
    def _on_capture_ready(self, transcript: Transcript) -> None:
        self._reset_capture_controls()
        self.title_edit.clear()
        self.refresh_library(select_id=transcript.id)
        if transcript.status is TranscriptStatus.PROVISIONAL:
            self.status.setText(
                "Deepgram could not transcribe the Recording, so it was kept. "
                "The Live Captions are shown as a provisional Transcript; press Retry when you are back online."
            )
        else:
            self.status.setText(f"Saved to {transcript.rendering_path}")

    @Slot(str)
    def _on_capture_failed(self, message: str) -> None:
        self._reset_capture_controls()
        self.status.setText(f"{message} The Recording was kept.")

    def _reset_capture_controls(self) -> None:
        self.capture_button.setText("Start capture")
        self.capture_button.setEnabled(True)
        self.source_kind.setEnabled(True)
        self.title_edit.setEnabled(True)
        self.capture_indicator.setText("")
        self.capture_indicator.setStyleSheet("")
        self.right_stack.setCurrentWidget(self.viewer_panel)

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
            if t.status is TranscriptStatus.PROVISIONAL:
                summary += " · provisional"
            if entry.missing_files:
                summary += " · not found"
            row = QListWidgetItem(f"{t.title}\n{summary}")
            row.setData(ID_ROLE, t.id)
            if entry.missing_files:
                row.setForeground(QColor("gray"))
            elif t.status is TranscriptStatus.PROVISIONAL:
                row.setForeground(QColor("darkorange"))
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
        provisional = has_entry and entry.transcript.status is TranscriptStatus.PROVISIONAL
        self.open_folder_button.setEnabled(has_entry and entry.transcript.rendering_path is not None)
        self.open_file_button.setEnabled(has_entry and entry.rendering_found and entry.transcript.rendering_path is not None)
        self.retranscribe_button.setText("Retry" if provisional else "Re-transcribe")
        self.retranscribe_button.setEnabled(has_entry and entry.can_retranscribe)
        self.remove_button.setEnabled(has_entry)
        for b in (self.copy_button, self.rename_button, self.rename_speaker_button):
            b.setEnabled(has_entry)
        if entry is None:
            self.viewer.clear()
            return
        self.viewer.setPlainText(render(entry.transcript, self._viewer_options()))
        missing = []
        if not entry.rendering_found:
            missing.append("Rendering file not found")
        if not entry.source_found:
            missing.append("source file not found")
        if not entry.recording_found:
            missing.append("Recording not found, so it cannot be retried")
        if missing:
            self.status.setText(", ".join(missing).capitalize() + ". The Transcript is still kept by the app.")
        elif provisional:
            self.status.setText(
                "Provisional: this is what the Live Captions heard. Press Retry to send the kept Recording to Deepgram."
            )
        else:
            self.status.setText(f"{entry.transcript.rendering_path}")

    # ---- Rendering toggles, Copy, renaming -----------------------------------------------

    def _viewer_options(self) -> RenderingOptions:
        return RenderingOptions(self.timestamps_toggle.isChecked(), self.speakers_toggle.isChecked())

    @Slot()
    def _rerender(self) -> None:
        """The toggles change only what is shown; the Rendering file and the defaults are untouched."""
        entry = self.selected_entry()
        if entry is not None:
            self.viewer.setPlainText(render(entry.transcript, self._viewer_options()))

    @Slot()
    def _make_default(self) -> None:
        options = self._viewer_options()
        self._core.update_settings(
            include_timestamps=options.include_timestamps, include_speaker_labels=options.include_speaker_labels
        )
        self.status.setText("New Rendering files will use these toggles. Existing files are unchanged.")

    @Slot()
    def _copy(self) -> None:
        QApplication.clipboard().setText(self.viewer.toPlainText())
        self.status.setText("Copied the Rendering to the clipboard.")

    @Slot()
    def _rename_transcript(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        title, ok = QInputDialog.getText(self, "Rename Transcript", "Title:", text=entry.transcript.title)
        if ok and title.strip():
            renamed = self._core.rename_transcript(entry.transcript.id, title)
            self.refresh_library(select_id=renamed.id)

    @Slot()
    def _rename_speaker(self) -> None:
        entry = self.selected_entry()
        if entry is None or not entry.transcript.speaker_names:
            return
        names = entry.transcript.speaker_names
        choices = [names[s] for s in sorted(names)]
        current, ok = QInputDialog.getItem(self, "Rename speaker", "Which speaker?", choices, 0, editable=False)
        if not ok:
            return
        speaker = sorted(names)[choices.index(current)]
        name, ok = QInputDialog.getText(self, "Rename speaker", f"New name for {current}:", text=current)
        if ok and name.strip():
            renamed = self._core.rename_speaker(entry.transcript.id, speaker, name)
            self.refresh_library(select_id=renamed.id)

    # ---- Library actions -----------------------------------------------------------------

    @Slot()
    def _retranscribe(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        verb = "Retrying" if entry.transcript.status is TranscriptStatus.PROVISIONAL else "Re-transcribing"
        self._run(f"{verb} {entry.transcript.title}…", lambda: self._core.retranscribe(entry.transcript.id))

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
        self._worker.key_problem.connect(self.open_settings)
        self._worker.start()

    @Slot(object)
    def _on_ready(self, transcript: Transcript) -> None:
        self.refresh_library(select_id=transcript.id)
        if transcript.status is TranscriptStatus.PROVISIONAL:
            self.status.setText("Still could not reach Deepgram. The Recording is kept; try again later.")
        else:
            self.status.setText(f"Saved to {transcript.rendering_path}")

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self._show_entry(self.selected_entry())
