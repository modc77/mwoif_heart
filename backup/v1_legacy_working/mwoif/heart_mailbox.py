from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from .auth_store import AuthRecord
from .ds_v4 import (
    compact_json_bytes,
    decode_v4_data_b64,
    decode_v4_form_body,
    encode_v4,
)
from .heart_ds import (
    _game_process_ms,
    _meta,
    _new_cable,
    _timezone_distance_seconds,
)
from .session_store import SessionRecord


MY_MAIL_LIST_ENDPOINT = "game/myMailList.ds"
MY_MAIL_LIST_ENDPOINT_GH = "0x007BC9F2"
MY_MAIL_LIST_REQUEST_BUILDER_GH = "0x01624E58"
MY_MAIL_LIST_RESPONSE_DISPATCH_GH = "0x013D0CA4"
MY_MAIL_LIST_MAIL_PARSER_GH = "0x013D3530"
MY_MAIL_LIST_REWARD_PARSER_GH = "0x013D36C8"
MY_MAIL_LIST_ITEM_PARSER_GH = "0x013BDB34"

# Ghidra FUN_013BDB34 confirms each normal life mail item reads:
#   insertDt, fromMemberSeq, seq
MAIL_ITEM_FIELDS = ("insertDt", "fromMemberSeq", "seq")

_REQUIRED_LIVE_FIELDS = (
    "memberSeq",
    "sessionKey",
    "socialUidCommon",
    "ver",
    "buildVer",
    "cc",
    "osType",
    "osVersion",
    "timeZone",
    "marketType",
    "fgsId",
    "locale",
    "deviceId",
    "deviceName",
    "deviceModel",
    "accessToken",
    "loginPlatform",
    "cable",
)


def _build_url(cfg) -> str:
    base = str(cfg.server.get("game_base_url") or "").strip()
    if not base:
        return ""
    if not base.endswith("/"):
        base += "/"
    return urljoin(base, MY_MAIL_LIST_ENDPOINT)


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


