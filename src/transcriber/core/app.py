"""TranscriberCore: the public API of the headless core."""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .capture import (
    AudioCapture,
    AudioDevice,
    CaptureError,
    CaptureHandle,
    CaptureState,
    CaptureStatus,
    DeviceKind,
    Recorder,
)
from .gateway import MAX_FILE_BYTES, TranscriptionError, TranscriptionGateway, TranscriptionResult
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


@dataclass(frozen=True)
class CaptureChanged:
    """The running Capture changed state."""

    status: CaptureStatus


Event = TranscriptReady | QueueChanged | CaptureChanged
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


@dataclass
class _ActiveCapture:
    id: str
    kind: SourceKind
    title: str
    started_at: datetime
    device: AudioDevice
    recorder: Recorder
    handle: CaptureHandle


def default_transcripts_dir() -> Path:
    return Path.home() / "Documents" / "Transcriber"


class TranscriberCore:
    def __init__(
        self,
        gateway: TranscriptionGateway,
        data_dir: Path,
        audio_capture: AudioCapture | None = None,
        transcripts_dir: Path | None = None,
        clock: Clock = datetime.now,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self._gateway = gateway
        self._audio = audio_capture
        self._store = TranscriptStore(data_dir)
        self._recordings_dir = data_dir / "recordings"
        self._transcripts_dir = transcripts_dir or default_transcripts_dir()
        self._clock = clock
        self._max_file_bytes = max_file_bytes
        self._queue = FileQueue()
        self._handlers: list[EventHandler] = []
        self._capture: _ActiveCapture | None = None
        self._capture_status = CaptureStatus(CaptureState.IDLE)
        self._capture_lock = threading.Lock()

    def subscribe(self, handler: EventHandler) -> None:
        """Receive every event the core emits. Handlers run on the thread that did the work."""
        self._handlers.append(handler)

    def _emit(self, event: Event) -> None:
        for handler in list(self._handlers):
            handler(event)

    # ---- Files ---------------------------------------------------------------------------

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

    # ---- Capture -------------------------------------------------------------------------

    def list_devices(self) -> list[AudioDevice]:
        return self._audio.list_devices() if self._audio is not None else []

    def capture_status(self) -> CaptureStatus:
        return self._capture_status

    def start_capture(self, kind: SourceKind = SourceKind.MICROPHONE, title: str | None = None) -> CaptureStatus:
        """Begin a Capture from the default device for `kind`. Only one Capture runs at a time."""
        kind = SourceKind(kind)
        if self._audio is None:
            raise CaptureError("Audio capture is not available on this platform.")
        if kind is not SourceKind.MICROPHONE:
            raise CaptureError(f"{kind.label} capture is not available yet.")
        with self._capture_lock:
            if self._capture_status.state in (CaptureState.CAPTURING, CaptureState.FINALISING):
                raise CaptureError("A Capture is already running.")
            device = self._audio.default_device(DeviceKind.INPUT)
            if device is None:
                raise CaptureError("No microphone was found.")
            started = self._clock()
            capture_id = uuid.uuid4().hex
            clean_title = (title or "").strip() or started.strftime("%Y-%m-%d %H:%M")
            recorder = Recorder(self._recordings_dir / f"{capture_id}.wav")
            handle = self._audio.open(device, recorder.write)
            self._capture = _ActiveCapture(capture_id, kind, clean_title, started, device, recorder, handle)
            status = CaptureStatus(CaptureState.CAPTURING, clean_title, started, recorder.path)
            self._capture_status = status
        self._emit(CaptureChanged(status))
        return status

    def stop_capture(self) -> Transcript:
        """Stop the running Capture and produce its Transcript from the Recording.

        The Recording is deleted only once the Transcript and its Rendering are written (ADR-0002).
        """
        with self._capture_lock:
            active = self._capture
            if active is None or self._capture_status.state is not CaptureState.CAPTURING:
                raise CaptureError("No Capture is running.")
            active.handle.stop()
            active.recorder.close()
            status = CaptureStatus(CaptureState.FINALISING, active.title, active.started_at, active.recorder.path)
            self._capture_status = status
        self._emit(CaptureChanged(status))
        try:
            result = self._gateway.transcribe(active.recorder.path)
        except TranscriptionError:
            with self._capture_lock:
                self._capture = None
                self._capture_status = CaptureStatus(CaptureState.IDLE)
            self._emit(CaptureChanged(self._capture_status))
            raise
        transcript = self._finish(
            transcript_id=active.id,
            title=active.title,
            kind=active.kind,
            source_path=None,
            rendering_path=self._capture_rendering_path(active.started_at, active.title),
            result=result,
        )
        active.recorder.path.unlink(missing_ok=True)
        with self._capture_lock:
            self._capture = None
            self._capture_status = CaptureStatus(
                CaptureState.COMPLETE, active.title, active.started_at, transcript_id=transcript.id
            )
        self._emit(CaptureChanged(self._capture_status))
        return transcript

    def _capture_rendering_path(self, started: datetime, title: str) -> Path:
        stamp = started.strftime("%Y-%m-%d %H-%M")
        if title == started.strftime("%Y-%m-%d %H:%M"):
            name = stamp
        else:
            safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", title).strip(" .")
            name = f"{stamp} {safe}" if safe else stamp
        return self._transcripts_dir / f"{name}.txt"

    # ---- Library -------------------------------------------------------------------------

    def get_transcript(self, transcript_id: str) -> Transcript:
        return self._store.load(transcript_id)

    def library(self) -> list[LibraryEntry]:
        """Every Transcript the app has produced, newest first, with a check that its files still exist."""
        transcripts = sorted(self._store.load_all(), key=lambda t: t.created, reverse=True)
        return [LibraryEntry.for_transcript(t) for t in transcripts]

    def remove(self, transcript_id: str) -> None:
        """Forget a Transcript. Its Rendering and source File are left where they are."""
        self._store.delete(transcript_id)

    # ---- Internals -----------------------------------------------------------------------

    def _transcribe_source(self, transcript_id: str, path: Path) -> Transcript:
        if path.stat().st_size > self._max_file_bytes:
            raise TranscriptionError("File is larger than Deepgram's 2 GB limit.")
        result = self._gateway.transcribe(path)
        return self._finish(
            transcript_id=transcript_id,
            title=path.stem,
            kind=SourceKind.FILE,
            source_path=path,
            rendering_path=path.with_suffix(".txt"),
            result=result,
        )

    def _finish(
        self,
        *,
        transcript_id: str,
        title: str,
        kind: SourceKind,
        source_path: Path | None,
        rendering_path: Path,
        result: TranscriptionResult,
    ) -> Transcript:
        """Turn a gateway result into a persisted Transcript with its Rendering written."""
        speakers = sorted({p.speaker for p in result.paragraphs})
        transcript = Transcript(
            id=transcript_id,
            title=title,
            created=self._clock(),
            duration=result.duration,
            source_kind=kind,
            source_path=source_path,
            paragraphs=list(result.paragraphs),
            speaker_names={s: default_speaker_name(s) for s in speakers},
            rendering_path=rendering_path,
        )
        rendering_path.parent.mkdir(parents=True, exist_ok=True)
        rendering_path.write_text(render(transcript), encoding="utf-8")
        self._store.save(transcript)
        self._emit(TranscriptReady(transcript))
        return transcript

    def _find_complete_for_source(self, path: Path) -> Transcript | None:
        for t in self._store.load_all():
            if t.status is TranscriptStatus.COMPLETE and t.source_path == path:
                return t
        return None
