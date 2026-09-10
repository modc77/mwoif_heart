from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .slots import normalize_slot
from .config import load_config
from .auth_context import merge_auth_metadata


class AuthImportError(ValueError):
    pass


@dataclass(slots=True)
class AuthRecord:
    schema: str
    slot: str
    mid: str
    game_access_token: str
    fgs_id: str
    device_id: str
    source: str
    metadata: dict[str, str]
    imported_at: str

    @property
    def ready(self) -> bool:
        return bool(self.mid and self.game_access_token)

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "slot": self.slot,
            "mid": self.mid,
            "game_access_token": (
                f"PRESENT(len={len(self.game_access_token)})"
                if self.game_access_token else "MISSING"
            ),
            "fgs_id": self.fgs_id,
            "device_id": self.device_id,
            "source": self.source,
            "metadata_keys": sorted(self.metadata.keys()),
            "imported_at": self.imported_at,
            "ready": self.ready,
        }


class AuthStore:
    def __init__(self, root: Path):
        self.root = root
        self.state_dir = root / ".state"

    def path(self, slot: str) -> Path:
        return self.state_dir / f"auth_{normalize_slot(slot)}.json"

    def save(self, record: AuthRecord) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path(record.slot).write_text(
            json.dumps(asdict(record), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, slot: str) -> AuthRecord | None:
        path = self.path(slot)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        # Backward compatible: old cache may contain email.
        # It is ignored because Auth V13.1 does not require email.
        return AuthRecord(
            schema=str(data.get("schema") or "mwoif-auth-cache-v1"),
            slot=normalize_slot(str(data.get("slot") or slot)),
            mid=str(data.get("mid") or ""),
            game_access_token=str(data.get("game_access_token") or ""),
            fgs_id=str(data.get("fgs_id") or ""),
            device_id=str(data.get("device_id") or ""),
            source=str(data.get("source") or "unknown"),
            metadata={str(k): str(v) for k, v in metadata.items() if v is not None},
            imported_at=str(data.get("imported_at") or ""),
        )

    @staticmethod
    def _extract_json(raw: str) -> str:
        text = (raw or "").lstrip("\ufeff").strip()
        if not text:
            raise AuthImportError("auth input is empty")
        if text == "PASTE_V13_1_AUTH_JSON_HERE":
            raise AuthImportError(
                "placeholder is still present; replace it with COPY AUTH CONTEXT JSON"
            )
        if text.startswith("{"):
            return text

        for prefix in ("authJson=", "authContextJson="):
            for line in text.splitlines():
                line = line.strip()
                if line.startswith(prefix):
                    value = line[len(prefix):].strip()
                    if value:
                        return value

        for marker in (
            '{"schema":"mwoif-auth-min-v1"',
            '{"schema":"mwoif-auth-v13.1"',
        ):
            start = text.find(marker)
            if start >= 0:
                candidate = text[start:].strip()
                end = candidate.rfind("}")
                if end >= 0:
                    return candidate[: end + 1]

        raise AuthImportError(
            "Auth JSON not found; use COPY AUTH CONTEXT JSON from LAB"
        )

    def import_v13_1(
        self,
        *,
        slot: str,
        raw: str,
        account: dict[str, Any] | None = None,
    ) -> AuthRecord:
        """Import either the new minimal LAB format or old full V13.1 format.

        New minimal format only needs:
          mid, game_access_token, fgs_id, game_process_elapsed_ms
        Everything stable is rebuilt from config.json.
        """
        text = self._extract_json(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AuthImportError(f"invalid auth JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise AuthImportError("auth JSON root must be an object")

        schema = str(data.get("schema") or "")
        allowed = {"", "mwoif-auth-min-v1", "mwoif-auth-v13.1"}
        if schema not in allowed:
            raise AuthImportError(
                "expected schema=mwoif-auth-min-v1 or mwoif-auth-v13.1, "
                f"got {schema!r}"
            )

        mid = str(data.get("mid") or data.get("player_id") or "").strip()
        token = str(
            data.get("game_access_token")
            or data.get("gameAccessToken")
            or ""
        ).strip()
        fgs_id = str(
            data.get("fgs_id")
            or data.get("fgs-id")
            or ""
        ).strip()
        game_process_elapsed_ms = data.get(
            "game_process_elapsed_ms",
            data.get("game-process-elapsed-ms", ""),
        )

        if not mid:
            raise AuthImportError("mid is empty")
        if not token or token in {
            "***", "****", "<REDACTED>",
            "<REAL_TOKEN_FROM_COPY_AUTH_CONTEXT_JSON>",
        }:
            raise AuthImportError("real game_access_token is missing")

        captured: dict[str, str] = {}
        raw_meta = data.get("metadata")
        if isinstance(raw_meta, dict):
            captured.update({
                str(k): str(v)
                for k, v in raw_meta.items()
                if v not in (None, "")
            })

        # Old full V13.1 aliases remain accepted during migration.
        aliases = {
            "fgs_id": "fgs-id",
            "device_id": "device-id",
            "device_name": "device-name",
            "device_model": "device-model",
            "index_file_hash": "index-file-hash",
            "game_process_elapsed_ms": "game-process-elapsed-ms",
            "combo_name": "combo-name",
            "login_platform": "login_platform",
            "market_type": "market_type",
            "locale": "locale",
            "country": "country",
            "timezone": "timezone",
            "os_version": "os_version",
        }
        for src_key, dst_key in aliases.items():
            value = data.get(src_key)
            if value not in (None, "") and dst_key not in captured:
                captured[dst_key] = str(value)

        metadata_mid = str(captured.get("player-id") or "").strip()
        if metadata_mid and metadata_mid != mid:
            raise AuthImportError(
                f"auth MID mismatch: top-level mid={mid}, "
                f"metadata player-id={metadata_mid}"
            )

        # New minimal input must carry the two changing runtime values.
        # Old full V13.1 can carry them inside metadata.
        fgs_id = fgs_id or str(captured.get("fgs-id") or "")
        if game_process_elapsed_ms in (None, ""):
            game_process_elapsed_ms = captured.get(
                "game-process-elapsed-ms", ""
            )

        if schema == "mwoif-auth-min-v1":
            if not fgs_id:
                raise AuthImportError("fgs_id is required in minimal Auth JSON")
            if game_process_elapsed_ms in (None, ""):
                raise AuthImportError(
                    "game_process_elapsed_ms is required in minimal Auth JSON"
                )

        try:
            cfg = load_config(self.root)
        except Exception:
            # Unit-test/backward compatibility fallback: use package project config.
            cfg = load_config(Path(__file__).resolve().parents[1])
        metadata = merge_auth_metadata(
            cfg,
            mid=mid,
            captured=captured,
            fgs_id=fgs_id,
            game_process_elapsed_ms=game_process_elapsed_ms,
        )

        record = AuthRecord(
            schema="mwoif-auth-cache-v1",
            slot=normalize_slot(slot),
            mid=mid,
            game_access_token=token,
            fgs_id=str(metadata.get("fgs-id") or ""),
            device_id=str(metadata.get("device-id") or ""),
            source="game_owned_materializer",
            metadata=metadata,
            imported_at=datetime.now(timezone.utc).isoformat(),
        )
        self.save(record)
        return record

