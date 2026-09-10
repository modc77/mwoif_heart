from __future__ import annotations

from pathlib import Path

from .account_health import clear_health
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
    clear_health(sessions.root, slot)
    clear_health(sessions.root, slot)
    return {
        "slot": slot,
        "session": session.public_dict(),
        "auth": auth.public_dict(),
    }



def next_sender_slot(root: Path, receiver_slot: str = "A") -> str:
    """Return the next convenient numeric sender slot: 1, 2, 3, ..."""
    receiver = normalize_slot(receiver_slot)
    used = set(existing_slots(root))
    number = 1
    while True:
        candidate = str(number)
        if candidate != receiver and candidate not in used:
            return candidate
        number += 1


def import_pair_raw(
    *,
    slot: str,
    session_raw: str,
    auth_raw: str,
    sessions: SessionStore,
    auths: AuthStore,
) -> dict[str, Any]:
    """Import Session/Auth directly from LAB clipboard text.

    This is transactional for the two local cache files: when one side fails,
    existing cache is restored instead of leaving a half-imported account.
    """
    slot = normalize_slot(slot)
    session_path = sessions.path(slot)
    auth_path = auths.path(slot)

    old_session = session_path.read_bytes() if session_path.is_file() else None
    old_auth = auth_path.read_bytes() if auth_path.is_file() else None

    try:
        session = sessions.import_v13(slot=slot, raw=session_raw)
        auth = auths.import_v13_1(slot=slot, raw=auth_raw)
    except Exception:
        if old_session is None:
            session_path.unlink(missing_ok=True)
        else:
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_bytes(old_session)

        if old_auth is None:
            auth_path.unlink(missing_ok=True)
        else:
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_bytes(old_auth)
        raise

    return {
        "slot": slot,
        "session": session.public_dict(),
        "auth": auth.public_dict(),
    }



def update_pair_raw(
    *,
    slot: str,
    sessions: SessionStore,
    auths: AuthStore,
    session_raw: str = "",
    auth_raw: str = "",
) -> dict[str, Any]:
    """Replace only the pasted side(s), preserving the other cached side.

    Blank Session means keep existing Session.
    Blank Auth means keep existing Auth.
    The local files are rolled back together if any supplied replacement fails.
    """
    slot = normalize_slot(slot)
    session_raw = str(session_raw or "").strip()
    auth_raw = str(auth_raw or "").strip()
    if not session_raw and not auth_raw:
        raise ValueError("วาง Session ใหม่ หรือ Auth ใหม่ อย่างน้อย 1 รายการ")

    session_path = sessions.path(slot)
    auth_path = auths.path(slot)
    old_session = session_path.read_bytes() if session_path.is_file() else None
    old_auth = auth_path.read_bytes() if auth_path.is_file() else None

    try:
        if session_raw:
            sessions.import_v13(slot=slot, raw=session_raw)
        if auth_raw:
            auths.import_v13_1(slot=slot, raw=auth_raw)

        session = sessions.load(slot)
        auth = auths.load(slot)
        if session is None or not session.established:
            raise ValueError("Session ของไอดีนี้ยังไม่พร้อม")
        if auth is None or not auth.ready:
            raise ValueError("Auth ของไอดีนี้ยังไม่พร้อม")
    except Exception:
        if old_session is None:
            session_path.unlink(missing_ok=True)
        else:
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_bytes(old_session)
        if old_auth is None:
            auth_path.unlink(missing_ok=True)
        else:
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_bytes(old_auth)
        raise

    clear_health(sessions.root, slot)
    return {
        "slot": slot,
        "updated": {
            "session": bool(session_raw),
            "auth": bool(auth_raw),
        },
        "session": session.public_dict(),
        "auth": auth.public_dict(),
    }
