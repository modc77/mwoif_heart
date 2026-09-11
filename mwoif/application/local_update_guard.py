from __future__ import annotations

import time
from typing import Any, Callable

from mwoif.application.bootstrap import check_health
from mwoif.application.login_session_manager import LoginSessionManager
from mwoif.core.config import AppConfig
from mwoif.friend.service import list_friends
from mwoif.heart.mailbox import mailbox_read
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault

Event = Callable[[str], None]


class LocalUpdateGuard:
    """Read-only smoke test used after a game update.

    It intentionally avoids ADD/ACCEPT/SEND/RECEIVE mutations.  The goal is to
    tell the operator which transport layer changed: DB/config, direct HTTP
    login, initMember3, Friend gRPC, or DS-v4 mailbox.
    """

    def __init__(self, config: AppConfig, repo: HeartRepository, vault: CredentialVault) -> None:
        self.config = config
        self.repo = repo
        self.vault = vault
        self.login_manager = LoginSessionManager(config, repo, vault)

    @staticmethod
    def _emit(cb: Event | None, text: str) -> None:
        if cb:
            cb(text)

    def run(
        self,
        *,
        sender_hs_id: int | None = None,
        template_hs_id: int | None = None,
        event_cb: Event | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        self.repo.restore_local_auto_disabled_senders()
        stages: list[dict[str, Any]] = []

        health = check_health(self.config, include_dashboard=False)
        db_ok = bool(health.ok)
        stages.append({"stage": "DB", "ok": db_ok, "missing_tables": list(health.missing_tables)})
        self._emit(event_cb, f"UPDATE CHECK DB {'PASS' if db_ok else 'FAIL'} missing={len(health.missing_tables)} secretOutput=NONE")
        if not db_ok:
            return self._finish(stages, started, sender_hs_id=None)

        hs_id = int(sender_hs_id or template_hs_id or 0)
        if hs_id <= 0 or self.repo.get_sender(hs_id) is None:
            rows = self.repo.list_sender_warm_candidates(limit=1)
            hs_id = int(rows[0]["hs_id"]) if rows else 0
        if hs_id <= 0:
            stages.append({"stage": "SENDER", "ok": False, "error": "NO_SENDER_AVAILABLE"})
            self._emit(event_cb, "UPDATE CHECK SENDER FAIL code=NO_SENDER_AVAILABLE secretOutput=NONE")
            return self._finish(stages, started, sender_hs_id=None)

        template_id = int(template_hs_id or hs_id)
        login_started = time.monotonic()
        try:
            login = self.login_manager.login_sender_http_template(
                hs_id=hs_id,
                template_hs_id=template_id,
                event_cb=event_cb,
            )
        except Exception as exc:
            stages.append({
                "stage": "LOGIN_SESSION",
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc)[:240],
                "elapsed_ms": round((time.monotonic() - login_started) * 1000, 1),
            })
            self._emit(event_cb, f"UPDATE CHECK LOGIN/SESSION FAIL error={type(exc).__name__} secretOutput=NONE")
            return self._finish(stages, started, sender_hs_id=hs_id)

        stages.append({
            "stage": "LOGIN_SESSION",
            "ok": bool(login.auth.ready and login.session.established),
            "elapsed_ms": round((time.monotonic() - login_started) * 1000, 1),
            "member_seq_present": login.session.member_seq > 0,
        })
        self._emit(event_cb, "UPDATE CHECK LOGIN/SESSION PASS provider=direct-http-exact-template browser=NO secretOutput=NONE")

        # Friend/Heart V2-pass services consume the runtime compatibility
        # config shape (server/devplay/game/workflow), not the V3 AppConfig.
        # LoginSessionManager already owns the exact adapter used by the
        # proven login/session path, so reuse it here instead of rebuilding
        # or passing AppConfig directly.
        runtime_cfg = self.login_manager.runtime_cfg

        friend_started = time.monotonic()
        friend = list_friends(
            cfg=runtime_cfg,
            slot="S",
            auth=login.auth,
            timeout=12.0,
            live=True,
            capacity=300,
            include_player_ids=False,
        )
        friend_ok = bool(friend.get("ok"))
        stages.append({
            "stage": "FRIEND_GRPC",
            "ok": friend_ok,
            "elapsed_ms": friend.get("elapsed_ms") or round((time.monotonic() - friend_started) * 1000, 1),
            "grpc_code": friend.get("grpc_code"),
            "friend_count": friend.get("friend_count") if friend_ok else None,
        })
        self._emit(event_cb, f"UPDATE CHECK FRIEND gRPC {'PASS' if friend_ok else 'FAIL'} secretOutput=NONE")

        mail_started = time.monotonic()
        mail = mailbox_read(
            cfg=runtime_cfg,
            slot="S",
            session=login.session,
            auth=login.auth,
            live=True,
            timeout=20.0,
        )
        mail_ok = bool(mail.get("ok"))
        stages.append({
            "stage": "MAILBOX_DS_V4",
            "ok": mail_ok,
            "elapsed_ms": mail.get("elapsed_ms") or round((time.monotonic() - mail_started) * 1000, 1),
            "response_code": mail.get("response_code"),
            "decoded_response_present": bool(mail.get("decoded_response_present")),
        })
        self._emit(event_cb, f"UPDATE CHECK MAILBOX/DSv4 {'PASS' if mail_ok else 'FAIL'} secretOutput=NONE")

        return self._finish(stages, started, sender_hs_id=hs_id)

    def _finish(self, stages: list[dict[str, Any]], started: float, *, sender_hs_id: int | None) -> dict[str, Any]:
        ok = bool(stages) and all(bool(item.get("ok")) for item in stages)
        failed = [str(item.get("stage")) for item in stages if not item.get("ok")]
        result = {
            "ok": ok,
            "mode": "LOCAL_UPDATE_READ_ONLY_SMOKE",
            "sender_hs_id": sender_hs_id,
            "stages": stages,
            "failed_stages": failed,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "writes": "NONE_GAME_STATE",
            "browser": "NO",
            "secretOutput": "NONE",
        }
        self.repo.log_event(
            event_type="LOCAL_UPDATE_CHECK_PASS" if ok else "LOCAL_UPDATE_CHECK_FAIL",
            hs_id=sender_hs_id,
            success=ok,
            error_scope="update-check" if not ok else None,
            error_code=(failed[0] if failed else None),
            detail=f"read-only update check stages={len(stages)} failed={','.join(failed) if failed else 'none'}",
        )
        return result
