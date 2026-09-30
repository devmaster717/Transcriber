"""TranscriberCore: the public API of the headless core."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .gateway import TranscriptionGateway
from .model import SourceKind, Transcript, default_speaker_name
from .rendering import render
from .store import TranscriptStore

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class TranscriptReady:
    """A Transcript and its Rendering have been written."""

    transcript: Transcript


Event = TranscriptReady
EventHandler = Callable[[Event], None]


class TranscriberCore:
    def __init__(
        self,
        gateway: TranscriptionGateway,
        data_dir: Path,
        clock: Clock = datetime.now,
    ) -> None:
        self._gateway = gateway
        self._store = TranscriptStore(data_dir)
        self._clock = clock
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Receive every event the core emits. Handlers run on the thread that did the work."""
        self._handlers.append(handler)

    def _emit(self, event: Event) -> None:
        for handler in list(self._handlers):
            handler(event)

    def transcribe_file(self, path: Path) -> Transcript:
        """Transcribe a File Source, persist the Transcript, and return it."""
        result = self._gateway.transcribe(path)
        speakers = sorted({p.speaker for p in result.paragraphs})
        transcript = Transcript(
            id=uuid.uuid4().hex,
            title=path.stem,
            created=self._clock(),
            duration=result.duration,
            source_kind=SourceKind.FILE,
            source_path=path,
            paragraphs=list(result.paragraphs),
            speaker_names={s: default_speaker_name(s) for s in speakers},
            rendering_path=path.with_suffix(".txt"),
        )
        transcript.rendering_path.write_text(render(transcript), encoding="utf-8")
        self._store.save(transcript)
        self._emit(TranscriptReady(transcript))
        return transcript

    def get_transcript(self, transcript_id: str) -> Transcript:
        return self._store.load(transcript_id)
