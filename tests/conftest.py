from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from transcriber.core.app import TranscriberCore

from .fakes import FakeTranscriptionGateway

FIXED_NOW = datetime(2026, 9, 30, 14, 5, 0)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def gateway() -> FakeTranscriptionGateway:
    return FakeTranscriptionGateway()


@pytest.fixture
def core(gateway: FakeTranscriptionGateway, data_dir: Path) -> TranscriberCore:
    return TranscriberCore(gateway=gateway, data_dir=data_dir, clock=lambda: FIXED_NOW)


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    """A File Source: any bytes will do, the fake gateway never reads them."""
    path = tmp_path / "recordings" / "interview.m4a"
    path.parent.mkdir()
    path.write_bytes(b"not really audio")
    return path
