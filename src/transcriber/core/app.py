"""TranscriberCore: the public API of the headless core."""

from __future__ import annotations

import functools
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
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
    LiveCaptions,
)
from .credentials import CredentialStore, NoCredentialStore
from .gateway import (
    MAX_FILE_BYTES,
    Caption,
    KeyProblem,
    LiveStream,
    MissingKeyError,
    TranscriptionError,
    TranscriptionGateway,
    TranscriptionResult,
)
from .mixer import ChannelMixer
from .model import Paragraph, SourceKind, Transcript, TranscriptStatus, default_speaker_name
from .queue import FileQueue, QueueItem, QueueState
from .rendering import RenderingOptions, render
from .settings import Settings, SettingsStore
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


@dataclass(frozen=True)
class CaptionsChanged:
    """The Live Captions changed: a new caption arrived or the connection state flipped."""

    live: LiveCaptions


@dataclass(frozen=True)
class ApiKeyProblem:
    """Deepgram rejected the key, or there is none. The settings screen is the fix."""

    message: str


Event = TranscriptReady | QueueChanged | CaptureChanged | CaptionsChanged | ApiKeyProblem
API_KEY_ENV = "DEEPGRAM_API_KEY"
EventHandler = Callable[[Event], None]

RECONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0, 8.0, 10.0)

# Which devices each Capture kind listens to, in channel order. A Meeting Capture is two channels:
# 0 is the Microphone (the Speaker "You"), 1 is System Audio (everyone else).
CAPTURE_DEVICE_KINDS: dict[SourceKind, tuple[DeviceKind, ...]] = {
    SourceKind.MICROPHONE: (DeviceKind.INPUT,),
    SourceKind.SYSTEM_AUDIO: (DeviceKind.LOOPBACK,),
    SourceKind.MEETING: (DeviceKind.INPUT, DeviceKind.LOOPBACK),
}

YOU = "You"


def attribute_speakers(kind: SourceKind, paragraphs: list[Paragraph]) -> tuple[list[Paragraph], dict[int, str]]:
    """Give every paragraph a Speaker id that is unique across channels, with default names.

    Deepgram numbers speakers per channel. For a Meeting Capture the Microphone channel is one
    Speaker, "You" (id 0), and System Audio speakers are numbered from 1. Paragraphs come back in
    time order across channels.
    """
    if kind is not SourceKind.MEETING:
        ordered = sorted(paragraphs, key=lambda p: p.start)
        return ordered, {s: default_speaker_name(s) for s in sorted({p.speaker for p in ordered})}
    attributed = []
    for p in sorted(paragraphs, key=lambda p: p.start):
        speaker = 0 if p.channel == 0 else p.speaker + 1
        attributed.append(replace(p, speaker=speaker))
    names = {0: YOU} if any(p.speaker == 0 for p in attributed) else {}
    for p in attributed:
        if p.speaker > 0:
            names[p.speaker] = f"Speaker {p.speaker}"
    return attributed, names


@dataclass(frozen=True)
class LibraryEntry:
    """One row of the Library: a Transcript plus whether the files it points at are still there."""

    transcript: Transcript
    rendering_found: bool
    source_found: bool
    recording_found: bool = True

    @property
    def missing_files(self) -> bool:
        return not (self.rendering_found and self.source_found and self.recording_found)

    @property
    def can_retranscribe(self) -> bool:
        """Only a File with its source present; a Capture's Transcript came from the live stream (ADR-0004)."""
        return self.transcript.source_path is not None and self.source_found

    @classmethod
    def for_transcript(cls, t: Transcript) -> LibraryEntry:
        return cls(
            transcript=t,
            rendering_found=t.rendering_path is None or t.rendering_path.exists(),
            source_found=t.source_path is None or t.source_path.exists(),
            recording_found=t.recording_path is None or t.recording_path.exists(),
        )


@dataclass
class _ActiveCapture:
    id: str
    kind: SourceKind
    title: str
    started_at: datetime
    devices: list[AudioDevice]
    channels: int = 1
    handles: list[CaptureHandle] = field(default_factory=list)
    mixer: ChannelMixer | None = None
    stream: LiveStream | None = None
    stream_offset: float = 0.0  # seconds into the Capture when the current connection opened


