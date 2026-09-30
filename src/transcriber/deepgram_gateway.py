"""The real transcription gateway: Deepgram, via the official SDK (v7).

Requests use Nova-3, English, smart formatting, diarization, paragraphs and punctuation,
as fixed by the spec. The core never sees SDK types; everything is mapped to domain terms here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from deepgram import DeepgramClient
from deepgram.core.api_error import ApiError

from .core.gateway import RejectedKeyError, TranscriptionError, TranscriptionResult
from .core.model import Paragraph

MAX_FILE_BYTES = 2 * 1024**3  # Deepgram's pre-recorded limit

PRERECORDED_OPTIONS: dict[str, Any] = {
    "model": "nova-3",
    "language": "en",
    "smart_format": True,
    "diarize": True,
    "paragraphs": True,
    "punctuate": True,
}


class DeepgramGateway:
    def __init__(self, api_key: str) -> None:
        self._client = DeepgramClient(api_key=api_key)

    def transcribe(self, audio: Path) -> TranscriptionResult:
        if audio.stat().st_size > MAX_FILE_BYTES:
            raise TranscriptionError("File is larger than Deepgram's 2 GB limit.")
        try:
            response = self._client.listen.v1.media.transcribe_file(
                request=audio.read_bytes(), **PRERECORDED_OPTIONS
            )
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
    for channel in payload["results"].get("channels") or []:
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
                )
            )
    return TranscriptionResult(duration=duration, paragraphs=paragraphs)
