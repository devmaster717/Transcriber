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
    """Returns a canned result, or fails on command. Records what it was asked to transcribe."""

    def __init__(self, result: TranscriptionResult | None = None) -> None:
        self.result = result or canned_result()
        self.fail_with: Exception | None = None
        self.requests: list[Path] = []

    def transcribe(self, audio: Path) -> TranscriptionResult:
        self.requests.append(audio)
        if self.fail_with is not None:
            raise self.fail_with
        return self.result


__all__ = ["FakeTranscriptionGateway", "TranscriptionError", "canned_result"]
