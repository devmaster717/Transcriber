"""The real transcription gateway: Deepgram, via the official SDK (v7).

Requests use Nova-3, English, smart formatting, diarization, paragraphs and punctuation,
as fixed by the spec. The core never sees SDK types; everything is mapped to domain terms here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from deepgram import DeepgramClient
from deepgram.core.api_error import ApiError
from deepgram.core.events import EventType
from websockets.exceptions import InvalidStatus

from .core.capture import SAMPLE_RATE
from .core.gateway import (
    Caption,
    MissingKeyError,
    OnCaption,
    OnStreamError,
    RejectedKeyError,
    TranscriptionError,
    TranscriptionResult,
)
from .core.model import Paragraph

PRERECORDED_OPTIONS: dict[str, Any] = {
    "model": "nova-3",
    "language": "en",
    "smart_format": True,
    "diarize": True,
    "paragraphs": True,
    "punctuate": True,
}

LIVE_OPTIONS: dict[str, Any] = {
    "model": "nova-3",
    "language": "en",
    "encoding": "linear16",
    "sample_rate": SAMPLE_RATE,
    "smart_format": True,
    "diarize": True,
    "punctuate": True,
    "interim_results": True,
}


class _DeepgramStream:
    """One live connection. Listens on its own thread; `send` and `close` come from other threads."""

    def __init__(self, context, socket, on_caption: OnCaption, on_error: OnStreamError) -> None:
        self._context = context
        self._socket = socket
        self._on_caption = on_caption
        self._on_error = on_error
        self._closed = False
        socket.on(EventType.MESSAGE, self._on_message)
        socket.on(EventType.ERROR, self._on_socket_error)
        socket.on(EventType.CLOSE, self._on_socket_close)
        self._thread = threading.Thread(target=self._listen, name="deepgram-live", daemon=True)
        self._thread.start()

    def _listen(self) -> None:
        try:
            self._socket.start_listening()
        except Exception as e:  # noqa: BLE001 - any listener failure is "the connection is gone"
            self._report(f"Live connection lost: {e}")
        finally:
            try:
                self._context.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    def _on_message(self, message: Any) -> None:
        if getattr(message, "type", None) != "Results":
            return
        alternatives = message.channel.alternatives
        if not alternatives or not alternatives[0].transcript:
            return
        best = alternatives[0]
        words = best.words or []
        self._on_caption(
            Caption(
                text=best.transcript,
                is_final=bool(message.is_final),
                start=float(message.start),
                speaker=words[0].speaker if words else None,
                channel=int(message.channel_index[0]) if message.channel_index else 0,
            )
        )

    def _on_socket_error(self, error: Any) -> None:
        self._report(f"Live connection error: {error}")

    def _on_socket_close(self, _: Any) -> None:
        self._report("Live connection closed.")

    def _report(self, reason: str) -> None:
        if not self._closed:
            self._closed = True
            self._on_error(reason)

    def send(self, chunk: bytes) -> None:
        if not self._closed:
            self._socket.send_media(chunk)

    def close(self) -> None:
        self._closed = True
        try:
            self._socket.send_close_stream()
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
        self._thread.join(timeout=3.0)


KeyProvider = Callable[[], str | None]


class DeepgramGateway:
    """Reads the API key through `key_provider` at request time, so a key saved in Settings takes effect at once."""

    def __init__(self, key_provider: KeyProvider | str) -> None:
        self._key_provider: KeyProvider = (lambda: key_provider) if isinstance(key_provider, str) else key_provider

    @property
    def _client(self) -> DeepgramClient:
        key = self._key_provider()
        if not key:
            raise MissingKeyError("No Deepgram API key. Enter one in Settings.")
        return DeepgramClient(api_key=key)

    def open_stream(self, on_caption: OnCaption, on_error: OnStreamError, channels: int = 1) -> _DeepgramStream:
        options = dict(LIVE_OPTIONS, channels=channels)
        if channels > 1:
            options["multichannel"] = True
        context = self._client.listen.v1.connect(**options)
        try:
            socket = context.__enter__()
        except ApiError as e:
            raise _refused(e.status_code) from e
        except InvalidStatus as e:  # the websockets library's own handshake rejection, which the SDK lets through
            raise _refused(e.response.status_code) from e
        except Exception as e:  # noqa: BLE001 - DNS, TLS, socket errors from the websocket layer
            raise TranscriptionError("Could not reach Deepgram. Check your connection.") from e
        return _DeepgramStream(context, socket, on_caption, on_error)


def _refused(status_code: int | None) -> TranscriptionError:
    if status_code in (401, 403):
        return RejectedKeyError("Deepgram rejected the API key.")
    return TranscriptionError(f"Deepgram refused the live connection ({status_code}).")

    def transcribe(self, audio: Path, multichannel: bool = False) -> TranscriptionResult:
        options = dict(PRERECORDED_OPTIONS)
        if multichannel:
            options["multichannel"] = True
        try:
            response = self._client.listen.v1.media.transcribe_file(request=audio.read_bytes(), **options)
        except ApiError as e:
            if e.status_code in (401, 403):
                raise RejectedKeyError("Deepgram rejected the API key.") from e
            raise TranscriptionError(f"Deepgram returned an error ({e.status_code}).") from e
        except httpx.HTTPError as e:
            raise TranscriptionError("Could not reach Deepgram. Check your connection.") from e
        return to_result(response)


def to_result(response: Any) -> TranscriptionResult:
    """Map a Deepgram pre-recorded response (SDK model or parsed JSON) to domain terms.

    Paragraphs come from the first alternative of each channel. Channel order is preserved,
    and within a channel, paragraph order. A channel with no paragraphs but a transcript
    (Deepgram omits paragraphs for very short audio) becomes one paragraph.
    """
    payload = response.model_dump() if hasattr(response, "model_dump") else response
    if "results" not in payload:
        raise TranscriptionError("Deepgram accepted the request but returned no transcript.")

    duration = float(payload["metadata"]["duration"])
    paragraphs: list[Paragraph] = []
    for channel_index, channel in enumerate(payload["results"].get("channels") or []):
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            continue
        best = alternatives[0]
        items = ((best.get("paragraphs") or {}).get("paragraphs")) or []
        if not items and best.get("transcript"):
            words = best.get("words") or []
            paragraphs.append(
                Paragraph(
                    start=float(words[0]["start"]) if words else 0.0,
                    end=float(words[-1]["end"]) if words else duration,
                    speaker=0,
                    text=best["transcript"].strip(),
                    channel=channel_index,
                )
            )
            continue
        for item in items:
            text = " ".join((s.get("text") or "").strip() for s in item.get("sentences") or [])
            if not text:
                continue
            paragraphs.append(
                Paragraph(
                    start=float(item.get("start") or 0.0),
                    end=float(item.get("end") or 0.0),
                    speaker=int(item.get("speaker") or 0),
                    text=text,
                    channel=channel_index,
                )
            )
    return TranscriptionResult(duration=duration, paragraphs=paragraphs)
