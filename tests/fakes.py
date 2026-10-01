"""Test doubles for the two edges the core cannot own (see the spec's Testing Decisions)."""

from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path

from transcriber.core.capture import AudioDevice, CaptureHandle, DeviceKind
from transcriber.core.gateway import Caption, OnCaption, OnStreamError, TranscriptionError, TranscriptionResult
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


class FakeLiveStream:
    """A live connection the test drives by hand: deliver captions, or drop it."""

    def __init__(self, on_caption: OnCaption, on_error: OnStreamError, channels: int) -> None:
        self._on_caption = on_caption
        self._on_error = on_error
        self.channels = channels
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, chunk: bytes) -> None:
        self.sent.append(chunk)

    def close(self) -> None:
        self.closed = True

    def deliver(self, caption: Caption) -> None:
        self._on_caption(caption)

    def drop(self, reason: str = "connection lost") -> None:
        self._on_error(reason)


class FakeTranscriptionGateway:
    """Returns a canned result, or fails on command. Records what it was asked to transcribe.

    `fail_with` fails every request; `fail_for[path]` fails just that path. Both are read at
    request time, so a test can clear them to model a retry that succeeds. `request_bytes`
    keeps the content of each file at request time, since a Recording is deleted afterwards.
    `streams` holds every live stream opened; `fail_open` makes the next N opens raise.
    """

    def __init__(self, result: TranscriptionResult | None = None) -> None:
        self.result = result or canned_result()
        self.fail_with: Exception | None = None
        self.fail_for: dict[Path, Exception] = {}
        self.requests: list[Path] = []
        self.request_bytes: list[bytes] = []
        self.multichannel_requests: list[bool] = []
        self.streams: list[FakeLiveStream] = []
        self.fail_open = 0

    def transcribe(self, audio: Path, multichannel: bool = False) -> TranscriptionResult:
        self.requests.append(audio)
        self.request_bytes.append(audio.read_bytes())
        self.multichannel_requests.append(multichannel)
        if audio in self.fail_for:
            raise self.fail_for[audio]
        if self.fail_with is not None:
            raise self.fail_with
        return self.result

    def open_stream(self, on_caption: OnCaption, on_error: OnStreamError, channels: int = 1) -> FakeLiveStream:
        if self.fail_open > 0:
            self.fail_open -= 1
            raise TranscriptionError("Could not reach Deepgram. Check your connection.")
        stream = FakeLiveStream(on_caption, on_error, channels)
        self.streams.append(stream)
        return stream

    @property
    def stream(self) -> FakeLiveStream:
        return self.streams[-1]


def wav_frames(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), path
        return w.readframes(w.getnframes())


class FakeAudioCapture:
    """Plays a 16 kHz mono WAV as if it were the device.

    With `auto_play` every chunk is delivered before `open` returns; without it the test feeds
    chunks by hand with `deliver`, so it can interleave audio with other events. Several devices
    can be open at once (a Meeting Capture opens two); `deliver` picks one by id.
    """

    def __init__(self, wav: Path = FIXTURES / "hello.wav", chunk_frames: int = 1600, auto_play: bool = True) -> None:
        self.frames = wav_frames(wav)
        self.chunk_bytes = chunk_frames * 2
        self.auto_play = auto_play
        self.devices = [
            AudioDevice(id="fake-mic", name="Fake Microphone", kind=DeviceKind.INPUT),
            AudioDevice(id="fake-loop", name="Fake Speakers [Loopback]", kind=DeviceKind.LOOPBACK),
        ]
        self.opened: list[AudioDevice] = []
        self.stopped = 0
        self._sinks: dict[str, Callable[[bytes], None]] = {}

    def list_devices(self) -> list[AudioDevice]:
        return list(self.devices)

    def default_device(self, kind: DeviceKind) -> AudioDevice | None:
        return next((d for d in self.devices if d.kind is kind), None)

    def open(self, device: AudioDevice, on_chunk: Callable[[bytes], None]) -> CaptureHandle:
        self.opened.append(device)
        self._sinks[device.id] = on_chunk
        if self.auto_play:
            for i in range(0, len(self.frames), self.chunk_bytes):
                on_chunk(self.frames[i : i + self.chunk_bytes])
        fake = self

        class _Handle:
            def stop(self) -> None:
                fake.stopped += 1
                fake._sinks.pop(device.id, None)

        return _Handle()

    def deliver(self, chunk: bytes, device_id: str | None = None) -> None:
        if device_id is None:
            assert len(self._sinks) == 1, "several devices are open; say which one"
            device_id = next(iter(self._sinks))
        assert device_id in self._sinks, f"{device_id} is not open"
        self._sinks[device_id](chunk)


__all__ = [
    "FIXTURES",
    "FakeAudioCapture",
    "FakeLiveStream",
    "FakeTranscriptionGateway",
    "TranscriptionError",
    "canned_result",
    "wav_frames",
]