def _captions_to_result(live: LiveCaptions, duration: float) -> TranscriptionResult:
    """The Live Captions as a transcription result: one paragraph per line, each ending where the next begins."""
    lines = list(live.final)
    paragraphs = []
    for i, c in enumerate(lines):
        end = lines[i + 1].start if i + 1 < len(lines) else duration
        paragraphs.append(
            Paragraph(start=c.start, end=max(end, c.start), speaker=c.speaker or 0, text=c.text, channel=c.channel)
        )
    return TranscriptionResult(duration=duration, paragraphs=paragraphs)


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
        sleeper: Callable[[float], None] = time.sleep,
        credentials: CredentialStore | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._gateway = gateway
        self._audio = audio_capture
        self._credentials = credentials if credentials is not None else NoCredentialStore()
        self._env = env if env is not None else os.environ
        self._store = TranscriptStore(data_dir)
        self._settings = SettingsStore(data_dir)
        self._transcripts_dir = transcripts_dir or default_transcripts_dir()
        self._clock = clock
        self._max_file_bytes = max_file_bytes
        self._sleep = sleeper
        self._queue = FileQueue()
        self._handlers: list[EventHandler] = []
        self._capture: _ActiveCapture | None = None
        self._capture_status = CaptureStatus(CaptureState.IDLE)
        self._capture_lock = threading.Lock()
        self._live = LiveCaptions()
        self._live_lock = threading.Lock()

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
        """Send a File Transcript's source through the gateway again, replacing the Transcript in place.

        A Capture cannot be re-transcribed: its Transcript came from the live stream and nothing was kept (ADR-0004).
        """
        previous = self._store.load(transcript_id)
        if previous.source_path is None:
            raise ValueError("A Capture cannot be re-transcribed; only a File can (ADR-0004).")
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
            self._note_key_problem(e)
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

    def start_capture(self, kind: SourceKind = SourceKind.MEETING, title: str | None = None) -> CaptureStatus:
        """Begin a Capture. A Meeting Capture takes the Microphone and System Audio together as two channels.

        Only one Capture runs at a time.
        """
        kind = SourceKind(kind)
        if self._audio is None:
            raise CaptureError("Audio capture is not available on this platform.")
        if kind not in CAPTURE_DEVICE_KINDS:
            raise CaptureError(f"{kind.label} capture is not available yet.")
        self._require_api_key()
        with self._capture_lock:
            if self._capture_status.state in (CaptureState.CAPTURING, CaptureState.FINALISING):
                raise CaptureError("A Capture is already running.")
            devices = [self._require_device(k) for k in CAPTURE_DEVICE_KINDS[kind]]
            started = self._clock()
            capture_id = uuid.uuid4().hex
            clean_title = (title or "").strip() or started.strftime("%Y-%m-%d %H:%M")
            active = _ActiveCapture(
                id=capture_id, kind=kind, title=clean_title, started_at=started, devices=devices, channels=len(devices)
            )
            self._capture = active
            with self._live_lock:
                # In a Meeting Capture channel 0 is the Microphone: one voice, "You".
                single_voice = frozenset({0}) if kind is SourceKind.MEETING else frozenset()
                self._live = LiveCaptions(single_voice_channels=single_voice)
            status = CaptureStatus(CaptureState.CAPTURING, clean_title, started, kind=kind)
            self._capture_status = status
        self._emit(CaptureChanged(status))
        # The stream is the Transcript (ADR-0004); a stream that cannot open now is retried in the
        # background and the Capture keeps running, so the user can at least try again without restarting.
        try:
            self._open_stream(active)
        except TranscriptionError as e:
            threading.Thread(target=self._on_stream_error, args=(e.message,), daemon=True).start()
        sink = self._make_chunk_sink(active)
        if len(devices) == 1:
            active.handles = [self._audio.open(devices[0], sink)]
        else:
            # Channel order is the order of CAPTURE_DEVICE_KINDS[kind]: Microphone first, then System Audio.
            active.mixer = ChannelMixer(sink)
            active.handles = [
                self._audio.open(device, functools.partial(active.mixer.push, channel))
                for channel, device in enumerate(devices)
            ]
        return status

    def _open_stream(self, active: _ActiveCapture) -> None:
        """Open a live connection and remember how far into the Capture it started.

        Deepgram's timestamps restart with every connection; the offset keeps the Transcript's
        timeline continuous across reconnects.
        """
        stream = self._gateway.open_stream(self._on_caption, self._on_stream_error, active.channels)
        active.stream_offset = (self._clock() - active.started_at).total_seconds()
        active.stream = stream

    def _require_device(self, device_kind: DeviceKind) -> AudioDevice:
        """The device chosen in settings if it is still present, else the Windows default."""
        settings = self.settings()
        chosen = settings.microphone_device_id if device_kind is DeviceKind.INPUT else settings.output_device_id
        device = None
        if chosen:
            device = next((d for d in self._audio.list_devices() if d.id == chosen and d.kind is device_kind), None)
        if device is None:
            device = self._audio.default_device(device_kind)
        if device is None:
            raise CaptureError(
                "No microphone was found."
                if device_kind is DeviceKind.INPUT
                else "No output device was found to capture System Audio from."
            )
        return device

    def live_captions(self) -> LiveCaptions:
        with self._live_lock:
            return self._live

    def _make_chunk_sink(self, active: _ActiveCapture) -> Callable[[bytes], None]:
        def on_chunk(chunk: bytes) -> None:
            stream = active.stream
            if stream is not None:
                try:
                    stream.send(chunk)
                except Exception:  # noqa: BLE001 - the stream reports its own failure through on_error
                    pass

        return on_chunk

    def _on_caption(self, caption: Caption) -> None:
        active = self._capture
        if active is not None and active.stream_offset:
            caption = replace(caption, start=caption.start + active.stream_offset)
        with self._live_lock:
            self._live = self._live.with_caption(caption)
            live = self._live
        self._emit(CaptionsChanged(live))

    def _on_stream_error(self, reason: str) -> None:
        """The live connection dropped. Reopen it with backoff until the Capture stops; audio sent meanwhile is lost."""
        active = self._capture
        if active is None or self._capture_status.state is not CaptureState.CAPTURING:
            return
        active.stream = None
        self._set_reconnecting(True)
        attempt = 0
        while self._capture is active and self._capture_status.state is CaptureState.CAPTURING:
            self._sleep(RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)])
            attempt += 1
            if self._capture is not active or self._capture_status.state is not CaptureState.CAPTURING:
                return
            try:
                self._open_stream(active)
            except TranscriptionError:
                continue
            self._set_reconnecting(False)
            return

    def _set_reconnecting(self, reconnecting: bool) -> None:
        with self._live_lock:
            self._live = replace(self._live, reconnecting=reconnecting)
            live = self._live
        self._emit(CaptionsChanged(live))

    def stop_capture(self) -> Transcript:
        """Stop the running Capture and turn its Live Captions into the Transcript (ADR-0004).

        Nothing is sent to Deepgram again. Closing the stream waits briefly for the last results.
        """
        with self._capture_lock:
            active = self._capture
            if active is None or self._capture_status.state is not CaptureState.CAPTURING:
                raise CaptureError("No Capture is running.")
            for handle in active.handles:
                handle.stop()
            if active.mixer is not None:
                active.mixer.flush()
            status = CaptureStatus(CaptureState.FINALISING, active.title, active.started_at, kind=active.kind)
            self._capture_status = status
        self._emit(CaptureChanged(status))
        stream, active.stream = active.stream, None
        if stream is not None:
            try:
                stream.close()  # lets the service flush its final results into the Live Captions
            except Exception:  # noqa: BLE001 - a connection that is already gone is fine here
                pass
        duration = (self._clock() - active.started_at).total_seconds()
        with self._live_lock:
            live = self._live
            if live.provisional is not None and live.provisional.text:
                live = live.with_caption(replace(live.provisional, is_final=True))
            self._live = live
        transcript = self._finish(
            transcript_id=active.id,
            title=active.title,
            kind=active.kind,
            source_path=None,
            rendering_path=self._capture_rendering_path(active.started_at, active.title),
            result=_captions_to_result(live, duration),
            created=active.started_at,
        )
        with self._capture_lock:
            self._capture = None
            self._capture_status = CaptureStatus(
                CaptureState.COMPLETE, active.title, active.started_at, transcript_id=transcript.id, kind=active.kind
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
        return self.transcripts_dir() / f"{name}.txt"

    def transcripts_dir(self) -> Path:
        """Where Capture Renderings go: the settings value if set, else the configured default."""
        configured = self.settings().transcripts_dir
        return Path(configured) if configured else self._transcripts_dir

    # ---- API key -------------------------------------------------------------------------

    def api_key(self) -> str | None:
        """The key Deepgram calls use: the credential store first, then the environment variable."""
        stored = self._credentials.get_api_key()
        if stored:
            return stored
        return self._env.get(API_KEY_ENV, "").strip() or None

    def has_stored_api_key(self) -> bool:
        return bool(self._credentials.get_api_key())

    def set_api_key(self, key: str) -> None:
        """Store the key in the credential store only. It never lands in a file the app writes."""
        clean = key.strip()
        if not clean:
            self._credentials.clear_api_key()
        else:
            self._credentials.set_api_key(clean)

    def _require_api_key(self) -> None:
        if self.api_key() is None:
            raise MissingKeyError("No Deepgram API key. Enter one in Settings.")

    def _note_key_problem(self, error: TranscriptionError) -> None:
        if isinstance(error, KeyProblem):
            self._emit(ApiKeyProblem(error.message))

    # ---- Settings ------------------------------------------------------------------------

    def settings(self) -> Settings:
        return self._settings.load()

    def update_settings(self, **changes) -> Settings:
        """Change settings. Rendering defaults apply to Renderings written from now on."""
        return self._settings.update(**changes)

    # ---- Renderings and renaming ---------------------------------------------------------

    def render(self, transcript_id: str, options: RenderingOptions | None = None) -> str:
        """A one-off Rendering with the given options (defaults from settings). Touches no file."""
        return render(self._store.load(transcript_id), options or self.settings().rendering_options)

    def rename_speaker(self, transcript_id: str, speaker: int, name: str) -> Transcript:
        """Give a Speaker a real name. Every paragraph and the Rendering file follow."""
        transcript = self._store.load(transcript_id)
        clean = name.strip()
        if not clean:
            raise ValueError("A Speaker needs a name.")
        transcript.speaker_names[speaker] = clean
        self._save_with_rendering(transcript)
        return transcript

    def rename_transcript(self, transcript_id: str, title: str) -> Transcript:
        """Change a Transcript's title. A Capture's Rendering file is renamed; a File's keeps the File's name."""
        transcript = self._store.load(transcript_id)
        clean = title.strip()
        if not clean:
            raise ValueError("A Transcript needs a title.")
        transcript.title = clean
        if transcript.source_path is None and transcript.rendering_path is not None:
            new_path = self._capture_rendering_path(transcript.created, clean)
            if new_path != transcript.rendering_path:
                old_path = transcript.rendering_path
                transcript.rendering_path = new_path
                if old_path.exists():
                    old_path.unlink()
        self._save_with_rendering(transcript)
        return transcript

    def _save_with_rendering(self, transcript: Transcript) -> None:
        if transcript.rendering_path is not None and transcript.status is TranscriptStatus.COMPLETE:
            self._write_rendering(transcript)
        self._store.save(transcript)
        self._emit(TranscriptReady(transcript))

    def _write_rendering(self, transcript: Transcript) -> None:
        path = transcript.rendering_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(transcript, self.settings().rendering_options), encoding="utf-8")

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
        self._require_api_key()
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
        created: datetime | None = None,
    ) -> Transcript:
        """Turn a gateway result into a persisted Transcript with its Rendering written."""
        paragraphs, speaker_names = attribute_speakers(kind, result.paragraphs)
        transcript = Transcript(
            id=transcript_id,
            title=title,
            created=created or self._clock(),
            duration=result.duration,
            source_kind=kind,
            source_path=source_path,
            paragraphs=paragraphs,
            speaker_names=speaker_names,
            rendering_path=rendering_path,
        )
        self._write_rendering(transcript)
        self._store.save(transcript)
        self._emit(TranscriptReady(transcript))
        return transcript

    def _find_complete_for_source(self, path: Path) -> Transcript | None:
        for t in self._store.load_all():
            if t.status is TranscriptStatus.COMPLETE and t.source_path == path:
                return t
        return None
