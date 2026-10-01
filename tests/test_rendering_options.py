from __future__ import annotations

from transcriber.core.app import TranscriberCore
from transcriber.core.model import SourceKind
from transcriber.core.rendering import RenderingOptions

PLAIN = (
    "interview\n"
    "2026-09-30 14:05 · 12 s · File\n"
    "\n"
    "Morning everyone, can you hear me?\n"
    "Yes, loud and clear.\n"
)


def test_rendering_defaults_are_stored_and_applied_to_new_renderings(core, gateway, audio, data_dir, transcripts_dir, clock, audio_file):
    core.update_settings(include_timestamps=False, include_speaker_labels=False)

    transcript = core.transcribe_file(audio_file)

    assert transcript.rendering_path.read_text(encoding="utf-8") == PLAIN
    fresh = TranscriberCore(gateway=gateway, audio_capture=audio, data_dir=data_dir, transcripts_dir=transcripts_dir, clock=clock)
    assert fresh.settings().include_timestamps is False
    assert fresh.settings().include_speaker_labels is False
    assert fresh.settings().custom_vocabulary == []


def test_every_combination_of_the_two_options_renders_as_expected(core, audio_file):
    transcript = core.transcribe_file(audio_file)
    render = lambda ts, sp: core.render(transcript.id, RenderingOptions(ts, sp)).splitlines()[3:]  # noqa: E731

    assert render(True, True) == [
        "[00:00:00] Speaker 1: Morning everyone, can you hear me?",
        "[00:00:03] Speaker 2: Yes, loud and clear.",
    ]
    assert render(True, False) == [
        "[00:00:00] Morning everyone, can you hear me?",
        "[00:00:03] Yes, loud and clear.",
    ]
    assert render(False, True) == [
        "Speaker 1: Morning everyone, can you hear me?",
        "Speaker 2: Yes, loud and clear.",
    ]
    assert render(False, False) == [
        "Morning everyone, can you hear me?",
        "Yes, loud and clear.",
    ]


def test_a_one_off_rendering_changes_neither_the_file_nor_the_defaults(core, audio_file):
    transcript = core.transcribe_file(audio_file)
    before = transcript.rendering_path.read_text(encoding="utf-8")

    text = core.render(transcript.id, RenderingOptions(include_timestamps=False, include_speaker_labels=False))

    assert text == PLAIN
    assert transcript.rendering_path.read_text(encoding="utf-8") == before
    assert core.settings().include_timestamps is True


def test_renaming_a_speaker_updates_the_transcript_and_rewrites_the_rendering(core, audio_file):
    transcript = core.transcribe_file(audio_file)

    renamed = core.rename_speaker(transcript.id, speaker=1, name="Dana")

    assert renamed.speaker_names == {0: "Speaker 1", 1: "Dana"}
    assert core.get_transcript(transcript.id).speaker_names[1] == "Dana"
    assert "[00:00:03] Dana: Yes, loud and clear." in transcript.rendering_path.read_text(encoding="utf-8")


def test_renaming_a_capture_transcript_renames_its_rendering_file(core, transcripts_dir):
    core.start_capture(SourceKind.MICROPHONE)
    transcript = core.stop_capture()
    old_path = transcript.rendering_path
    assert old_path == transcripts_dir / "2026-09-30 14-05.txt"

    renamed = core.rename_transcript(transcript.id, "Weekly sync")

    assert renamed.title == "Weekly sync"
    assert renamed.rendering_path == transcripts_dir / "2026-09-30 14-05 Weekly sync.txt"
    assert not old_path.exists()
    assert renamed.rendering_path.read_text(encoding="utf-8").startswith("Weekly sync\n")
    assert core.library()[0].transcript.title == "Weekly sync"


def test_renaming_a_file_transcript_keeps_the_rendering_next_to_the_file_under_its_name(core, audio_file):
    transcript = core.transcribe_file(audio_file)

    renamed = core.rename_transcript(transcript.id, "Interview with Sam")

    assert renamed.title == "Interview with Sam"
    assert renamed.rendering_path == audio_file.with_suffix(".txt")
    assert renamed.rendering_path.read_text(encoding="utf-8").startswith("Interview with Sam\n")
