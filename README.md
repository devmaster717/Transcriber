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

## Layout

- `src/transcriber/core/`: the headless core. No Qt imports. The public API is `TranscriberCore`.
- `src/transcriber/deepgram_gateway.py`: the real transcription gateway over the Deepgram SDK.
- `src/transcriber/gui/`: the thin PySide6 shell.
- `tests/`: core tests, the fakes, and fixtures.
