# Transcriber

A personal Windows desktop app that turns audio into text with Deepgram. Audio comes from a file on
disk, the microphone, or the computer's own sound output (a Zoom or Teams call).

The vocabulary used in code and docs is defined in [CONTEXT.md](CONTEXT.md). Decisions that are hard
to reverse are recorded in [docs/adr/](docs/adr/). The spec and tickets live in the repo's GitHub
Issues.

## Run from source

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
DEEPGRAM_API_KEY=... uv run transcriber
```

The key comes from the `DEEPGRAM_API_KEY` environment variable for now; a settings screen that
stores it in Windows Credential Manager is a later ticket.

## Tests

```bash
uv run pytest
```

Every test drives the headless core with a fake Deepgram gateway and a temporary data directory.
One contract test talks to the real Deepgram API and only runs when `DEEPGRAM_API_KEY` is set:

```bash
DEEPGRAM_API_KEY=... uv run pytest tests/test_deepgram_gateway.py
```

## Capturing

Pick a Source, optionally type a title, and press **Start capture**. While capturing, Live
Captions stream into the right-hand panel; provisional text is grey and is replaced as Deepgram
finalises it. If the live connection drops the app reconnects with backoff and says so, and the
Recording keeps going regardless. Press **Stop** and the Recording is sent to Deepgram, the
Transcript is written to `Documents/Transcriber/`, and the Recording is deleted (see ADR-0002 and
ADR-0003: the captions are display-only, the Transcript comes from the Recording). If the Deepgram
call fails the Recording is kept and the Live Captions are saved as a **provisional** Transcript;
select it in the Library and press **Retry** to send the Recording again. The Recording is deleted
only once a Retry succeeds.

Sources: **Meeting Capture** (the default: Microphone and System Audio together, so one Transcript
covers both sides of a call, with your own words attributed to the Speaker "You"), **Microphone**
(the default Windows microphone) and **System Audio** (whatever the default output device is
playing, such as a Zoom or Teams call, captured through WASAPI loopback; see ADR-0001). A Meeting
Capture records two channels and asks Deepgram for a per-channel transcript. System Audio captures
the whole output device, so anything else playing through it is transcribed too. Device selection
is a later ticket.

## Reading, copying and renaming

Above the viewer, **Timestamps** and **Speaker labels** change what is shown without touching the
Rendering file. **Make default** stores the current toggles so new Rendering files use them.
**Copy** puts the displayed text on the clipboard. **Rename…** changes a Transcript's title (a
Capture's Rendering file is renamed to match; a File's keeps the File's name). **Rename speaker…**
turns "Speaker 2" into a real name everywhere, including the Rendering file. The app keeps the full
Transcript, so a Rendering can always be regenerated (ADR-0002).

## Layout

- `src/transcriber/core/`: the headless core. No Qt imports. The public API is `TranscriberCore`.
- `src/transcriber/deepgram_gateway.py`: the real transcription gateway over the Deepgram SDK.
- `src/transcriber/audio/`: platform implementations of the audio capture interface (Windows WASAPI).
- `src/transcriber/gui/`: the thin PySide6 shell.
- `tests/`: core tests, the fakes, and fixtures.
