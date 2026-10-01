from __future__ import annotations

from transcriber.core.app import TranscriberCore


def test_window_state_lives_in_the_settings_document_and_survives_a_restart(core, gateway, audio, data_dir, transcripts_dir, clock):
    core.update_settings(window={"geometry": "AdnQywADAAA=", "always_on_top": True, "selected": "abc123"})

    fresh = TranscriberCore(
        gateway=gateway, audio_capture=audio, data_dir=data_dir, transcripts_dir=transcripts_dir, clock=clock,
        env={"DEEPGRAM_API_KEY": "test-key"},
    )

    assert fresh.settings().window == {"geometry": "AdnQywADAAA=", "always_on_top": True, "selected": "abc123"}
    assert fresh.settings().include_timestamps is True  # the rest of the document is untouched
