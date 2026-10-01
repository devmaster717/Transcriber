"""The settings dialog: API key, devices, transcripts folder, Rendering defaults."""

from __future__ import annotations

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.app import TranscriberCore
from ..core.capture import DeviceKind

WINDOWS_DEFAULT = "(Windows default)"


class SettingsDialog(QDialog):
    def __init__(self, core: TranscriberCore, parent: QWidget | None = None, message: str | None = None) -> None:
        super().__init__(parent)
        self._core = core
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        settings = core.settings()

        layout = QVBoxLayout(self)
        self.message = QLabel(message or "")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("color: firebrick;")
        self.message.setVisible(bool(message))
        layout.addWidget(self.message)

        form = QFormLayout()

        # API key: stored in the OS credential manager, never in a file the app writes.
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste a Deepgram API key to store it")
        self.key_status = QLabel()
        self.key_status.setWordWrap(True)
        self.forget_key = QCheckBox("Forget the stored key")
        key_box = QVBoxLayout()
        key_box.addWidget(self.key_edit)
        key_box.addWidget(self.key_status)
        key_box.addWidget(self.forget_key)
        form.addRow("Deepgram API key", key_box)
        self._refresh_key_status()

        # Devices
        self.microphone = QComboBox()
        self.output = QComboBox()
        self._fill_devices(self.microphone, DeviceKind.INPUT, settings.microphone_device_id)
        self._fill_devices(self.output, DeviceKind.LOOPBACK, settings.output_device_id)
        form.addRow("Microphone", self.microphone)
        form.addRow("System Audio from", self.output)

        # Transcripts folder
        self.folder_edit = QLineEdit(settings.transcripts_dir or str(core.transcripts_dir()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, stretch=1)
        folder_row.addWidget(browse)
        form.addRow("Capture transcripts folder", folder_row)

        # Rendering defaults
        self.timestamps = QCheckBox("Include timestamps")
        self.timestamps.setChecked(settings.include_timestamps)
        self.speakers = QCheckBox("Include speaker labels")
        self.speakers.setChecked(settings.include_speaker_labels)
        defaults = QVBoxLayout()
        defaults.addWidget(self.timestamps)
        defaults.addWidget(self.speakers)
        form.addRow("New Rendering files", defaults)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_key_status(self) -> None:
        if self._core.has_stored_api_key():
            text = "A key is stored in Windows Credential Manager."
        elif self._core.api_key():
            text = "Using the DEEPGRAM_API_KEY environment variable. Paste a key here to store it instead."
        else:
            text = "No key yet. Deepgram calls will be refused until one is entered."
        self.key_status.setText(text)
        self.forget_key.setVisible(self._core.has_stored_api_key())

    def _fill_devices(self, combo: QComboBox, kind: DeviceKind, chosen: str | None) -> None:
        combo.addItem(WINDOWS_DEFAULT, None)
        for device in self._core.list_devices():
            if device.kind is kind:
                combo.addItem(device.name, device.id)
        index = combo.findData(chosen) if chosen else -1
        combo.setCurrentIndex(index if index >= 0 else 0)

    @Slot()
    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Capture transcripts folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def accept(self) -> None:  # noqa: D102 - Qt override
        if self.forget_key.isChecked():
            self._core.set_api_key("")
        elif self.key_edit.text().strip():
            self._core.set_api_key(self.key_edit.text())
        self._core.update_settings(
            microphone_device_id=self.microphone.currentData(),
            output_device_id=self.output.currentData(),
            transcripts_dir=self.folder_edit.text().strip() or None,
            include_timestamps=self.timestamps.isChecked(),
            include_speaker_labels=self.speakers.isChecked(),
        )
        super().accept()
