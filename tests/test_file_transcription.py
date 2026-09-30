from __future__ import annotations

from transcriber.core.model import SourceKind
from transcriber.core.app import TranscriberCore, TranscriptReady


def test_opening_a_file_produces_a_speaker_labelled_transcript(core, audio_file):
    transcript = core.transcribe_file(audio_file)

    assert transcript.source_kind == SourceKind.FILE
    assert transcript.source_path == audio_file
    assert transcript.title == "interview"
    assert transcript.duration == 12.5
    assert [(p.speaker, p.start, p.end, p.text) for p in transcript.paragraphs] == [
        (0, 0.4, 3.1, "Morning everyone, can you hear me?"),
        (1, 3.5, 5.0, "Yes, loud and clear."),
    ]
    assert transcript.speaker_names == {0: "Speaker 1", 1: "Speaker 2"}


def test_transcript_is_retrievable_by_a_fresh_core_over_the_same_data_dir(core, audio_file, gateway, data_dir):
    transcript = core.transcribe_file(audio_file)

    fresh = TranscriberCore(gateway=gateway, data_dir=data_dir)

    assert fresh.get_transcript(transcript.id) == transcript


def test_default_rendering_is_written_next_to_the_file(core, audio_file):
    transcript = core.transcribe_file(audio_file)

    assert transcript.rendering_path == audio_file.with_suffix(".txt")
    assert transcript.rendering_path.read_text(encoding="utf-8") == (
        "interview\n"
        "2026-09-30 14:05 · 12 s · File\n"
        "\n"
        "[00:00:00] Speaker 1: Morning everyone, can you hear me?\n"
        "[00:00:03] Speaker 2: Yes, loud and clear.\n"
    )


def test_subscribers_are_told_when_a_transcript_is_ready(core, audio_file):
    received = []
    core.subscribe(received.append)

    transcript = core.transcribe_file(audio_file)

    assert received == [TranscriptReady(transcript)]
