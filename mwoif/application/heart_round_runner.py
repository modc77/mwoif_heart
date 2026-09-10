from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from mwoif.application.login_session_manager import LoginSessionManager, LoginSessionResult
from mwoif.core.config import AppConfig
from mwoif.domain.errors import ConfigError
from mwoif.friend.service import handle_friend_request, remove_friend, send_friend_request
from mwoif.heart.mailbox import mailbox_read as mailbox_read_call
from mwoif.heart.receive import heart_receive as heart_receive_call
from mwoif.heart.send import heart_send as heart_send_call
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault

Event = Callable[[str], None]


@dataclass(slots=True)
class RoundFailure(Exception):
    step: str
    scope: str
    code: str
    message: str
    result: dict[str, Any] | None = None
    recovery_required: bool = False

    def __str__(self) -> str:
        return f"{self.step}:{self.code}:{self.message}"


def _emit(cb: Event | None, text: str) -> None:
    if cb:
        cb(text)


def _cfg_int(config: AppConfig, name: str, default: int | None = None) -> int | None:
    raw = config.extra.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _cfg_float(config: AppConfig, name: str, default: float) -> float:
    raw = config.extra.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def _safe_code(result: dict[str, Any] | None) -> str:
    if not isinstance(result, dict):
        return "UNKNOWN"
    return str(
        result.get("error")
        or result.get("grpc_code")
        or result.get("response_code")
        or result.get("code")
        or "FAILED"
    )


def _step_summary(step: str, result: dict[str, Any] | None = None, *, ok: bool | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"step": step, "ok": bool(result.get("ok")) if ok is None and isinstance(result, dict) else bool(ok)}
    if isinstance(result, dict):
        for key in (
            "action",
            "endpoint",
            "method",
            "elapsed_ms",
            "http_status",
            "response_code",
            "response_message",
            "grpc_code",
            "wire_error",
            "suggested_life_mail_seq",
            "mail_list_count",
            "network_action_enabled",
            "read_only",
        ):
            if key in result:
                data[key] = result.get(key)
    if extra:
        data.update(extra)
    data["secretOutput"] = "NONE"
    return data


def _require_step(step: str, result: dict[str, Any], *, scope: str, recovery_required: bool = False) -> dict[str, Any]:
    if bool(result.get("ok")):
        return result
    code = _safe_code(result)
    message = str(result.get("message") or result.get("grpc_details") or result.get("response_message") or code)
    raise RoundFailure(step=step, scope=scope, code=code, message=message, result=result, recovery_required=recovery_required)


