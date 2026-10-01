"""Interleaves two mono streams into one two-channel stream for a Meeting Capture.

Both devices deliver 16 kHz int16 chunks on their own threads. Frames go out in lock-step once
both channels have a chunk's worth; if one device stalls for longer than `max_lag_frames`, the
other is not held back and the stalled channel is padded with silence, so the channels stay
aligned in time.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

from .capture import SAMPLE_RATE, SAMPLE_WIDTH


class ChannelMixer:
    def __init__(
        self,
        on_frames: Callable[[bytes], None],
        chunk_frames: int = SAMPLE_RATE // 10,
        max_lag_frames: int = SAMPLE_RATE * 2,
    ) -> None:
        self._on_frames = on_frames
        self._chunk = chunk_frames
        self._max_lag = max_lag_frames
        self._buffers = [bytearray(), bytearray()]
        self._lock = threading.Lock()

    def push(self, channel: int, chunk: bytes) -> None:
        with self._lock:
            self._buffers[channel] += chunk
            self._drain(force=False)

    def flush(self) -> None:
        """Emit whatever is left, padding the shorter channel with silence."""
        with self._lock:
            self._drain(force=True)

    def _drain(self, force: bool) -> None:
        while True:
            a = len(self._buffers[0]) // SAMPLE_WIDTH
            b = len(self._buffers[1]) // SAMPLE_WIDTH
            if min(a, b) >= self._chunk:
                n = self._chunk
            elif force and max(a, b) > 0:
                n = max(a, b)
            elif max(a, b) >= self._max_lag:
                n = self._chunk
            else:
                return
            self._emit(n)

    def _emit(self, n: int) -> None:
        columns = []
        for buf in self._buffers:
            take = buf[: n * SAMPLE_WIDTH]
            del buf[: n * SAMPLE_WIDTH]
            samples = np.frombuffer(bytes(take), dtype=np.int16)
            if len(samples) < n:
                samples = np.concatenate([samples, np.zeros(n - len(samples), dtype=np.int16)])
            columns.append(samples)
        self._on_frames(np.stack(columns, axis=1).tobytes())
