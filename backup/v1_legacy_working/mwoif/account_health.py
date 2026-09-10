from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth_store import AuthRecord
from .friend_grpc import list_friends
from .heart_mailbox import preview_mail_list
from .session_store import SessionRecord
from .slots import normalize_slot


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    raw = parts[1]
    raw += "=" * ((-len(raw)) % 4)
    try:
        data = base64.urlsafe_b64decode(raw.encode("ascii"))
        obj = json.loads(data.decode("utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def format_remaining(seconds: int | None) -> str:
    if seconds is None:
        return "ไม่ทราบ"
    if seconds <= 0:
        return "หมดแล้ว"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} นาที"
    hours, mins = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} ชม. {mins} นาที" if mins else f"{hours} ชม."
    days, hrs = divmod(hours, 24)
    return f"{days} วัน {hrs} ชม." if hrs else f"{days} วัน"


def auth_token_info(auth: AuthRecord | None, *, warn_seconds: int = 1800) -> dict[str, Any]:
    if auth is None or not auth.game_access_token:
        return {
            "present": False,
            "exp": None,
            "expires_at": "",
            "remaining_seconds": None,
            "remaining_text": "ไม่มี",
            "state": "MISSING",
        }

    payload = _jwt_payload(auth.game_access_token)
    exp_raw = payload.get("exp")
    try:
        exp = int(exp_raw)
    except (TypeError, ValueError):
        exp = None

    if exp is None:
        return {
            "present": True,
            "exp": None,
            "expires_at": "",
            "remaining_seconds": None,
            "remaining_text": "ไม่พบ exp",
            "state": "UNKNOWN_EXP",
            "subject_matches_mid": None,
        }

    now = int(datetime.now(timezone.utc).timestamp())
    remaining = exp - now
    local_dt = datetime.fromtimestamp(exp, timezone.utc).astimezone()
    state = "OK"
    if remaining <= 0:
        state = "EXPIRED"
    elif remaining <= warn_seconds:
        state = "EXPIRING_SOON"

    sub = str(payload.get("sub") or "")
    return {
        "present": True,
        "exp": exp,
        "expires_at": local_dt.isoformat(timespec="seconds"),
        "expires_at_display": local_dt.strftime("%d/%m %H:%M:%S"),
        "remaining_seconds": remaining,
        "remaining_text": format_remaining(remaining),
        "state": state,
        "subject_matches_mid": (sub == auth.mid) if sub else None,
    }


def health_path(root: Path, slot: str) -> Path:
    return root / ".state" / f"health_{normalize_slot(slot)}.json"


def load_health(root: Path, slot: str) -> dict[str, Any]:
    path = health_path(root, slot)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_health(root: Path, slot: str, result: dict[str, Any]) -> None:
    path = health_path(root, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = {
        "slot": normalize_slot(slot),
        "checked_at": result.get("checked_at", ""),
        "status": result.get("status", "UNKNOWN"),
        "auth_online": result.get("auth_online"),
        "session_online": result.get("session_online"),
        "auth_detail": result.get("auth_detail", ""),
        "session_detail": result.get("session_detail", ""),
        "auth_elapsed_ms": result.get("auth_elapsed_ms"),
        "session_elapsed_ms": result.get("session_elapsed_ms"),
    }
    path.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_health(root: Path, slot: str) -> None:
    health_path(root, slot).unlink(missing_ok=True)


def health_label(
    *,
    token_info: dict[str, Any],
    cached: dict[str, Any],
) -> tuple[str, str]:
    if token_info.get("state") == "EXPIRED":
        return "Auth หมด", "fail"
    status = str(cached.get("status") or "")
    mapping = {
        "PASS": ("ผ่านทั้งคู่", "pass"),
        "AUTH_FAIL": ("Auth ไม่ผ่าน", "fail"),
        "SESSION_FAIL": ("Session ไม่ผ่าน", "fail"),
        "AUTH_EXPIRED": ("Auth หมด", "fail"),
        "MISSING": ("ข้อมูลไม่ครบ", "fail"),
    }
    if status in mapping:
        label, tag = mapping[status]
        if token_info.get("state") == "EXPIRING_SOON" and status == "PASS":
            return "ผ่าน • Auth ใกล้หมด", "warn"
        return label, tag
    if token_info.get("state") == "EXPIRING_SOON":
        return "Auth ใกล้หมด", "warn"
    return "ยังไม่ทดสอบ", "muted"


def test_account_health(
    *,
    cfg,
    slot: str,
    session: SessionRecord | None,
    auth: AuthRecord | None,
    grpc_timeout: float = 6.0,
    ds_timeout: float = 8.0,
) -> dict[str, Any]:
    slot = normalize_slot(slot)
    checked_at = datetime.now(timezone.utc).isoformat()
    token = auth_token_info(auth)
    result: dict[str, Any] = {
        "slot": slot,
        "checked_at": checked_at,
        "status": "MISSING",
        "auth_online": None,
        "session_online": None,
        "auth_detail": "",
        "session_detail": "",
        "auth_elapsed_ms": None,
        "session_elapsed_ms": None,
        "token": token,
    }

    if session is None or not session.established or auth is None or not auth.ready:
        result["auth_detail"] = "Session/Auth ในเครื่องไม่ครบ"
        return result

    # JWT exp is server-signed. Once it is past exp, rewriting imported_at or
    # saving the cache again cannot renew it, so skip a guaranteed-fail call.
    if token.get("state") == "EXPIRED":
        result.update({
            "status": "AUTH_EXPIRED",
            "auth_online": False,
            "auth_detail": "game_access_token เลยเวลา exp แล้ว",
        })
        return result

    auth_result = list_friends(
        cfg=cfg,
        slot=slot,
        auth=auth,
        request_body=b"",
        timeout=grpc_timeout,
    )
    result["auth_online"] = bool(auth_result.get("ok"))
    result["auth_elapsed_ms"] = auth_result.get("elapsed_ms")
    if not auth_result.get("ok"):
        detail = (
            auth_result.get("grpc_details")
            or auth_result.get("error")
            or auth_result.get("message")
            or "gRPC Friend List ไม่ผ่าน"
        )
        result.update({
            "status": "AUTH_FAIL",
            "auth_detail": str(detail),
            "session_detail": "ยังไม่ทดสอบ Session เพราะ Auth ไม่ผ่าน",
        })
        return result

    result["auth_detail"] = "Friend List gRPC ผ่าน"

    session_result = preview_mail_list(
        cfg=cfg,
        slot=slot,
        session=session,
        auth=auth,
        from_slot=None,
        from_member_seq=None,
        live=True,
        timeout=ds_timeout,
    )
    result["session_online"] = bool(session_result.get("ok"))
    result["session_elapsed_ms"] = session_result.get("elapsed_ms")
    if not session_result.get("ok"):
        detail = (
            session_result.get("response_message")
            or session_result.get("error")
            or session_result.get("message")
            or "myMailList.ds ไม่ผ่าน"
        )
        result.update({
            "status": "SESSION_FAIL",
            "session_detail": str(detail),
        })
        return result

    result.update({
        "status": "PASS",
        "session_detail": "myMailList.ds ผ่าน",
    })
    return result