class HeartRoundRunner:
    """Phase 5.0 one-round runner.

    This is intentionally the first production-core step only: one command runs
    one complete Heart round using existing Phase 1 DB tables.  It does not add
    or migrate database schema; random sender leasing/cooldown tables come in a
    later Phase 5.x migration after this baseline passes.
    """

    def __init__(self, config: AppConfig, repo: HeartRepository, vault: CredentialVault) -> None:
        self.config = config
        self.repo = repo
        self.vault = vault
        self.login_manager = LoginSessionManager(config, repo, vault)
        self.runtime_cfg = self.login_manager.runtime_cfg

    def _job_hr_id(self, hj_id: int) -> int:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="HEART_ROUND_RUN")
        return int(job["hr_id"])

    def _timeout(self, key: str, default: float) -> float:
        return float(self.runtime_cfg.workflow.get(key) or default)

    def run_one(
        self,
        *,
        hj_id: int,
        hs_id: int,
        live: bool = False,
        source_type: int = 2,
        template_hs_id: int | None = None,
        template_hr_id: int | None = None,
        sequence_no: int | None = None,
        mailbox_attempts: int | None = None,
        mailbox_delay_seconds: float | None = None,
        verbose_actions: bool = False,
        event_cb: Event | None = None,
    ) -> dict[str, Any]:
        hr_id = self._job_hr_id(hj_id)
        template_hs_id = int(template_hs_id or _cfg_int(self.config, "MWOIF_HEART_ROUND_TEMPLATE_SENDER_HS_ID", hs_id) or hs_id)
        template_hr_id = int(template_hr_id or _cfg_int(self.config, "MWOIF_HEART_ROUND_TEMPLATE_RECEIVER_HR_ID", hr_id) or hr_id)
        mailbox_attempts = int(mailbox_attempts if mailbox_attempts is not None else (_cfg_int(self.config, "MWOIF_HEART_ROUND_MAILBOX_ATTEMPTS", 5) or 5))
        mailbox_delay_seconds = float(mailbox_delay_seconds if mailbox_delay_seconds is not None else _cfg_float(self.config, "MWOIF_HEART_ROUND_MAILBOX_DELAY_SECONDS", 2.0))

        if not live:
            return {
                "ok": True,
                "read_only": True,
                "action": "heart-round-plan",
                "hj_id": hj_id,
                "hr_id": hr_id,
                "hs_id": hs_id,
                "template_source": {"receiver_hr_id": template_hr_id, "sender_hs_id": template_hs_id},
                "plan": ["LOGIN_RECEIVER_ONCE", "LOGIN_SENDER_ONCE", "ADD", "ACCEPT", "SEND", "MAILBOX", "RECEIVE", "REMOVE"],
                "write_guard": "ADD_--live_TO_RUN_ONE_REAL_ROUND",
                "database_schema_changed": False,
                "secretOutput": "NONE",
            }

        started = time.monotonic()
        steps: list[dict[str, Any]] = []
        actions: dict[str, Any] = {}
        hround_id: int | None = None
        sender_login: LoginSessionResult | None = None
        receiver_login: LoginSessionResult | None = None
        sent_confirmed = False

        try:
            sequence = int(sequence_no or self.repo.next_round_sequence(hj_id=hj_id))
            round_row = self.repo.create_round(hj_id=hj_id, hs_id=hs_id, hr_id=hr_id, sequence_no=sequence)
            hround_id = int(round_row["hround_id"])
            self.repo.mark_job_running(hj_id=hj_id)
            self.repo.update_round_step(hround_id=hround_id, current_step="LOGIN_RECEIVER", status="running")
            self.repo.log_event(event_type="HEART_ROUND_START", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="START", success=True, detail="phase5 one-round runner started")
            _emit(event_cb, f"P5 ROUND START hj_id={hj_id} hround_id={hround_id} hs_id={hs_id} receiver={hr_id} browser=NO secretOutput=NONE")

            _emit(event_cb, "P5 LOGIN RECEIVER once provider=direct-http-exact-template secretOutput=NONE")
            receiver_login = self.login_manager.login_receiver_for_job_http_template(
                hj_id=hj_id,
                template_hr_id=template_hr_id,
                event_cb=event_cb,
            )
            steps.append(_step_summary("LOGIN_RECEIVER", ok=True, extra={"provider": "direct-http-exact-template", "account_id": receiver_login.account_id}))

            self.repo.update_round_step(hround_id=hround_id, current_step="LOGIN_SENDER", last_confirmed_step="LOGIN_RECEIVER")
            _emit(event_cb, "P5 LOGIN SENDER once provider=direct-http-exact-template secretOutput=NONE")
            sender_login = self.login_manager.login_sender_http_template(
                hs_id=hs_id,
                template_hs_id=template_hs_id,
                event_cb=event_cb,
            )
            steps.append(_step_summary("LOGIN_SENDER", ok=True, extra={"provider": "direct-http-exact-template", "account_id": sender_login.account_id, "template_hs_id": template_hs_id}))

            self.repo.update_round_step(hround_id=hround_id, current_step="ADD", last_confirmed_step="LOGIN_SENDER")
            add_result = send_friend_request(
                cfg=self.runtime_cfg,
                slot="S",
                auth=sender_login.auth,
                target_mid=receiver_login.auth.mid,
                source_type=source_type,
                timeout=self._timeout("grpc_timeout_seconds", 12),
                live=True,
            )
            actions["add"] = add_result if verbose_actions else None
            _require_step("ADD", add_result, scope="sender")
            steps.append(_step_summary("ADD", add_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="ACCEPT", last_confirmed_step="ADD")
            self.repo.log_event(event_type="HEART_ROUND_ADD_OK", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="ADD", success=True, detail="sender sent friend request")
            _emit(event_cb, f"P5 STEP ADD OK elapsed_ms={add_result.get('elapsed_ms')} secretOutput=NONE")

            accept_result = handle_friend_request(
                cfg=self.runtime_cfg,
                slot="R",
                auth=receiver_login.auth,
                target_mid=sender_login.auth.mid,
                accept=True,
                timeout=self._timeout("grpc_timeout_seconds", 12),
                live=True,
            )
            actions["accept"] = accept_result if verbose_actions else None
            _require_step("ACCEPT", accept_result, scope="receiver")
            steps.append(_step_summary("ACCEPT", accept_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="SEND", last_confirmed_step="ACCEPT")
            self.repo.log_event(event_type="HEART_ROUND_ACCEPT_OK", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="ACCEPT", success=True, detail="receiver accepted friend request")
            _emit(event_cb, f"P5 STEP ACCEPT OK elapsed_ms={accept_result.get('elapsed_ms')} secretOutput=NONE")

            send_result = heart_send_call(
                cfg=self.runtime_cfg,
                actor_slot="S",
                target_slot="R",
                actor_session=sender_login.session,
                target_session=receiver_login.session,
                actor_auth=sender_login.auth,
                live=True,
                timeout=self._timeout("ds_timeout_seconds", 20),
            )
            actions["send"] = send_result if verbose_actions else None
            _require_step("SEND", send_result, scope="sender")
            sent_confirmed = True
            steps.append(_step_summary("SEND", send_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="MAILBOX", last_confirmed_step="SEND", send_outcome="confirmed", cancel_locked=True)
            self.repo.log_event(event_type="HEART_ROUND_SEND_OK", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="SEND", success=True, detail="sender sent one heart")
            _emit(event_cb, f"P5 STEP SEND OK http_status={send_result.get('http_status')} response_code={send_result.get('response_code')} secretOutput=NONE")

            selected_seq: int | None = None
            mailbox_result: dict[str, Any] | None = None
            for attempt in range(1, max(1, mailbox_attempts) + 1):
                mailbox_result = mailbox_read_call(
                    cfg=self.runtime_cfg,
                    slot="R",
                    session=receiver_login.session,
                    auth=receiver_login.auth,
                    from_member_seq=sender_login.session.member_seq,
                    live=True,
                    timeout=self._timeout("ds_timeout_seconds", 20),
                )
                actions[f"mailbox_{attempt}"] = mailbox_result if verbose_actions else None
                selected_seq = mailbox_result.get("suggested_life_mail_seq") if bool(mailbox_result.get("ok")) else None
                steps.append(_step_summary("MAILBOX", mailbox_result, extra={"attempt": attempt}))
                if selected_seq:
                    break
                if attempt < max(1, mailbox_attempts):
                    _emit(event_cb, f"P5 STEP MAILBOX WAIT attempt={attempt}/{mailbox_attempts} seq=NONE delay={mailbox_delay_seconds}s secretOutput=NONE")
                    time.sleep(max(0.0, mailbox_delay_seconds))
            if not mailbox_result or not bool(mailbox_result.get("ok")):
                _require_step("MAILBOX", mailbox_result or {"ok": False, "error": "MAILBOX_NO_RESULT"}, scope="receiver", recovery_required=sent_confirmed)
            if not selected_seq:
                raise RoundFailure(step="MAILBOX", scope="receiver", code="LIFE_MAIL_SEQ_NOT_FOUND", message="mailbox read passed but no life mail seq from requested sender was found", result=mailbox_result, recovery_required=sent_confirmed)
            self.repo.update_round_step(hround_id=hround_id, current_step="RECEIVE", last_confirmed_step="MAILBOX")
            self.repo.log_event(event_type="HEART_ROUND_MAILBOX_OK", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="MAILBOX", success=True, detail="receiver selected latest life mail seq")
            _emit(event_cb, f"P5 STEP MAILBOX OK seq=present attempts={len([s for s in steps if s.get('step') == 'MAILBOX'])} secretOutput=NONE")

            receive_result = heart_receive_call(
                cfg=self.runtime_cfg,
                actor_slot="R",
                actor_session=receiver_login.session,
                actor_auth=receiver_login.auth,
                life_mail_box_seqs=[int(selected_seq)],
                live=True,
                timeout=self._timeout("ds_timeout_seconds", 20),
            )
            actions["receive"] = receive_result if verbose_actions else None
            _require_step("RECEIVE", receive_result, scope="receiver", recovery_required=sent_confirmed)
            steps.append(_step_summary("RECEIVE", receive_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="REMOVE", last_confirmed_step="RECEIVE", send_outcome="confirmed")
            self.repo.log_event(event_type="HEART_ROUND_RECEIVE_OK", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="RECEIVE", success=True, detail="receiver accepted one heart")
            _emit(event_cb, f"P5 STEP RECEIVE OK http_status={receive_result.get('http_status')} response_code={receive_result.get('response_code')} secretOutput=NONE")

            remove_result = remove_friend(
                cfg=self.runtime_cfg,
                slot="S",
                auth=sender_login.auth,
                target_mids=[receiver_login.auth.mid],
                timeout=self._timeout("grpc_timeout_seconds", 12),
                live=True,
            )
            actions["remove"] = remove_result if verbose_actions else None
            _require_step("REMOVE", remove_result, scope="sender")
            steps.append(_step_summary("REMOVE", remove_result))
            self.repo.mark_round_passed(hround_id=hround_id)
            self.repo.mark_one_round_success(hj_id=hj_id, hs_id=hs_id, hr_id=hr_id)
            self.repo.log_event(event_type="HEART_ROUND_PASS", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="PASS", success=True, detail="one complete heart round passed")
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            _emit(event_cb, f"P5 ROUND PASS hj_id={hj_id} hround_id={hround_id} hs_id={hs_id} elapsed_ms={elapsed_ms} secretOutput=NONE")
            out = {
                "ok": True,
                "action": "heart-round-run",
                "hj_id": hj_id,
                "hround_id": hround_id,
                "hr_id": hr_id,
                "hs_id": hs_id,
                "sequence_no": sequence,
                "login_counts": {"receiver": 1, "sender": 1},
                "login_provider": "direct-http-exact-template",
                "browser_during_replay": False,
                "template_source": {"receiver_hr_id": template_hr_id, "sender_hs_id": template_hs_id},
                "selected_life_mail_seq_present": bool(selected_seq),
                "steps": steps,
                "database_schema_changed": False,
                "elapsed_ms": elapsed_ms,
                "secretOutput": "NONE",
            }
            if verbose_actions:
                out["actions"] = {k: v for k, v in actions.items() if v is not None}
            return out

        except RoundFailure as exc:
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            if hround_id is not None:
                self.repo.mark_round_failed(
                    hround_id=hround_id,
                    error_scope=exc.scope,
                    error_stage=exc.step,
                    error_code=exc.code,
                    error_message=exc.message,
                    recovery_required=exc.recovery_required,
                )
                self.repo.mark_one_round_failed(hj_id=hj_id, hs_id=hs_id, hr_id=hr_id, error_scope=exc.scope, error_code=exc.code, message=exc.message)
                self.repo.log_event(event_type="HEART_ROUND_FAILED", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step=exc.step, success=False, error_scope=exc.scope, error_code=exc.code, detail=exc.message)
            _emit(event_cb, f"P5 ROUND FAILED step={exc.step} scope={exc.scope} code={exc.code} recovery_required={exc.recovery_required} secretOutput=NONE")
            out = {
                "ok": False,
                "action": "heart-round-run",
                "hj_id": hj_id,
                "hround_id": hround_id,
                "hr_id": hr_id,
                "hs_id": hs_id,
                "failed_step": exc.step,
                "error_scope": exc.scope,
                "error_code": exc.code,
                "message": exc.message,
                "recovery_required": exc.recovery_required,
                "login_counts": {"receiver": 1 if receiver_login else 0, "sender": 1 if sender_login else 0},
                "steps": steps,
                "database_schema_changed": False,
                "elapsed_ms": elapsed_ms,
                "secretOutput": "NONE",
            }
            if verbose_actions and exc.result is not None:
                out["failed_result"] = exc.result
            return out
