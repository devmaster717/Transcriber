"""Settings: one JSON document in the app's data directory.

The API key is deliberately not here; it lives in the credential store (see the settings
screen ticket). This document holds everything else the user can configure.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from .rendering import RenderingOptions


@dataclass(frozen=True)
class Settings:
    include_timestamps: bool = True
    include_speaker_labels: bool = True
    microphone_device_id: str | None = None
    output_device_id: str | None = None
    transcripts_dir: str | None = None
    custom_vocabulary: list[str] = field(default_factory=list)
    window: dict = field(default_factory=dict)

    @property
    def rendering_options(self) -> RenderingOptions:
        return RenderingOptions(self.include_timestamps, self.include_speaker_labels)


class SettingsStore:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "settings.json"

    def load(self) -> Settings:
        if not self._path.exists():
            return Settings()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in raw.items() if k in known})

    def save(self, settings: Settings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def update(self, **changes) -> Settings:
        settings = replace(self.load(), **changes)
        self.save(settings)
        return settings
