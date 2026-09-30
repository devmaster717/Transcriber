"""The real transcription gateway: mapping is tested offline, the wire is tested opt-in."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from transcriber.core.model import Paragraph
from transcriber.deepgram_gateway import DeepgramGateway, to_result

FIXTURES = Path(__file__).parent / "fixtures"


def test_deepgram_paragraphs_become_speaker_turns():
    payload = json.loads((FIXTURES / "deepgram_prerecorded.json").read_text(encoding="utf-8"))

    result = to_result(payload)

    assert result.duration == 12.5
    assert result.paragraphs == [
        Paragraph(start=0.4, end=3.1, speaker=0, text="Morning everyone, can you hear me?"),
        Paragraph(start=3.5, end=5.0, speaker=1, text="Yes, loud and clear."),
    ]


@pytest.mark.skipif(not os.environ.get("DEEPGRAM_API_KEY"), reason="DEEPGRAM_API_KEY not set")
def test_real_deepgram_transcribes_the_spoken_fixture():
    gateway = DeepgramGateway(api_key=os.environ["DEEPGRAM_API_KEY"])

    result = gateway.transcribe(FIXTURES / "hello.wav")

    spoken = " ".join(p.text for p in result.paragraphs).lower()
    assert 4.0 < result.duration < 10.0
    assert "quick brown fox" in spoken
    assert all(p.speaker == 0 for p in result.paragraphs)
