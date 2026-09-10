from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .auth_store import AuthRecord
from .auth_context import fixed_auth_metadata


NATIVE_ORDER = [
    "authorization",
    "player-id",
    "combo-name",
    "index-file-hash",
    "version",
    "version-code",
    "timezone",
    "country",
    "os.type",
    "os_version",
    "login_platform",
    "market_type",
    "fgs-id",
    "locale",
    "device-id",
    "device-name",
    "device-model",
]


def _put_if_missing(values: dict[str, str], key: str, value: Any) -> None:
    if not values.get(key) and value not in (None, ""):
        values[key] = str(value)


def build_metadata(
    *,
    cfg,
    slot: str,
    auth: AuthRecord,
    extra: dict[str, Any] | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    if not auth.ready:
        raise ValueError("auth context is not ready")
    if not auth.game_access_token:
        raise ValueError("game_access_token is required")
    if not auth.mid:
        raise ValueError("MID/player-id is required")

    # Stable values are owned by Python config; account/runtime values come from Auth.
    values: dict[str, str] = fixed_auth_metadata(cfg, mid=auth.mid)
    values.update(auth.metadata)

    # Never use Session V13 session_key here.
    values["authorization"] = "Bearer " + auth.game_access_token
    values["player-id"] = auth.mid

    _put_if_missing(values, "fgs-id", auth.fgs_id)
    _put_if_missing(values, "device-id", auth.device_id)

    if extra:
        for k, v in extra.items():
            if v not in (None, ""):
                values[str(k)] = str(v)

    metadata = [
        (key, values[key])
        for key in NATIVE_ORDER
        if values.get(key) not in (None, "")
    ]

    missing_dynamic = [
        key
        for key in ("combo-name", "login_platform", "fgs-id")
        if not values.get(key)
    ]
    return metadata, missing_dynamic


def redacted_metadata(items: list[tuple[str, str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for key, value in items:
        if key == "authorization":
            token_len = max(0, len(value) - len("Bearer "))
            value = f"Bearer <REDACTED len={token_len}>"
        out.append([key, value])
    return out


def load_metadata_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("metadata file root must be a JSON object")
    return data
