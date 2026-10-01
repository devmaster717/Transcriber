from __future__ import annotations

from transcriber.core.capture import CaptureState
from transcriber.core.gateway import Caption, TranscriptionError
from transcriber.core.model import SourceKind, TranscriptStatus


def _capture_that_fails_to_finalise(core, gateway, title="Standup"):
    core.start_capture(SourceKind.MICROPHONE, title=title)
    gateway.stream.deliver(Caption("Morning everyone, can you hear me?", True, 0.4, speaker=0))
    gateway.stream.deliver(Caption("Yes, loud and clear.", True, 3.5, speaker=1))
    gateway.stream.deliver(Caption("One more th", False, 5.0, speaker=1))
    gateway.fail_with = TranscriptionError("Could not reach Deepgram. Check your connection.")
    return core.stop_capture()


def test_a_failed_finalising_pass_keeps_the_recording_and_saves_the_captions_as_a_provisional_transcript(
    core, gateway
):
    provisional = _capture_that_fails_to_finalise(core, gateway)

    status = core.capture_status()
    assert status.state is CaptureState.NEEDS_RETRY
    assert status.recording_path is not None and status.recording_path.exists()
    assert status.transcript_id == provisional.id

    assert provisional.status is TranscriptStatus.PROVISIONAL
    assert provisional.title == "Standup"
    assert provisional.source_kind is SourceKind.MICROPHONE
    assert [(p.speaker, p.text) for p in provisional.paragraphs] == [
        (0, "Morning everyone, can you hear me?"),
        (1, "Yes, loud and clear."),
    ]
    assert provisional.recording_path == status.recording_path
    assert provisional.rendering_path is None

    (entry,) = core.library()
    assert entry.transcript.status is TranscriptStatus.PROVISIONAL
    assert entry.missing_files is False


def test_retry_reruns_the_pass_and_completes_the_transcript_under_the_same_id(core, gateway, transcripts_dir):
    provisional = _capture_that_fails_to_finalise(core, gateway)
    recording = provisional.recording_path
    gateway.fail_with = None

    done = core.retranscribe(provisional.id)

    assert done.id == provisional.id
    assert done.status is TranscriptStatus.COMPLETE
    assert done.title == "Standup"
    assert [p.text for p in done.paragraphs] == ["Morning everyone, can you hear me?", "Yes, loud and clear."]
    assert done.rendering_path == transcripts_dir / "2026-09-30 14-05 Standup.txt"
    assert done.rendering_path.read_text(encoding="utf-8").startswith("Standup\n2026-09-30 14:05 · 12 s · Microphone\n")
    assert done.recording_path is None
    assert not recording.exists()
    assert core.capture_status().state is CaptureState.COMPLETE
    assert [e.transcript.status for e in core.library()] == [TranscriptStatus.COMPLETE]
    assert gateway.requests == [recording, recording]


def test_a_second_failure_keeps_the_recording_and_the_provisional_transcript(core, gateway):
    provisional = _capture_that_fails_to_finalise(core, gateway)

    again = core.retranscribe(provisional.id)

    assert again.status is TranscriptStatus.PROVISIONAL
    assert again.recording_path.exists()
    assert core.capture_status().state is CaptureState.NEEDS_RETRY
    assert len(core.library()) == 1


def test_a_new_capture_may_start_while_an_old_one_still_needs_retry(core, gateway):
    provisional = _capture_that_fails_to_finalise(core, gateway)
    gateway.fail_with = None

    core.start_capture(SourceKind.MICROPHONE, title="Next call")
    done = core.stop_capture()

    assert done.status is TranscriptStatus.COMPLETE
    assert {e.transcript.status for e in core.library()} == {TranscriptStatus.COMPLETE, TranscriptStatus.PROVISIONAL}
    assert core.retranscribe(provisional.id).status is TranscriptStatus.COMPLETE
