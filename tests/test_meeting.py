from __future__ import annotations

from transcriber.core.app import TranscriberCore
from transcriber.core.gateway import Caption, TranscriptionResult
from transcriber.core.model import Paragraph, SourceKind

from .fakes import FakeAudioCapture

MIC = b"\x01\x00" * 1600
SYS = b"\x02\x00" * 1600
INTERLEAVED = b"\x01\x00\x02\x00" * 1600


def meeting_result() -> TranscriptionResult:
    """What Deepgram returns for a two-channel Recording: channel 0 is the Microphone, 1 is System Audio."""
    return TranscriptionResult(
        duration=20.0,
        paragraphs=[
            Paragraph(start=0.4, end=3.1, speaker=0, text="Morning everyone, can you hear me?", channel=0),
            Paragraph(start=9.0, end=10.0, speaker=0, text="Great, let's start.", channel=0),
            Paragraph(start=3.5, end=5.0, speaker=0, text="Yes, loud and clear.", channel=1),
            Paragraph(start=6.0, end=8.0, speaker=1, text="Me too.", channel=1),
        ],
    )


def make_core(gateway, data_dir, transcripts_dir, clock):
    audio = FakeAudioCapture(auto_play=False)
    core = TranscriberCore(
        gateway=gateway, audio_capture=audio, data_dir=data_dir, transcripts_dir=transcripts_dir, clock=clock, env={"DEEPGRAM_API_KEY": "test-key"}
    )
    return core, audio


def wav_channels(wav_bytes: bytes) -> int:
    return int.from_bytes(wav_bytes[22:24], "little")


def test_a_meeting_capture_interleaves_both_devices_and_attributes_the_microphone_to_you(
    gateway, data_dir, transcripts_dir, clock
):
    core, audio = make_core(gateway, data_dir, transcripts_dir, clock)
    gateway.result = meeting_result()

    core.start_capture(SourceKind.MEETING, title="Team sync")

    assert [d.id for d in audio.opened] == ["fake-mic", "fake-loop"]
    assert gateway.stream.channels == 2
    audio.deliver(MIC, "fake-mic")
    assert gateway.stream.sent == []  # nothing goes out until both channels have a chunk
    audio.deliver(SYS, "fake-loop")
    assert b"".join(gateway.stream.sent) == INTERLEAVED

    transcript = core.stop_capture()

    assert gateway.multichannel_requests == [True]
    assert wav_channels(gateway.request_bytes[0]) == 2
    assert gateway.request_bytes[0][44:] == INTERLEAVED
    assert transcript.source_kind is SourceKind.MEETING
    assert [(transcript.speaker_name(p.speaker), p.text) for p in transcript.paragraphs] == [
        ("You", "Morning everyone, can you hear me?"),
        ("Speaker 1", "Yes, loud and clear."),
        ("Speaker 2", "Me too."),
        ("You", "Great, let's start."),
    ]
    assert transcript.rendering_path.read_text(encoding="utf-8") == (
        "Team sync\n"
        "2026-09-30 14:05 · 20 s · Meeting Capture\n"
        "\n"
        "[00:00:00] You: Morning everyone, can you hear me?\n"
        "[00:00:03] Speaker 1: Yes, loud and clear.\n"
        "[00:00:06] Speaker 2: Me too.\n"
        "[00:00:09] You: Great, let's start.\n"
    )


def test_meeting_is_the_default_capture_kind(gateway, data_dir, transcripts_dir, clock):
    core, audio = make_core(gateway, data_dir, transcripts_dir, clock)

    core.start_capture()

    assert core.capture_status().kind is SourceKind.MEETING
    assert len(audio.opened) == 2


def test_a_stalled_device_is_padded_with_silence_so_the_channels_stay_aligned(
    gateway, data_dir, transcripts_dir, clock
):
    core, audio = make_core(gateway, data_dir, transcripts_dir, clock)
    core.start_capture(SourceKind.MEETING)
    for _ in range(3):
        audio.deliver(MIC, "fake-mic")

    core.stop_capture()

    assert gateway.request_bytes[0][44:] == b"\x01\x00\x00\x00" * (1600 * 3)


def test_live_captions_in_a_meeting_carry_the_channel(gateway, data_dir, transcripts_dir, clock):
    core, audio = make_core(gateway, data_dir, transcripts_dir, clock)
    core.start_capture(SourceKind.MEETING)

    gateway.stream.deliver(Caption("Can you hear me?", True, 0.4, speaker=0, channel=0))
    gateway.stream.deliver(Caption("Loud and clear.", True, 3.5, speaker=0, channel=1))

    assert [(c.channel, c.text) for c in core.live_captions().final] == [
        (0, "Can you hear me?"),
        (1, "Loud and clear."),
    ]
