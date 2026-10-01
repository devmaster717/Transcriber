"""The File queue: Files waiting to be transcribed, one at a time, in the order they were added."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path


class QueueState(StrEnum):
    WAITING = "waiting"
    TRANSCRIBING = "transcribing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINISHED_STATES = (QueueState.DONE, QueueState.FAILED, QueueState.CANCELLED)


@dataclass(frozen=True)
class QueueItem:
    id: str
    path: Path
    state: QueueState = QueueState.WAITING
    error: str | None = None
    transcript_id: str | None = None


class FileQueue:
    """Thread-safe: the GUI thread adds while a worker thread processes."""

    def __init__(self) -> None:
        self._items: list[QueueItem] = []
        self._lock = threading.Lock()

    def add(self, paths: list[Path]) -> list[QueueItem]:
        new = [QueueItem(id=uuid.uuid4().hex, path=p) for p in paths]
        with self._lock:
            self._items.extend(new)
        return new

    def items(self) -> list[QueueItem]:
        with self._lock:
            return list(self._items)

    def get(self, item_id: str) -> QueueItem:
        with self._lock:
            return self._find(item_id)

    def claim_next(self) -> QueueItem | None:
        """Atomically move the first waiting item to transcribing and return it."""
        with self._lock:
            for i, item in enumerate(self._items):
                if item.state is QueueState.WAITING:
                    claimed = replace(item, state=QueueState.TRANSCRIBING, error=None)
                    self._items[i] = claimed
                    return claimed
            return None

    def update(self, item_id: str, **changes) -> QueueItem:
        with self._lock:
            item = self._find(item_id)
            updated = replace(item, **changes)
            self._items[self._items.index(item)] = updated
            return updated

    def remove(self, item_id: str) -> QueueItem:
        with self._lock:
            item = self._find(item_id)
            self._items.remove(item)
            return item

    def remove_finished(self) -> list[QueueItem]:
        with self._lock:
            gone = [i for i in self._items if i.state in FINISHED_STATES]
            self._items = [i for i in self._items if i.state not in FINISHED_STATES]
            return gone

    def _find(self, item_id: str) -> QueueItem:
        for item in self._items:
            if item.id == item_id:
                return item
        raise KeyError(item_id)
