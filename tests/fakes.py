"""Test doubles for the two edges the core cannot own (see the spec's Testing Decisions)."""

from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path

from transcriber.core.capture import AudioDevice, CaptureHandle, DeviceKind
from transcriber.core.gateway import TranscriptionError, TranscriptionResult
from transcriber.core.model import Paragraph

FIXTURES = Path(__file__).parent / "fixtures"


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
    request time, so a test can clear them to model a retry that succeeds. `request_bytes`
    keeps the content of each file at request time, since a Recording is deleted afterwards.
    """

    def __init__(self, result: TranscriptionResult | None = None) -> None:
        self.result = result or canned_result()
        self.fail_with: Exception | None = None
        self.fail_for: dict[Path, Exception] = {}
        self.requests: list[Path] = []
        self.request_bytes: list[bytes] = []

    def transcribe(self, audio: Path) -> TranscriptionResult:
        self.requests.append(audio)
        self.request_bytes.append(audio.read_bytes())
        if audio in self.fail_for:
            raise self.fail_for[audio]
        if self.fail_with is not None:
            raise self.fail_with
        return self.result


def wav_frames(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), path
        return w.readframes(w.getnframes())


class FakeAudioCapture:
    """Plays a 16 kHz mono WAV as if it were the device, delivering every chunk before `open` returns."""

    def __init__(self, wav: Path = FIXTURES / "hello.wav", chunk_frames: int = 1600) -> None:
        self.frames = wav_frames(wav)
        self.chunk_bytes = chunk_frames * 2
        self.devices = [
            AudioDevice(id="fake-mic", name="Fake Microphone", kind=DeviceKind.INPUT),
            AudioDevice(id="fake-loop", name="Fake Speakers [Loopback]", kind=DeviceKind.LOOPBACK),
        ]
        self.opened: list[AudioDevice] = []
        self.stopped = 0

    def list_devices(self) -> list[AudioDevice]:
        return list(self.devices)

    def default_device(self, kind: DeviceKind) -> AudioDevice | None:
        return next((d for d in self.devices if d.kind is kind), None)

    def open(self, device: AudioDevice, on_chunk: Callable[[bytes], None]) -> CaptureHandle:
        self.opened.append(device)
        for i in range(0, len(self.frames), self.chunk_bytes):
            on_chunk(self.frames[i : i + self.chunk_bytes])
        fake = self

        class _Handle:
            def stop(self) -> None:
                fake.stopped += 1

        return _Handle()


__all__ = ["FIXTURES", "FakeAudioCapture", "FakeTranscriptionGateway", "TranscriptionError", "canned_result", "wav_frames"]
