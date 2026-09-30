"""TranscriberCore: the public API of the headless core."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .gateway import MAX_FILE_BYTES, TranscriptionError, TranscriptionGateway
from .model import SourceKind, Transcript, TranscriptStatus, default_speaker_name
from .queue import FileQueue, QueueItem, QueueState
from .rendering import render
from .store import TranscriptStore

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class TranscriptReady:
    """A Transcript and its Rendering have been written."""

    transcript: Transcript


@dataclass(frozen=True)
class QueueChanged:
    """A queued File changed state."""

    item: QueueItem


Event = TranscriptReady | QueueChanged
EventHandler = Callable[[Event], None]


@dataclass(frozen=True)
class LibraryEntry:
    """One row of the Library: a Transcript plus whether the files it points at are still there."""

    transcript: Transcript
    rendering_found: bool
    source_found: bool

    @property
    def missing_files(self) -> bool:
        return not (self.rendering_found and self.source_found)

    @classmethod
    def for_transcript(cls, t: Transcript) -> LibraryEntry:
        return cls(
            transcript=t,
            rendering_found=t.rendering_path is None or t.rendering_path.exists(),
            source_found=t.source_path is None or t.source_path.exists(),
        )


class TranscriberCore:
    def __init__(
        self,
        gateway: TranscriptionGateway,
        data_dir: Path,
        clock: Clock = datetime.now,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self._gateway = gateway
        self._store = TranscriptStore(data_dir)
        self._clock = clock
        self._max_file_bytes = max_file_bytes
        self._queue = FileQueue()
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Receive every event the core emits. Handlers run on the thread that did the work."""
        self._handlers.append(handler)

    def _emit(self, event: Event) -> None:
        for handler in list(self._handlers):
            handler(event)

    def transcribe_file(self, path: Path) -> Transcript:
        """Transcribe a File Source, persist the Transcript, and return it.

        A File that already has a complete Transcript is not sent again; the existing
        Transcript is returned. Use `retranscribe` to force a fresh one.
        """
        existing = self._find_complete_for_source(path)
        if existing is not None:
            self._emit(TranscriptReady(existing))
            return existing
        return self._transcribe_source(uuid.uuid4().hex, path)

    def retranscribe(self, transcript_id: str) -> Transcript:
        """Send a File Transcript's source through the gateway again, replacing the Transcript in place."""
        previous = self._store.load(transcript_id)
        if previous.source_path is None:
            raise ValueError("Only a File Transcript can be re-transcribed; a Capture's Recording is gone.")
        return self._transcribe_source(previous.id, previous.source_path)

    # ---- File queue ----------------------------------------------------------------------

    def enqueue(self, paths: list[Path]) -> list[QueueItem]:
        """Add Files to the queue. They wait until `process_next` reaches them."""
        items = self._queue.add(paths)
        for item in items:
            self._emit(QueueChanged(item))
        return items

    def queue(self) -> list[QueueItem]:
        return self._queue.items()

    def process_next(self) -> QueueItem | None:
        """Transcribe the first waiting File. Returns its final item, or None if nothing waits.

        A failure marks only that item failed, with a message; the rest of the queue is untouched.
        """
        item = self._queue.claim_next()
        if item is None:
            return None
        self._emit(QueueChanged(item))
        try:
            transcript = self.transcribe_file(item.path)
        except TranscriptionError as e:
            item = self._queue.update(item.id, state=QueueState.FAILED, error=e.message)
        except OSError as e:
            item = self._queue.update(item.id, state=QueueState.FAILED, error=f"Could not read the file: {e.strerror or e}")
        else:
            item = self._queue.update(item.id, state=QueueState.DONE, transcript_id=transcript.id)
        self._emit(QueueChanged(item))
        return item

    def retry(self, item_id: str) -> QueueItem:
        """Put a failed item back in line, in its original position."""
        item = self._queue.update(item_id, state=QueueState.WAITING, error=None)
        self._emit(QueueChanged(item))
        return item

    # ---- Internals -----------------------------------------------------------------------

    def _transcribe_source(self, transcript_id: str, path: Path) -> Transcript:
        if path.stat().st_size > self._max_file_bytes:
            raise TranscriptionError("File is larger than Deepgram's 2 GB limit.")
        result = self._gateway.transcribe(path)
        speakers = sorted({p.speaker for p in result.paragraphs})
        transcript = Transcript(
            id=transcript_id,
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

    def library(self) -> list[LibraryEntry]:
        """Every Transcript the app has produced, newest first, with a check that its files still exist."""
        transcripts = sorted(self._store.load_all(), key=lambda t: t.created, reverse=True)
        return [LibraryEntry.for_transcript(t) for t in transcripts]

    def remove(self, transcript_id: str) -> None:
        """Forget a Transcript. Its Rendering and source File are left where they are."""
        self._store.delete(transcript_id)

    def _find_complete_for_source(self, path: Path) -> Transcript | None:
        for t in self._store.load_all():
            if t.status is TranscriptStatus.COMPLETE and t.source_path == path:
                return t
        return None
