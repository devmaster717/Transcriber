"""Test doubles for the two edges the core cannot own (see the spec's Testing Decisions)."""

from __future__ import annotations

from pathlib import Path

from transcriber.core.gateway import TranscriptionError, TranscriptionResult
from transcriber.core.model import Paragraph


def canned_result() -> TranscriptionResult:
    """A small diarized result: the worked example from the spec's Rendering section."""
    return TranscriptionResult(
        duration=12.5,
        paragraphs=[
            Paragraph(start=0.4, end=3.1, speaker=0, text="Morning everyone, can you hear me?"),
            Paragraph(start=3.5, end=5.0, speaker=1, text="Yes, loud and clear."),
        ],
    )


class FakeTranscriptionGateway:
    """Returns a canned result, or fails on command. Records what it was asked to transcribe.

    `fail_with` fails every request; `fail_for[path]` fails just that path. Both are read at
    request time, so a test can clear them to model a retry that succeeds.
    """

    def __init__(self, result: TranscriptionResult | None = None) -> None:
        self.result = result or canned_result()
        self.fail_with: Exception | None = None
        self.fail_for: dict[Path, Exception] = {}
        self.requests: list[Path] = []

    def transcribe(self, audio: Path) -> TranscriptionResult:
        self.requests.append(audio)
        if audio in self.fail_for:
            raise self.fail_for[audio]
        if self.fail_with is not None:
            raise self.fail_with
        return self.result


__all__ = ["FakeTranscriptionGateway", "TranscriptionError", "canned_result"]
