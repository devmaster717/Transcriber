"""The audio capture seam and the Capture state machine.

The interface delivers 16 kHz, 16-bit, mono PCM chunks; whatever the device's native format,
the implementation resamples before calling back (ADR-0001 keeps platform code behind this).
"""

from __future__ import annotations

import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .gateway import Caption
from .model import SourceKind

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2  # bytes: 16-bit linear PCM


class DeviceKind(StrEnum):
    INPUT = "input"  # a microphone
    LOOPBACK = "loopback"  # what an output device is playing (System Audio)


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    kind: DeviceKind


class CaptureHandle(Protocol):
    def stop(self) -> None:
        """Stop delivering chunks. Returns once no further callback will fire."""
        ...


class AudioCapture(Protocol):
    def list_devices(self) -> list[AudioDevice]: ...

    def default_device(self, kind: DeviceKind) -> AudioDevice | None: ...

    def open(self, device: AudioDevice, on_chunk: Callable[[bytes], None]) -> CaptureHandle:
        """Start capturing. `on_chunk` receives 16 kHz mono int16 PCM, possibly from another thread."""
        ...


class CaptureError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CaptureState(StrEnum):
    IDLE = "idle"
    CAPTURING = "capturing"
    FINALISING = "finalising"
    COMPLETE = "complete"
    NEEDS_RETRY = "needs_retry"  # the finalising pass failed; the Recording is kept for a Retry


@dataclass(frozen=True)
class CaptureStatus:
    state: CaptureState
    title: str | None = None
    started_at: datetime | None = None
    recording_path: Path | None = None
    transcript_id: str | None = None
    kind: SourceKind | None = None


MAX_LINE_CHARS = 240  # a line of Live Captions that never gets punctuation is still cut at a readable length
SENTENCE_END = (".", "?", "!", "。", "？", "！")


def _sentence_done(text: str, paused: bool) -> bool:
    """A line is complete when the service noticed a pause and the text ends like a sentence."""
    return paused and text.rstrip().endswith(SENTENCE_END)


@dataclass(frozen=True)
class CaptionLine:
    """One line on screen: the final text so far, plus the provisional tail still being recognised."""

    speaker: int | None
    channel: int
    text: str
    provisional_text: str = ""

    @property
    def full_text(self) -> str:
        return f"{self.text} {self.provisional_text}".strip()


@dataclass(frozen=True)
class LiveCaptions:
    """What the screen shows during a Capture. Never the Transcript (ADR-0003).

    The service sends final text in time-sliced segments, so one sentence arrives in pieces. A line
    keeps absorbing segments from the same voice until the service says the speaker paused
    (`ends_utterance`), or a different Speaker or channel takes over.
    """

    final: tuple[Caption, ...] = ()
    provisional: Caption | None = None
    reconnecting: bool = False
    # Channels carrying exactly one voice (the Microphone in a Meeting Capture): speaker ids
    # the service guesses there are noise and must not split lines.
    single_voice_channels: frozenset[int] = frozenset()

    def with_caption(self, caption: Caption) -> LiveCaptions:
        if not caption.is_final:
            return replace(self, provisional=caption)
        if caption.silence:
            # A long quiet: whatever line is open on that channel is finished, punctuation or not.
            if self.final and self.final[-1].channel == caption.channel and not self.final[-1].closed:
                return replace(self, final=(*self.final[:-1], replace(self.final[-1], closed=True)), provisional=None)
            return replace(self, provisional=None)
        if not caption.text:
            return replace(self, provisional=None)
        last = self.final[-1] if self.final else None
        if last is not None and self._continues(last, caption):
            text = f"{last.text} {caption.text}".strip()
            merged = Caption(
                text=text,
                is_final=True,
                start=last.start,
                speaker=last.speaker if last.speaker is not None else caption.speaker,
                channel=last.channel,
                ends_utterance=caption.ends_utterance,
                closed=_sentence_done(text, caption.ends_utterance),
            )
            return replace(self, final=(*self.final[:-1], merged), provisional=None)
        opened = replace(caption, closed=_sentence_done(caption.text, caption.ends_utterance))
        return replace(self, final=(*self.final, opened), provisional=None)

    def _continues(self, last: Caption, new: Caption) -> bool:
        """A new segment extends the open line if the same voice is still speaking and the line has room.

        The service flags a pause (`ends_utterance`) on almost every segment with some speakers, so a
        pause alone does not end a line; a pause after a complete sentence does (see `_sentence_done`).
        """
        if last.closed or last.channel != new.channel:
            return False
        if len(last.text) + 1 + len(new.text) > MAX_LINE_CHARS:
            return False
        if last.channel in self.single_voice_channels:
            return True
        return last.speaker is None or new.speaker is None or last.speaker == new.speaker

    def lines(self) -> list[CaptionLine]:
        lines = [CaptionLine(c.speaker, c.channel, c.text) for c in self.final]
        p = self.provisional
        if p is not None and p.text:
            if lines and self.final and self._continues(self.final[-1], p):
                lines[-1] = CaptionLine(lines[-1].speaker, lines[-1].channel, lines[-1].text, p.text)
            else:
                lines.append(CaptionLine(p.speaker, p.channel, "", p.text))
        return lines

    def current_line_text(self) -> str:
        lines = self.lines()
        return lines[-1].full_text if lines else ""


class Recorder:
    """Writes PCM chunks to a WAV as they arrive. Safe to feed from a capture thread and close from another."""

    def __init__(self, path: Path, channels: int = 1) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(channels)
        self._wave.setsampwidth(SAMPLE_WIDTH)
        self._wave.setframerate(SAMPLE_RATE)
        self._lock = threading.Lock()
        self._closed = False
        self.frames = 0

    def write(self, chunk: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            self._wave.writeframes(chunk)
            self.frames += len(chunk) // SAMPLE_WIDTH

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._wave.close()
