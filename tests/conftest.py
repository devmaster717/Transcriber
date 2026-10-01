from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from transcriber.core.app import TranscriberCore

from .fakes import FakeAudioCapture, FakeTranscriptionGateway

FIXED_NOW = datetime(2026, 9, 30, 14, 5, 0)


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime = FIXED_NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def transcripts_dir(tmp_path: Path) -> Path:
    return tmp_path / "Documents" / "Transcriber"


@pytest.fixture
def gateway() -> FakeTranscriptionGateway:
    return FakeTranscriptionGateway()


@pytest.fixture
def audio() -> FakeAudioCapture:
    return FakeAudioCapture()


@pytest.fixture
def core(
    gateway: FakeTranscriptionGateway,
    audio: FakeAudioCapture,
    data_dir: Path,
    transcripts_dir: Path,
    clock: FakeClock,
) -> TranscriberCore:
    return TranscriberCore(
        gateway=gateway,
        audio_capture=audio,
        data_dir=data_dir,
        transcripts_dir=transcripts_dir,
        clock=clock,
        env={"DEEPGRAM_API_KEY": "test-key"},
    )


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """A File Source: any bytes will do, the fake gateway never reads them."""
    path = tmp_path / "recordings" / "interview.m4a"
    path.parent.mkdir()
    path.write_bytes(b"not really audio")
    return path


@pytest.fixture
def make_audio_file(tmp_path: Path):
    """Create further File Sources by name, in the same recordings folder."""
    folder = tmp_path / "recordings"
    folder.mkdir(exist_ok=True)

    def _make(name: str) -> Path:
        path = folder / name
        path.write_bytes(b"not really audio")
        return path

    return _make
