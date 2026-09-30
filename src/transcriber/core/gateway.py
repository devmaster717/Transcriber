"""The transcription gateway seam: the one place the core talks to Deepgram.

The core depends only on this interface. The real adapter lives outside the core;
tests substitute a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .model import Paragraph


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


class TranscriptionGateway(Protocol):
    def transcribe(self, audio: Path) -> TranscriptionResult:
        """Transcribe a whole audio file in one request (the pre-recorded operation)."""
        ...
