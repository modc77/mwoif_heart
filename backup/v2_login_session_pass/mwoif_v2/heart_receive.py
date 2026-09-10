from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from .auth_store import AuthRecord
from .ds_v4 import compact_json_bytes, decode_v4_form_body, encode_v4
from .heart_mailbox import build_mail_list_payload
from .session_store import SessionRecord


ACCEPT_LIFE_MAIL_ENDPOINT = "game/acceptLifeMail4.ds"
ACCEPT_LIFE_MAIL_ENDPOINT_GH = "0x0073B69B"
ACCEPT_LIFE_MAIL_NATIVE_BUILDER_GH = "0x013D518C"
ACCEPT_LIFE_MAIL_CALLER_GH = "0x01422AC0"

# Ghidra FUN_013D518C:
#   memberSeq
#   lifeMailBoxSeqList
# are inserted before the common DS fields.
RECEIVE_FIELD_ORDER = (
    "memberSeq",
    "lifeMailBoxSeqList",
    "pid",
    "sessionKey",
    "currentLv",
    "socialUidCommon",
    "ver",
    "buildVer",
    "cc",
    "ms",
    "osType",
    "osVersion",
    "timeZone",
    "timeZoneDistance",
    "marketType",
    "carrier",
    "fgsId",
    "locale",
    "deviceId",
    "deviceName",
    "deviceModel",
    "accessToken",
    "loginPlatform",
    "cable",
)


@dataclass(frozen=True, slots=True)
class HeartReceiveRequest:
    member_seq: int
    life_mail_box_seq_list: tuple[int, ...]

    def as_native_map(self) -> dict[str, Any]:
        return {
            "memberSeq": self.member_seq,
            "lifeMailBoxSeqList": list(self.life_mail_box_seq_list),
        }


def build_heart_receive_request(
    actor_session: SessionRecord,
    life_mail_box_seqs: list[int] | tuple[int, ...],
) -> HeartReceiveRequest:
    if not actor_session.established:
        raise ValueError("actor session is not established")

    seqs: list[int] = []
    seen: set[int] = set()
    for raw in life_mail_box_seqs:
        seq = int(raw)
        if seq <= 0:
            raise ValueError("lifeMailBoxSeq must be > 0")
        if seq in seen:
            continue
        # signed int64 upper bound; captured seq is in this range.
        if seq > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("lifeMailBoxSeq exceeds signed int64")
        seen.add(seq)
        seqs.append(seq)

    if not seqs:
        raise ValueError("at least one --seq is required")

    return HeartReceiveRequest(
        member_seq=int(actor_session.member_seq),
        life_mail_box_seq_list=tuple(seqs),
    )


def _build_url(cfg) -> str:
    base = str(cfg.server.get("game_base_url") or "").strip()
    if not base:
        return ""
    if not base.endswith("/"):
        base += "/"
    return urljoin(base, ACCEPT_LIFE_MAIL_ENDPOINT)


def _redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"sessionKey", "accessToken"}:
            out[key] = (
                f"<REDACTED len={len(str(value))}>"
                if value
                else "<MISSING>"
            )
        elif key == "cable":
            out[key] = (
                f"<GENERATED len={len(str(value))}>"
                if value
                else "<MISSING>"
            )
        else:
            out[key] = value
    return out


def build_accept_life_payload(
    *,
    cfg,
    actor_session: SessionRecord,
    actor_auth: AuthRecord,
    life_mail_box_seqs: list[int] | tuple[int, ...],
    ms: int | None = None,
    cable: str | None = None,
    fgs_id: str | None = None,
    timezone_distance: int | None = None,
) -> tuple[
    HeartReceiveRequest,
    dict[str, Any],
    dict[str, str],
    dict[str, str],
]:
    request = build_heart_receive_request(
        actor_session,
        life_mail_box_seqs,
    )

    # Reuse the already-live-validated DS common-field builder used by
    # game/myMailList.ds, then replace only the endpoint-specific prefix.
    common, missing, runtime_sources = build_mail_list_payload(
        cfg=cfg,
        session=actor_session,
        auth=actor_auth,
        ms=ms,
        cable=cable,
        fgs_id=fgs_id,
        timezone_distance=timezone_distance,
    )

    payload: dict[str, Any] = {
        "memberSeq": request.member_seq,
        "lifeMailBoxSeqList": list(request.life_mail_box_seq_list),
        "pid": ACCEPT_LIFE_MAIL_ENDPOINT,
    }

    for key, value in common.items():
        if key in {"memberSeq", "pid"}:
            continue
        payload[key] = value

    return request, payload, missing, runtime_sources


