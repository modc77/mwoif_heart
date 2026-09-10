from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mwoif.application.login_session_manager import LoginSessionManager, LoginSessionResult
from mwoif.core.config import AppConfig
from mwoif.friend.service import handle_friend_request, list_friends, remove_friend, send_friend_request
from mwoif.heart.mailbox import mailbox_read as mailbox_read_call
from mwoif.heart.receive import heart_receive as heart_receive_call
from mwoif.heart.send import heart_send as heart_send_call
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault

Event = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ActionContext:
    receiver: LoginSessionResult
    sender: LoginSessionResult

    def public_dict(self) -> dict[str, Any]:
        return {
            "receiver": self.receiver.public_dict(),
            "sender": self.sender.public_dict(),
            "secretOutput": "NONE",
        }


class HeartActionManager:
    """Phase 4 one-step service harness.

    Each command logs in Receiver + Sender in the same process, then runs exactly
    one Friend/Heart action. It does not run the full Heart Round yet; that is
    Phase 5 so errors can be isolated step-by-step.
    """

    def __init__(self, config: AppConfig, repo: HeartRepository, vault: CredentialVault) -> None:
        self.config = config
        self.repo = repo
        self.vault = vault
        self.login_manager = LoginSessionManager(config, repo, vault)
        self.runtime_cfg = self.login_manager.runtime_cfg

    def _context(self, *, hj_id: int, hs_id: int, event_cb: Event | None = None) -> ActionContext:
        receiver = self.login_manager.login_receiver_for_job(hj_id=hj_id, event_cb=event_cb)
        sender = self.login_manager.login_sender(hs_id=hs_id, event_cb=event_cb)
        return ActionContext(receiver=receiver, sender=sender)

    def _log_action(self, *, name: str, result: dict[str, Any], hj_id: int, hs_id: int, hr_id: int, step: str, error_scope: str | None) -> None:
        ok = bool(result.get("ok"))
        err = str(result.get("error") or result.get("grpc_code") or "") or None
        detail = f"{name} ok={ok} read_only={result.get('read_only')}"
        self.repo.log_event(
            event_type=name.upper(),
            hj_id=hj_id,
            hs_id=hs_id,
            hr_id=hr_id,
            step=step,
            success=ok,
            error_scope=None if ok else error_scope,
            error_code=None if ok else err,
            detail=detail,
        )

    def friend_add(self, *, hj_id: int, hs_id: int, source_type: int = 2, live: bool = False, event_cb: Event | None = None) -> dict[str, Any]:
        ctx = self._context(hj_id=hj_id, hs_id=hs_id, event_cb=event_cb)
        result = send_friend_request(
            cfg=self.runtime_cfg,
            slot="S",
            auth=ctx.sender.auth,
            target_mid=ctx.receiver.auth.mid,
            source_type=source_type,
            timeout=float(self.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
            live=live,
        )
        self._log_action(name="friend_add", result=result, hj_id=hj_id, hs_id=hs_id, hr_id=ctx.receiver.account_id, step="ADD", error_scope="sender")
        return {"ok": bool(result.get("ok")), "action": result, "context": ctx.public_dict(), "secretOutput": "NONE"}

    def friend_accept(self, *, hj_id: int, hs_id: int, live: bool = False, event_cb: Event | None = None) -> dict[str, Any]:
        ctx = self._context(hj_id=hj_id, hs_id=hs_id, event_cb=event_cb)
        result = handle_friend_request(
            cfg=self.runtime_cfg,
            slot="R",
            auth=ctx.receiver.auth,
            target_mid=ctx.sender.auth.mid,
            accept=True,
            timeout=float(self.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
            live=live,
        )
        self._log_action(name="friend_accept", result=result, hj_id=hj_id, hs_id=hs_id, hr_id=ctx.receiver.account_id, step="ACCEPT", error_scope="receiver")
        return {"ok": bool(result.get("ok")), "action": result, "context": ctx.public_dict(), "secretOutput": "NONE"}

    def friend_remove(self, *, hj_id: int, hs_id: int, actor: str = "sender", live: bool = False, event_cb: Event | None = None) -> dict[str, Any]:
        ctx = self._context(hj_id=hj_id, hs_id=hs_id, event_cb=event_cb)
        actor_norm = actor.strip().lower()
        if actor_norm == "receiver":
            auth = ctx.receiver.auth
            target = [ctx.sender.auth.mid]
            slot = "R"
            error_scope = "receiver"
        else:
            auth = ctx.sender.auth
            target = [ctx.receiver.auth.mid]
            slot = "S"
            error_scope = "sender"
        result = remove_friend(
            cfg=self.runtime_cfg,
            slot=slot,
            auth=auth,
            target_mids=target,
            timeout=float(self.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
            live=live,
        )
        self._log_action(name="friend_remove", result=result, hj_id=hj_id, hs_id=hs_id, hr_id=ctx.receiver.account_id, step="REMOVE", error_scope=error_scope)
        return {"ok": bool(result.get("ok")), "actor": actor_norm, "action": result, "context": ctx.public_dict(), "secretOutput": "NONE"}

    def heart_send(self, *, hj_id: int, hs_id: int, live: bool = False, event_cb: Event | None = None) -> dict[str, Any]:
        ctx = self._context(hj_id=hj_id, hs_id=hs_id, event_cb=event_cb)
        result = heart_send_call(
            cfg=self.runtime_cfg,
            actor_slot="S",
            target_slot="R",
            actor_session=ctx.sender.session,
            target_session=ctx.receiver.session,
            actor_auth=ctx.sender.auth,
            live=live,
            timeout=float(self.runtime_cfg.workflow.get("ds_timeout_seconds") or 20),
        )
        self._log_action(name="heart_send", result=result, hj_id=hj_id, hs_id=hs_id, hr_id=ctx.receiver.account_id, step="SEND", error_scope="sender")
        return {"ok": bool(result.get("ok")), "action": result, "context": ctx.public_dict(), "secretOutput": "NONE"}

    def mailbox_read(self, *, hj_id: int, hs_id: int, live: bool = False, event_cb: Event | None = None) -> dict[str, Any]:
        ctx = self._context(hj_id=hj_id, hs_id=hs_id, event_cb=event_cb)
        result = mailbox_read_call(
            cfg=self.runtime_cfg,
            slot="R",
            session=ctx.receiver.session,
            auth=ctx.receiver.auth,
            from_member_seq=ctx.sender.session.member_seq,
            live=live,
            timeout=float(self.runtime_cfg.workflow.get("ds_timeout_seconds") or 20),
        )
        self._log_action(name="mailbox_read", result=result, hj_id=hj_id, hs_id=hs_id, hr_id=ctx.receiver.account_id, step="READ_MAILBOX", error_scope="receiver")
        return {"ok": bool(result.get("ok")), "suggested_life_mail_seq": result.get("suggested_life_mail_seq"), "action": result, "context": ctx.public_dict(), "secretOutput": "NONE"}

    def heart_receive(self, *, hj_id: int, hs_id: int, seq: int, live: bool = False, event_cb: Event | None = None) -> dict[str, Any]:
        ctx = self._context(hj_id=hj_id, hs_id=hs_id, event_cb=event_cb)
        result = heart_receive_call(
            cfg=self.runtime_cfg,
            actor_slot="R",
            actor_session=ctx.receiver.session,
            actor_auth=ctx.receiver.auth,
            life_mail_box_seqs=[seq],
            live=live,
            timeout=float(self.runtime_cfg.workflow.get("ds_timeout_seconds") or 20),
        )
        self._log_action(name="heart_receive", result=result, hj_id=hj_id, hs_id=hs_id, hr_id=ctx.receiver.account_id, step="RECEIVE", error_scope="receiver")
        return {"ok": bool(result.get("ok")), "action": result, "context": ctx.public_dict(), "secretOutput": "NONE"}

    def friend_list_sender(self, *, hj_id: int, hs_id: int, live: bool = False, event_cb: Event | None = None) -> dict[str, Any]:
        ctx = self._context(hj_id=hj_id, hs_id=hs_id, event_cb=event_cb)
        result = list_friends(
            cfg=self.runtime_cfg,
            slot="S",
            auth=ctx.sender.auth,
            timeout=float(self.runtime_cfg.workflow.get("grpc_timeout_seconds") or 12),
            live=live,
        )
        self._log_action(name="friend_list_sender", result=result, hj_id=hj_id, hs_id=hs_id, hr_id=ctx.receiver.account_id, step="FRIEND_LIST", error_scope="sender")
        return {"ok": bool(result.get("ok")), "action": result, "context": ctx.public_dict(), "secretOutput": "NONE"}
