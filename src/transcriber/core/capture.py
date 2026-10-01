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
    FINALISING = "finalising"  # the stream is closing and the Live Captions are being written as the Transcript
    COMPLETE = "complete"


@dataclass(frozen=True)
class CaptureStatus:
    state: CaptureState
    title: str | None = None
    started_at: datetime | None = None
    recording_path: Path | None = None
    transcript_id: str | None = None
    kind: SourceKind | None = None


SOFT_LINE_CHARS = 100  # past this, a line is closed at its last sentence boundary
MAX_LINE_CHARS = 240  # a line that never gets any punctuation is still cut at a readable length
SENTENCE_END = (".", "?", "!", "。", "？", "！")


def _sentence_done(text: str, paused: bool) -> bool:
    """A line is complete when the service noticed a pause and the text ends like a sentence."""
    return paused and text.rstrip().endswith(SENTENCE_END)


def _lay_out(text: str) -> tuple[str, str]:
    """Split a line that has grown long: the head ends at its last sentence boundary, the tail carries on.

    Continuous speech never triggers the service's pause signal, so without this a line would only
    ever end at the hard cap, mid-sentence. Returns (head, tail); an empty tail means no split.
    """
    if len(text) <= SOFT_LINE_CHARS:
        return text, ""
    boundary = -1
    for i, ch in enumerate(text):
        if ch in SENTENCE_END and (i + 1 == len(text) or text[i + 1] == " "):
            boundary = i
    if 0 <= boundary < len(text) - 1:
        return text[: boundary + 1].rstrip(), text[boundary + 1 :].strip()
    if boundary == -1 and len(text) > MAX_LINE_CHARS:
        cut = text.rfind(" ", 0, MAX_LINE_CHARS)
        if cut > 0:
            return text[:cut].rstrip(), text[cut:].strip()
    return text, ""


def _line_closed(text: str, paused: bool) -> bool:
    """Closed on a pause after a sentence, or once a long line ends with a sentence (no pause needed)."""
    return _sentence_done(text, paused) or (len(text) > SOFT_LINE_CHARS and text.rstrip().endswith(SENTENCE_END))


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
            line = Caption(
                text=text,
                is_final=True,
                start=last.start,
                speaker=last.speaker if last.speaker is not None else caption.speaker,
                channel=last.channel,
                ends_utterance=caption.ends_utterance,
            )
            kept = self.final[:-1]
        else:
            line = caption
            kept = self.final
        return replace(self, final=(*kept, *self._lines_from(line, caption)), provisional=None)

    @staticmethod
    def _lines_from(line: Caption, new: Caption) -> list[Caption]:
        """Lay a (possibly merged) line out as closed lines plus an open tail, breaking only at sentences."""
        out: list[Caption] = []
        text = line.text
        start = line.start
        while True:
            head, tail = _lay_out(text)
            out.append(
                replace(line, text=head, start=start, closed=bool(tail) or _line_closed(head, new.ends_utterance))
            )
            if not tail:
                return out
            text, start = tail, new.start

    def _continues(self, last: Caption, new: Caption) -> bool:
        """A new segment extends the open line if the same voice is still speaking.

        The service flags a pause (`ends_utterance`) on almost every segment with some speakers, so a
        pause alone does not end a line; a pause after a complete sentence does (see `_line_closed`).
        """
        if last.closed or last.channel != new.channel:
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
