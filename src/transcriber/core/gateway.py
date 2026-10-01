"""The transcription gateway seam: the one place the core talks to Deepgram.

The core depends only on this interface. The real adapter lives outside the core;
tests substitute a fake.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .model import Paragraph

MAX_FILE_BYTES = 2 * 1024**3  # Deepgram's pre-recorded limit; the core refuses larger Files before any request


class TranscriptionError(Exception):
    """The gateway could not produce a result. `message` is safe to show to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RejectedKeyError(TranscriptionError):
    """Deepgram rejected the API key (401 or 403)."""


@dataclass(frozen=True)
class TranscriptionResult:
    """What the gateway returns for one piece of audio, already in domain terms."""

    duration: float
    paragraphs: list[Paragraph]


@dataclass(frozen=True)
class Caption:
    """One piece of Live Captions text. Provisional captions are replaced; final ones accumulate."""

    text: str
    is_final: bool
    start: float
    speaker: int | None = None
    channel: int = 0


class LiveStream(Protocol):
    def send(self, chunk: bytes) -> None:
        """Send 16 kHz int16 PCM. Safe to call from the capture thread."""
        ...

    def close(self) -> None:
        """Tell the service the audio is over and release the connection."""
        ...


OnCaption = Callable[[Caption], None]
OnStreamError = Callable[[str], None]


class TranscriptionGateway(Protocol):
    def transcribe(self, audio: Path) -> TranscriptionResult:
        """Transcribe a whole audio file in one request (the pre-recorded operation)."""
        ...

    def open_stream(self, on_caption: OnCaption, on_error: OnStreamError, channels: int = 1) -> LiveStream:
        """Open a live connection. Captions and a terminal error arrive on the gateway's own thread."""
        ...
