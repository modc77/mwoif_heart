from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .slots import normalize_slot


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

        marker = '{"schema":"mwoif-auth-v13.1"'
        start = text.find(marker)
        if start >= 0:
            candidate = text[start:].strip()
            end = candidate.rfind("}")
            if end >= 0:
                return candidate[: end + 1]

        raise AuthImportError(
            "V13.1 Auth JSON not found; use COPY AUTH CONTEXT JSON"
        )

    def import_v13_1(
        self,
        *,
        slot: str,
        raw: str,
        account: dict[str, Any] | None = None,
    ) -> AuthRecord:
        # `account` is accepted only for backward CLI/test compatibility.
        # email/mid_expected from config are intentionally ignored.
        text = self._extract_json(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AuthImportError(f"invalid V13.1 auth JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise AuthImportError("auth JSON root must be an object")

        schema = str(data.get("schema") or "")
        if schema and schema != "mwoif-auth-v13.1":
            raise AuthImportError(
                f"expected schema=mwoif-auth-v13.1, got {schema!r}"
            )

        mid = str(data.get("mid") or data.get("player_id") or "").strip()
        token = str(
            data.get("game_access_token")
            or data.get("gameAccessToken")
            or ""
        ).strip()

        if not mid:
            raise AuthImportError("mid/player-id is empty")
        if not token or token in {
            "***", "****", "<REDACTED>", "<REAL_TOKEN_FROM_COPY_AUTH_CONTEXT_JSON>"
        }:
            raise AuthImportError("real game_access_token is missing")

        metadata: dict[str, str] = {}
        raw_meta = data.get("metadata")
        if isinstance(raw_meta, dict):
            metadata.update({
                str(k): str(v)
                for k, v in raw_meta.items()
                if v not in (None, "")
            })

        # Internal consistency check only: MID is owned by Auth itself.
        metadata_mid = str(metadata.get("player-id") or "").strip()
        if metadata_mid and metadata_mid != mid:
            raise AuthImportError(
                f"auth MID mismatch: top-level mid={mid}, metadata player-id={metadata_mid}"
            )

        aliases = {
            "fgs_id": "fgs-id",
            "device_id": "device-id",
            "device_name": "device-name",
            "device_model": "device-model",
            "index_file_hash": "index-file-hash",
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
            if value not in (None, "") and dst_key not in metadata:
                metadata[dst_key] = str(value)

        record = AuthRecord(
            schema="mwoif-auth-cache-v1",
            slot=normalize_slot(slot),
            mid=mid,
            game_access_token=token,
            fgs_id=str(data.get("fgs_id") or metadata.get("fgs-id") or ""),
            device_id=str(data.get("device_id") or metadata.get("device-id") or ""),
            source=str(data.get("source") or "game_owned_materializer"),
            metadata=metadata,
            imported_at=datetime.now(timezone.utc).isoformat(),
        )
        self.save(record)
        return record