def preview_heart_receive(
    *,
    cfg,
    actor_slot: str,
    actor_session: SessionRecord,
    actor_auth: AuthRecord,
    life_mail_box_seqs: list[int] | tuple[int, ...],
    live: bool = False,
    timeout: float = 20.0,
    padding_spaces: int | None = None,
    ms: int | None = None,
    cable: str | None = None,
    fgs_id: str | None = None,
    timezone_distance: int | None = None,
) -> dict[str, Any]:
    if not actor_auth.ready:
        raise ValueError("actor Auth context is not ready")

    request, payload, missing, runtime_sources = build_accept_life_payload(
        cfg=cfg,
        actor_session=actor_session,
        actor_auth=actor_auth,
        life_mail_box_seqs=life_mail_box_seqs,
        ms=ms,
        cable=cable,
        fgs_id=fgs_id,
        timezone_distance=timezone_distance,
    )

    plaintext = compact_json_bytes(payload)
    encoded = encode_v4(
        plaintext,
        padding_spaces=padding_spaces,
    )

    decoded = decode_v4_form_body(encoded.form_body)
    self_check = decoded.rstrip(b" ") == plaintext
    url = _build_url(cfg)

    result: dict[str, Any] = {
        "ok": bool(self_check and not missing),
        "read_only": True,
        "network_action_enabled": False,
        "action": "heart-receive",
        "transport": "legacy-ds-v4",
        "endpoint": ACCEPT_LIFE_MAIL_ENDPOINT,
        "url": url,
        "endpoint_gh": ACCEPT_LIFE_MAIL_ENDPOINT_GH,
        "native_builder_gh": ACCEPT_LIFE_MAIL_NATIVE_BUILDER_GH,
        "native_caller_gh": ACCEPT_LIFE_MAIL_CALLER_GH,
        "actor": actor_slot.upper(),
        "request": request.as_native_map(),
        "common_payload_redacted": _redacted_payload(payload),
        "common_field_order": list(RECEIVE_FIELD_ORDER),
        "missing_live_fields": sorted(missing),
        "runtime_field_sources": runtime_sources,
        "request_policy": "FUN_013D518C_FIELDS_FROZEN",
        "seq_policy": "EXPLICIT_USER_SUPPLIED_MAILBOX_SEQ",
        "ds_v4": encoded.public_dict(),
        "crypto_self_check": self_check,
        "http": {
            "method": "POST",
            "content_type": "application/x-www-form-urlencoded",
            "body_shape": "isEncryptedData=4&data=<url-safe-base64-padded>",
        },
    }

    if not self_check:
        result.update({
            "ok": False,
            "error": "DS_V4_SELF_CHECK_FAILED",
        })
        return result

    if missing:
        result.update({
            "ok": False,
            "error": "DS_COMMON_FIELDS_MISSING",
            "message": "refresh/import Auth for actor before receive",
        })
        return result

    if not live:
        result["write_guard"] = "PREVIEW_ONLY_USE_--live_TO_CLAIM"
        return result

    if not url:
        result.update({
            "ok": False,
            "error": "GAME_BASE_URL_MISSING",
        })
        return result

    try:
        import requests
    except Exception as exc:
        result.update({
            "ok": False,
            "error": "REQUESTS_MISSING",
            "message": str(exc),
        })
        return result

    started = time.monotonic()
    try:
        response = requests.post(
            url,
            data=encoded.form_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=timeout,
            verify=bool(cfg.server.get("verify_ssl", True)),
        )
        elapsed_ms = round(
            (time.monotonic() - started) * 1000,
            1,
        )
        response_bytes = response.content or b""
        response_text = response_bytes.decode(
            "utf-8",
            errors="replace",
        )

        wrapper: dict[str, Any] | None = None
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                wrapper = parsed
        except Exception:
            pass

        app_code = (
            wrapper.get("responseCode")
            if wrapper is not None
            else None
        )
        app_message = (
            wrapper.get("responseMessage")
            if wrapper is not None
            else None
        )

        success = bool(
            response.ok
            and app_code == 200
            and app_message == "COMPLETE"
        )

        result.update({
            "ok": success,
            "read_only": False,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "http_status": response.status_code,
            "response_bytes": len(response_bytes),
            "response_code": app_code,
            "response_message": app_message,
            "response_preview": response_text[:400],
            "claim_submitted_seqs": list(
                request.life_mail_box_seq_list
            ),
        })

        if not success:
            result["error"] = "ACCEPT_LIFE_MAIL_NOT_COMPLETE"

        return result

    except requests.RequestException as exc:
        elapsed_ms = round(
            (time.monotonic() - started) * 1000,
            1,
        )
        result.update({
            "ok": False,
            "read_only": False,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "error": "HTTP_REQUEST_FAILED",
            "message": str(exc),
        })
        return result
