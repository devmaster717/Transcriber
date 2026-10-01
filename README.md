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
uv run transcriber
```

On first run the app asks for your Deepgram API key and stores it in Windows Credential Manager.
It never lands in a file the app writes. If no key is stored, the `DEEPGRAM_API_KEY` environment
variable is used instead, which is handy for development. **Settings…** also lets you pick which
microphone and which output device to capture (Windows defaults preselected), where Capture
transcripts go, and the Rendering defaults. If Deepgram rejects the key, the settings screen opens
with the reason.

## Build a standalone app

```bash
uv run python scripts/build.py
```

This produces `dist/Transcriber/` with `Transcriber.exe` inside, and zips it to
`dist/Transcriber-<version>-windows.zip`. Unzip anywhere on a Windows machine and run the
executable; Python is not needed. The build is **unsigned**, so Windows SmartScreen shows
"Windows protected your PC" the first time: click **More info**, then **Run anyway**.

To confirm a build works on a machine, run its self-check, which exercises the audio devices, a
short loopback capture, the credential store and both Deepgram request paths (with a bogus key, so
"rejected" is the expected outcome) and writes a JSON report:

```bash
dist/Transcriber/Transcriber.exe --self-check report.json
```

## Tests

```bash
uv run pytest
```

Every test drives the headless core with a fake Deepgram gateway and a temporary data directory.
One contract test talks to the real Deepgram API and only runs when `DEEPGRAM_API_KEY` is set:

```bash
DEEPGRAM_API_KEY=... uv run pytest tests/test_deepgram_gateway.py
```

## Transcribing files

Open files or drop them onto the window; they are transcribed one at a time. In the queue,
**Remove** takes a waiting or finished item out, **Cancel** abandons the upload in progress (the
result, if any, is discarded), **Retry** re-queues a failed item, and **Clear finished** empties
done, failed and cancelled items. A large file takes a while: the app waits up to ten minutes for
Deepgram to process it, and says so if that runs out. If Deepgram rejects a format, the error
names the reason; a video container Deepgram does not accept can be converted to MP4 or M4A first.

## Capturing

Pick a Source, optionally type a title, and press **Start capture**. While capturing, Live
Captions stream into the right-hand panel, built into whole sentences as Deepgram returns them;
provisional text is grey and is replaced as it is finalised. Press **Stop** and, within a second
or two, the Live Captions become the Transcript, written to `Documents/Transcriber/`. Nothing is
recorded and nothing is sent to Deepgram a second time (see ADR-0004). If the live connection
drops the app reconnects with backoff and says so, but the words spoken during the gap are lost;
a Capture cannot be re-transcribed afterwards.

Sources: **Meeting Capture** (the default: Microphone and System Audio together, so one Transcript
covers both sides of a call, with your own words attributed to the Speaker "You"), **Microphone**
(the default Windows microphone) and **System Audio** (whatever the default output device is
playing, such as a Zoom or Teams call, captured through WASAPI loopback; see ADR-0001). A Meeting
Capture records two channels and asks Deepgram for a per-channel transcript. System Audio captures
the whole output device, so anything else playing through it is transcribed too. Device selection
is a later ticket.

**Always on top** keeps the window, and so the Live Captions, above the call you are in. The window
remembers its size, position, always-on-top state and the last selected Transcript between runs.

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
