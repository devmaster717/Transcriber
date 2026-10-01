"""Composition root: builds the core with real adapters and shows the main window."""

from __future__ import annotations

import sys
from pathlib import Path

from platformdirs import user_data_dir
from PySide6.QtWidgets import QApplication

from ..core.app import TranscriberCore
from ..core.capture import AudioCapture
from ..core.credentials import CredentialStore, KeyringCredentialStore, NoCredentialStore
from ..deepgram_gateway import DeepgramGateway
from .main_window import MainWindow

APP_NAME = "Transcriber"


def build_audio_capture() -> AudioCapture | None:
    if sys.platform != "win32":
        return None  # ADR-0001: only the Windows implementation exists so far
    from ..audio.wasapi import WasapiCapture

    return WasapiCapture()


def build_credentials() -> CredentialStore:
    try:
        import keyring

        keyring.get_keyring()
        return KeyringCredentialStore()
    except Exception:  # noqa: BLE001 - no usable backend: the env var still works
        return NoCredentialStore()


def build_core() -> TranscriberCore:
    holder: dict[str, TranscriberCore] = {}
    gateway = DeepgramGateway(key_provider=lambda: holder["core"].api_key())
    core = TranscriberCore(
        gateway=gateway,
        audio_capture=build_audio_capture(),
        data_dir=Path(user_data_dir(APP_NAME, appauthor=False)),
        credentials=build_credentials(),
    )
    holder["core"] = core
    return core


def run(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow(build_core())
    window.show()
    if window._core.api_key() is None:
        window.open_settings("Welcome. Enter your Deepgram API key to get started.")
    return app.exec()
