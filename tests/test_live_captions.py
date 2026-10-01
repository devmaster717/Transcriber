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


def test_final_segments_of_one_utterance_build_one_line_until_the_speaker_pauses(core, gateway):
    """Deepgram cuts final text by time window, so one sentence arrives as several `is_final` pieces."""
    core.start_capture(SourceKind.MICROPHONE)
    s = gateway.stream
    s.deliver(Caption("Morning everyone, can you", True, 0.4, speaker=0))
    s.deliver(Caption("hear me? I wanted to go", True, 2.9, speaker=0))
    s.deliver(Caption("over the plan for next week.", True, 5.1, speaker=0, ends_utterance=True))
    s.deliver(Caption("Sounds good.", True, 7.0, speaker=1, ends_utterance=True))

    assert [(c.speaker, c.text) for c in core.live_captions().final] == [
        (0, "Morning everyone, can you hear me? I wanted to go over the plan for next week."),
        (1, "Sounds good."),
    ]


def test_a_pause_in_the_middle_of_a_sentence_does_not_end_the_line(core, gateway):
    """Deepgram flags a pause (speech_final) on nearly every segment with a lecturer's cadence.
    A line ends only when the pause comes after a complete sentence."""
    core.start_capture(SourceKind.MICROPHONE)
    s = gateway.stream
    s.deliver(Caption("Okay. I used", True, 0.0, speaker=0, ends_utterance=True))
    s.deliver(Caption("algorithms, like", True, 1.2, speaker=0, ends_utterance=True))
    s.deliver(Caption("SIFT, which stands for scale", True, 2.4, speaker=0, ends_utterance=True))
    s.deliver(Caption("invariant feature transform.", True, 3.9, speaker=0, ends_utterance=True))
    s.deliver(Caption("So SIFT helped in detecting", True, 5.0, speaker=0, ends_utterance=True))

    assert [c.text for c in core.live_captions().final] == [
        "Okay. I used algorithms, like SIFT, which stands for scale invariant feature transform.",
        "So SIFT helped in detecting",
    ]


def test_a_long_silence_ends_the_line_even_without_punctuation(core, gateway):
    core.start_capture(SourceKind.MICROPHONE)
    s = gateway.stream
    s.deliver(Caption("So what I meant was", True, 0.0, speaker=0, ends_utterance=True))
    s.deliver(Caption("", True, 2.0, speaker=None, silence=True))  # UtteranceEnd after a second of quiet
    s.deliver(Caption("Anyway, moving on.", True, 4.0, speaker=0, ends_utterance=True))

    assert [c.text for c in core.live_captions().final] == ["So what I meant was", "Anyway, moving on."]


def test_a_line_that_never_gets_punctuation_is_still_cut_at_a_readable_length(core, gateway):
    core.start_capture(SourceKind.MICROPHONE)
    s = gateway.stream
    for i in range(12):
        s.deliver(Caption(f"segment number {i} of an endless run-on", True, float(i), speaker=0))

    lines = [c.text for c in core.live_captions().final]
    assert len(lines) >= 2
    assert all(len(line) <= 260 for line in lines)
    assert " ".join(lines).count("segment number") == 12


def test_a_speaker_change_starts_a_new_line_even_without_a_pause(core, gateway):
    core.start_capture(SourceKind.MICROPHONE)
    s = gateway.stream
    s.deliver(Caption("So what do you think", True, 0.4, speaker=0))
    s.deliver(Caption("I think it works.", True, 2.0, speaker=1))

    assert [(c.speaker, c.text) for c in core.live_captions().final] == [
        (0, "So what do you think"),
        (1, "I think it works."),
    ]


def test_provisional_text_continues_the_line_being_built(core, gateway):
    core.start_capture(SourceKind.MICROPHONE)
    s = gateway.stream
    s.deliver(Caption("Morning everyone, can you", True, 0.4, speaker=0))
    s.deliver(Caption("hear me", False, 2.9, speaker=0))

    live = core.live_captions()
    assert [c.text for c in live.final] == ["Morning everyone, can you"]
    assert live.provisional.text == "hear me"
    assert live.current_line_text() == "Morning everyone, can you hear me"


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
        clock=clock, sleeper=sleeps.append, env={"DEEPGRAM_API_KEY": "test-key"},
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
        clock=clock, sleeper=sleeps.append, env={"DEEPGRAM_API_KEY": "test-key"},
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
