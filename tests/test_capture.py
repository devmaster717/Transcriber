from __future__ import annotations

import pytest

from transcriber.core.app import CaptureChanged
from transcriber.core.capture import CaptureError, CaptureState
from transcriber.core.gateway import TranscriptionError
from transcriber.core.model import SourceKind, TranscriptStatus

from .fakes import FIXTURES, wav_frames


def test_stopping_a_microphone_capture_produces_a_transcript_from_the_recording(
    core, gateway, audio, transcripts_dir
):
    core.start_capture(SourceKind.MICROPHONE, title="Standup")
    assert core.capture_status().state is CaptureState.CAPTURING
    assert [d.id for d in audio.opened] == ["fake-mic"]

    transcript = core.stop_capture()

    assert core.capture_status().state is CaptureState.COMPLETE
    assert transcript.source_kind is SourceKind.MICROPHONE
    assert transcript.title == "Standup"
    assert transcript.source_path is None
    assert [p.text for p in transcript.paragraphs] == [
        "Morning everyone, can you hear me?",
        "Yes, loud and clear.",
    ]
    assert transcript.rendering_path == transcripts_dir / "2026-09-30 14-05 Standup.txt"
    assert transcript.rendering_path.read_text(encoding="utf-8").startswith(
        "Standup\n2026-09-30 14:05 · 12 s · Microphone\n"
    )
    assert [e.transcript.id for e in core.library()] == [transcript.id]
    # The gateway received the whole Recording as a 16 kHz mono WAV.
    assert gateway.request_bytes[0][:4] == b"RIFF"
    assert gateway.request_bytes[0][44:] == wav_frames(FIXTURES / "hello.wav")


def test_the_recording_exists_during_a_capture_and_is_gone_once_the_transcript_is_written(core):
    core.start_capture(SourceKind.MICROPHONE)
    recording = core.capture_status().recording_path
    assert recording is not None and recording.exists()

    transcript = core.stop_capture()

    assert not recording.exists()
    assert transcript.recording_path is None
    assert core.capture_status().transcript_id == transcript.id


def test_the_recording_survives_a_failed_finalising_pass(core, gateway):
    gateway.fail_with = TranscriptionError("Could not reach Deepgram. Check your connection.")
    core.start_capture(SourceKind.MICROPHONE)
    recording = core.capture_status().recording_path

    provisional = core.stop_capture()

    assert recording.exists()
    assert provisional.status is TranscriptStatus.PROVISIONAL
    assert core.capture_status().state is CaptureState.NEEDS_RETRY


def test_the_source_kind_may_be_given_as_its_plain_string(core):
    # Qt widgets hand a StrEnum back as a bare str; the core must treat it as the enum.
    core.start_capture("microphone", title="From a combo box")

    transcript = core.stop_capture()

    assert transcript.source_kind is SourceKind.MICROPHONE


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