def build_mail_list_payload(
    *,
    cfg,
    session: SessionRecord,
    auth: AuthRecord,
    ms: int | None = None,
    cable: str | None = None,
    fgs_id: str | None = None,
    timezone_distance: int | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    if not session.established:
        raise ValueError("session is not established")
    if not auth.ready:
        raise ValueError("Auth context is not ready")

    timezone_name = _meta(
        auth,
        "timezone",
        default=str(cfg.devplay.get("timezone") or ""),
    )
    country = _meta(
        auth,
        "country",
        default=str(cfg.devplay.get("location_country") or ""),
    )
    version = _meta(
        auth,
        "version",
        default=str(cfg.game.get("version") or ""),
    )
    build_version = _meta(
        auth,
        "version-code",
        "version_code",
        default=str(cfg.game.get("build_version") or ""),
    )
    os_type = _meta(auth, "os.type", "osType", default="A")
    os_version = _meta(
        auth,
        "os_version",
        "os-version",
        default=str(cfg.devplay.get("os_version") or ""),
    )
    market_type = _meta(
        auth,
        "market_type",
        "market-type",
        default="GOOGLE_PLAY",
    )
    locale = _meta(auth, "locale", default="en-US")
    device_name = _meta(
        auth,
        "device-name",
        "device_name",
        default=str(cfg.devplay.get("model") or ""),
    )
    device_model = _meta(
        auth,
        "device-model",
        "device_model",
        default=device_name,
    )
    device_id = _meta(auth, "device-id", "device_id") or auth.device_id

    resolved_fgs_id = (
        str(fgs_id).strip()
        if fgs_id not in (None, "")
        else (_meta(auth, "fgs-id", "fgs_id") or auth.fgs_id)
    )
    login_platform = _meta(
        auth,
        "login_platform",
        "login-platform",
        default="email",
    )

    resolved_ms, ms_source = _game_process_ms(auth, explicit=ms)
    resolved_tz_distance, tz_source = _timezone_distance_seconds(
        auth,
        timezone_name,
        explicit=timezone_distance,
    )

    # Endpoint-specific memberSeq is inserted first by FUN_01624E58.
    # Common DS fields follow the same game common-builder path as Heart SEND.
    payload: dict[str, Any] = {
        "memberSeq": int(session.member_seq),
        "pid": MY_MAIL_LIST_ENDPOINT,
        "sessionKey": session.session_key,
        "currentLv": int(session.current_lv),
        "socialUidCommon": auth.mid,
        "ver": version,
        "buildVer": build_version,
        "cc": country,
        "ms": int(resolved_ms or 0),
        "osType": os_type,
        "osVersion": os_version,
        "timeZone": timezone_name,
        "timeZoneDistance": int(resolved_tz_distance or 0),
        "marketType": market_type,
        "carrier": _meta(auth, "carrier", default=""),
        "fgsId": resolved_fgs_id,
        "locale": locale,
        "deviceId": device_id,
        "deviceName": device_name,
        "deviceModel": device_model,
        "accessToken": auth.game_access_token,
        "loginPlatform": login_platform,
        "cable": cable or _new_cable(),
    }

    missing = {
        key: "missing"
        for key in _REQUIRED_LIVE_FIELDS
        if payload.get(key) in (None, "")
    }
    if resolved_ms is None:
        missing["ms"] = "missing game-process-elapsed-ms"
    if resolved_tz_distance is None:
        missing["timeZoneDistance"] = "missing time-zone-distance"

    sources = {
        "ms": ms_source,
        "timeZoneDistance": tz_source,
        "fgsId": (
            "CLI_OVERRIDE"
            if fgs_id not in (None, "")
            else ("AUTH_METADATA" if resolved_fgs_id else "MISSING")
        ),
        "cable": "CLI_OVERRIDE" if cable else "GENERATED_20_CHAR_ALNUM",
        "loginPlatform": "AUTH_METADATA_OR_EMAIL_FALLBACK",
    }
    return payload, missing, sources


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _insert_dt_sort_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def extract_life_mail_items(
    decoded_response: Any,
    *,
    from_member_seq: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(decoded_response, dict):
        return []

    mail_list = decoded_response.get("mailList")
    if not isinstance(mail_list, list):
        return []

    items: list[dict[str, Any]] = []
    for index, item in enumerate(mail_list):
        if not isinstance(item, dict):
            continue

        seq = _as_int(item.get("seq"))
        sender = _as_int(item.get("fromMemberSeq"))
        if seq is None or sender is None:
            continue
        if from_member_seq is not None and sender != int(from_member_seq):
            continue

        items.append({
            "index": index,
            "seq": seq,
            "fromMemberSeq": sender,
            "insertDt": item.get("insertDt"),
        })

    items.sort(
        key=lambda x: _insert_dt_sort_value(x.get("insertDt")),
        reverse=True,
    )
    return items


def preview_mail_list(
    *,
    cfg,
    slot: str,
    session: SessionRecord,
    auth: AuthRecord,
    from_slot: str | None = None,
    from_member_seq: int | None = None,
    live: bool = False,
    timeout: float = 20.0,
    padding_spaces: int | None = None,
    ms: int | None = None,
    cable: str | None = None,
    fgs_id: str | None = None,
    timezone_distance: int | None = None,
) -> dict[str, Any]:
    payload, missing, runtime_sources = build_mail_list_payload(
        cfg=cfg,
        session=session,
        auth=auth,
        ms=ms,
        cable=cable,
        fgs_id=fgs_id,
        timezone_distance=timezone_distance,
    )
    plaintext = compact_json_bytes(payload)
    encoded = encode_v4(plaintext, padding_spaces=padding_spaces)

    decoded_local = decode_v4_form_body(encoded.form_body)
    self_check = decoded_local.rstrip(b" ") == plaintext

    url = _build_url(cfg)
    result: dict[str, Any] = {
        "ok": bool(self_check and not missing),
        "read_only": True,
        "network_action_enabled": False,
        "action": "heart-mail-list",
        "transport": "legacy-ds-v4",
        "endpoint": MY_MAIL_LIST_ENDPOINT,
        "url": url,
        "slot": slot.upper(),
        "from_slot": from_slot.upper() if from_slot else None,
        "from_member_seq": from_member_seq,
        "endpoint_gh": MY_MAIL_LIST_ENDPOINT_GH,
        "request_builder_gh": MY_MAIL_LIST_REQUEST_BUILDER_GH,
        "response_dispatch_gh": MY_MAIL_LIST_RESPONSE_DISPATCH_GH,
        "mail_parser_gh": MY_MAIL_LIST_MAIL_PARSER_GH,
        "reward_parser_gh": MY_MAIL_LIST_REWARD_PARSER_GH,
        "mail_item_parser_gh": MY_MAIL_LIST_ITEM_PARSER_GH,
        "request": {"memberSeq": int(session.member_seq)},
        "common_payload_redacted": _redacted_payload(payload),
        "missing_live_fields": sorted(missing),
        "runtime_field_sources": runtime_sources,
        "request_policy": "READ_ONLY_MAILBOX_LIST",
        "response_item_fields": list(MAIL_ITEM_FIELDS),
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
            "message": "refresh/import Auth for this slot before mailbox read",
        })
        return result

    if not live:
        result["read_guard"] = "PREVIEW_ONLY_USE_--live_TO_READ_MAILBOX"
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
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
            verify=bool(cfg.server.get("verify_ssl", True)),
        )
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)

        wrapper_text = (response.content or b"").decode(
            "utf-8",
            errors="replace",
        )
        result.update({
            "read_only": True,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "http_status": response.status_code,
            "response_bytes": len(response.content or b""),
        })

        try:
            wrapper = response.json()
        except Exception:
            result.update({
                "ok": False,
                "error": "MAIL_LIST_HTTP_JSON_INVALID",
                "response_preview": wrapper_text[:400],
            })
            return result

        result["response_code"] = wrapper.get("responseCode")
        result["response_message"] = wrapper.get("responseMessage")

        data_b64 = wrapper.get("responseData") or ""
        if not data_b64:
            result.update({
                "ok": bool(response.ok and wrapper.get("responseCode") == 200),
                "decoded_response_present": False,
                "life_mail_candidates": [],
                "suggested_life_mail_seq": None,
            })
            return result

        try:
            decoded_bytes = decode_v4_data_b64(str(data_b64))
            decoded_text = decoded_bytes.rstrip(b" ").decode("utf-8")
            decoded_obj = json.loads(decoded_text)
        except Exception as exc:
            result.update({
                "ok": False,
                "error": "MAIL_LIST_RESPONSE_DECODE_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
                "response_data_length": len(str(data_b64)),
            })
            return result

        candidates = extract_life_mail_items(
            decoded_obj,
            from_member_seq=from_member_seq,
        )
        result.update({
            "ok": bool(
                response.ok
                and wrapper.get("responseCode") == 200
            ),
            "decoded_response_present": True,
            "decoded_top_level_keys": (
                sorted(decoded_obj.keys())
                if isinstance(decoded_obj, dict)
                else []
            ),
            "mail_list_count": (
                len(decoded_obj.get("mailList", []))
                if isinstance(decoded_obj, dict)
                and isinstance(decoded_obj.get("mailList"), list)
                else 0
            ),
            "life_mail_candidates": candidates,
            "suggested_life_mail_seq": (
                candidates[0]["seq"] if candidates else None
            ),
            "selection_policy": (
                "LATEST_INSERT_DT_FROM_REQUESTED_SENDER"
                if from_member_seq is not None
                else "LATEST_INSERT_DT"
            ),
        })
        return result

    except requests.RequestException as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        result.update({
            "ok": False,
            "read_only": True,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "error": "HTTP_REQUEST_FAILED",
            "message": str(exc),
        })
        return result
