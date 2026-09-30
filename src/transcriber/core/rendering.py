"""Renderings: the plain-text view of a Transcript."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Transcript


@dataclass(frozen=True)
class RenderingOptions:
    include_timestamps: bool = True
    include_speaker_labels: bool = True


DEFAULT_OPTIONS = RenderingOptions()


def render(transcript: Transcript, options: RenderingOptions = DEFAULT_OPTIONS) -> str:
    header = [
        transcript.title,
        " · ".join(
            [
                transcript.created.strftime("%Y-%m-%d %H:%M"),
                format_duration(transcript.duration),
                transcript.source_kind.label,
            ]
        ),
        "",
    ]
    lines = []
    for p in transcript.paragraphs:
        prefix = []
        if options.include_timestamps:
            prefix.append(f"[{format_timestamp(p.start)}]")
        if options.include_speaker_labels:
            prefix.append(f"{transcript.speaker_name(p.speaker)}:")
        lines.append(" ".join([*prefix, p.text]))
    return "\n".join([*header, *lines]) + "\n"


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_duration(seconds: float) -> str:
    total = round(seconds)
    if total < 60:
        return f"{total} s"
    h, rem = divmod(total, 3600)
    m = round(rem / 60)
    if h == 0:
        return f"{m} min"
    return f"{h} h {m:02d} min"
