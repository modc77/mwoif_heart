from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from .redact import redact_mapping


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, dict):
            record.msg = redact_mapping(record.msg)
        return True


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        if isinstance(record.msg, dict):
            payload.update(redact_mapping(record.msg))
        else:
            payload["message"] = record.getMessage()
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=_json_default)


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def configure_logging(level: str = "INFO", *, json_lines: bool = False) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SecretRedactionFilter())
    if json_lines:
        handler.setFormatter(JsonLineFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mwoif-heart-v3.{name}")
