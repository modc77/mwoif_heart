from __future__ import annotations

import base64
import os
import time
from typing import Any

from .auth_store import AuthRecord
from .grpc_metadata import build_metadata, redacted_metadata
from .friend_proto import (
    build_handle_friend_request,
    build_remove_friend_request,
    build_send_friend_request,
)
from .wire import WireError, inspect_message


# FriendAPI target is NOT the static streaming endpoint.
# The game receives game_endpoints["grpc"] from DevPlay metadata at runtime.
# Keep this public constant for main.py/doctor compatibility, but never use it
# as a network destination.
GRPC_HOST = "DYNAMIC_GAME_ENDPOINT_REQUIRED"
LIST_FRIENDS_METHOD = "/service.api.FriendAPI/ListFriends"
SEND_FRIEND_REQUEST_METHOD = "/service.api.FriendAPI/SendFriendRequest"
HANDLE_FRIEND_REQUEST_METHOD = "/service.api.FriendAPI/HandleFriendRequest"
REMOVE_FRIEND_METHOD = "/service.api.FriendAPI/RemoveFriend"


def _clean_target(value: Any) -> str:
    target = str(value or "").strip()
    if not target:
        return ""

    for prefix in ("grpc://", "grpcs://", "https://", "http://"):
        if target.lower().startswith(prefix):
            target = target[len(prefix):]
            break

    target = target.strip().rstrip("/")
    if "/" in target:
        target = target.split("/", 1)[0]

    # Runtime DevPlay endpoint is normally host:port. If the server returns
    # only a hostname, TLS gRPC uses 443.
    if target and ":" not in target:
        target = f"{target}:443"

    return target


def resolve_friend_grpc_target(*, cfg, auth: AuthRecord) -> tuple[str, str]:
    """Resolve game_endpoints['grpc'] without falling back to streaming.

    Preferred source is a value exported from the running game/LAB into the
    auth metadata. Environment/config overrides exist only for controlled
    testing and do not silently substitute another service host.
    """

    for key in (
        "friend-grpc-target",
        "friend_grpc_target",
        "grpc-target",
        "grpc_target",
        "game-grpc-target",
        "game_grpc_target",
        "grpc",
    ):
        value = auth.metadata.get(key)
        target = _clean_target(value)
        if target:
            return target, f"auth.metadata.{key}"

    env_target = _clean_target(os.getenv("MWOIF_FRIEND_GRPC_TARGET", ""))
    if env_target:
        return env_target, "env:MWOIF_FRIEND_GRPC_TARGET"

    try:
        cfg_target = _clean_target(cfg.server.get("friend_grpc_target", ""))
    except Exception:
        cfg_target = ""
    if cfg_target:
        return cfg_target, "config.server.friend_grpc_target"

    return "", "missing"


def classify_failure(code: str) -> str:
    return {
        "UNAUTHENTICATED": "auth_token_or_authorization",
        "PERMISSION_DENIED": "auth_or_common_metadata",
        "INVALID_ARGUMENT": "protobuf_or_required_metadata",
        "UNIMPLEMENTED": "wrong_method_path_or_service",
        "FAILED_PRECONDITION": "account_or_server_state",
        "UNAVAILABLE": "dns_tls_connectivity_or_backend",
        "DEADLINE_EXCEEDED": "network_or_backend_timeout",
    }.get(code, "grpc_or_server")


