from __future__ import annotations

import time

from transcriber.core.app import CaptionsChanged, TranscriberCore
from transcriber.core.capture import LiveCaptions
from transcriber.core.gateway import Caption
from transcriber.core.model import SourceKind

from .fakes import FIXTURES, FakeAudioCapture, wav_frames


def test_captured_audio_is_streamed_and_captions_arrive_provisional_then_final(core, gateway, audio):
    core.start_capture(SourceKind.MICROPHONE)
    stream = gateway.stream
    assert stream.channels == 1
    assert b"".join(stream.sent) == wav_frames(FIXTURES / "hello.wav")

    stream.deliver(Caption(text="Morning every", is_final=False, start=0.4, speaker=0))
    live = core.live_captions()
    assert live.final == ()
    assert live.provisional == Caption(text="Morning every", is_final=False, start=0.4, speaker=0)

    stream.deliver(Caption(text="Morning everyone, can you hear me?", is_final=True, start=0.4, speaker=0))
    stream.deliver(Caption(text="Yes, loud", is_final=False, start=3.5, speaker=1))
    live = core.live_captions()
    assert [c.text for c in live.final] == ["Morning everyone, can you hear me?"]
    assert live.provisional.text == "Yes, loud"
    assert live.reconnecting is False


def test_stopping_closes_the_stream_and_the_transcript_comes_from_the_recording_not_the_captions(core, gateway):
    core.start_capture(SourceKind.MICROPHONE)
    gateway.stream.deliver(Caption(text="Something the stream heard", is_final=True, start=0.0, speaker=0))

    transcript = core.stop_capture()

    assert gateway.stream.closed is True
    assert [p.text for p in transcript.paragraphs] == [
        "Morning everyone, can you hear me?",
        "Yes, loud and clear.",
    ]


def test_a_dropped_stream_is_reopened_with_backoff_while_the_recording_continues(gateway, data_dir, transcripts_dir, clock):
    audio = FakeAudioCapture(auto_play=False)
    sleeps: list[float] = []
    core = TranscriberCore(
        gateway=gateway, audio_capture=audio, data_dir=data_dir, transcripts_dir=transcripts_dir,
        clock=clock, sleeper=sleeps.append,
    )
    seen: list[LiveCaptions] = []
    core.subscribe(lambda e: seen.append(e.live) if isinstance(e, CaptionsChanged) else None)
    core.start_capture(SourceKind.MICROPHONE)
    first = gateway.stream
    audio.deliver(b"\x01\x00" * 1600)

    gateway.fail_open = 2  # the first two reopen attempts fail
    first.drop("connection lost")

    assert sleeps == [0.5, 1.0, 2.0]
    assert len(gateway.streams) == 2
    assert [live.reconnecting for live in seen] == [True, False]
    second = gateway.stream
    audio.deliver(b"\x02\x00" * 1600)
    assert first.sent == [b"\x01\x00" * 1600]
    assert second.sent == [b"\x02\x00" * 1600]

    transcript = core.stop_capture()

    assert gateway.request_bytes[0][44:] == b"\x01\x00" * 1600 + b"\x02\x00" * 1600
    assert transcript.source_kind is SourceKind.MICROPHONE


def test_a_stream_that_cannot_open_at_start_is_retried_and_the_capture_still_records(gateway, data_dir, transcripts_dir, clock):
    audio = FakeAudioCapture(auto_play=False)
    sleeps: list[float] = []
    core = TranscriberCore(
        gateway=gateway, audio_capture=audio, data_dir=data_dir, transcripts_dir=transcripts_dir,
        clock=clock, sleeper=sleeps.append,
    )
    gateway.fail_open = 1

    core.start_capture(SourceKind.MICROPHONE)
    audio.deliver(b"\x03\x00" * 1600)
    for _ in range(50):
        if gateway.streams:
            break
        time.sleep(0.02)

    assert sleeps[:1] == [0.5]
    assert len(gateway.streams) == 1
    assert core.live_captions().reconnecting is False
    core.stop_capture()
    assert gateway.request_bytes[0][44:] == b"\x03\x00" * 1600
