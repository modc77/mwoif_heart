from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .slots import normalize_slot


class SessionImportError(ValueError):
    pass


@dataclass(slots=True)
class SessionRecord:
    schema: str
    slot: str
    member_seq: int
    current_lv: int
    session_key: str
    source: str
    imported_at: str

    @property
    def established(self) -> bool:
        return self.member_seq > 0 and bool(self.session_key)

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "slot": self.slot,
            "member_seq": self.member_seq,
            "current_lv": self.current_lv,
            "session_key": "<REDACTED>" if self.session_key else "",
            "source": self.source,
            "imported_at": self.imported_at,
            "established": self.established,
        }


class SessionStore:
    def __init__(self, root: Path):
        self.root = root
        self.state_dir = root / ".state"

    def path(self, slot: str) -> Path:
        return self.state_dir / f"session_{normalize_slot(slot)}.json"

    def save(self, record: SessionRecord) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path(record.slot).write_text(
            json.dumps(asdict(record), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, slot: str) -> SessionRecord | None:
        path = self.path(slot)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        # Backward compatible: old cache may still contain email/mid.
        # They are ignored because Session V13 does not own those fields.
        return SessionRecord(
            schema=str(data.get("schema") or "mwoif-session-cache-v1"),
            slot=normalize_slot(str(data.get("slot") or slot)),
            member_seq=int(data.get("member_seq") or 0),
            current_lv=int(data.get("current_lv") or 0),
            session_key=str(data.get("session_key") or ""),
            source=str(data.get("source") or "unknown"),
            imported_at=str(data.get("imported_at") or ""),
        )

    @staticmethod
    def _extract_v13_json(raw: str) -> str:
        text = (raw or "").lstrip("\ufeff").strip()
        if not text:
            raise SessionImportError(
                "session input is empty; paste V13 Session JSON into the import file and save it"
            )
        if text == "PASTE_V13_SESSION_JSON_HERE":
            raise SessionImportError(
                "placeholder is still present; replace it with the V13 Session JSON and save the file"
            )
        if text.startswith("{"):
            return text

        for line in text.splitlines():
            line = line.strip()
            if line.startswith("sessionJson="):
                candidate = line[len("sessionJson="):].strip()
                if candidate:
                    return candidate

        marker = '{"schema":"mwoif-session-v13"'
        start = text.find(marker)
        if start >= 0:
            candidate = text[start:].strip()
            end = candidate.rfind("}")
            if end >= 0:
                return candidate[: end + 1]

        raise SessionImportError(
            "V13 Session JSON not found; paste the exact JSON copied by COPY SESSION JSON"
        )

    def import_v13(
        self,
        *,
        slot: str,
        raw: str,
        account: dict[str, Any] | None = None,
    ) -> SessionRecord:
        # `account` is accepted only for backward CLI/test compatibility.
        # It is intentionally ignored.
        json_text = self._extract_v13_json(raw)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise SessionImportError(f"invalid V13 session JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SessionImportError("V13 session JSON root must be an object")
        if str(data.get("schema") or "") != "mwoif-session-v13":
            raise SessionImportError("expected schema=mwoif-session-v13")

        member_seq = int(data.get("member_seq") or 0)
        current_lv = int(data.get("current_lv") or 0)
        session_key = str(data.get("session_key") or "")
        if member_seq <= 0:
            raise SessionImportError("member_seq must be > 0")
        if not session_key:
            raise SessionImportError("session_key is empty")

        record = SessionRecord(
            schema="mwoif-session-cache-v1",
            slot=normalize_slot(slot),
            member_seq=member_seq,
            current_lv=current_lv,
            session_key=session_key,
            source=str(data.get("source") or "mwoif-session-v13"),
            imported_at=datetime.now(timezone.utc).isoformat(),
        )
        self.save(record)
        return record
