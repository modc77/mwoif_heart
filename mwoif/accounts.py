from __future__ import annotations

from pathlib import Path
from typing import Any

from .auth_store import AuthStore
from .session_store import SessionStore
from .slots import normalize_slot


def existing_slots(root: Path) -> list[str]:
    state = root / ".state"
    if not state.is_dir():
        return []

    slots: set[str] = set()
    for prefix in ("session_", "auth_"):
        for path in state.glob(f"{prefix}*.json"):
            raw = path.stem[len(prefix):]
            try:
                slots.add(normalize_slot(raw))
            except ValueError:
                continue
    return sorted(slots, key=_natural_key)


def _natural_key(value: str):
    parts = []
    for token in __import__("re").split(r"(\d+)", value):
        parts.append(int(token) if token.isdigit() else token)
    return parts


def pair_status(
    slot: str,
    sessions: SessionStore,
    auths: AuthStore,
) -> dict[str, Any]:
    slot = normalize_slot(slot)
    session = sessions.load(slot)
    auth = auths.load(slot)

    ready = bool(
        session
        and session.established
        and auth
        and auth.ready
    )
    return {
        "slot": slot,
        "session": bool(session and session.established),
        "auth": bool(auth and auth.ready),
        "mid": auth.mid if auth else "",
        "member_seq": session.member_seq if session else 0,
        "ready": ready,
    }


def require_pair(
    slot: str,
    sessions: SessionStore,
    auths: AuthStore,
):
    slot = normalize_slot(slot)
    session = sessions.load(slot)
    auth = auths.load(slot)
    if session is None or not session.established:
        raise ValueError(f"slot {slot}: Session is missing/not established")
    if auth is None or not auth.ready:
        raise ValueError(f"slot {slot}: Auth is missing/not ready")
    return slot, session, auth


def import_pair(
    *,
    slot: str,
    session_file: Path,
    auth_file: Path,
    sessions: SessionStore,
    auths: AuthStore,
) -> dict[str, Any]:
    slot = normalize_slot(slot)
    session = sessions.import_v13(
        slot=slot,
        raw=session_file.read_text(encoding="utf-8"),
    )
    auth = auths.import_v13_1(
        slot=slot,
        raw=auth_file.read_text(encoding="utf-8"),
    )
    return {
        "slot": slot,
        "session": session.public_dict(),
        "auth": auth.public_dict(),
    }
