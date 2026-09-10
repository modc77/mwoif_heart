from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class ClientIdentity:
    fgs_id: str

    @classmethod
    def load_or_create(cls, root: Path) -> "ClientIdentity":
        path = root / "state" / "client_identity.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                fgs = str(data.get("fgs_id") or "").strip()
                if fgs:
                    return cls(fgs_id=fgs)
            except Exception:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        fgs = str(uuid.uuid4())
        path.write_text(
            json.dumps({
                "schema": "mwoif-v2-client-identity",
                "fgs_id": fgs,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return cls(fgs_id=fgs)
