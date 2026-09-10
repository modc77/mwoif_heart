from __future__ import annotations

from .slots import normalize_slot


class MemoryStore:
    """Store Auth/Session records only in RAM; nothing secret is written to disk."""
    def __init__(self):
        self._records: dict[str, object] = {}

    def load(self, slot: str):
        return self._records.get(normalize_slot(slot))

    def set(self, slot: str, record) -> None:
        self._records[normalize_slot(slot)] = record

    def remove(self, slot: str) -> None:
        self._records.pop(normalize_slot(slot), None)

    def clear(self) -> None:
        self._records.clear()
