from __future__ import annotations

import os
import time
from typing import Any, Callable

from mwoif.application.heart_round_runner import RoundFailure, _emit, _require_step, _step_summary
from mwoif.application.login_session_manager import LoginSessionManager, LoginSessionResult
from mwoif.core.config import AppConfig
from mwoif.domain.errors import AuthError, ConfigError, CredentialError, MwoifHeartError, SessionError
from mwoif.friend.service import handle_friend_request, remove_friend, send_friend_request
from mwoif.heart.mailbox import mailbox_read as mailbox_read_call
from mwoif.heart.receive import heart_receive as heart_receive_call
from mwoif.heart.send import heart_send as heart_send_call
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault

Event = Callable[[str], None]


def _cfg_int(config: AppConfig, name: str, default: int) -> int:
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


def _err_code(exc: BaseException) -> str:
    return str(getattr(exc, "code", None) or type(exc).__name__)


def _err_stage(exc: BaseException, default: str) -> str:
    return str(getattr(exc, "stage", None) or default)


def _is_sender_account_fatal(exc: BaseException) -> bool:
    return isinstance(exc, (CredentialError, AuthError, SessionError))


class ProductionJobRunner:
    """Phase 5.1 local production foundation runner.

    This is still deliberately sequential, but it uses production semantics:
    random sender selection, active sender lease, sender+receiver cooldown,
    replacement on sender/account errors, and receiver login once per job.
    Phase 5.2 can put 10 sender workers on top of these same tables.
    """

    def __init__(self, config: AppConfig, repo: HeartRepository, vault: CredentialVault) -> None:
        self.config = config
        self.repo = repo
        self.vault = vault
        self.login_manager = LoginSessionManager(config, repo, vault)
        self.runtime_cfg = self.login_manager.runtime_cfg

    def _timeout(self, key: str, default: float) -> float:
        return float(self.runtime_cfg.workflow.get(key) or default)

    def _job(self, hj_id: int) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="PROD_JOB_RUN")
        return job

    def _run_one_round_with_logins(
        self,
        *,
        hj_id: int,
        hr_id: int,
        hs_id: int,
        sequence_no: int,
        receiver_login: LoginSessionResult,
        sender_login: LoginSessionResult,
        source_type: int,
        mailbox_attempts: int,
        mailbox_delay_seconds: float,
        event_cb: Event | None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        steps: list[dict[str, Any]] = []
        hround_id: int | None = None
        sent_confirmed = False
        selected_seq: int | None = None
        try:
            round_row = self.repo.create_round(hj_id=hj_id, hs_id=hs_id, hr_id=hr_id, sequence_no=sequence_no)
            hround_id = int(round_row["hround_id"])
            self.repo.mark_sender_attempt_running(hj_id=hj_id, hs_id=hs_id, hround_id=hround_id, sequence_no=sequence_no)
            self.repo.update_round_step(hround_id=hround_id, current_step="ADD", status="running")
            self.repo.log_event(event_type="P51_ROUND_START", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="START", success=True, detail="phase5.1 production round started")

            add_result = send_friend_request(
                cfg=self.runtime_cfg,
                slot="S",
                auth=sender_login.auth,
                target_mid=receiver_login.auth.mid,
                source_type=source_type,
                timeout=self._timeout("grpc_timeout_seconds", 12),
                live=True,
            )
            _require_step("ADD", add_result, scope="sender")
            steps.append(_step_summary("ADD", add_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="ACCEPT", last_confirmed_step="ADD")
            _emit(event_cb, f"P51 STEP ADD OK hs_id={hs_id} elapsed_ms={add_result.get('elapsed_ms')} secretOutput=NONE")

            accept_result = handle_friend_request(
                cfg=self.runtime_cfg,
                slot="R",
                auth=receiver_login.auth,
                target_mid=sender_login.auth.mid,
                accept=True,
                timeout=self._timeout("grpc_timeout_seconds", 12),
                live=True,
            )
            _require_step("ACCEPT", accept_result, scope="receiver")
            steps.append(_step_summary("ACCEPT", accept_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="SEND", last_confirmed_step="ACCEPT")
            _emit(event_cb, f"P51 STEP ACCEPT OK hs_id={hs_id} elapsed_ms={accept_result.get('elapsed_ms')} secretOutput=NONE")

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
            _require_step("SEND", send_result, scope="sender")
            sent_confirmed = True
            steps.append(_step_summary("SEND", send_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="MAILBOX", last_confirmed_step="SEND", send_outcome="confirmed", cancel_locked=True)
            _emit(event_cb, f"P51 STEP SEND OK hs_id={hs_id} response_code={send_result.get('response_code')} secretOutput=NONE")

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
                selected_seq = mailbox_result.get("suggested_life_mail_seq") if bool(mailbox_result.get("ok")) else None
                steps.append(_step_summary("MAILBOX", mailbox_result, extra={"attempt": attempt}))
                if selected_seq:
                    break
                if attempt < max(1, mailbox_attempts):
                    _emit(event_cb, f"P51 STEP MAILBOX WAIT hs_id={hs_id} attempt={attempt}/{mailbox_attempts} secretOutput=NONE")
                    time.sleep(max(0.0, mailbox_delay_seconds))
            if not mailbox_result or not bool(mailbox_result.get("ok")):
                _require_step("MAILBOX", mailbox_result or {"ok": False, "error": "MAILBOX_NO_RESULT"}, scope="receiver", recovery_required=sent_confirmed)
            if not selected_seq:
                raise RoundFailure(step="MAILBOX", scope="receiver", code="LIFE_MAIL_SEQ_NOT_FOUND", message="mailbox read passed but no life mail seq from requested sender was found", result=mailbox_result, recovery_required=sent_confirmed)
            self.repo.update_round_step(hround_id=hround_id, current_step="RECEIVE", last_confirmed_step="MAILBOX")
            _emit(event_cb, f"P51 STEP MAILBOX OK hs_id={hs_id} seq=present secretOutput=NONE")

            receive_result = heart_receive_call(
                cfg=self.runtime_cfg,
                actor_slot="R",
                actor_session=receiver_login.session,
                actor_auth=receiver_login.auth,
                life_mail_box_seqs=[int(selected_seq)],
                live=True,
                timeout=self._timeout("ds_timeout_seconds", 20),
            )
            _require_step("RECEIVE", receive_result, scope="receiver", recovery_required=sent_confirmed)
            steps.append(_step_summary("RECEIVE", receive_result))
            self.repo.update_round_step(hround_id=hround_id, current_step="REMOVE", last_confirmed_step="RECEIVE", send_outcome="confirmed")
            _emit(event_cb, f"P51 STEP RECEIVE OK hs_id={hs_id} response_code={receive_result.get('response_code')} secretOutput=NONE")

            remove_result = remove_friend(
                cfg=self.runtime_cfg,
                slot="S",
                auth=sender_login.auth,
                target_mids=[receiver_login.auth.mid],
                timeout=self._timeout("grpc_timeout_seconds", 12),
                live=True,
            )
            _require_step("REMOVE", remove_result, scope="sender", recovery_required=True)
            steps.append(_step_summary("REMOVE", remove_result))

            self.repo.mark_round_passed(hround_id=hround_id)
            self.repo.mark_one_round_success(hj_id=hj_id, hs_id=hs_id, hr_id=hr_id)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            self.repo.log_event(event_type="P51_ROUND_PASS", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step="PASS", success=True, detail="phase5.1 production round passed")
            return {
                "ok": True,
                "hround_id": hround_id,
                "hs_id": hs_id,
                "hr_id": hr_id,
                "sequence_no": sequence_no,
                "selected_life_mail_seq_present": bool(selected_seq),
                "steps": steps,
                "elapsed_ms": elapsed_ms,
                "secretOutput": "NONE",
            }
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
                self.repo.log_event(event_type="P51_ROUND_FAILED", hj_id=hj_id, hround_id=hround_id, hs_id=hs_id, hr_id=hr_id, step=exc.step, success=False, error_scope=exc.scope, error_code=exc.code, detail=exc.message)
            return {
                "ok": False,
                "hround_id": hround_id,
                "hs_id": hs_id,
                "hr_id": hr_id,
                "sequence_no": sequence_no,
                "failed_step": exc.step,
                "error_scope": exc.scope,
                "error_code": exc.code,
                "message": exc.message,
                "recovery_required": exc.recovery_required,
                "steps": steps,
                "elapsed_ms": elapsed_ms,
                "secretOutput": "NONE",
            }

    def run_job(
        self,
        *,
        hj_id: int,
        live: bool,
        template_hs_id: int | None = None,
        template_hr_id: int | None = None,
        source_type: int = 2,
        max_rounds: int | None = None,
        lease_seconds: int | None = None,
        cooldown_seconds: int | None = None,
        mailbox_attempts: int | None = None,
        mailbox_delay_seconds: float | None = None,
        event_cb: Event | None = None,
    ) -> dict[str, Any]:
        job = self._job(hj_id)
        hr_id = int(job["hr_id"])
        requested = int(job["requested_hearts"] or 0)
        completed = int(job["completed_hearts"] or 0)
        remaining = max(0, requested - completed)
        lease_seconds = int(lease_seconds or _cfg_int(self.config, "MWOIF_HEART_SENDER_LEASE_SECONDS", 900))
        cooldown_seconds = int(cooldown_seconds or _cfg_int(self.config, "MWOIF_HEART_PAIR_COOLDOWN_SECONDS", 3600))
        mailbox_attempts = int(mailbox_attempts or _cfg_int(self.config, "MWOIF_HEART_ROUND_MAILBOX_ATTEMPTS", 5))
        mailbox_delay_seconds = float(mailbox_delay_seconds if mailbox_delay_seconds is not None else _cfg_float(self.config, "MWOIF_HEART_ROUND_MAILBOX_DELAY_SECONDS", 2.0))
        template_hr_id = int(template_hr_id or _cfg_int(self.config, "MWOIF_HEART_ROUND_TEMPLATE_RECEIVER_HR_ID", hr_id))
        template_hs_id = int(template_hs_id or _cfg_int(self.config, "MWOIF_HEART_ROUND_TEMPLATE_SENDER_HS_ID", 0) or 0)
        max_rounds = int(max_rounds or remaining or 1)
        worker_id = f"local-p51-{os.getpid()}"
        eligible = self.repo.eligible_sender_counts(hj_id=hj_id, hr_id=hr_id)

        if not live:
            return {
                "ok": True,
                "read_only": True,
                "action": "heart-job-run-plan",
                "hj_id": hj_id,
                "hr_id": hr_id,
                "requested_hearts": requested,
                "completed_hearts": completed,
                "remaining_hearts": remaining,
                "eligible": eligible,
                "template_source": {"receiver_hr_id": template_hr_id, "sender_hs_id": template_hs_id or None},
                "policy": {
                    "sender_selection": "random-without-replacement-per-job",
                    "replacement_on_error": True,
                    "sender_receiver_cooldown_seconds": cooldown_seconds,
                    "receiver_login": "once-per-job",
                    "sender_login": "once-per-round",
                    "workers": 1,
                },
                "write_guard": "ADD_--live_TO_RUN_REAL_JOB",
                "secretOutput": "NONE",
            }

        if remaining <= 0:
            return {"ok": True, "action": "heart-job-run", "hj_id": hj_id, "status": "already-completed", "secretOutput": "NONE"}
        if template_hs_id <= 0:
            raise ConfigError("template sender is required: pass --template-hs-id or set MWOIF_HEART_ROUND_TEMPLATE_SENDER_HS_ID", stage="PROD_JOB_RUN")

        started = time.monotonic()
        rounds: list[dict[str, Any]] = []
        _emit(event_cb, f"P51 JOB START hj_id={hj_id} hr_id={hr_id} remaining={remaining} workers=1 browser=NO secretOutput=NONE")
        self.repo.mark_job_running(hj_id=hj_id)
        _emit(event_cb, "P51 LOGIN RECEIVER once provider=direct-http-exact-template secretOutput=NONE")
        receiver_login = self.login_manager.login_receiver_for_job_http_template(hj_id=hj_id, template_hr_id=template_hr_id, event_cb=event_cb)
        receiver_login_count = 1
        sender_login_count = 0
        passed = 0
        failed = 0
        account_disabled = 0
        no_eligible = False
        paused_for_recovery = False

        for _ in range(max_rounds):
            job_now = self._job(hj_id)
            if int(job_now["completed_hearts"] or 0) >= int(job_now["requested_hearts"] or 0):
                break
            lease = self.repo.lease_random_sender(hj_id=hj_id, hr_id=hr_id, worker_id=worker_id, lease_seconds=lease_seconds)
            if not lease:
                no_eligible = True
                self.repo.mark_job_paused(hj_id=hj_id, error_scope="system", error_code="NO_ELIGIBLE_SENDER", message="No eligible sender remains for this job/receiver at the current time")
                _emit(event_cb, "P51 JOB PAUSED no_eligible_sender secretOutput=NONE")
                break
            hs_id = int(lease["hs_id"])
            lease_token = str(lease["lease_token"])
            sequence_no = self.repo.next_round_sequence(hj_id=hj_id)
            hround_id: int | None = None
            try:
                _emit(event_cb, f"P51 SELECT SENDER hs_id={hs_id} seq={sequence_no} random=YES lease=active secretOutput=NONE")
                sender_login = self.login_manager.login_sender_http_template(hs_id=hs_id, template_hs_id=template_hs_id, event_cb=event_cb)
                sender_login_count += 1
                result = self._run_one_round_with_logins(
                    hj_id=hj_id,
                    hr_id=hr_id,
                    hs_id=hs_id,
                    sequence_no=sequence_no,
                    receiver_login=receiver_login,
                    sender_login=sender_login,
                    source_type=source_type,
                    mailbox_attempts=mailbox_attempts,
                    mailbox_delay_seconds=mailbox_delay_seconds,
                    event_cb=event_cb,
                )
                hround_id = int(result.get("hround_id") or 0) or None
                if hround_id:
                    self.repo.attach_lease_round(hs_id=hs_id, lease_token=lease_token, hround_id=hround_id)
                if result.get("ok"):
                    self.repo.mark_sender_receiver_cooldown(hs_id=hs_id, hr_id=hr_id, cooldown_seconds=cooldown_seconds)
                    self.repo.mark_sender_attempt_passed(hj_id=hj_id, hs_id=hs_id, hround_id=hround_id, sequence_no=sequence_no)
                    passed += 1
                    _emit(event_cb, f"P51 ROUND PASS hs_id={hs_id} cooldown={cooldown_seconds}s secretOutput=NONE")
                else:
                    failed += 1
                    self.repo.mark_sender_attempt_failed(
                        hj_id=hj_id,
                        hs_id=hs_id,
                        hround_id=hround_id,
                        sequence_no=sequence_no,
                        error_scope=str(result.get("error_scope") or "system"),
                        error_stage=str(result.get("failed_step") or "ROUND"),
                        error_code=str(result.get("error_code") or "ROUND_FAILED"),
                        error_message=str(result.get("message") or "round failed"),
                    )
                    if result.get("recovery_required"):
                        paused_for_recovery = True
                        self.repo.mark_job_paused(hj_id=hj_id, error_scope=str(result.get("error_scope") or "system"), error_code=str(result.get("error_code") or "RECOVERY_REQUIRED"), message=str(result.get("message") or "round requires recovery"))
                        rounds.append(result)
                        break
                rounds.append(result)
            except MwoifHeartError as exc:
                failed += 1
                code = _err_code(exc)
                stage = _err_stage(exc, "LOGIN_SENDER")
                msg = str(exc)
                self.repo.mark_one_round_failed(hj_id=hj_id, hs_id=hs_id, hr_id=hr_id, error_scope="sender" if _is_sender_account_fatal(exc) else "system", error_code=code, message=msg)
                self.repo.mark_sender_attempt_failed(hj_id=hj_id, hs_id=hs_id, hround_id=hround_id, sequence_no=sequence_no, error_scope="sender" if _is_sender_account_fatal(exc) else "system", error_stage=stage, error_code=code, error_message=msg)
                if _is_sender_account_fatal(exc):
                    self.repo.disable_sender_needs_attention(hs_id=hs_id, stage=stage, code=code, message=msg)
                    account_disabled += 1
                    _emit(event_cb, f"P51 SENDER DISABLED hs_id={hs_id} stage={stage} code={code} replacement=YES secretOutput=NONE")
                else:
                    self.repo.mark_job_paused(hj_id=hj_id, error_scope="system", error_code=code, message=msg)
                    paused_for_recovery = True
                    rounds.append({"ok": False, "hs_id": hs_id, "error_scope": "system", "error_code": code, "message": msg, "secretOutput": "NONE"})
                    break
                rounds.append({"ok": False, "hs_id": hs_id, "error_scope": "sender", "error_code": code, "message": "sender moved to needs attention", "replacement": True, "secretOutput": "NONE"})
            finally:
                self.repo.release_sender_lease(hs_id=hs_id, lease_token=lease_token)

        final_job = self._job(hj_id)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        ok = str(final_job.get("status")) == "completed" or int(final_job.get("completed_hearts") or 0) >= int(final_job.get("requested_hearts") or 0)
        return {
            "ok": ok and not paused_for_recovery and not no_eligible,
            "action": "heart-job-run",
            "hj_id": hj_id,
            "hr_id": hr_id,
            "status": final_job.get("status"),
            "requested_hearts": int(final_job.get("requested_hearts") or 0),
            "completed_hearts": int(final_job.get("completed_hearts") or 0),
            "remaining_hearts": max(0, int(final_job.get("requested_hearts") or 0) - int(final_job.get("completed_hearts") or 0)),
            "passed_this_run": passed,
            "failed_this_run": failed,
            "sender_account_disabled_this_run": account_disabled,
            "login_counts": {"receiver": receiver_login_count, "sender": sender_login_count},
            "login_provider": "direct-http-exact-template",
            "browser_during_replay": False,
            "selection": "random-without-replacement",
            "replacement_on_error": True,
            "cooldown_seconds": cooldown_seconds,
            "workers": 1,
            "no_eligible_sender": no_eligible,
            "paused_for_recovery": paused_for_recovery,
            "rounds": rounds,
            "elapsed_ms": elapsed_ms,
            "secretOutput": "NONE",
        }
