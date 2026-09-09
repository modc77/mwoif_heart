from __future__ import annotations

import re


_SLOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def normalize_slot(value: str) -> str:
    slot = str(value or "").strip().upper()
    if not slot:
        raise ValueError("slot is required")
    if slot in {".", ".."} or not _SLOT_RE.fullmatch(slot):
        raise ValueError(
            "invalid slot; use letters/numbers plus . _ - (max 64 chars)"
        )
    return slot
