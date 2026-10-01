from __future__ import annotations

import pytest

from transcriber.core.app import CaptureChanged
from transcriber.core.capture import CaptureError, CaptureState, DeviceKind
from transcriber.core.gateway import Caption
from transcriber.core.model import SourceKind, TranscriptStatus

from .fakes import FIXTURES, wav_frames


def say(stream, *lines):
    for i, (speaker, text) in enumerate(lines):
        stream.deliver(Caption(text, True, float(i * 3), speaker=speaker, ends_utterance=True))


def test_stopping_a_microphone_capture_turns_the_live_captions_into_the_transcript(
    core, gateway, audio, transcripts_dir, clock
):
    core.start_capture(SourceKind.MICROPHONE, title="Standup")
    assert core.capture_status().state is CaptureState.CAPTURING
    assert [d.id for d in audio.opened] == ["fake-mic"]
    assert b"".join(gateway.stream.sent) == wav_frames(FIXTURES / "hello.wav")
    say(gateway.stream, (0, "Morning everyone, can you hear me?"), (1, "Yes, loud and clear."))
    clock.advance(seconds=12)

    transcript = core.stop_capture()

    assert core.capture_status().state is CaptureState.COMPLETE
    assert gateway.requests == []  # nothing is sent to Deepgram again (ADR-0004)
    assert gateway.stream.closed is True
    assert transcript.source_kind is SourceKind.MICROPHONE
    assert transcript.title == "Standup"
    assert transcript.status is TranscriptStatus.COMPLETE
    assert transcript.source_path is None and transcript.recording_path is None
    assert transcript.duration == 12.0
    assert [(p.speaker, p.start, p.end, p.text) for p in transcript.paragraphs] == [
        (0, 0.0, 3.0, "Morning everyone, can you hear me?"),
        (1, 3.0, 12.0, "Yes, loud and clear."),
    ]
    assert transcript.rendering_path == transcripts_dir / "2026-09-30 14-05 Standup.txt"
    assert transcript.rendering_path.read_text(encoding="utf-8") == (
        "Standup\n"
        "2026-09-30 14:05 · 12 s · Microphone\n"
        "\n"
        "[00:00:00] Speaker 1: Morning everyone, can you hear me?\n"
        "[00:00:03] Speaker 2: Yes, loud and clear.\n"
    )
    assert [e.transcript.id for e in core.library()] == [transcript.id]


def test_text_still_provisional_at_stop_is_kept_rather_than_thrown_away(core, gateway):
    core.start_capture(SourceKind.MICROPHONE)
    gateway.stream.deliver(Caption("Morning everyone,", True, 0.4, speaker=0))
    gateway.stream.deliver(Caption("can you hear", False, 1.5, speaker=0))

    transcript = core.stop_capture()

    assert [p.text for p in transcript.paragraphs] == ["Morning everyone, can you hear"]


def test_a_capture_with_no_speech_still_produces_an_empty_transcript(core, gateway):
    core.start_capture(SourceKind.MICROPHONE, title="Silence")

    transcript = core.stop_capture()

    assert transcript.paragraphs == []
    assert transcript.rendering_path.read_text(encoding="utf-8").startswith("Silence\n")


def test_no_recording_is_written_during_a_capture(core, data_dir):
    core.start_capture(SourceKind.MICROPHONE)
    assert core.capture_status().recording_path is None
    assert not (data_dir / "recordings").exists()

    core.stop_capture()

    assert not (data_dir / "recordings").exists()


def test_the_source_kind_may_be_given_as_its_plain_string(core):
    # Qt widgets hand a StrEnum back as a bare str; the core must treat it as the enum.
    core.start_capture("microphone", title="From a combo box")

    transcript = core.stop_capture()

    assert transcript.source_kind is SourceKind.MICROPHONE


def test_a_system_audio_capture_listens_to_the_loopback_device_and_is_labelled_so(core, gateway, audio, transcripts_dir):
    core.start_capture(SourceKind.SYSTEM_AUDIO, title="Webinar")
    assert [d.id for d in audio.opened] == ["fake-loop"]
    assert gateway.stream.channels == 1
    say(gateway.stream, (0, "Welcome to the webinar."))

    transcript = core.stop_capture()

    assert transcript.source_kind is SourceKind.SYSTEM_AUDIO
    assert transcript.rendering_path == transcripts_dir / "2026-09-30 14-05 Webinar.txt"
    assert transcript.rendering_path.read_text(encoding="utf-8").startswith("Webinar\n2026-09-30 14:05 · 0 s · System Audio\n")
    assert gateway.stream.closed is True


def test_a_system_audio_capture_is_refused_when_there_is_nothing_to_capture_from(core, audio):
    audio.devices = [d for d in audio.devices if d.kind is not DeviceKind.LOOPBACK]

    with pytest.raises(CaptureError, match="output device"):
        core.start_capture(SourceKind.SYSTEM_AUDIO)

    assert core.capture_status().state is CaptureState.IDLE
    assert audio.opened == []


def test_only_one_capture_runs_at_a_time(core, audio):
    core.start_capture(SourceKind.MICROPHONE)

    with pytest.raises(CaptureError, match="already running"):
        core.start_capture(SourceKind.MICROPHONE)

    assert len(audio.opened) == 1
    core.stop_capture()
    core.start_capture(SourceKind.MICROPHONE, title="Second")
    assert core.capture_status().title == "Second"


def test_an_untitled_capture_is_named_after_its_start_time(core, transcripts_dir):
    core.start_capture(SourceKind.MICROPHONE)

    transcript = core.stop_capture()

    assert transcript.title == "2026-09-30 14:05"
    assert transcript.rendering_path == transcripts_dir / "2026-09-30 14-05.txt"


def test_capture_state_changes_are_announced_to_subscribers(core):
    received = []
    core.subscribe(received.append)

    core.start_capture(SourceKind.MICROPHONE, title="Standup")
    core.stop_capture()

    states = [e.status.state for e in received if isinstance(e, CaptureChanged)]
    assert states == [CaptureState.CAPTURING, CaptureState.FINALISING, CaptureState.COMPLETE]
    assert all(e.status.title == "Standup" for e in received if isinstance(e, CaptureChanged))


def test_a_completed_capture_cannot_be_retranscribed(core):
    core.start_capture(SourceKind.MICROPHONE)
    transcript = core.stop_capture()

    with pytest.raises(ValueError, match="re-transcribed"):
        core.retranscribe(transcript.id)
