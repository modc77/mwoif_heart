from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Config:
    root: Path
    raw: dict[str, Any]

    @property
    def server(self) -> dict[str, Any]:
        return self.raw["server"]

    @property
    def devplay(self) -> dict[str, Any]:
        return self.raw["devplay"]

    @property
    def game(self) -> dict[str, Any]:
        return self.raw["game"]

    @property
    def workflow(self) -> dict[str, Any]:
        return self.raw.get("workflow", {})


def load_config(root: Path | None = None) -> Config:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    path = root / "config.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config.json root must be an object")
    for key in ("server", "devplay", "game"):
        if not isinstance(raw.get(key), dict):
            raise ValueError(f"config.json missing object: {key}")
    return Config(root=root, raw=raw)
