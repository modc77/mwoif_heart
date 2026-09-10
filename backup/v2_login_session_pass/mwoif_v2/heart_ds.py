from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from .auth_store import AuthRecord
from .ds_v4 import compact_json_bytes, decode_v4_form_body, encode_v4
from .session_store import SessionRecord


SEND_LIFE_MAIL_ENDPOINT = "game/sendLifeMail2.ds"
SEND_LIFE_MAIL_NATIVE_BUILDER_GH = "0x013D4D10"
SEND_LIFE_MAIL_UI_CALLER_GH = "0x01464104"
SEND_LIFE_MAIL_REQUEST_VTABLE_GH = "0x020EC238"
SEND_LIFE_MAIL_SERIALIZER_GH = "0x00F17830"

# Captured normal request JSON order from the game. Python dict preserves
# insertion order, so preview/debug output remains comparable with the game.
COMMON_FIELD_ORDER = (
    "fromMemberSeq",
    "toMemberSeq",
    "requestId",
    "pid",
    "memberSeq",
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

_CABLE_ALPHABET = string.ascii_letters + string.digits


@dataclass(frozen=True, slots=True)
class HeartSendRequest:
    from_member_seq: int
    to_member_seq: int
    request_id: str = ""

    def as_native_map(self) -> dict[str, Any]:
        return {
            "fromMemberSeq": self.from_member_seq,
            "toMemberSeq": self.to_member_seq,
            "requestId": self.request_id,
        }


def build_heart_send_request(
    actor_session: SessionRecord,
    target_session: SessionRecord,
) -> HeartSendRequest:
    if not actor_session.established:
        raise ValueError("actor session is not established")
    if not target_session.established:
        raise ValueError("target session is not established")

    return HeartSendRequest(
        from_member_seq=int(actor_session.member_seq),
        to_member_seq=int(target_session.member_seq),
        request_id="",
    )


def _meta(auth: AuthRecord, *keys: str, default: str = "") -> str:
    for key in keys:
        value = auth.metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _timezone_distance_seconds(
    auth: AuthRecord,
    timezone_name: str,
    explicit: int | None = None,
) -> tuple[int | None, str]:
    if explicit is not None:
        return int(explicit), "CLI_OVERRIDE"

    for key in (
        "time-zone-distance",
        "timeZoneDistance",
        "timezone-distance",
        "timezone_distance",
    ):
        parsed = _parse_int(auth.metadata.get(key))
        if parsed is not None:
            return parsed, f"AUTH_METADATA:{key}"

    try:
        now = datetime.now(ZoneInfo(timezone_name))
        offset = now.utcoffset()
        if offset is not None:
            return int(offset.total_seconds()), "ZONEINFO"
    except Exception:
        pass
    return None, "MISSING"


def _new_cable(length: int = 20) -> str:
    return "".join(secrets.choice(_CABLE_ALPHABET) for _ in range(length))


def _game_process_ms(
    auth: AuthRecord,
    explicit: int | None = None,
) -> tuple[int | None, str]:
    if explicit is not None:
        return max(0, int(explicit)), "CLI_OVERRIDE"

    base = None
    source_key = ""
    for key in (
        "game-process-elapsed-ms",
        "game_process_elapsed_ms",
        "gameProcessElapsedMs",
    ):
        parsed = _parse_int(auth.metadata.get(key))
        if parsed is not None and parsed >= 0:
            base = parsed
            source_key = key
            break

    if base is None:
        return None, "MISSING"

    delta_ms = 0
    try:
        imported = datetime.fromisoformat(auth.imported_at.replace("Z", "+00:00"))
        if imported.tzinfo is None:
            imported = imported.replace(tzinfo=timezone.utc)
        delta_ms = max(
            0,
            int((datetime.now(timezone.utc) - imported.astimezone(timezone.utc)).total_seconds() * 1000),
        )
    except Exception:
        delta_ms = 0

    return base + delta_ms, f"AUTH_METADATA:{source_key}+IMPORT_AGE"


def build_common_ds_payload(
    *,
    cfg,
    actor_session: SessionRecord,
    target_session: SessionRecord,
    actor_auth: AuthRecord,
    ms: int | None = None,
    cable: str | None = None,
    fgs_id: str | None = None,
    timezone_distance: int | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    request = build_heart_send_request(actor_session, target_session)

    timezone_name = _meta(
        actor_auth,
        "timezone",
        default=str(cfg.devplay.get("timezone") or ""),
    )
    country = _meta(
        actor_auth,
        "country",
        default=str(cfg.devplay.get("location_country") or "US"),
    )
    version = _meta(
        actor_auth,
        "version",
        default=str(cfg.game.get("version") or ""),
    )
    build_version = _meta(
        actor_auth,
        "version-code",
        "version_code",
        default=str(cfg.game.get("build_version") or ""),
    )
    os_type = _meta(actor_auth, "os.type", "osType", default="A")
    os_version = _meta(
        actor_auth,
        "os_version",
        "os-version",
        default=str(cfg.devplay.get("os_version") or ""),
    )
    market_type = _meta(
        actor_auth,
        "market_type",
        "market-type",
        default="GOOGLE_PLAY",
    )
    locale = _meta(
        actor_auth,
        "locale",
        default="en-US",
    )
    device_name = _meta(
        actor_auth,
        "device-name",
        "device_name",
        default=str(cfg.devplay.get("device_name") or cfg.devplay.get("model") or ""),
    )
    device_model = _meta(
        actor_auth,
        "device-model",
        "device_model",
        default=str(cfg.devplay.get("device_model") or device_name),
    )
    device_id = (
        _meta(actor_auth, "device-id", "device_id")
        or actor_auth.device_id
        or str(cfg.devplay.get("device_id") or "")
    )
    resolved_fgs_id = (
        str(fgs_id).strip()
        if fgs_id not in (None, "")
        else (_meta(actor_auth, "fgs-id", "fgs_id") or actor_auth.fgs_id)
    )
    login_platform = _meta(
        actor_auth,
        "login_platform",
        "login-platform",
        default="email",
    )

    resolved_ms, ms_source = _game_process_ms(actor_auth, explicit=ms)
    resolved_tz_distance, tz_source = _timezone_distance_seconds(
        actor_auth, timezone_name, explicit=timezone_distance
    )

    payload: dict[str, Any] = {
        "fromMemberSeq": request.from_member_seq,
        "toMemberSeq": request.to_member_seq,
        "requestId": request.request_id,
        "pid": SEND_LIFE_MAIL_ENDPOINT,
        "memberSeq": int(actor_session.member_seq),
        "sessionKey": actor_session.session_key,
        "currentLv": int(actor_session.current_lv),
        "socialUidCommon": actor_auth.mid,
        "ver": version,
        "buildVer": build_version,
        "cc": country,
        "ms": int(resolved_ms or 0),
        "osType": os_type,
        "osVersion": os_version,
        "timeZone": timezone_name,
        "timeZoneDistance": int(resolved_tz_distance or 0),
        "marketType": market_type,
        "carrier": _meta(actor_auth, "carrier", default=""),
        "fgsId": resolved_fgs_id,
        "locale": locale,
        "deviceId": device_id,
        "deviceName": device_name,
        "deviceModel": device_model,
        "accessToken": actor_auth.game_access_token,
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
        "fgsId": "CLI_OVERRIDE" if fgs_id not in (None, "") else ("AUTH_METADATA" if resolved_fgs_id else "MISSING"),
        "cable": "CLI_OVERRIDE" if cable else "GENERATED_20_CHAR_ALNUM",
        "loginPlatform": "AUTH_METADATA_OR_EMAIL_FALLBACK",
    }
    return payload, missing, sources


def _redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"sessionKey", "accessToken"}:
            out[key] = f"<REDACTED len={len(str(value))}>" if value else "<MISSING>"
        elif key == "cable":
            out[key] = f"<GENERATED len={len(str(value))}>" if value else "<MISSING>"
        else:
            out[key] = value
    return out


def _build_url(cfg) -> str:
    base = str(cfg.server.get("game_base_url") or "").strip()
    if not base:
        return ""
    if not base.endswith("/"):
        base += "/"
    return urljoin(base, SEND_LIFE_MAIL_ENDPOINT)


def preview_heart_send(
    *,
    cfg,
    actor_slot: str,
    target_slot: str,
    actor_session: SessionRecord,
    target_session: SessionRecord,
    actor_auth: AuthRecord,
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

    request = build_heart_send_request(actor_session, target_session)
    payload, missing, runtime_sources = build_common_ds_payload(
        cfg=cfg,
        actor_session=actor_session,
        target_session=target_session,
        actor_auth=actor_auth,
        ms=ms,
        cable=cable,
        fgs_id=fgs_id,
        timezone_distance=timezone_distance,
    )
    plaintext = compact_json_bytes(payload)
    encoded = encode_v4(plaintext, padding_spaces=padding_spaces)

    # Local proof: decrypt/decompress the body we just produced and verify the
    # game JSON bytes are recovered exactly after removing random spaces.
    decoded = decode_v4_form_body(encoded.form_body)
    self_check = decoded.rstrip(b" ") == plaintext

    url = _build_url(cfg)
    result: dict[str, Any] = {
        "ok": bool(self_check and not missing),
        "read_only": True,
        "network_action_enabled": False,
        "action": "heart-send",
        "transport": "legacy-ds-v4",
        "endpoint": SEND_LIFE_MAIL_ENDPOINT,
        "url": url,
        "native_builder_gh": SEND_LIFE_MAIL_NATIVE_BUILDER_GH,
        "native_ui_caller_gh": SEND_LIFE_MAIL_UI_CALLER_GH,
        "request_vtable_gh": SEND_LIFE_MAIL_REQUEST_VTABLE_GH,
        "serializer_gh": SEND_LIFE_MAIL_SERIALIZER_GH,
        "actor": actor_slot.upper(),
        "target": target_slot.upper(),
        "request": request.as_native_map(),
        "common_payload_redacted": _redacted_payload(payload),
        "common_field_order": list(COMMON_FIELD_ORDER),
        "missing_live_fields": sorted(missing),
        "request_policy": "GAME_UI_PATH_CONFIRMED",
        "request_id_policy": "EMPTY_STRING_CONFIRMED_NORMAL_UI_PATH",
        "runtime_field_policy": {
            "ms": "GAME_PROCESS_ELAPSED_FROM_AUTH_METADATA_PLUS_IMPORT_AGE_OR_--ms",
            "cable": "RANDOM_20_CHAR_ALNUM_UNLESS_--cable",
            "loginPlatform": "AUTH_METADATA_OR_EMAIL_FALLBACK",
            "timeZoneDistance": "AUTH_METADATA_OR_ZONEINFO_OR_--time-zone-distance",
            "fgsId": "AUTH_METADATA_OR_--fgs-id",
        },
        "runtime_field_sources": runtime_sources,
        "ds_v4": encoded.public_dict(),
        "crypto_self_check": self_check,
        "http": {
            "method": "POST",
            "content_type": "application/x-www-form-urlencoded",
            "body_shape": "isEncryptedData=4&data=<url-safe-base64-padded>",
            "body_preview": (
                encoded.form_body[:48].decode("ascii")
                + "..."
                + encoded.form_body[-16:].decode("ascii")
            ),
        },
    }

    if not self_check:
        result.update({
            "ok": False,
            "error": "DS_V4_SELF_CHECK_FAILED",
            "message": "local ChaCha20/FastLZ round-trip did not reproduce the JSON",
        })
        return result

    if missing:
        result.update({
            "ok": False,
            "error": "DS_COMMON_FIELDS_MISSING",
            "message": "export/import fresh Auth B from LAB V13.6 before live send",
        })
        return result

    if not live:
        result["write_guard"] = "PREVIEW_ONLY_USE_--live_TO_SEND"
        return result

    if not url:
        result.update({
            "ok": False,
            "error": "GAME_BASE_URL_MISSING",
            "message": "config.server.game_base_url is empty",
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

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }
    verify_ssl = bool(cfg.server.get("verify_ssl", True))
    started = time.monotonic()
    try:
        response = requests.post(
            url,
            data=encoded.form_body,
            headers=headers,
            timeout=timeout,
            verify=verify_ssl,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        response_bytes = response.content or b""
        response_text = response_bytes.decode("utf-8", errors="replace")
        result.update({
            "ok": response.ok,
            "read_only": False,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "http_status": response.status_code,
            "response_bytes": len(response_bytes),
            "response_preview": response_text[:400],
        })
        return result
    except requests.RequestException as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        result.update({
            "ok": False,
            "read_only": False,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "error": "HTTP_REQUEST_FAILED",
            "message": str(exc),
        })
        return result
