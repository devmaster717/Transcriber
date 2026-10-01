"""Domain model. Vocabulary follows CONTEXT.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class SourceKind(StrEnum):
    """Where the audio for a Transcript came from."""

    FILE = "file"
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"
    MEETING = "meeting"

    @property
    def label(self) -> str:
        return {
            SourceKind.FILE: "File",
            SourceKind.MICROPHONE: "Microphone",
            SourceKind.SYSTEM_AUDIO: "System Audio",
            SourceKind.MEETING: "Meeting Capture",
        }[self]


class TranscriptStatus(StrEnum):
    COMPLETE = "complete"
    PROVISIONAL = "provisional"
    FAILED = "failed"


@dataclass(frozen=True)
class Paragraph:
    """One Speaker turn: a stretch of speech attributed to a single Speaker.

    `channel` is which audio channel it was heard on. In a Meeting Capture channel 0 is the
    Microphone (the Speaker "You") and channel 1 is System Audio (everyone else).
    """

    start: float
    end: float
    speaker: int
    text: str
    channel: int = 0


def default_speaker_name(speaker: int) -> str:
    return f"Speaker {speaker + 1}"


@dataclass
class Transcript:
    """The full result for one piece of audio. A Rendering is derived from this, never the reverse."""

    id: str
    title: str
    created: datetime
    duration: float
    source_kind: SourceKind
    source_path: Path | None
    paragraphs: list[Paragraph]
    speaker_names: dict[int, str] = field(default_factory=dict)
    status: TranscriptStatus = TranscriptStatus.COMPLETE
    rendering_path: Path | None = None
    recording_path: Path | None = None

    def speaker_name(self, speaker: int) -> str:
        return self.speaker_names.get(speaker, default_speaker_name(speaker))