def list_friends(
    *,
    cfg,
    slot: str,
    auth: AuthRecord,
    request_body: bytes = b"",
    extra_metadata: dict[str, Any] | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    target, target_source = resolve_friend_grpc_target(cfg=cfg, auth=auth)
    if not target:
        return {
            "ok": False,
            "read_only": True,
            "network_action_enabled": False,
            "error": "FRIEND_GRPC_TARGET_MISSING",
            "message": (
                "FriendAPI uses runtime DevPlay game_endpoints['grpc']; "
                "the old streaming.live.prod.devsnova.cloud:443 target is invalid."
            ),
            "required_source": "game_endpoints[grpc] / GH 0x0225C988",
            "accepted_inputs": [
                "auth.metadata.friend-grpc-target",
                "MWOIF_FRIEND_GRPC_TARGET",
                "config.server.friend_grpc_target",
            ],
            "method": LIST_FRIENDS_METHOD,
            "request_bytes": len(request_body),
            "request_policy": (
                "EXPLICIT_OVERRIDE"
                if request_body
                else "EMPTY_CONFIRMED_GAME_PATH"
            ),
        }

    try:
        import grpc  # type: ignore
    except Exception as exc:
        return {
            "ok": False,
            "read_only": True,
            "network_action_enabled": False,
            "error": "GRPCIO_MISSING",
            "message": str(exc),
        }

    metadata, missing = build_metadata(
        cfg=cfg,
        slot=slot,
        auth=auth,
        extra=extra_metadata,
    )

    channel = grpc.secure_channel(
        target,
        grpc.ssl_channel_credentials(),
    )
    method = channel.unary_unary(
        LIST_FRIENDS_METHOD,
        request_serializer=lambda x: x,
        response_deserializer=lambda x: x,
    )

    started = time.monotonic()
    try:
        raw = method(
            request_body,
            timeout=timeout,
            metadata=metadata,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)

        try:
            fields = inspect_message(raw)
            wire_error = None
        except WireError as exc:
            fields = []
            wire_error = str(exc)

        return {
            "ok": True,
            "read_only": True,
            "network_action_enabled": True,
            "action": "friend-list",
            "host": target,
            "host_source": target_source,
            "method": LIST_FRIENDS_METHOD,
            "elapsed_ms": elapsed_ms,
            "request_bytes": len(request_body),
            "request_policy": (
                "EXPLICIT_OVERRIDE"
                if request_body
                else "EMPTY_CONFIRMED_GAME_PATH"
            ),
            "metadata": redacted_metadata(metadata),
            "missing_dynamic_metadata": missing,
            "response_bytes": len(raw),
            "response_b64": base64.b64encode(raw).decode("ascii"),
            "response_hex": raw.hex(),
            "wire_fields": fields,
            "wire_error": wire_error,
        }
    except grpc.RpcError as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        code = exc.code()
        code_name = code.name if code is not None else "UNKNOWN"
        return {
            "ok": False,
            "read_only": True,
            "network_action_enabled": True,
            "action": "friend-list",
            "host": target,
            "host_source": target_source,
            "method": LIST_FRIENDS_METHOD,
            "elapsed_ms": elapsed_ms,
            "request_bytes": len(request_body),
            "request_policy": (
                "EXPLICIT_OVERRIDE"
                if request_body
                else "EMPTY_CONFIRMED_GAME_PATH"
            ),
            "metadata": redacted_metadata(metadata),
            "missing_dynamic_metadata": missing,
            "grpc_code": code_name,
            "grpc_details": exc.details() or "",
            "failure_class": classify_failure(code_name),
        }
    finally:
        channel.close()


def _friend_write_call(
    *,
    cfg,
    slot: str,
    auth: AuthRecord,
    action: str,
    method_path: str,
    request_body: bytes,
    extra_metadata: dict[str, Any] | None = None,
    timeout: float = 12.0,
    live: bool = False,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target, target_source = resolve_friend_grpc_target(cfg=cfg, auth=auth)
    base = {
        "ok": True,
        "read_only": not live,
        "network_action_enabled": bool(live and target),
        "action": action,
        "host": target,
        "host_source": target_source,
        "method": method_path,
        "request_bytes": len(request_body),
        "request_hex": request_body.hex(),
        "request_b64": base64.b64encode(request_body).decode("ascii"),
        "schema": schema or {},
    }

    if not target:
        base.update({
            "ok": False,
            "network_action_enabled": False,
            "error": "FRIEND_GRPC_TARGET_MISSING",
            "required_source": "auth.metadata.friend-grpc-target",
        })
        return base

    metadata, missing = build_metadata(
        cfg=cfg,
        slot=slot,
        auth=auth,
        extra=extra_metadata,
    )
    base["metadata"] = redacted_metadata(metadata)
    base["missing_dynamic_metadata"] = missing

    if not live:
        base["write_guard"] = "PREVIEW_ONLY_USE_--live_TO_SEND"
        return base

    try:
        import grpc  # type: ignore
    except Exception as exc:
        base.update({
            "ok": False,
            "network_action_enabled": False,
            "error": "GRPCIO_MISSING",
            "message": str(exc),
        })
        return base

    channel = grpc.secure_channel(target, grpc.ssl_channel_credentials())
    method = channel.unary_unary(
        method_path,
        request_serializer=lambda x: x,
        response_deserializer=lambda x: x,
    )

    started = time.monotonic()
    try:
        raw = method(request_body, timeout=timeout, metadata=metadata)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        try:
            fields = inspect_message(raw)
            wire_error = None
        except WireError as exc:
            fields = []
            wire_error = str(exc)

        base.update({
            "ok": True,
            "read_only": False,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "response_bytes": len(raw),
            "response_b64": base64.b64encode(raw).decode("ascii"),
            "response_hex": raw.hex(),
            "wire_fields": fields,
            "wire_error": wire_error,
        })
        return base
    except grpc.RpcError as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        code = exc.code()
        code_name = code.name if code is not None else "UNKNOWN"
        base.update({
            "ok": False,
            "read_only": False,
            "network_action_enabled": True,
            "elapsed_ms": elapsed_ms,
            "grpc_code": code_name,
            "grpc_details": exc.details() or "",
            "failure_class": classify_failure(code_name),
        })
        return base
    finally:
        channel.close()


def send_friend_request(
    *,
    cfg,
    slot: str,
    auth: AuthRecord,
    target_mid: str,
    source_type: int,
    extra_metadata: dict[str, Any] | None = None,
    timeout: float = 12.0,
    live: bool = False,
) -> dict[str, Any]:
    body = build_send_friend_request(target_mid, source_type)
    return _friend_write_call(
        cfg=cfg,
        slot=slot,
        auth=auth,
        action="friend-add",
        method_path=SEND_FRIEND_REQUEST_METHOD,
        request_body=body,
        extra_metadata=extra_metadata,
        timeout=timeout,
        live=live,
        schema={
            "field_2": "player_id:string",
            "field_3": "source_type:varint",
            "source_type": source_type,
            "source_type_policy": "EXPLICIT_1_TO_4_NOT_GUESSED",
        },
    )


def handle_friend_request(
    *,
    cfg,
    slot: str,
    auth: AuthRecord,
    target_mid: str,
    accept: bool,
    extra_metadata: dict[str, Any] | None = None,
    timeout: float = 12.0,
    live: bool = False,
) -> dict[str, Any]:
    body = build_handle_friend_request(target_mid, accept)
    return _friend_write_call(
        cfg=cfg,
        slot=slot,
        auth=auth,
        action="friend-accept",
        method_path=HANDLE_FRIEND_REQUEST_METHOD,
        request_body=body,
        extra_metadata=extra_metadata,
        timeout=timeout,
        live=live,
        schema={
            "field_2": "player_id:string",
            "field_3": "accept:bool",
            "accept": bool(accept),
        },
    )



def remove_friend(
    *,
    cfg,
    slot: str,
    auth: AuthRecord,
    target_mids: list[str] | tuple[str, ...],
    extra_metadata: dict[str, Any] | None = None,
    timeout: float = 12.0,
    live: bool = False,
) -> dict[str, Any]:
    body = build_remove_friend_request(target_mids)
    return _friend_write_call(
        cfg=cfg,
        slot=slot,
        auth=auth,
        action="friend-remove",
        method_path=REMOVE_FRIEND_METHOD,
        request_body=body,
        extra_metadata=extra_metadata,
        timeout=timeout,
        live=live,
        schema={
            "field_2": "player_ids:repeated string",
            "target_count": len(tuple(target_mids)),
            "wire_policy": "REMOVE_FRIEND_REQUEST_PLAYER_IDS_FIELD_2",
        },
    )
