from __future__ import annotations

from datetime import timedelta

from transcriber.core.gateway import TranscriptionResult
from transcriber.core.model import Paragraph, SourceKind

from .conftest import FIXED_NOW


def test_library_lists_every_transcript_newest_first(core, clock, make_audio_file):
    core.transcribe_file(make_audio_file("monday.mp3"))
    clock.advance(days=1)
    core.transcribe_file(make_audio_file("tuesday.wav"))

    entries = core.library()

    assert [(e.transcript.title, e.transcript.source_kind) for e in entries] == [
        ("tuesday", SourceKind.FILE),
        ("monday", SourceKind.FILE),
    ]
    assert [e.transcript.created.day for e in entries] == [1, 30]
    assert all(e.transcript.duration == 12.5 for e in entries)


def test_entries_report_when_their_files_have_gone_missing(core, make_audio_file):
    kept = core.transcribe_file(make_audio_file("kept.mp3"))
    lost_rendering = core.transcribe_file(make_audio_file("lost-rendering.mp3"))
    lost_source = core.transcribe_file(make_audio_file("lost-source.mp3"))
    lost_rendering.rendering_path.unlink()
    lost_source.source_path.unlink()

    by_title = {e.transcript.title: e for e in core.library()}

    assert by_title["kept"].missing_files is False
    assert (by_title["lost-rendering"].rendering_found, by_title["lost-rendering"].source_found) == (False, True)
    assert (by_title["lost-source"].rendering_found, by_title["lost-source"].source_found) == (True, False)
    assert by_title["lost-source"].missing_files is True


def test_removing_an_entry_leaves_the_rendering_and_source_files_alone(core, audio_file):
    transcript = core.transcribe_file(audio_file)

    core.remove(transcript.id)

    assert core.library() == []
    assert transcript.rendering_path.exists()
    assert audio_file.exists()


def test_reopening_a_transcribed_file_returns_the_existing_transcript_without_transcribing_again(
    core, gateway, audio_file
):
    first = core.transcribe_file(audio_file)

    again = core.transcribe_file(audio_file)

    assert again == first
    assert len(gateway.requests) == 1
    assert len(core.library()) == 1


def test_retranscribe_asks_the_gateway_again_and_replaces_the_transcript_and_rendering(
    core, gateway, clock, audio_file
):
    first = core.transcribe_file(audio_file)
    gateway.result = TranscriptionResult(
        duration=8.0,
        paragraphs=[Paragraph(start=0.0, end=8.0, speaker=0, text="Take two, much clearer.")],
    )
    clock.advance(hours=2)

    redone = core.retranscribe(first.id)

    assert len(gateway.requests) == 2
    assert redone.id == first.id
    assert redone.created == FIXED_NOW + timedelta(hours=2)
    assert [p.text for p in redone.paragraphs] == ["Take two, much clearer."]
    assert redone.speaker_names == {0: "Speaker 1"}
    assert core.library()[0].transcript == redone
    assert len(core.library()) == 1
    assert redone.rendering_path.read_text(encoding="utf-8") == (
        "interview\n"
        "2026-09-30 16:05 · 8 s · File\n"
        "\n"
        "[00:00:00] Speaker 1: Take two, much clearer.\n"
    )
