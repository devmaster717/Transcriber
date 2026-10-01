"""The audio capture seam and the Capture state machine.

The interface delivers 16 kHz, 16-bit, mono PCM chunks; whatever the device's native format,
the implementation resamples before calling back (ADR-0001 keeps platform code behind this).
"""

from __future__ import annotations

import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .gateway import Caption

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


@dataclass(frozen=True)
class LiveCaptions:
    """What the screen shows during a Capture. Never the Transcript (ADR-0003)."""

    final: tuple[Caption, ...] = ()
    provisional: Caption | None = None
    reconnecting: bool = False

    def with_caption(self, caption: Caption) -> LiveCaptions:
        if caption.is_final:
            return LiveCaptions((*self.final, caption), None, self.reconnecting)
        return LiveCaptions(self.final, caption, self.reconnecting)


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
