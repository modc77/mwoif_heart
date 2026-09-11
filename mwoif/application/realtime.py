from __future__ import annotations

import json
import time
from typing import Any, Callable

Event = Callable[[str], None]


def emit_rt(cb: Event | None, event: str, **payload: Any) -> None:
    """Emit a compact structured runtime event over the existing string callback.

    The UI recognizes the @RT prefix and updates live widgets without polling.
    CLI callers remain backwards-compatible because this still travels through the
    normal text event callback.
    """
    if cb is None:
        return
    data = {
        "event": str(event),
        "ts": round(time.time(), 3),
        **payload,
        "secretOutput": "NONE",
    }
    cb("@RT " + json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str))


def emit_user(cb: Event | None, text: str) -> None:
    if cb is not None:
        cb(str(text))
