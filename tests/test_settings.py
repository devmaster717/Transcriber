from __future__ import annotations

import json

import pytest

from transcriber.core.app import ApiKeyProblem, TranscriberCore
from transcriber.core.capture import AudioDevice, DeviceKind
from transcriber.core.gateway import MissingKeyError, RejectedKeyError
from transcriber.core.model import SourceKind
from transcriber.core.queue import QueueState

from .fakes import FakeAudioCapture, FakeCredentialStore


def make_core(gateway, data_dir, transcripts_dir, clock, *, credentials=None, env=None, audio=None):
    return TranscriberCore(
        gateway=gateway,
        audio_capture=audio or FakeAudioCapture(),
        data_dir=data_dir,
        transcripts_dir=transcripts_dir,
        clock=clock,
        credentials=credentials if credentials is not None else FakeCredentialStore(),
        env=env if env is not None else {},
    )


def test_the_stored_key_wins_over_the_environment_and_neither_means_no_key(gateway, data_dir, transcripts_dir, clock):
    store = FakeCredentialStore()
    core = make_core(gateway, data_dir, transcripts_dir, clock, credentials=store, env={})
    assert core.api_key() is None

    core = make_core(gateway, data_dir, transcripts_dir, clock, credentials=store, env={"DEEPGRAM_API_KEY": "env-key"})
    assert core.api_key() == "env-key"

    core.set_api_key("stored-key")
    assert core.api_key() == "stored-key"
    assert store.key == "stored-key"
    assert "stored-key" not in json.dumps(core.settings().__dict__)
    assert not (data_dir / "settings.json").exists() or "stored-key" not in (data_dir / "settings.json").read_text()


def test_without_any_key_transcribing_and_capturing_are_refused_before_deepgram_is_called(
    gateway, data_dir, transcripts_dir, clock, audio_file
):
    core = make_core(gateway, data_dir, transcripts_dir, clock)

    with pytest.raises(MissingKeyError):
        core.transcribe_file(audio_file)
    with pytest.raises(MissingKeyError):
        core.start_capture(SourceKind.MICROPHONE)

    assert gateway.requests == []
    assert gateway.streams == []


def test_a_rejected_key_fails_the_queued_file_and_announces_a_key_problem(gateway, data_dir, transcripts_dir, clock, audio_file):
    core = make_core(gateway, data_dir, transcripts_dir, clock, env={"DEEPGRAM_API_KEY": "bad"})
    gateway.fail_with = RejectedKeyError("Deepgram rejected the API key.")
    received = []
    core.subscribe(received.append)
    core.enqueue([audio_file])

    item = core.process_next()

    assert item.state is QueueState.FAILED
    assert item.error == "Deepgram rejected the API key."
    problems = [e for e in received if isinstance(e, ApiKeyProblem)]
    assert [p.message for p in problems] == ["Deepgram rejected the API key."]


def test_chosen_devices_are_used_and_a_vanished_device_falls_back_to_the_default(
    gateway, data_dir, transcripts_dir, clock
):
    audio = FakeAudioCapture()
    audio.devices += [
        AudioDevice(id="desk-mic", name="Desk Microphone", kind=DeviceKind.INPUT),
        AudioDevice(id="monitor-loop", name="Monitor [Loopback]", kind=DeviceKind.LOOPBACK),
    ]
    core = make_core(gateway, data_dir, transcripts_dir, clock, env={"DEEPGRAM_API_KEY": "k"}, audio=audio)
    core.update_settings(microphone_device_id="desk-mic", output_device_id="monitor-loop")

    core.start_capture(SourceKind.MEETING)
    core.stop_capture()
    assert [d.id for d in audio.opened] == ["desk-mic", "monitor-loop"]

    core.update_settings(microphone_device_id="unplugged-mic")
    core.start_capture(SourceKind.MICROPHONE)
    core.stop_capture()
    assert audio.opened[-1].id == "fake-mic"


def test_the_transcripts_folder_setting_moves_capture_renderings(gateway, data_dir, transcripts_dir, clock, tmp_path):
    core = make_core(gateway, data_dir, transcripts_dir, clock, env={"DEEPGRAM_API_KEY": "k"})
    elsewhere = tmp_path / "Notes" / "Calls"
    core.update_settings(transcripts_dir=str(elsewhere))

    core.start_capture(SourceKind.MICROPHONE, title="Standup")
    transcript = core.stop_capture()

    assert transcript.rendering_path == elsewhere / "2026-09-30 14-05 Standup.txt"
    assert transcript.rendering_path.exists()
