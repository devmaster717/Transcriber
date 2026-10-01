from __future__ import annotations

from transcriber.core.app import QueueChanged, TranscriberCore
from transcriber.core.gateway import TranscriptionError
from transcriber.core.queue import QueueState


def test_enqueued_files_are_transcribed_one_at_a_time_in_order(core, gateway, make_audio_file):
    first, second, third = (make_audio_file(n) for n in ("a.mp3", "b.mp3", "c.mp3"))

    items = core.enqueue([first, second, third])
    assert [(i.path, i.state) for i in items] == [
        (first, QueueState.WAITING),
        (second, QueueState.WAITING),
        (third, QueueState.WAITING),
    ]

    done = core.process_next()

    assert done.path == first
    assert done.state == QueueState.DONE
    assert core.get_transcript(done.transcript_id).source_path == first
    assert [i.state for i in core.queue()] == [QueueState.DONE, QueueState.WAITING, QueueState.WAITING]
    assert gateway.requests == [first]

    core.process_next()
    core.process_next()

    assert [i.state for i in core.queue()] == [QueueState.DONE] * 3
    assert gateway.requests == [first, second, third]
    assert core.process_next() is None


def test_a_failing_file_is_marked_failed_and_the_rest_still_go_through(core, gateway, make_audio_file):
    good, bad, also_good = (make_audio_file(n) for n in ("good.mp3", "bad.mp3", "also-good.mp3"))
    gateway.fail_for[bad] = TranscriptionError("Deepgram returned an error (503).")
    core.enqueue([good, bad, also_good])

    while core.process_next() is not None:
        pass

    states = [(i.state, i.error) for i in core.queue()]
    assert states == [
        (QueueState.DONE, None),
        (QueueState.FAILED, "Deepgram returned an error (503)."),
        (QueueState.DONE, None),
    ]
    assert sorted(e.transcript.title for e in core.library()) == ["also-good", "good"]


def test_retrying_a_failed_file_puts_it_back_in_line(core, gateway, make_audio_file):
    bad = make_audio_file("bad.mp3")
    gateway.fail_for[bad] = TranscriptionError("Could not reach Deepgram. Check your connection.")
    (item,) = core.enqueue([bad])
    core.process_next()
    assert core.queue()[0].state == QueueState.FAILED

    del gateway.fail_for[bad]
    retried = core.retry(item.id)
    assert (retried.state, retried.error) == (QueueState.WAITING, None)

    done = core.process_next()

    assert done.id == item.id
    assert done.state == QueueState.DONE
    assert gateway.requests == [bad, bad]


def test_a_file_over_the_size_limit_is_rejected_without_a_request(gateway, data_dir, clock, make_audio_file):
    core = TranscriberCore(gateway=gateway, data_dir=data_dir, clock=clock, max_file_bytes=100, env={"DEEPGRAM_API_KEY": "test-key"})
    huge = make_audio_file("huge.wav")
    huge.write_bytes(b"\0" * 101)
    core.enqueue([huge])

    item = core.process_next()

    assert item.state == QueueState.FAILED
    assert item.error == "File is larger than Deepgram's 2 GB limit."
    assert gateway.requests == []


def test_a_waiting_file_can_be_removed_from_the_queue(core, gateway, make_audio_file):
    first, second = make_audio_file("a.mp3"), make_audio_file("b.mp3")
    items = core.enqueue([first, second])

    core.cancel(items[1].id)

    assert [i.path for i in core.queue()] == [first]
    core.process_next()
    assert gateway.requests == [first]


def test_cancelling_the_file_being_transcribed_discards_its_result_and_the_queue_moves_on(core, gateway, make_audio_file):
    first, second = make_audio_file("long.mkv"), make_audio_file("next.mp3")
    items = core.enqueue([first, second])
    gateway.during_request = lambda: core.cancel(items[0].id)  # the user presses Cancel mid-upload

    cancelled = core.process_next()

    assert cancelled.state is QueueState.CANCELLED
    assert gateway.cancel_calls == 1
    assert core.library() == []  # the result that came back anyway is thrown away
    assert not first.with_suffix(".txt").exists()
    gateway.during_request = None
    assert core.process_next().state is QueueState.DONE
    assert [e.transcript.title for e in core.library()] == ["next"]


def test_finished_and_failed_items_can_be_cleared_in_one_go(core, gateway, make_audio_file):
    good, bad, waiting = make_audio_file("good.mp3"), make_audio_file("bad.mp3"), make_audio_file("later.mp3")
    gateway.fail_for[bad] = TranscriptionError("Deepgram returned an error (503).")
    core.enqueue([good, bad, waiting])
    core.process_next()
    core.process_next()

    core.clear_finished()

    assert [(i.path.name, i.state) for i in core.queue()] == [("later.mp3", QueueState.WAITING)]


def test_queue_changes_are_announced_to_subscribers(core, make_audio_file):
    path = make_audio_file("a.mp3")
    received = []
    core.subscribe(received.append)

    (item,) = core.enqueue([path])
    core.process_next()

    queue_states = [e.item.state for e in received if isinstance(e, QueueChanged)]
    assert queue_states == [QueueState.WAITING, QueueState.TRANSCRIBING, QueueState.DONE]
    assert all(e.item.id == item.id for e in received if isinstance(e, QueueChanged))
