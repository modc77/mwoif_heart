from __future__ import annotations

from typing import Any


def fixed_auth_metadata(cfg, *, mid: str) -> dict[str, str]:
    """Build account-independent metadata from the Python project config.

    Only account/runtime values that really change are expected from LAB:
      mid, game_access_token, fgs_id, game_process_elapsed_ms
    """
    dev = cfg.devplay
    game = cfg.game
    server = cfg.server

    values = {
        "runtime-metadata-version": dev.get("runtime_metadata_version", "V13.6.1"),
        "player-id": mid,
        "version": game.get("version", ""),
        "version-code": game.get("build_version", ""),
        "timezone": dev.get("timezone", "Asia/Bangkok"),
        "country": dev.get("location_country", "US"),
        "os.type": dev.get("os_type", "A"),
        "os_version": dev.get("os_version", "12"),
        "locale": dev.get("locale", "en-US"),
        "device-name": dev.get("device_name", dev.get("model", "")),
        "device-model": dev.get("device_model", dev.get("model", "")),
        "device-id": dev.get("device_id", ""),
        "index-file-hash": game.get("index_file_hash", ""),
        "time-zone-distance": dev.get("time_zone_distance", "25200"),
        "market_type": dev.get("market_type", "GOOGLE_PLAY"),
        "login_platform": dev.get("login_platform", "email"),
        "friend-grpc-target": server.get("friend_grpc_target", ""),
    }
    return {
        str(k): str(v)
        for k, v in values.items()
        if v not in (None, "")
    }


def merge_auth_metadata(
    cfg,
    *,
    mid: str,
    captured: dict[str, Any] | None = None,
    fgs_id: str = "",
    game_process_elapsed_ms: str | int = "",
) -> dict[str, str]:
    """Merge fixed Python metadata with the small changing LAB context.

    Captured metadata wins for backward compatibility with old full V13.1 JSON.
    """
    metadata = fixed_auth_metadata(cfg, mid=mid)
    if captured:
        for key, value in captured.items():
            if value not in (None, ""):
                metadata[str(key)] = str(value)

    # Identity is canonical from top-level MID.
    metadata["player-id"] = mid

    if fgs_id:
        metadata["fgs-id"] = str(fgs_id)
    if game_process_elapsed_ms not in (None, ""):
        metadata["game-process-elapsed-ms"] = str(game_process_elapsed_ms)

    # Fixed target is always guaranteed even when LAB did not export it.
    target = str(cfg.server.get("friend_grpc_target") or "").strip()
    if target:
        metadata.setdefault("friend-grpc-target", target)

    return metadata
