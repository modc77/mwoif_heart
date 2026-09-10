from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .auth_context import merge_auth_metadata
from .auth_store import AuthRecord
from .client_identity import ClientIdentity
from .devplay_login import login_with_email_password
from .game_session import bootstrap_session
from .models import LoginBundle

Event = Callable[[str], None]


def make_auth_record(cfg, root: Path, slot: str, bundle: LoginBundle, identity: ClientIdentity) -> AuthRecord:
    metadata = merge_auth_metadata(
        cfg,
        mid=bundle.mid,
        captured={"login_platform": "email"},
        fgs_id=identity.fgs_id,
        game_process_elapsed_ms=0,
    )
    return AuthRecord(
        schema="mwoif-auth-v2-runtime",
        slot=slot,
        mid=bundle.mid,
        game_access_token=bundle.game_access_token,
        fgs_id=identity.fgs_id,
        device_id=str(cfg.devplay.get("device_id") or ""),
        source="devplay_email_login",
        metadata=metadata,
        imported_at=datetime.now(timezone.utc).isoformat(),
    )


def login_and_bootstrap(cfg, root: Path, slot: str, email: str, password: str, identity: ClientIdentity, event_cb: Event | None = None):
    bundle = login_with_email_password(
        cfg, email, password, event_cb=event_cb, fgs_id=identity.fgs_id
    )
    auth = make_auth_record(cfg, root, slot, bundle, identity)
    session = bootstrap_session(cfg, slot, auth, event_cb=event_cb)
    return bundle, auth, session
