"""Persistence for Transcripts: one JSON document per Transcript in the app's data directory."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .model import Paragraph, SourceKind, Transcript, TranscriptStatus


class TranscriptStore:
    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "transcripts"

    def save(self, transcript: Transcript) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(transcript.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_to_json(transcript), indent=2), encoding="utf-8")
        tmp.replace(path)

    def load(self, transcript_id: str) -> Transcript:
        return _from_json(json.loads(self._path(transcript_id).read_text(encoding="utf-8")))

    def _path(self, transcript_id: str) -> Path:
        return self._dir / f"{transcript_id}.json"


def _to_json(t: Transcript) -> dict:
    d = asdict(t)
    d["created"] = t.created.isoformat()
    d["source_kind"] = t.source_kind.value
    d["status"] = t.status.value
    for key in ("source_path", "rendering_path", "recording_path"):
        d[key] = str(getattr(t, key)) if getattr(t, key) is not None else None
    d["speaker_names"] = {str(k): v for k, v in t.speaker_names.items()}
    return d


def _from_json(d: dict) -> Transcript:
    def path_or_none(value: str | None) -> Path | None:
        return Path(value) if value is not None else None

    return Transcript(
        id=d["id"],
        title=d["title"],
        created=datetime.fromisoformat(d["created"]),
        duration=d["duration"],
        source_kind=SourceKind(d["source_kind"]),
        source_path=path_or_none(d["source_path"]),
        paragraphs=[Paragraph(**p) for p in d["paragraphs"]],
        speaker_names={int(k): v for k, v in d["speaker_names"].items()},
        status=TranscriptStatus(d["status"]),
        rendering_path=path_or_none(d["rendering_path"]),
        recording_path=path_or_none(d["recording_path"]),
    )
