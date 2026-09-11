from __future__ import annotations

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from mwoif.application.login_session_manager import LoginSessionManager, LoginSessionResult
from mwoif.application.realtime import emit_rt, emit_user
from mwoif.application.warm_sender_pool import WarmSenderPool
from mwoif.core.config import AppConfig
from mwoif.domain.errors import AuthError, ConfigError, CredentialError, SessionError
from mwoif.friend.service import handle_friend_request, remove_friend, send_friend_request
from mwoif.heart.mailbox import mailbox_read as mailbox_read_call
from mwoif.heart.receive import heart_receive as heart_receive_call
from mwoif.heart.send import heart_send as heart_send_call
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault

Event = Callable[[str], None]


@dataclass(slots=True)
class SenderWork:
    hs_id: int
    lease_token: str
    worker_id: str
    email_mask: str = ""
    login: LoginSessionResult | None = None
    hround_id: int | None = None
    sequence_no: int | None = None
    stage: str = "LEASED"
    send_confirmed: bool = False
    mail_seq: int | None = None
    error_scope: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def member_seq(self) -> int | None:
        if not self.login or not self.login.session.established:
            return None
        return int(self.login.session.member_seq)

    @property
    def mid(self) -> str:
        return str(self.login.auth.mid) if self.login else ""


def _cfg_int(config: AppConfig, key: str, default: int) -> int:
    raw = config.extra.get(key)
    try:
        return int(raw) if raw not in (None, "") else int(default)
    except Exception:
        return int(default)


def _cfg_float(config: AppConfig, key: str, default: float) -> float:
    raw = config.extra.get(key)
    try:
        return float(raw) if raw not in (None, "") else float(default)
    except Exception:
        return float(default)


def _fatal_sender_error(exc: BaseException) -> bool:
    return isinstance(exc, (CredentialError, AuthError, SessionError))


def _err_code(exc: BaseException) -> str:
    return str(getattr(exc, "code", None) or type(exc).__name__)


def _err_stage(exc: BaseException, default: str) -> str:
    return str(getattr(exc, "stage", None) or default)


def _network_ok(result: dict[str, Any] | None) -> bool:
    return bool(isinstance(result, dict) and result.get("ok"))


class FastBatchJobRunner:
    """Phase 5.4.6 Local no-disable + retryable pre-send engine.

    Design goals:
      * Receiver logs in once and stays alive for the whole job.
      * Sender accounts are leased randomly and log in concurrently.
      * Add / accept / send are bounded parallel network stages.
      * Mailbox is read once per batch (with short retries), not once per sender.
      * acceptLifeMail4 receives many lifeMailBoxSeq values in one request.
      * Receiver removes successful Sender friends in one bulk RemoveFriend call.
      * Pair cooldown and DB checkpoints continue to use the Phase 5.1 tables.
      * Local never auto-disables Sender accounts; ADD/ACCEPT misses are retryable
        in the same job. Future Web quarantine policy is intentionally separate.
      * Structured @RT events drive the Local UI in real time without DB polling.
    """

    def __init__(self, config: AppConfig, repo: HeartRepository, vault: CredentialVault, *, warm_pool: WarmSenderPool | None = None) -> None:
        self.config = config
        self.repo = repo
        self.vault = vault
        self.login_manager = LoginSessionManager(config, repo, vault)
        self.runtime_cfg = self.login_manager.runtime_cfg
        self.warm_pool = warm_pool
        self._stage_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=50, thread_name_prefix="p54-net")
        self._login_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=50, thread_name_prefix="p54-login")
        self._warm_hits = 0
        self._cold_logins = 0

    def close(self, *, wait: bool = True) -> None:
        stage_pool, self._stage_executor = self._stage_executor, None
        login_pool, self._login_executor = self._login_executor, None
        for pool in (stage_pool, login_pool):
            if pool is None:
                continue
            try:
                pool.shutdown(wait=bool(wait), cancel_futures=not bool(wait))
            except TypeError:
                pool.shutdown(wait=bool(wait))

    def _timeout(self, name: str, default: float) -> float:
        try:
            return float(self.runtime_cfg.workflow.get(name) or default)
        except Exception:
            return default

    def _job(self, hj_id: int) -> dict[str, Any]:
        job = self.repo.get_job(hj_id)
        if not job:
            raise ConfigError(f"heart job not found: hj_id={hj_id}", stage="P53_FAST_JOB")
        return job

    def _stage_parallel(
        self,
        works: list[SenderWork],
        *,
        stage: str,
        max_workers: int,
        fn: Callable[[SenderWork], dict[str, Any]],
        event_cb: Event | None,
    ) -> tuple[list[SenderWork], list[SenderWork]]:
        if not works:
            return [], []
        ok: list[SenderWork] = []
        failed: list[SenderWork] = []
        total = len(works)
        emit_rt(event_cb, "BATCH_STAGE", stage=stage, done=0, total=total)
        limit = max(1, min(int(max_workers), total))
        sem = threading.Semaphore(limit)

        def guarded(work: SenderWork) -> dict[str, Any]:
            with sem:
                return fn(work)

        owned_pool = self._stage_executor is None
        pool = self._stage_executor or ThreadPoolExecutor(max_workers=limit, thread_name_prefix=f"p54-{stage.lower()}")
        try:
            future_map = {pool.submit(guarded, work): work for work in works}
            done = 0
            for future in as_completed(future_map):
                work = future_map[future]
                try:
                    result = future.result()
                    work.steps[stage] = result
                    if _network_ok(result):
                        work.stage = stage
                        work.error_scope = None
                        work.error_code = None
                        work.error_message = None
                        ok.append(work)
                        status = "ok"
                    else:
                        work.error_scope = "sender" if stage in {"ADD", "SEND"} else "receiver"
                        work.error_code = str(result.get("grpc_code") or result.get("response_code") or result.get("error") or f"{stage}_FAILED")
                        work.error_message = str(result.get("grpc_details") or result.get("response_message") or result.get("message") or f"{stage} failed")
                        failed.append(work)
                        status = "error"
                except Exception as exc:
                    work.error_scope = "system"
                    work.error_code = _err_code(exc)
                    work.error_message = str(exc)
                    failed.append(work)
                    status = "error"
                done += 1
                emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage=stage, status=status, done=done, total=total)
                emit_rt(event_cb, "BATCH_STAGE", stage=stage, done=done, total=total)
        finally:
            if owned_pool:
                pool.shutdown(wait=True)
        return ok, failed

    def _stage_parallel_adaptive(
        self,
        works: list[SenderWork],
        *,
        stage: str,
        initial_workers: int,
        retry_workers: tuple[int, ...],
        fn: Callable[[SenderWork], dict[str, Any]],
        event_cb: Event | None,
    ) -> tuple[list[SenderWork], list[SenderWork], list[dict[str, Any]]]:
        """Run a safe pre-send stage with bounded automatic backoff.

        ADD and ACCEPT are safe to retry before any heart SEND has happened.
        The first pass keeps the fast path.  If the backend rejects a burst,
        remaining failures are retried with progressively lower concurrency
        instead of burning every sender as an attempted account.
        """
        if not works:
            return [], [], []
        pending = list(works)
        passed: list[SenderWork] = []
        passes: list[dict[str, Any]] = []
        worker_plan = [max(1, int(initial_workers))] + [max(1, int(x)) for x in retry_workers]
        for pass_no, workers in enumerate(worker_plan, 1):
            if not pending:
                break
            if pass_no > 1:
                emit_user(event_cb, f"{stage}: เหลือ {len(pending)} รายการ • ลดพร้อมกันเหลือ {workers} แล้วลองซ้ำ")
                time.sleep(0.20)
            ok, failed = self._stage_parallel(
                pending,
                stage=stage,
                max_workers=workers,
                event_cb=event_cb,
                fn=fn,
            )
            passed.extend(ok)
            passes.append({
                "pass": pass_no,
                "workers": workers,
                "input": len(pending),
                "passed": len(ok),
                "failed": len(failed),
            })
            pending = failed
        emit_user(event_cb, f"{stage}: ผ่าน {len(passed)}/{len(works)}")
        return passed, pending, passes


    def _send_adaptive_verified(
        self,
        works: list[SenderWork],
        *,
        receiver_login: LoginSessionResult,
        initial_workers: int,
        retry_workers: tuple[int, ...],
        settle_seconds: float,
        verify_delay_seconds: float,
        event_cb: Event | None,
    ) -> tuple[list[SenderWork], list[SenderWork], list[SenderWork], list[dict[str, Any]]]:
        """Send hearts with conservative, duplicate-safe recovery.

        A SEND exception is an unknown commit outcome and is never blindly
        retried.  Before retrying explicit failures, the receiver mailbox is
        checked; any matching life mail proves the SEND committed.  Only
        explicit failures with no matching mail are retried at lower
        concurrency.  This keeps the fast path while avoiding duplicate hearts.
        """
        if not works:
            return [], [], [], []

        if settle_seconds > 0:
            emit_user(event_cb, f"SEND: รอ Friend state {settle_seconds:.1f}s ก่อนเริ่มส่ง")
            time.sleep(settle_seconds)

        confirmed: list[SenderWork] = []
        pending = list(works)
        unknown: list[SenderWork] = []
        passes: list[dict[str, Any]] = []
        worker_plan = [max(1, int(initial_workers))] + [max(1, int(x)) for x in retry_workers]

        for pass_no, workers in enumerate(worker_plan, 1):
            if not pending:
                break
            if pass_no > 1:
                emit_user(event_cb, f"SEND: เหลือ {len(pending)} รายการ • ตรวจ Mailbox แล้วลดพร้อมกันเหลือ {workers}")
                time.sleep(max(0.10, verify_delay_seconds))

            ok, failed = self._stage_parallel(
                pending,
                stage="SEND",
                max_workers=workers,
                event_cb=event_cb,
                fn=lambda w: heart_send_call(
                    cfg=self.runtime_cfg,
                    actor_slot="S",
                    target_slot="R",
                    actor_session=w.login.session,
                    target_session=receiver_login.session,
                    actor_auth=w.login.auth,
                    live=True,
                    timeout=self._timeout("ds_timeout_seconds", 20),
                ),
            )
            for w in ok:
                w.send_confirmed = True
                w.error_scope = None
                w.error_code = None
                w.error_message = None
            confirmed.extend(ok)

            explicit = [w for w in failed if str(w.error_scope or "") != "system"]
            pass_unknown = [w for w in failed if str(w.error_scope or "") == "system"]

            # Give the mailbox a short propagation window, then verify every
            # failed/unknown request before deciding whether another SEND is safe.
            verify_input = explicit + pass_unknown
            verified: list[SenderWork] = []
            still_missing: list[SenderWork] = list(verify_input)
            verify_summary: list[dict[str, Any]] = []
            if verify_input:
                time.sleep(max(0.05, verify_delay_seconds))
                verified, still_missing, verify_summary = self._mailbox_collect(
                    receiver_login=receiver_login,
                    works=verify_input,
                    attempts=2,
                    delay_seconds=max(0.10, verify_delay_seconds),
                    event_cb=event_cb,
                )
                for w in verified:
                    w.send_confirmed = True
                    w.error_scope = None
                    w.error_code = None
                    w.error_message = None
                confirmed.extend(verified)

            unknown_missing = [w for w in still_missing if w in pass_unknown]
            explicit_missing = [w for w in still_missing if w in explicit]
            unknown.extend(unknown_missing)

            passes.append({
                "pass": pass_no,
                "workers": workers,
                "input": len(pending),
                "http_confirmed": len(ok),
                "mailbox_confirmed": len(verified),
                "explicit_remaining": len(explicit_missing),
                "unknown_remaining": len(unknown_missing),
                "verify": verify_summary,
            })

            # Never retry an unknown commit outcome.  Only explicit failures
            # that are absent from the mailbox are eligible for a lower-rate retry.
            pending = explicit_missing

        # De-duplicate while preserving order.
        seen: set[int] = set()
        confirmed_unique: list[SenderWork] = []
        for w in confirmed:
            if w.hs_id not in seen:
                seen.add(w.hs_id)
                confirmed_unique.append(w)
        unknown_unique: list[SenderWork] = []
        seen_unknown: set[int] = set()
        for w in unknown:
            if w.hs_id not in seen and w.hs_id not in seen_unknown:
                seen_unknown.add(w.hs_id)
                unknown_unique.append(w)
        explicit_fail = [w for w in pending if w.hs_id not in seen]

        emit_user(
            event_cb,
            f"SEND: ยืนยัน {len(confirmed_unique)}/{len(works)} • explicit fail={len(explicit_fail)} • unknown={len(unknown_unique)}",
        )
        return confirmed_unique, explicit_fail, unknown_unique, passes

    def _turbo_accept_send_pipeline(
        self,
        works: list[SenderWork],
        *,
        receiver_login: LoginSessionResult,
        accept_workers: int,
        send_workers: int,
        accept_attempts: int,
        add_settle_seconds: float,
        friend_settle_seconds: float,
        retry_delay_seconds: float,
        verify_delay_seconds: float,
        event_cb: Event | None,
    ) -> tuple[
        list[SenderWork], list[SenderWork], list[SenderWork], list[SenderWork], list[SenderWork],
        list[dict[str, Any]], list[dict[str, Any]], dict[str, Any],
    ]:
        """Overlap receiver ACCEPT and sender SEND for Turbo batches.

        Each Sender advances independently: once its ACCEPT is confirmed, its
        SEND can start without waiting for all other ACCEPT calls.  This removes
        the old ACCEPT->SEND wall between stages.  SEND failures are still
        duplicate-safe: unknown outcomes are never blindly retried and explicit
        failures are mailbox-verified before recovery retries.
        """
        if not works:
            return [], [], [], [], [], [], [], {
                "pipeline_ms": 0.0, "accept_wall_ms": 0.0, "send_wall_ms": 0.0,
                "accept_workers": 0, "send_workers": 0,
            }

        started = time.monotonic()
        if add_settle_seconds > 0:
            emit_user(event_cb, f"TURBO: รอ Add state {add_settle_seconds:.2f}s แล้วเริ่ม ACCEPT→SEND แบบซ้อนกัน")
            time.sleep(add_settle_seconds)

        accept_limit = max(1, min(int(accept_workers), len(works)))
        send_limit = max(1, min(int(send_workers), len(works)))
        accept_sem = threading.Semaphore(accept_limit)
        send_sem = threading.Semaphore(send_limit)
        state_lock = threading.RLock()

        accepted: list[SenderWork] = []
        accept_failed: list[SenderWork] = []
        send_confirmed: list[SenderWork] = []
        send_explicit_failed: list[SenderWork] = []
        send_unknown: list[SenderWork] = []
        accept_passes: list[dict[str, Any]] = []
        send_passes: list[dict[str, Any]] = []
        accept_finish_times: list[float] = []
        send_finish_times: list[float] = []

        emit_rt(event_cb, "BATCH_STAGE", stage="ACCEPT", done=0, total=len(works))
        emit_rt(event_cb, "BATCH_STAGE", stage="SEND", done=0, total=len(works))

        def pipeline_one(work: SenderWork) -> None:
            last_accept: dict[str, Any] | None = None
            accepted_here = False
            attempts_used = 0
            for attempt in range(1, max(1, int(accept_attempts)) + 1):
                attempts_used = attempt
                if attempt > 1:
                    # Deterministic jitter avoids synchronized retry waves while
                    # keeping the benchmark reproducible.
                    jitter = (int(work.hs_id) % 7) * 0.012
                    time.sleep(max(0.03, retry_delay_seconds * (attempt - 1)) + jitter)
                try:
                    with accept_sem:
                        last_accept = handle_friend_request(
                            cfg=self.runtime_cfg,
                            slot="R",
                            auth=receiver_login.auth,
                            target_mid=work.login.auth.mid,
                            accept=True,
                            timeout=self._timeout("grpc_timeout_seconds", 12),
                            live=True,
                        )
                except Exception as exc:
                    last_accept = {"ok": False, "error": _err_code(exc), "message": str(exc), "exception": True}
                work.steps[f"ACCEPT_{attempt}"] = last_accept
                if _network_ok(last_accept):
                    accepted_here = True
                    work.stage = "ACCEPT"
                    work.error_scope = None
                    work.error_code = None
                    work.error_message = None
                    break

            with state_lock:
                accept_passes.append({
                    "hs_id": work.hs_id,
                    "attempts": attempts_used,
                    "ok": bool(accepted_here),
                })
                accept_finish_times.append(time.monotonic())
                if accepted_here:
                    accepted.append(work)
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="ACCEPT", status="ok")
                else:
                    result = last_accept or {}
                    work.error_scope = "receiver"
                    work.error_code = str(result.get("grpc_code") or result.get("response_code") or result.get("error") or "ACCEPT_FAILED")
                    work.error_message = str(result.get("grpc_details") or result.get("response_message") or result.get("message") or "friend accept failed")
                    accept_failed.append(work)
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="ACCEPT", status="error")
                emit_rt(event_cb, "BATCH_STAGE", stage="ACCEPT", done=len(accepted)+len(accept_failed), total=len(works))

            if not accepted_here:
                return

            # The sender gets its own propagation wait. ACCEPT and SEND for the
            # whole batch therefore overlap instead of creating two long walls.
            if friend_settle_seconds > 0:
                jitter = (int(work.hs_id) % 5) * 0.010
                time.sleep(friend_settle_seconds + jitter)

            try:
                with send_sem:
                    sent = heart_send_call(
                        cfg=self.runtime_cfg,
                        actor_slot="S",
                        target_slot="R",
                        actor_session=work.login.session,
                        target_session=receiver_login.session,
                        actor_auth=work.login.auth,
                        live=True,
                        timeout=self._timeout("ds_timeout_seconds", 20),
                    )
                work.steps["SEND"] = sent
                if _network_ok(sent):
                    work.send_confirmed = True
                    work.stage = "SEND"
                    work.error_scope = None
                    work.error_code = None
                    work.error_message = None
                    with state_lock:
                        send_confirmed.append(work)
                        send_passes.append({"hs_id": work.hs_id, "pass": 1, "ok": True, "source": "http"})
                        emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="ok")
                else:
                    work.error_scope = "sender"
                    work.error_code = str(sent.get("response_code") or sent.get("error") or "SEND_FAILED")
                    work.error_message = str(sent.get("response_message") or sent.get("message") or "heart send failed")
                    with state_lock:
                        send_explicit_failed.append(work)
                        send_passes.append({"hs_id": work.hs_id, "pass": 1, "ok": False, "source": "explicit"})
                        emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="error")
            except Exception as exc:
                work.error_scope = "system"
                work.error_code = _err_code(exc)
                work.error_message = str(exc)
                with state_lock:
                    send_unknown.append(work)
                    send_passes.append({"hs_id": work.hs_id, "pass": 1, "ok": False, "source": "unknown"})
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="pending-recovery")
            finally:
                with state_lock:
                    send_finish_times.append(time.monotonic())
                    emit_rt(event_cb, "BATCH_STAGE", stage="SEND", done=len(send_confirmed)+len(send_explicit_failed)+len(send_unknown), total=len(works))

        pool = self._stage_executor or ThreadPoolExecutor(max_workers=min(50, len(works)), thread_name_prefix="p542-turbo")
        owned_pool = self._stage_executor is None
        try:
            futures = [pool.submit(pipeline_one, work) for work in works]
            for future in as_completed(futures):
                # pipeline_one records the work-level error; only an unexpected
                # implementation exception should escape here.
                try:
                    future.result()
                except Exception as exc:
                    emit_user(event_cb, f"TURBO pipeline exception: {type(exc).__name__}")
        finally:
            if owned_pool:
                pool.shutdown(wait=True)

        # Verify all non-confirmed SEND outcomes together.  Mailbox evidence is
        # authoritative and avoids duplicate sends after ambiguous responses.
        verify_input = list(send_explicit_failed) + list(send_unknown)
        verified: list[SenderWork] = []
        missing: list[SenderWork] = list(verify_input)
        verify_summary: list[dict[str, Any]] = []
        if verify_input:
            time.sleep(max(0.05, verify_delay_seconds))
            verified, missing, verify_summary = self._mailbox_collect(
                receiver_login=receiver_login,
                works=verify_input,
                attempts=2,
                delay_seconds=max(0.08, verify_delay_seconds),
                event_cb=event_cb,
            )
            verified_ids = {w.hs_id for w in verified}
            for w in verified:
                w.send_confirmed = True
                w.error_scope = None
                w.error_code = None
                w.error_message = None
            send_confirmed.extend(verified)
            send_unknown = [w for w in send_unknown if w.hs_id not in verified_ids]
            send_explicit_failed = [w for w in send_explicit_failed if w.hs_id not in verified_ids]

        # Explicit failures that are proven absent from the mailbox are safe to
        # retry. Keep recovery in waves; serial 1 is only the final safety net.
        explicit_missing_ids = {w.hs_id for w in missing if w in send_explicit_failed}
        explicit_missing = [w for w in send_explicit_failed if w.hs_id in explicit_missing_ids]
        recovery_confirmed: list[SenderWork] = []
        recovery_failed: list[SenderWork] = explicit_missing
        recovery_passes: list[dict[str, Any]] = []
        if explicit_missing:
            recovery_confirmed, recovery_failed, recovery_unknown, recovery_passes = self._send_adaptive_verified(
                explicit_missing,
                receiver_login=receiver_login,
                initial_workers=min(max(1, send_limit), 20),
                retry_workers=(min(10, send_limit), min(5, send_limit), 2, 1),
                settle_seconds=0.0,
                verify_delay_seconds=max(0.08, verify_delay_seconds),
                event_cb=event_cb,
            )
            send_confirmed.extend(recovery_confirmed)
            # A recovery retry can become unknown; preserve that safety state.
            send_unknown.extend(recovery_unknown)
            send_passes.extend(recovery_passes)

        # Deduplicate lists after mailbox verification/recovery.
        confirmed_ids: set[int] = set()
        confirmed_unique: list[SenderWork] = []
        for w in send_confirmed:
            if w.hs_id not in confirmed_ids:
                confirmed_ids.add(w.hs_id)
                confirmed_unique.append(w)
        unknown_unique: list[SenderWork] = []
        unknown_ids: set[int] = set()
        for w in send_unknown:
            if w.hs_id not in confirmed_ids and w.hs_id not in unknown_ids:
                unknown_ids.add(w.hs_id)
                unknown_unique.append(w)
        fail_unique = [w for w in recovery_failed if w.hs_id not in confirmed_ids and w.hs_id not in unknown_ids]

        now = time.monotonic()
        accept_wall_ms = round(((max(accept_finish_times) if accept_finish_times else now) - started) * 1000, 1)
        send_wall_ms = round(((max(send_finish_times) if send_finish_times else now) - started) * 1000, 1)
        pipeline_ms = round((time.monotonic() - started) * 1000, 1)
        metrics = {
            "turbo": True,
            "pipeline_ms": pipeline_ms,
            "accept_wall_ms": accept_wall_ms,
            "send_wall_ms": send_wall_ms,
            "accept_workers": accept_limit,
            "send_workers": send_limit,
            "accept_attempts": max(1, int(accept_attempts)),
            "mailbox_verify": verify_summary,
            "accepted": len(accepted),
            "sent_confirmed": len(confirmed_unique),
        }
        emit_user(event_cb, f"TURBO ACCEPT: ผ่าน {len(accepted)}/{len(works)} • SEND ยืนยัน {len(confirmed_unique)}/{len(accepted)} • pipeline {pipeline_ms/1000:.2f}s")
        return accepted, accept_failed, confirmed_unique, fail_unique, unknown_unique, accept_passes, send_passes, metrics

    def _adaptive_wave_accept_send_pipeline(
        self,
        works: list[SenderWork],
        *,
        receiver_login: LoginSessionResult,
        wave_size: int,
        wave_stagger_seconds: float,
        accept_workers: int,
        send_workers: int,
        accept_attempts: int,
        add_settle_seconds: float,
        friend_settle_seconds: float,
        accept_retry_delay_seconds: float,
        verify_delay_seconds: float,
        event_cb: Event | None,
    ) -> tuple[
        list[SenderWork], list[SenderWork], list[SenderWork], list[SenderWork], list[SenderWork],
        list[dict[str, Any]], list[dict[str, Any]], dict[str, Any],
    ]:
        """Phase 5.4.4: bounded rolling waves instead of a burst + rescue barrier.

        The previous Turbo path could overload receiver friend-state propagation:
        many ACCEPT/SEND requests failed together, then a large rescue stage ran
        after the whole batch.  This path keeps the same duplicate-safety rules
        but smooths pressure into small overlapping waves.  ACCEPT retries happen
        per sender while the rest of the batch keeps moving.  SEND gets only one
        mailbox-verified recovery wave; there is no 10->5->2->1 serial tail on
        the critical path.
        """
        if not works:
            return [], [], [], [], [], [], [], {
                "adaptive_wave": True,
                "pipeline_ms": 0.0,
                "initial_ms": 0.0,
                "verify_ms": 0.0,
                "send_retry_ms": 0.0,
                "wave_size": 0,
                "wave_stagger_ms": 0.0,
                "accept_workers": 0,
                "send_workers": 0,
            }

        started = time.monotonic()
        if add_settle_seconds > 0:
            emit_user(
                event_cb,
                f"ADAPTIVE WAVE: รอ Add state {add_settle_seconds:.2f}s • wave {wave_size} • ACCEPT {accept_workers} • SEND {send_workers}",
            )
            time.sleep(add_settle_seconds)

        wave_size = max(5, min(50, int(wave_size)))
        wave_stagger_seconds = max(0.0, min(1.0, float(wave_stagger_seconds)))
        accept_limit = max(1, min(int(accept_workers), len(works)))
        send_limit = max(1, min(int(send_workers), len(works)))
        accept_attempts = max(1, min(4, int(accept_attempts)))
        accept_sem = threading.Semaphore(accept_limit)
        send_sem = threading.Semaphore(send_limit)
        state_lock = threading.RLock()

        accepted: list[SenderWork] = []
        accept_failed: list[SenderWork] = []
        send_confirmed: list[SenderWork] = []
        send_explicit_failed: list[SenderWork] = []
        send_unknown: list[SenderWork] = []
        accept_passes: list[dict[str, Any]] = []
        send_passes: list[dict[str, Any]] = []
        accept_done = 0
        send_done = 0

        emit_rt(event_cb, "BATCH_STAGE", stage="ACCEPT", done=0, total=len(works))
        emit_rt(event_cb, "BATCH_STAGE", stage="SEND", done=0, total=len(works))

        def pipeline_one(work: SenderWork, wave_no: int) -> None:
            nonlocal accept_done, send_done
            last_accept: dict[str, Any] | None = None
            accepted_here = False
            attempts_used = 0
            for attempt in range(1, accept_attempts + 1):
                attempts_used = attempt
                if attempt > 1:
                    # Rolling retry.  The sender waits locally; other senders and
                    # later waves keep progressing instead of waiting at a wall.
                    jitter = (int(work.hs_id) % 11) * 0.009
                    delay = max(0.08, accept_retry_delay_seconds * (1.0 + 0.55 * (attempt - 2))) + jitter
                    time.sleep(delay)
                try:
                    with accept_sem:
                        last_accept = handle_friend_request(
                            cfg=self.runtime_cfg,
                            slot="R",
                            auth=receiver_login.auth,
                            target_mid=work.login.auth.mid,
                            accept=True,
                            timeout=self._timeout("grpc_timeout_seconds", 12),
                            live=True,
                        )
                except Exception as exc:
                    last_accept = {"ok": False, "error": _err_code(exc), "message": str(exc), "exception": True}
                work.steps[f"ACCEPT_{attempt}"] = last_accept
                if _network_ok(last_accept):
                    accepted_here = True
                    work.stage = "ACCEPT"
                    work.error_scope = None
                    work.error_code = None
                    work.error_message = None
                    break

            with state_lock:
                accept_done += 1
                accept_passes.append({
                    "hs_id": work.hs_id,
                    "wave": wave_no,
                    "attempts": attempts_used,
                    "ok": bool(accepted_here),
                })
                if accepted_here:
                    accepted.append(work)
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="ACCEPT", status="ok")
                else:
                    result = last_accept or {}
                    work.error_scope = "receiver"
                    work.error_code = str(result.get("grpc_code") or result.get("response_code") or result.get("error") or "ACCEPT_FAILED")
                    work.error_message = str(result.get("grpc_details") or result.get("response_message") or result.get("message") or "friend accept failed")
                    accept_failed.append(work)
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="ACCEPT", status="error")
                emit_rt(event_cb, "BATCH_STAGE", stage="ACCEPT", done=accept_done, total=len(works))

            if not accepted_here:
                return

            # Small per-sender propagation wait.  Because each sender reaches
            # this point at a different time, SEND traffic is naturally spread.
            if friend_settle_seconds > 0:
                jitter = (int(work.hs_id) % 7) * 0.008
                time.sleep(friend_settle_seconds + jitter)

            try:
                with send_sem:
                    sent = heart_send_call(
                        cfg=self.runtime_cfg,
                        actor_slot="S",
                        target_slot="R",
                        actor_session=work.login.session,
                        target_session=receiver_login.session,
                        actor_auth=work.login.auth,
                        live=True,
                        timeout=self._timeout("ds_timeout_seconds", 20),
                    )
                work.steps["SEND"] = sent
                if _network_ok(sent):
                    work.send_confirmed = True
                    work.stage = "SEND"
                    work.error_scope = None
                    work.error_code = None
                    work.error_message = None
                    with state_lock:
                        send_confirmed.append(work)
                        send_passes.append({"hs_id": work.hs_id, "wave": wave_no, "pass": 1, "ok": True, "source": "http"})
                        emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="ok")
                else:
                    work.error_scope = "sender"
                    work.error_code = str(sent.get("response_code") or sent.get("error") or "SEND_FAILED")
                    work.error_message = str(sent.get("response_message") or sent.get("message") or "heart send failed")
                    with state_lock:
                        send_explicit_failed.append(work)
                        send_passes.append({"hs_id": work.hs_id, "wave": wave_no, "pass": 1, "ok": False, "source": "explicit"})
                        emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="pending-verify")
            except Exception as exc:
                work.error_scope = "system"
                work.error_code = _err_code(exc)
                work.error_message = str(exc)
                with state_lock:
                    send_unknown.append(work)
                    send_passes.append({"hs_id": work.hs_id, "wave": wave_no, "pass": 1, "ok": False, "source": "unknown"})
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="pending-recovery")
            finally:
                with state_lock:
                    send_done += 1
                    emit_rt(event_cb, "BATCH_STAGE", stage="SEND", done=send_done, total=len(works))

        pool = self._stage_executor or ThreadPoolExecutor(max_workers=min(50, len(works)), thread_name_prefix="p544-wave")
        owned_pool = self._stage_executor is None
        futures = []
        try:
            for wave_no, start in enumerate(range(0, len(works), wave_size), 1):
                wave = works[start:start + wave_size]
                emit_user(event_cb, f"ADAPTIVE WAVE #{wave_no}: ปล่อย {len(wave)} ไอดี")
                for work in wave:
                    futures.append(pool.submit(pipeline_one, work, wave_no))
                if start + wave_size < len(works) and wave_stagger_seconds > 0:
                    time.sleep(wave_stagger_seconds)
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    emit_user(event_cb, f"ADAPTIVE WAVE pipeline exception: {type(exc).__name__}")
        finally:
            if owned_pool:
                pool.shutdown(wait=True)

        initial_ms = round((time.monotonic() - started) * 1000, 1)

        # Phase 5.4.5: ACCEPT catch-up.  An ACCEPT miss here is usually a
        # receiver friend-state propagation miss, not a bad Sender account.
        # Do not burn a fresh Sender for it immediately.  Keep the same already
        # added Sender in this batch, wait for receiver state to settle, then
        # retry ACCEPT in two bounded waves.  As soon as a retry passes, SEND
        # continues immediately through the same duplicate-safe path below.
        catchup_started = time.monotonic()
        catchup_summary: list[dict[str, Any]] = []
        pending_accept = list(accept_failed)
        accept_failed.clear()
        if pending_accept:
            emit_user(event_cb, f"ADAPTIVE ACCEPT CATCH-UP: ค้าง {len(pending_accept)} รายการ • ใช้ Sender เดิม ไม่กินไอดีใหม่")
            # Local completion policy: keep the same Sender alive through several
            # bounded receiver-state catch-up waves before giving the job a new
            # batch.  These are intentionally slower/smaller each pass; the goal
            # is completeness without a 50-worker burst.
            catch_plans = (
                (min(8, accept_limit), 0.55),
                (min(6, accept_limit), 0.65),
                (min(4, accept_limit), 0.75),
                (min(3, accept_limit), 0.85),
                (min(2, accept_limit), 0.95),
            )
            for catch_no, (catch_workers, catch_delay) in enumerate(catch_plans, 1):
                if not pending_accept:
                    break
                time.sleep(max(0.05, catch_delay))
                retry_input = list(pending_accept)
                pending_accept = []
                catch_accept_sem = threading.Semaphore(max(1, catch_workers))
                catch_send_sem = threading.Semaphore(max(1, min(send_limit, 8)))
                pass_lock = threading.RLock()

                def catchup_one(work: SenderWork) -> tuple[SenderWork, bool]:
                    last_accept: dict[str, Any] | None = None
                    try:
                        with catch_accept_sem:
                            last_accept = handle_friend_request(
                                cfg=self.runtime_cfg,
                                slot="R",
                                auth=receiver_login.auth,
                                target_mid=work.login.auth.mid,
                                accept=True,
                                timeout=self._timeout("grpc_timeout_seconds", 12),
                                live=True,
                            )
                    except Exception as exc:
                        last_accept = {"ok": False, "error": _err_code(exc), "message": str(exc), "exception": True}
                    work.steps[f"ACCEPT_CATCHUP_{catch_no}"] = last_accept
                    if not _network_ok(last_accept):
                        return work, False

                    work.stage = "ACCEPT"
                    work.error_scope = None
                    work.error_code = None
                    work.error_message = None
                    with state_lock:
                        accepted.append(work)
                        accept_passes.append({
                            "hs_id": work.hs_id,
                            "wave": "catchup",
                            "pass": catch_no,
                            "attempts": 1,
                            "ok": True,
                        })
                        emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="ACCEPT", status="retry-ok")

                    if friend_settle_seconds > 0:
                        time.sleep(min(0.35, max(0.08, friend_settle_seconds)))

                    try:
                        with catch_send_sem:
                            sent = heart_send_call(
                                cfg=self.runtime_cfg,
                                actor_slot="S",
                                target_slot="R",
                                actor_session=work.login.session,
                                target_session=receiver_login.session,
                                actor_auth=work.login.auth,
                                live=True,
                                timeout=self._timeout("ds_timeout_seconds", 20),
                            )
                        work.steps[f"SEND_CATCHUP_{catch_no}"] = sent
                        if _network_ok(sent):
                            work.send_confirmed = True
                            work.stage = "SEND"
                            work.error_scope = None
                            work.error_code = None
                            work.error_message = None
                            with state_lock:
                                send_confirmed.append(work)
                                send_passes.append({"hs_id": work.hs_id, "wave": "catchup", "pass": catch_no, "ok": True, "source": "http"})
                                emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="ok")
                        else:
                            work.error_scope = "sender"
                            work.error_code = str(sent.get("response_code") or sent.get("error") or "SEND_FAILED")
                            work.error_message = str(sent.get("response_message") or sent.get("message") or "heart send failed")
                            with state_lock:
                                send_explicit_failed.append(work)
                                send_passes.append({"hs_id": work.hs_id, "wave": "catchup", "pass": catch_no, "ok": False, "source": "explicit"})
                                emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="pending-verify")
                    except Exception as exc:
                        work.error_scope = "system"
                        work.error_code = _err_code(exc)
                        work.error_message = str(exc)
                        with state_lock:
                            send_unknown.append(work)
                            send_passes.append({"hs_id": work.hs_id, "wave": "catchup", "pass": catch_no, "ok": False, "source": "unknown"})
                            emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="SEND", status="pending-recovery")
                    return work, True

                pass_pool = self._stage_executor or ThreadPoolExecutor(max_workers=max(1, catch_workers), thread_name_prefix="p545-catchup")
                owned_pass_pool = self._stage_executor is None
                recovered = 0
                try:
                    fmap = {pass_pool.submit(catchup_one, work): work for work in retry_input}
                    for future in as_completed(fmap):
                        work = fmap[future]
                        try:
                            _work, ok_accept = future.result()
                        except Exception as exc:
                            work.error_scope = "system"
                            work.error_code = _err_code(exc)
                            work.error_message = str(exc)
                            ok_accept = False
                        if ok_accept:
                            recovered += 1
                        else:
                            pending_accept.append(work)
                finally:
                    if owned_pass_pool:
                        pass_pool.shutdown(wait=True)

                catchup_summary.append({
                    "pass": catch_no,
                    "workers": catch_workers,
                    "input": len(retry_input),
                    "recovered": recovered,
                    "remaining": len(pending_accept),
                })
                emit_user(event_cb, f"ADAPTIVE ACCEPT CATCH-UP #{catch_no}: กู้ได้ {recovered}/{len(retry_input)} • เหลือ {len(pending_accept)}")

        # Only the entries that still miss ACCEPT after the delayed catch-up are
        # true final ACCEPT failures for this batch.
        accept_failed.extend(pending_accept)
        catchup_ms = round((time.monotonic() - catchup_started) * 1000, 1)

        # One mailbox verification for all ambiguous/failed initial/catch-up SENDs.
        verify_started = time.monotonic()
        verify_input = list(send_explicit_failed) + list(send_unknown)
        verified: list[SenderWork] = []
        missing: list[SenderWork] = list(verify_input)
        verify_summary: list[dict[str, Any]] = []
        if verify_input:
            time.sleep(max(0.05, verify_delay_seconds))
            verified, missing, verify_summary = self._mailbox_collect(
                receiver_login=receiver_login,
                works=verify_input,
                attempts=1,
                delay_seconds=max(0.08, verify_delay_seconds),
                event_cb=event_cb,
            )
            verified_ids = {w.hs_id for w in verified}
            for work in verified:
                work.send_confirmed = True
                work.error_scope = None
                work.error_code = None
                work.error_message = None
            send_confirmed.extend(verified)
            send_explicit_failed = [w for w in send_explicit_failed if w.hs_id not in verified_ids]
            send_unknown = [w for w in send_unknown if w.hs_id not in verified_ids]
        verify_ms = round((time.monotonic() - verify_started) * 1000, 1)

        # Unknown outcomes are never retried.  Explicit failures that are proven
        # absent from the mailbox get one bounded recovery wave only.
        missing_ids = {w.hs_id for w in missing}
        retry_candidates = [w for w in send_explicit_failed if w.hs_id in missing_ids]
        retry_started = time.monotonic()
        retry_ok: list[SenderWork] = []
        retry_failed: list[SenderWork] = []
        retry_unknown: list[SenderWork] = []
        if retry_candidates:
            emit_user(event_cb, f"ADAPTIVE SEND RETRY: {len(retry_candidates)} รายการ • wave เดียว workers={min(8, send_limit)}")
            retry_ok, retry_raw_failed = self._stage_parallel(
                retry_candidates,
                stage="SEND",
                max_workers=min(8, send_limit),
                event_cb=event_cb,
                fn=lambda w: heart_send_call(
                    cfg=self.runtime_cfg,
                    actor_slot="S",
                    target_slot="R",
                    actor_session=w.login.session,
                    target_session=receiver_login.session,
                    actor_auth=w.login.auth,
                    live=True,
                    timeout=self._timeout("ds_timeout_seconds", 20),
                ),
            )
            for work in retry_ok:
                work.send_confirmed = True
                work.error_scope = None
                work.error_code = None
                work.error_message = None
            send_confirmed.extend(retry_ok)

            # Verify the retry failures exactly once before final classification.
            if retry_raw_failed:
                time.sleep(max(0.05, verify_delay_seconds))
                retry_verified, retry_missing, retry_verify = self._mailbox_collect(
                    receiver_login=receiver_login,
                    works=retry_raw_failed,
                    attempts=1,
                    delay_seconds=max(0.08, verify_delay_seconds),
                    event_cb=event_cb,
                )
                verify_summary.extend(retry_verify)
                retry_verified_ids = {w.hs_id for w in retry_verified}
                for work in retry_verified:
                    work.send_confirmed = True
                    work.error_scope = None
                    work.error_code = None
                    work.error_message = None
                send_confirmed.extend(retry_verified)
                for work in retry_missing:
                    if str(work.error_scope or "") == "system":
                        retry_unknown.append(work)
                    else:
                        retry_failed.append(work)
        send_retry_ms = round((time.monotonic() - retry_started) * 1000, 1)

        # Final classification.  Preserve unknown initial outcomes that were not
        # mailbox-confirmed and never retried.
        confirmed_ids: set[int] = set()
        confirmed_unique: list[SenderWork] = []
        for work in send_confirmed:
            if work.hs_id not in confirmed_ids:
                confirmed_ids.add(work.hs_id)
                confirmed_unique.append(work)

        unknown_unique: list[SenderWork] = []
        unknown_ids: set[int] = set()
        for work in list(send_unknown) + list(retry_unknown):
            if work.hs_id not in confirmed_ids and work.hs_id not in unknown_ids:
                unknown_ids.add(work.hs_id)
                unknown_unique.append(work)

        retry_failed_ids = {w.hs_id for w in retry_failed}
        # Explicit failures not selected for retry are already mailbox-missing.
        final_fail_candidates = [w for w in send_explicit_failed if w.hs_id not in confirmed_ids]
        final_fail_candidates.extend([w for w in retry_failed if w.hs_id not in confirmed_ids])
        fail_unique: list[SenderWork] = []
        fail_ids: set[int] = set()
        for work in final_fail_candidates:
            if work.hs_id in unknown_ids or work.hs_id in confirmed_ids or work.hs_id in fail_ids:
                continue
            # A candidate that was retried but succeeded must not be failed.
            if work.hs_id in retry_failed_ids or work in send_explicit_failed:
                fail_ids.add(work.hs_id)
                fail_unique.append(work)

        total_ms = round((time.monotonic() - started) * 1000, 1)
        metrics = {
            "adaptive_wave": True,
            "pipeline_ms": total_ms,
            "initial_ms": initial_ms,
            "verify_ms": verify_ms,
            "send_retry_ms": send_retry_ms,
            "accept_catchup_ms": catchup_ms,
            "accept_catchup_passes": catchup_summary,
            "wave_size": wave_size,
            "wave_stagger_ms": round(wave_stagger_seconds * 1000, 1),
            "accept_workers": accept_limit,
            "send_workers": send_limit,
            "accept_attempts": accept_attempts,
            "mailbox_verify": verify_summary,
            "accepted": len(accepted),
            "sent_confirmed": len(confirmed_unique),
            "accept_failed": len(accept_failed),
            "send_failed": len(fail_unique),
            "send_unknown": len(unknown_unique),
        }
        emit_user(
            event_cb,
            f"ADAPTIVE WAVE จบ: ACCEPT {len(accepted)}/{len(works)} • SEND {len(confirmed_unique)}/{len(accepted)} • CATCH-UP {catchup_ms/1000:.2f}s • {total_ms/1000:.2f}s",
        )
        return accepted, accept_failed, confirmed_unique, fail_unique, unknown_unique, accept_passes, send_passes, metrics

    def _lease_one(self, *, hj_id: int, hr_id: int, worker_no: int, lease_seconds: int) -> SenderWork | None:
        lease = self.repo.lease_random_sender(
            hj_id=hj_id,
            hr_id=hr_id,
            worker_id=f"local-p53-{os.getpid()}-{worker_no:02d}",
            lease_seconds=lease_seconds,
        )
        if not lease:
            return None
        return SenderWork(
            hs_id=int(lease["hs_id"]),
            lease_token=str(lease["lease_token"]),
            worker_id=str(lease.get("worker_id") or f"worker-{worker_no:02d}"),
            email_mask=str(lease.get("email_mask") or ""),
        )

    def _login_fill_batch(
        self,
        *,
        hj_id: int,
        hr_id: int,
        wanted: int,
        template_hs_id: int,
        sender_workers: int,
        lease_seconds: int,
        event_cb: Event | None,
    ) -> tuple[list[SenderWork], int, int, bool]:
        """Fill a batch from warmed sessions first, then cold-login replacements.

        Leasing is batched in MariaDB. A warmed session is never trusted until
        its sender passes the same lease/cooldown/attempt filters as a cold sender.
        """
        ready: list[SenderWork] = []
        failed_count = 0
        disabled_count = 0
        no_eligible = False
        safety_attempts = 0
        max_attempts = max(wanted * 4, wanted + 20)
        batch_worker = f"local-p54-{os.getpid()}"

        while len(ready) < wanted and safety_attempts < max_attempts:
            need = wanted - len(ready)

            # 1) Warm hits: lease only sessions that are already auth+session ready.
            warm_rows: list[dict[str, Any]] = []
            if self.warm_pool is not None:
                attempted_ids = set(self.repo.list_job_attempted_sender_ids(hj_id=hj_id))
                warm_ids = self.warm_pool.available_ids(exclude_ids=attempted_ids)
                if warm_ids:
                    warm_rows = self.repo.lease_sender_ids(
                        hj_id=hj_id, hr_id=hr_id, hs_ids=warm_ids,
                        worker_id=batch_worker + "-warm", lease_seconds=lease_seconds, count=need,
                    )
            for row in warm_rows:
                hs_id = int(row["hs_id"])
                login = self.warm_pool.get(hs_id) if self.warm_pool is not None else None
                if login is None:
                    self.repo.release_sender_lease(hs_id=hs_id, lease_token=str(row.get("lease_token") or ""))
                    continue
                work = SenderWork(
                    hs_id=hs_id, lease_token=str(row["lease_token"]),
                    worker_id=str(row.get("worker_id") or batch_worker),
                    email_mask=str(row.get("email_mask") or login.email_mask or ""), login=login, stage="LOGIN",
                )
                ready.append(work)
                safety_attempts += 1
                self._warm_hits += 1
                emit_rt(event_cb, "SENDER_STAGE", hs_id=hs_id, email_mask=work.email_mask, stage="WARM", status="ok")

            if len(ready) >= wanted:
                break

            # 2) Cold fallback: lease the remaining senders in one transaction.
            need = wanted - len(ready)
            cold_rows = self.repo.lease_random_senders(
                hj_id=hj_id, hr_id=hr_id, count=need,
                worker_id=batch_worker + "-cold", lease_seconds=lease_seconds,
            )
            if not cold_rows:
                no_eligible = True
                break
            safety_attempts += len(cold_rows)
            cold_works = [
                SenderWork(
                    hs_id=int(row["hs_id"]), lease_token=str(row["lease_token"]),
                    worker_id=str(row.get("worker_id") or batch_worker),
                    email_mask=str(row.get("email_mask") or ""),
                )
                for row in cold_rows
            ]

            sem = threading.Semaphore(max(1, min(sender_workers, len(cold_works))))
            def login_sender(work: SenderWork) -> LoginSessionResult:
                with sem:
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="LOGIN", status="running")
                    return self.login_manager.login_sender_http_template(
                        hs_id=work.hs_id, template_hs_id=template_hs_id, event_cb=event_cb,
                    )

            if self._login_executor is None:
                self._login_executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="p54-login")
            futures = {self._login_executor.submit(login_sender, w): w for w in cold_works}
            for future in as_completed(futures):
                work = futures[future]
                try:
                    work.login = future.result()
                    work.stage = "LOGIN"
                    ready.append(work)
                    self._cold_logins += 1
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="LOGIN", status="ok")
                except Exception as exc:
                    failed_count += 1
                    code = _err_code(exc)
                    stage = _err_stage(exc, "LOGIN_SENDER")
                    fatal = _fatal_sender_error(exc)
                    self.repo.mark_sender_attempt_failed(
                        hj_id=hj_id, hs_id=work.hs_id, hround_id=None, sequence_no=None,
                        error_scope="sender" if fatal else "system", error_stage=stage,
                        error_code=code, error_message=str(exc),
                    )
                    self.repo.mark_one_round_failed(
                        hj_id=hj_id, hs_id=work.hs_id, hr_id=hr_id,
                        error_scope="sender" if fatal else "system", error_code=code, message=str(exc),
                    )
                    if fatal:
                        # Local never auto-disables accounts. Historical method
                        # now records diagnostics only and keeps Sender selectable.
                        self.repo.disable_sender_needs_attention(hs_id=work.hs_id, stage=stage, code=code, message=str(exc))
                    if self.warm_pool is not None:
                        self.warm_pool.drop(work.hs_id)
                    self.repo.release_sender_lease(hs_id=work.hs_id, lease_token=work.lease_token)
                    emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="LOGIN", status="error", code=code, replacement=True)
                    emit_user(event_cb, f"Sender #{work.hs_id} เข้าใช้งานไม่ได้ • เลือกตัวสำรองแทน")

            if len(cold_rows) < need and len(ready) < wanted:
                no_eligible = True

        return ready[:wanted], failed_count, disabled_count, no_eligible

    def _create_rounds(self, *, hj_id: int, hr_id: int, works: list[SenderWork], event_cb: Event | None) -> None:
        if not works:
            return
        rows = self.repo.create_rounds_batch(
            hj_id=hj_id, hr_id=hr_id,
            items=[{"hs_id": w.hs_id, "lease_token": w.lease_token} for w in works],
        )
        by_hs = {int(r["hs_id"]): r for r in rows}
        for work in works:
            row = by_hs.get(work.hs_id)
            if not row:
                raise RuntimeError(f"round batch create missing hs_id={work.hs_id}")
            work.hround_id = int(row["hround_id"])
            work.sequence_no = int(row["sequence_no"])
            emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="READY", status="ok", sequence_no=work.sequence_no)

    def _fail_works_batch(
        self,
        *,
        hj_id: int,
        hr_id: int,
        works: list[SenderWork],
        stage: str,
        recovery_required: bool = False,
    ) -> int:
        """Persist transient/final failures in one DB transaction.

        The old Turbo path opened several MariaDB connections per failed sender.
        With 40+ ACCEPT misses this could add ~20-30 seconds that was invisible
        in the network stage timers.
        """
        if not works:
            return 0
        items: list[dict[str, Any]] = []
        for work in works:
            scope = work.error_scope or "system"
            code = work.error_code or f"{stage}_FAILED"
            message = work.error_message or f"{stage} failed"
            items.append({
                "hs_id": work.hs_id,
                "hround_id": work.hround_id,
                "sequence_no": work.sequence_no,
                "lease_token": work.lease_token,
                "error_scope": scope,
                "error_stage": stage,
                "error_code": code,
                "error_message": message,
                "recovery_required": recovery_required,
            })
        return self.repo.mark_batch_failed(hj_id=hj_id, items=items)

    def _retry_works_batch(
        self,
        *,
        hj_id: int,
        works: list[SenderWork],
        stage: str,
    ) -> int:
        """Persist a Local pre-send miss while keeping the Sender reusable.

        Only ADD/ACCEPT may use this path because no heart SEND has occurred yet.
        This is deliberately separate from SEND recovery, where duplicate safety
        requires mailbox confirmation/cooldown rules.
        """
        if not works:
            return 0
        if stage not in {"ADD", "ACCEPT"}:
            raise ValueError(f"retryable pre-send stage not allowed: {stage}")
        items: list[dict[str, Any]] = []
        for work in works:
            scope = work.error_scope or ("receiver" if stage == "ACCEPT" else "sender")
            code = work.error_code or f"{stage}_FAILED"
            message = work.error_message or f"{stage} failed"
            items.append({
                "hs_id": work.hs_id,
                "hround_id": work.hround_id,
                "sequence_no": work.sequence_no,
                "lease_token": work.lease_token,
                "error_scope": scope,
                "error_stage": stage,
                "error_code": code,
                "error_message": message,
            })
        return self.repo.mark_batch_retryable_pre_send(hj_id=hj_id, items=items)

    def _fail_work(self, *, hj_id: int, hr_id: int, work: SenderWork, stage: str, recovery_required: bool = False) -> None:
        scope = work.error_scope or "system"
        code = work.error_code or f"{stage}_FAILED"
        message = work.error_message or f"{stage} failed"
        if work.hround_id:
            self.repo.mark_round_failed(
                hround_id=work.hround_id,
                error_scope=scope,
                error_stage=stage,
                error_code=code,
                error_message=message,
                recovery_required=recovery_required,
            )
        self.repo.mark_sender_attempt_failed(
            hj_id=hj_id,
            hs_id=work.hs_id,
            hround_id=work.hround_id,
            sequence_no=work.sequence_no,
            error_scope=scope,
            error_stage=stage,
            error_code=code,
            error_message=message,
        )
        self.repo.mark_one_round_failed(
            hj_id=hj_id,
            hs_id=work.hs_id,
            hr_id=hr_id,
            error_scope=scope,
            error_code=code,
            message=message,
        )
        self.repo.release_sender_lease(hs_id=work.hs_id, lease_token=work.lease_token)

    def _mailbox_collect(
        self,
        *,
        receiver_login: LoginSessionResult,
        works: list[SenderWork],
        attempts: int,
        delay_seconds: float,
        event_cb: Event | None,
    ) -> tuple[list[SenderWork], list[SenderWork], list[dict[str, Any]]]:
        wanted = {int(w.member_seq): w for w in works if w.member_seq}
        found: dict[int, int] = {}
        summaries: list[dict[str, Any]] = []
        for attempt in range(1, max(1, attempts) + 1):
            result = mailbox_read_call(
                cfg=self.runtime_cfg,
                slot="R",
                session=receiver_login.session,
                auth=receiver_login.auth,
                from_member_seq=None,
                live=True,
                timeout=self._timeout("ds_timeout_seconds", 20),
            )
            summaries.append({
                "attempt": attempt,
                "ok": bool(result.get("ok")),
                "http_status": result.get("http_status"),
                "response_code": result.get("response_code"),
                "mail_list_count": result.get("mail_list_count"),
            })
            if not bool(result.get("ok")):
                if attempt < attempts:
                    time.sleep(max(0.05, delay_seconds))
                    continue
                break
            for item in result.get("life_mail_candidates") or []:
                try:
                    sender_seq = int(item.get("fromMemberSeq"))
                    mail_seq = int(item.get("seq"))
                except Exception:
                    continue
                if sender_seq in wanted and sender_seq not in found:
                    found[sender_seq] = mail_seq
            emit_rt(event_cb, "BATCH_STAGE", stage="MAILBOX", done=len(found), total=len(wanted), attempt=attempt)
            if len(found) >= len(wanted):
                break
            if attempt < attempts:
                time.sleep(max(0.05, delay_seconds))

        ok: list[SenderWork] = []
        missing: list[SenderWork] = []
        for sender_seq, work in wanted.items():
            seq = found.get(sender_seq)
            if seq:
                work.mail_seq = seq
                work.stage = "MAILBOX"
                ok.append(work)
                emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="MAILBOX", status="ok")
            else:
                work.error_scope = "receiver"
                work.error_code = "LIFE_MAIL_SEQ_NOT_FOUND"
                work.error_message = "send confirmed but life mail did not appear during batch mailbox window"
                missing.append(work)
                emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="MAILBOX", status="pending-recovery")
        return ok, missing, summaries

    def _receive_batch(
        self,
        *,
        receiver_login: LoginSessionResult,
        works: list[SenderWork],
        event_cb: Event | None,
    ) -> tuple[list[SenderWork], list[SenderWork], dict[str, Any]]:
        if not works:
            return [], [], {"ok": True, "count": 0}
        seqs = [int(w.mail_seq) for w in works if w.mail_seq]
        emit_rt(event_cb, "BATCH_STAGE", stage="RECEIVE", done=0, total=len(seqs))
        result = heart_receive_call(
            cfg=self.runtime_cfg,
            actor_slot="R",
            actor_session=receiver_login.session,
            actor_auth=receiver_login.auth,
            life_mail_box_seqs=seqs,
            live=True,
            timeout=self._timeout("ds_timeout_seconds", 20),
        )
        if bool(result.get("ok")):
            for idx, work in enumerate(works, 1):
                work.stage = "RECEIVE"
                work.steps["RECEIVE"] = result
                emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="RECEIVE", status="ok")
                emit_rt(event_cb, "BATCH_STAGE", stage="RECEIVE", done=idx, total=len(works))
            return list(works), [], result

        # Conservative recovery: re-read mailbox.  If a seq disappeared after
        # the failed batch call, treat it as committed.  Retry only seqs that
        # are still present, which avoids double-accepting a partially applied
        # batch request.
        verify = mailbox_read_call(
            cfg=self.runtime_cfg,
            slot="R",
            session=receiver_login.session,
            auth=receiver_login.auth,
            from_member_seq=None,
            live=True,
            timeout=self._timeout("ds_timeout_seconds", 20),
        )
        present = set()
        if bool(verify.get("ok")):
            for item in verify.get("life_mail_candidates") or []:
                try:
                    present.add(int(item.get("seq")))
                except Exception:
                    pass
        committed: list[SenderWork] = []
        retry: list[SenderWork] = []
        for work in works:
            if int(work.mail_seq or 0) not in present:
                committed.append(work)
            else:
                retry.append(work)

        for work in retry:
            single = heart_receive_call(
                cfg=self.runtime_cfg,
                actor_slot="R",
                actor_session=receiver_login.session,
                actor_auth=receiver_login.auth,
                life_mail_box_seqs=[int(work.mail_seq or 0)],
                live=True,
                timeout=self._timeout("ds_timeout_seconds", 20),
            )
            if bool(single.get("ok")):
                work.steps["RECEIVE"] = single
                committed.append(work)
            else:
                work.error_scope = "receiver"
                work.error_code = str(single.get("response_code") or single.get("error") or "BATCH_RECEIVE_FAILED")
                work.error_message = str(single.get("response_message") or single.get("message") or "heart receive failed")

        failed = [w for w in works if w not in committed]
        for idx, work in enumerate(committed, 1):
            work.stage = "RECEIVE"
            emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="RECEIVE", status="ok")
            emit_rt(event_cb, "BATCH_STAGE", stage="RECEIVE", done=idx, total=len(works))
        for work in failed:
            emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="RECEIVE", status="error")
        return committed, failed, result

    def _bulk_remove_receiver_friends(
        self,
        *,
        receiver_login: LoginSessionResult,
        works: list[SenderWork],
        event_cb: Event | None,
    ) -> dict[str, Any]:
        mids = [w.mid for w in works if w.mid]
        if not mids:
            return {"ok": True, "count": 0, "secretOutput": "NONE"}
        emit_rt(event_cb, "BATCH_STAGE", stage="REMOVE", done=0, total=len(mids))
        result = remove_friend(
            cfg=self.runtime_cfg,
            slot="R",
            auth=receiver_login.auth,
            target_mids=mids,
            timeout=self._timeout("grpc_timeout_seconds", 12),
            live=True,
        )
        status = "ok" if bool(result.get("ok")) else "error"
        for idx, work in enumerate(works, 1):
            emit_rt(event_cb, "SENDER_STAGE", hs_id=work.hs_id, email_mask=work.email_mask, stage="REMOVE", status=status)
            emit_rt(event_cb, "BATCH_STAGE", stage="REMOVE", done=idx if status == "ok" else 0, total=len(mids))
        return result

    def _job_progress(
        self,
        *,
        hj_id: int,
        started: float,
        completed_baseline: int,
        event_cb: Event | None,
        batch_no: int,
        batch_size: int,
        failed: int,
    ) -> dict[str, Any]:
        job = self._job(hj_id)
        requested = int(job.get("requested_hearts") or 0)
        completed = int(job.get("completed_hearts") or 0)
        remaining = max(0, requested - completed)
        elapsed = max(0.001, time.monotonic() - started)
        run_completed = max(0, completed - int(completed_baseline))
        speed = run_completed / elapsed
        eta = (remaining / speed) if speed > 0 else None
        emit_rt(
            event_cb,
            "JOB_PROGRESS",
            hj_id=hj_id,
            requested=requested,
            completed=completed,
            remaining=remaining,
            failed=failed,
            batch_no=batch_no,
            batch_size=batch_size,
            elapsed_seconds=round(elapsed, 2),
            hearts_per_second=round(speed, 3),
            eta_seconds=round(eta, 1) if eta is not None else None,
        )
        return job

    def run_job(
        self,
        *,
        hj_id: int,
        template_hs_id: int,
        template_hr_id: int | None = None,
        receiver_login_override: LoginSessionResult | None = None,
        sender_workers: int | None = None,
        network_workers: int | None = None,
        batch_size: int | None = None,
        lease_seconds: int | None = None,
        cooldown_seconds: int | None = None,
        mailbox_attempts: int | None = None,
        mailbox_delay_seconds: float | None = None,
        warm_target: int | None = None,
        warm_workers: int | None = None,
        event_cb: Event | None = None,
    ) -> dict[str, Any]:
        job = self._job(hj_id)
        hr_id = int(job["hr_id"])
        requested = int(job.get("requested_hearts") or 0)
        completed0 = int(job.get("completed_hearts") or 0)
        remaining0 = max(0, requested - completed0)
        if remaining0 <= 0:
            return {"ok": True, "status": "already-completed", "hj_id": hj_id, "secretOutput": "NONE"}
        if int(template_hs_id or 0) <= 0:
            raise ConfigError("template sender is required", stage="P53_FAST_JOB")

        sender_workers = max(1, min(50, int(sender_workers or _cfg_int(self.config, "MWOIF_HEART_FAST_SENDER_WORKERS", 10))))
        network_workers = max(1, min(50, int(network_workers or _cfg_int(self.config, "MWOIF_HEART_FAST_NETWORK_WORKERS", 20))))
        batch_size = max(1, min(100, int(batch_size or _cfg_int(self.config, "MWOIF_HEART_FAST_BATCH_SIZE", 50))))
        lease_seconds = max(60, int(lease_seconds or _cfg_int(self.config, "MWOIF_HEART_SENDER_LEASE_SECONDS", 900)))
        cooldown_seconds = max(1, int(cooldown_seconds or _cfg_int(self.config, "MWOIF_HEART_PAIR_COOLDOWN_SECONDS", 3600)))
        mailbox_attempts = max(1, min(20, int(mailbox_attempts or _cfg_int(self.config, "MWOIF_HEART_FAST_MAILBOX_ATTEMPTS", 8))))
        mailbox_delay_seconds = max(0.05, float(mailbox_delay_seconds if mailbox_delay_seconds is not None else _cfg_float(self.config, "MWOIF_HEART_FAST_MAILBOX_DELAY_SECONDS", 0.35)))
        friend_settle_seconds = max(0.0, min(5.0, _cfg_float(self.config, "MWOIF_HEART_FAST_FRIEND_SETTLE_SECONDS", 1.25)))
        accept_settle_seconds = max(0.0, min(2.0, _cfg_float(self.config, "MWOIF_HEART_FAST_ACCEPT_SETTLE_SECONDS", 0.20)))
        send_verify_delay_seconds = max(0.10, min(3.0, _cfg_float(self.config, "MWOIF_HEART_FAST_SEND_VERIFY_DELAY_SECONDS", 0.45)))
        send_workers = max(1, min(network_workers, sender_workers, _cfg_int(self.config, "MWOIF_HEART_FAST_SEND_WORKERS", 10)))
        turbo_mode = bool(batch_size >= 75)
        # P5.4.4 defaults intentionally cap the receiver-side pressure.  The UI
        # may still show a 50-worker Turbo profile for warm/login/ADD, but the
        # side-effectful ACCEPT/SEND lane uses a measured smaller sweet spot.
        turbo_accept_workers = max(1, min(network_workers, _cfg_int(self.config, "MWOIF_HEART_TURBO_ACCEPT_WORKERS", 14)))
        turbo_send_workers = max(1, min(network_workers, _cfg_int(self.config, "MWOIF_HEART_TURBO_SEND_WORKERS", 10)))
        turbo_accept_attempts = max(1, min(4, _cfg_int(self.config, "MWOIF_HEART_TURBO_ACCEPT_ATTEMPTS", 3)))
        turbo_add_settle_seconds = max(0.0, min(2.0, _cfg_float(self.config, "MWOIF_HEART_TURBO_ADD_SETTLE_SECONDS", 0.65)))
        turbo_friend_settle_seconds = max(0.0, min(2.0, _cfg_float(self.config, "MWOIF_HEART_TURBO_FRIEND_SETTLE_SECONDS", 0.35)))
        turbo_retry_delay_seconds = max(0.03, min(1.0, _cfg_float(self.config, "MWOIF_HEART_TURBO_RETRY_DELAY_SECONDS", 0.22)))
        turbo_wave_size = max(5, min(50, _cfg_int(self.config, "MWOIF_HEART_TURBO_WAVE_SIZE", 25)))
        turbo_wave_stagger_seconds = max(0.0, min(1.0, _cfg_float(self.config, "MWOIF_HEART_TURBO_WAVE_STAGGER_SECONDS", 0.30)))
        warm_target = max(batch_size, min(1000, int(warm_target or _cfg_int(self.config, "MWOIF_HEART_WARM_POOL_TARGET", max(100, batch_size * 2)))))
        warm_workers = max(1, min(50, int(warm_workers or _cfg_int(self.config, "MWOIF_HEART_WARM_LOGIN_WORKERS", 20))))
        template_hr_id = int(template_hr_id or _cfg_int(self.config, "MWOIF_HEART_ROUND_TEMPLATE_RECEIVER_HR_ID", hr_id))

        # Local operator policy: never quarantine/disable a Sender automatically.
        # This also restores rows that older P5.x builds had moved to Needs Attention.
        restored_local = self.repo.restore_local_auto_disabled_senders()
        if restored_local:
            emit_user(event_cb, f"LOCAL: คืน Sender ที่เคยถูกปิดอัตโนมัติ {restored_local} ไอดี • เก็บ Error ไว้แค่ Log")

        if self.warm_pool is not None:
            attempted_ids = set(self.repo.list_job_attempted_sender_ids(hj_id=hj_id))
            self.warm_pool.ensure_async(
                target=warm_target, workers=warm_workers, template_hs_id=int(template_hs_id),
                event_cb=event_cb, exclude_ids=attempted_ids,
            )
            warm_ready = len(self.warm_pool.available_ids(exclude_ids=attempted_ids))
            emit_user(event_cb, f"Warm Pool พร้อมใช้กับงานนี้ {warm_ready}/{warm_target} • ระบบเติมไอดีเบื้องหลัง")
        started = time.monotonic()
        self.repo.resume_job(hj_id=hj_id)
        self.repo.mark_job_running(hj_id=hj_id)
        emit_user(event_cb, f"เริ่ม JOB #{hj_id} • Fast Batch {batch_size} • Sender Workers {sender_workers}")
        emit_rt(event_cb, "JOB_START", hj_id=hj_id, requested=requested, completed=completed0, remaining=remaining0, batch_size=batch_size, sender_workers=sender_workers, network_workers=network_workers)

        if receiver_login_override is not None:
            receiver_login = receiver_login_override
            emit_user(event_cb, "ไอดีรับพร้อมใช้งาน • ใช้ Session จากการตรวจเพื่อนต่อทันที")
        else:
            emit_user(event_cb, "กำลังเข้าสู่ระบบไอดีรับ…")
            receiver_login = self.login_manager.login_receiver_for_job_http_template(
                hj_id=hj_id,
                template_hr_id=template_hr_id,
                event_cb=event_cb,
            )
        emit_rt(event_cb, "RECEIVER_READY", hj_id=hj_id, hr_id=hr_id)

        total_failed = 0
        disabled_count = 0
        batch_no = 0
        no_eligible = False
        paused_for_recovery = False
        stopped = False
        batch_reports: list[dict[str, Any]] = []
        no_progress_batches = 0

        while True:
            job_now = self._job(hj_id)
            requested_now = int(job_now.get("requested_hearts") or 0)
            completed_now = int(job_now.get("completed_hearts") or 0)
            remaining = max(0, requested_now - completed_now)
            if remaining <= 0:
                break
            if int(job_now.get("stop_requested") or 0) == 1:
                stopped = True
                self.repo.mark_job_stopped_after_batch(hj_id=hj_id)
                emit_user(event_cb, "หยุดงานตามคำสั่ง • ไม่มี Batch ใหม่ถูกเริ่ม")
                emit_rt(event_cb, "JOB_STOPPED", hj_id=hj_id, completed=completed_now, requested=requested_now)
                break

            batch_no += 1
            target = min(batch_size, remaining)
            emit_user(event_cb, f"Batch #{batch_no} • เตรียม {target} ไอดี")
            emit_rt(event_cb, "BATCH_START", hj_id=hj_id, batch_no=batch_no, target=target, completed=completed_now, requested=requested_now)
            batch_started = time.monotonic()

            _t_login = time.monotonic()
            ready, login_failed, disabled, exhausted = self._login_fill_batch(
                hj_id=hj_id,
                hr_id=hr_id,
                wanted=target,
                template_hs_id=int(template_hs_id),
                sender_workers=sender_workers,
                lease_seconds=lease_seconds,
                event_cb=event_cb,
            )
            login_stage_ms = round((time.monotonic() - _t_login) * 1000, 1)
            total_failed += login_failed
            disabled_count += disabled
            if self.warm_pool is not None:
                attempted_ids = set(self.repo.list_job_attempted_sender_ids(hj_id=hj_id))
                self.warm_pool.ensure_async(
                    target=warm_target, workers=warm_workers, template_hs_id=int(template_hs_id),
                    event_cb=event_cb, exclude_ids=attempted_ids,
                )
            if not ready:
                no_eligible = True
                self.repo.mark_job_paused(hj_id=hj_id, error_scope="system", error_code="NO_ELIGIBLE_SENDER", message="No eligible sender remains after cooldown/lease/attempt filters")
                emit_user(event_cb, "ไม่มีไอดีส่งที่พร้อมใช้งานสำหรับไอดีรับนี้ในตอนนี้")
                break
            if len(ready) < target and exhausted:
                no_eligible = True
                emit_user(event_cb, f"Batch #{batch_no} หา Sender ได้ {len(ready)}/{target} • ทำเท่าที่พร้อมก่อน")

            self._create_rounds(hj_id=hj_id, hr_id=hr_id, works=ready, event_cb=event_cb)

            _t_add = time.monotonic()
            add_ok, add_fail, add_passes = self._stage_parallel_adaptive(
                ready,
                stage="ADD",
                initial_workers=network_workers,
                retry_workers=(min(5, max(1, sender_workers)), 1),
                event_cb=event_cb,
                fn=lambda w: send_friend_request(
                    cfg=self.runtime_cfg,
                    slot="S",
                    auth=w.login.auth,
                    target_mid=receiver_login.auth.mid,
                    source_type=int(self.runtime_cfg.workflow.get("friend_source_type") or 2),
                    timeout=self._timeout("grpc_timeout_seconds", 12),
                    live=True,
                ),
            )
            add_stage_ms = round((time.monotonic() - _t_add) * 1000, 1)
            _t_add_faildb = time.monotonic()
            self._retry_works_batch(hj_id=hj_id, works=add_fail, stage="ADD")
            add_faildb_ms = round((time.monotonic() - _t_add_faildb) * 1000, 1)
            if add_fail:
                emit_user(event_cb, f"LOCAL RETRY: ADD ค้าง {len(add_fail)} ไอดี • ไม่ปิด Sender และคืนเข้าคิว JOB นี้")

            rescue_stage_ms = 0.0
            faildb_accept_ms = 0.0
            faildb_other_ms = 0.0
            failed_cleanup_ms = 0.0

            if turbo_mode:
                # Phase 5.4.2 Turbo: ACCEPT and SEND overlap per sender. A sender
                # starts SEND as soon as its own ACCEPT is confirmed rather than
                # waiting for the whole batch. This is the key latency reduction
                # for 100-heart jobs.
                _t_pipe = time.monotonic()
                (
                    accept_ok, accept_fail, send_ok, send_fail, send_unknown,
                    accept_passes, send_passes, turbo_metrics,
                ) = self._adaptive_wave_accept_send_pipeline(
                    add_ok,
                    receiver_login=receiver_login,
                    wave_size=turbo_wave_size,
                    wave_stagger_seconds=turbo_wave_stagger_seconds,
                    accept_workers=turbo_accept_workers,
                    send_workers=turbo_send_workers,
                    accept_attempts=turbo_accept_attempts,
                    add_settle_seconds=turbo_add_settle_seconds,
                    friend_settle_seconds=turbo_friend_settle_seconds,
                    accept_retry_delay_seconds=turbo_retry_delay_seconds,
                    verify_delay_seconds=send_verify_delay_seconds,
                    event_cb=event_cb,
                )
                pipeline_stage_ms = round((time.monotonic() - _t_pipe) * 1000, 1)
                accept_stage_ms = float(turbo_metrics.get("accept_wall_ms") or pipeline_stage_ms)
                send_stage_ms = float(turbo_metrics.get("pipeline_ms") or pipeline_stage_ms)

                # P5.4.4 has no end-of-batch ACCEPT rescue barrier.  ACCEPT
                # retries already roll inside each wave; remaining misses are
                # released for normal replacement in the next batch.
                rescue_stage_ms = 0.0
                turbo_metrics["rescue_ms"] = 0.0

                _t_faildb_accept = time.monotonic()
                self._retry_works_batch(hj_id=hj_id, works=accept_fail, stage="ACCEPT")
                faildb_accept_ms = round((time.monotonic() - _t_faildb_accept) * 1000, 1)
                if accept_fail:
                    emit_user(event_cb, f"LOCAL RETRY: ACCEPT ค้าง {len(accept_fail)} ไอดี • ไม่ตัดทิ้ง จะวน Sender เดิมใน Batch ถัดไป")
            else:
                # Stable path retained for 5..50-heart batches.
                _t_accept = time.monotonic()
                if add_ok and accept_settle_seconds > 0:
                    time.sleep(accept_settle_seconds)
                accept_initial = min(network_workers, sender_workers, 10)
                accept_ok, accept_fail, accept_passes = self._stage_parallel_adaptive(
                    add_ok,
                    stage="ACCEPT",
                    initial_workers=accept_initial,
                    retry_workers=(min(3, max(1, accept_initial)), 1),
                    event_cb=event_cb,
                    fn=lambda w: handle_friend_request(
                        cfg=self.runtime_cfg,
                        slot="R",
                        auth=receiver_login.auth,
                        target_mid=w.login.auth.mid,
                        accept=True,
                        timeout=self._timeout("grpc_timeout_seconds", 12),
                        live=True,
                    ),
                )
                accept_stage_ms = round((time.monotonic() - _t_accept) * 1000, 1)
                _t_faildb_accept = time.monotonic()
                self._retry_works_batch(hj_id=hj_id, works=accept_fail, stage="ACCEPT")
                faildb_accept_ms = round((time.monotonic() - _t_faildb_accept) * 1000, 1)
                if accept_fail:
                    emit_user(event_cb, f"LOCAL RETRY: ACCEPT ค้าง {len(accept_fail)} ไอดี • ไม่ตัดทิ้ง จะวน Sender เดิมใน Batch ถัดไป")

                _t_send = time.monotonic()
                send_ok, send_fail, send_unknown, send_passes = self._send_adaptive_verified(
                    accept_ok,
                    receiver_login=receiver_login,
                    initial_workers=send_workers,
                    retry_workers=(min(5, send_workers), min(2, send_workers), 1),
                    settle_seconds=friend_settle_seconds,
                    verify_delay_seconds=send_verify_delay_seconds,
                    event_cb=event_cb,
                )
                send_stage_ms = round((time.monotonic() - _t_send) * 1000, 1)
                turbo_metrics = {"turbo": False, "pipeline_ms": 0.0}

            _t_senddb = time.monotonic()
            send_round_ids: list[int] = []
            for w in send_ok:
                w.send_confirmed = True
                if w.hround_id:
                    send_round_ids.append(int(w.hround_id))
            self.repo.mark_batch_send_confirmed(hround_ids=send_round_ids)
            send_checkpoint_ms = round((time.monotonic() - _t_senddb) * 1000, 1)
            # Explicit failures were verified absent from the mailbox through all
            # retries and can be safely failed/replaced. Unknown transport outcomes
            # are never resent blindly and require recovery/cooldown.
            _t_faildb_other = time.monotonic()
            self._fail_works_batch(hj_id=hj_id, hr_id=hr_id, works=send_fail, stage="SEND", recovery_required=False)
            self._fail_works_batch(hj_id=hj_id, hr_id=hr_id, works=send_unknown, stage="SEND", recovery_required=True)
            self.repo.mark_batch_cooldowns(hr_id=hr_id, hs_ids=[w.hs_id for w in send_unknown], cooldown_seconds=cooldown_seconds)
            faildb_other_ms += round((time.monotonic() - _t_faildb_other) * 1000, 1)
            total_failed += len(send_fail) + len(send_unknown)

            # Free friend slots for accepted senders that did not reach a
            # confirmed send. This avoids capacity leakage between batches.
            stage_cleanup_targets = list({w.hs_id: w for w in (accept_fail + send_fail + send_unknown)}.values())
            stage_cleanup_ok = True
            if stage_cleanup_targets:
                _t_failed_cleanup = time.monotonic()
                cleanup_stage_result = self._bulk_remove_receiver_friends(
                    receiver_login=receiver_login,
                    works=stage_cleanup_targets,
                    event_cb=event_cb,
                )
                failed_cleanup_ms = round((time.monotonic() - _t_failed_cleanup) * 1000, 1)
                stage_cleanup_ok = bool(cleanup_stage_result.get("ok"))

            _t_mail = time.monotonic()
            mail_ok, mail_missing, mailbox_summary = self._mailbox_collect(
                receiver_login=receiver_login,
                works=send_ok,
                attempts=mailbox_attempts,
                delay_seconds=mailbox_delay_seconds,
                event_cb=event_cb,
            )
            mailbox_stage_ms = round((time.monotonic() - _t_mail) * 1000, 1)
            # Send was already confirmed. Missing mail must never be replaced in
            # the same job blindly; pause after processing all confirmed mails.
            _t_mail_faildb = time.monotonic()
            self._fail_works_batch(hj_id=hj_id, hr_id=hr_id, works=mail_missing, stage="MAILBOX", recovery_required=True)
            self.repo.mark_batch_cooldowns(hr_id=hr_id, hs_ids=[w.hs_id for w in mail_missing], cooldown_seconds=cooldown_seconds)
            faildb_other_ms += round((time.monotonic() - _t_mail_faildb) * 1000, 1)
            total_failed += len(mail_missing)
            emit_user(event_cb, f"MAILBOX: พบ {len(mail_ok)}/{len(send_ok)}")

            _t_receive = time.monotonic()
            receive_ok, receive_fail, receive_result = self._receive_batch(
                receiver_login=receiver_login,
                works=mail_ok,
                event_cb=event_cb,
            )
            receive_stage_ms = round((time.monotonic() - _t_receive) * 1000, 1)
            _t_recv_faildb = time.monotonic()
            self._fail_works_batch(hj_id=hj_id, hr_id=hr_id, works=receive_fail, stage="RECEIVE", recovery_required=True)
            self.repo.mark_batch_cooldowns(hr_id=hr_id, hs_ids=[w.hs_id for w in receive_fail], cooldown_seconds=cooldown_seconds)
            faildb_other_ms += round((time.monotonic() - _t_recv_faildb) * 1000, 1)
            total_failed += len(receive_fail)
            emit_user(event_cb, f"RECEIVE: ผ่าน {len(receive_ok)}/{len(mail_ok)}")

            _t_cleanup = time.monotonic()
            remove_result = self._bulk_remove_receiver_friends(
                receiver_login=receiver_login,
                works=receive_ok,
                event_cb=event_cb,
            )
            cleanup_ok = bool(remove_result.get("ok"))
            cleanup_stage_ms = round((time.monotonic() - _t_cleanup) * 1000, 1)

            # Heart receipt is the success boundary. Commit the whole received
            # batch in one DB transaction to keep large jobs fast.
            commit_items = [
                {
                    "hs_id": w.hs_id,
                    "hround_id": int(w.hround_id or 0),
                    "sequence_no": int(w.sequence_no or 0),
                    "lease_token": w.lease_token,
                }
                for w in receive_ok
                if w.hround_id and w.sequence_no
            ]
            if commit_items:
                self.repo.mark_batch_success(
                    hj_id=hj_id,
                    hr_id=hr_id,
                    items=commit_items,
                    cooldown_seconds=cooldown_seconds,
                    cleanup_ok=cleanup_ok,
                )
                job_after_commit = self._job(hj_id)
                emit_rt(
                    event_cb,
                    "JOB_PROGRESS",
                    hj_id=hj_id,
                    requested=int(job_after_commit.get("requested_hearts") or 0),
                    completed=int(job_after_commit.get("completed_hearts") or 0),
                    remaining=max(0, int(job_after_commit.get("requested_hearts") or 0)-int(job_after_commit.get("completed_hearts") or 0)),
                    failed=total_failed,
                    batch_no=batch_no,
                    batch_size=batch_size,
                    elapsed_seconds=round(max(0.001, time.monotonic()-started), 2),
                    hearts_per_second=round(max(0, int(job_after_commit.get("completed_hearts") or 0)-completed0)/max(0.001, time.monotonic()-started), 3),
                    eta_seconds=None,
                )

            batch_elapsed = max(0.001, time.monotonic() - batch_started)
            batch_reports.append({
                "batch_no": batch_no,
                "target": target,
                "ready": len(ready),
                "warm_pool_ready": len(self.warm_pool.available_ids(exclude_ids=set(self.repo.list_job_attempted_sender_ids(hj_id=hj_id)))) if self.warm_pool is not None else 0,
                "warm_hits_total": self._warm_hits,
                "cold_logins_total": self._cold_logins,
                "sent": len(send_ok),
                "mail_found": len(mail_ok),
                "received": len(receive_ok),
                "failed": len(send_fail) + len(send_unknown) + len(mail_missing) + len(receive_fail),
                "retryable_pre_send": len(add_fail) + len(accept_fail),
                "cleanup_ok": cleanup_ok and stage_cleanup_ok,
                "stage_cleanup_ok": stage_cleanup_ok,
                "elapsed_ms": round(batch_elapsed * 1000, 1),
                "mailbox_attempts": mailbox_summary,
                "batch_receive_ok": bool(receive_result.get("ok")),
                "add_passes": add_passes,
                "accept_passes": accept_passes,
                "send_passes": send_passes,
                "send_workers": send_workers,
                "turbo_mode": bool(turbo_mode),
                "turbo_metrics": turbo_metrics,
                "friend_settle_seconds": friend_settle_seconds,
                "accept_settle_seconds": accept_settle_seconds,
                "stage_ms": {
                    "prepare_login": login_stage_ms,
                    "add": add_stage_ms,
                    "add_fail_db": add_faildb_ms,
                    "accept": accept_stage_ms,
                    "rescue": rescue_stage_ms,
                    "send": send_stage_ms,
                    "send_checkpoint_db": send_checkpoint_ms,
                    "fail_db_accept": faildb_accept_ms,
                    "fail_db_other": faildb_other_ms,
                    "failed_friend_cleanup": failed_cleanup_ms,
                    "mailbox": mailbox_stage_ms,
                    "receive": receive_stage_ms,
                    "cleanup": cleanup_stage_ms,
                },
                "stage_counts": {
                    "ready": len(ready),
                    "add": len(add_ok),
                    "accept": len(accept_ok),
                    "send": len(send_ok),
                    "mailbox": len(mail_ok),
                    "receive": len(receive_ok),
                },
            })
            emit_user(
                event_cb,
                f"Batch #{batch_no} • READY {len(ready)} | ADD {len(add_ok)} | ACCEPT {len(accept_ok)} | SEND {len(send_ok)} | MAIL {len(mail_ok)} | RECEIVE {len(receive_ok)} • {batch_elapsed:.1f} วินาที",
            )
            if turbo_mode:
                emit_user(
                    event_cb,
                    f"PERF WAVE Batch #{batch_no}: เตรียม {login_stage_ms/1000:.2f}s | ADD {add_stage_ms/1000:.2f}s | "
                    f"WAVE {float(turbo_metrics.get('initial_ms') or send_stage_ms)/1000:.2f}s | "
                    f"CATCHUP {float(turbo_metrics.get('accept_catchup_ms') or 0)/1000:.2f}s | "
                    f"VERIFY {float(turbo_metrics.get('verify_ms') or 0)/1000:.2f}s | RETRY {float(turbo_metrics.get('send_retry_ms') or 0)/1000:.2f}s | "
                    f"DBCHK {send_checkpoint_ms/1000:.2f}s | FAILDB {(faildb_accept_ms+faildb_other_ms)/1000:.2f}s | "
                    f"FAIL-CLEAN {failed_cleanup_ms/1000:.2f}s | MAIL {mailbox_stage_ms/1000:.2f}s | RECEIVE {receive_stage_ms/1000:.2f}s | CLEAN {cleanup_stage_ms/1000:.2f}s"
                )
            else:
                emit_user(event_cb, f"PERF Batch #{batch_no}: เตรียม {login_stage_ms/1000:.2f}s | ADD {add_stage_ms/1000:.2f}s | ACCEPT {accept_stage_ms/1000:.2f}s | SEND {send_stage_ms/1000:.2f}s | MAIL {mailbox_stage_ms/1000:.2f}s | RECEIVE {receive_stage_ms/1000:.2f}s")
            emit_rt(event_cb, "BATCH_DONE", hj_id=hj_id, batch_no=batch_no, target=target, passed=len(receive_ok), failed=batch_reports[-1]["failed"], elapsed_seconds=round(batch_elapsed, 2), cleanup_ok=cleanup_ok, stage_ms=batch_reports[-1]["stage_ms"])
            final_job = self._job_progress(
                hj_id=hj_id,
                started=started,
                completed_baseline=completed0,
                event_cb=event_cb,
                batch_no=batch_no,
                batch_size=batch_size,
                failed=total_failed,
            )

            # Retryable ADD/ACCEPT misses can legally re-enter the same Local job.
            # Prevent an actually broken receiver/session from spinning forever: three
            # consecutive zero-heart batches pause the job, but still do not disable
            # any Sender account. Re-running the job starts a fresh retry window.
            if receive_ok:
                no_progress_batches = 0
            else:
                no_progress_batches += 1
            if no_progress_batches >= 3:
                paused_for_recovery = True
                self.repo.mark_job_paused(
                    hj_id=hj_id,
                    error_scope="receiver",
                    error_code="LOCAL_RETRY_STALLED",
                    message="Three consecutive Local batches made zero heart progress; no Sender was disabled",
                )
                emit_user(event_cb, "LOCAL: 3 Batch ติดกันยังไม่ได้ใจเพิ่ม • พัก JOB ไว้ แต่ไม่ปิด Sender แม้แต่ไอดีเดียว")
                break

            if send_unknown or mail_missing or receive_fail:
                paused_for_recovery = True
                self.repo.mark_job_paused(hj_id=hj_id, error_scope="receiver", error_code="BATCH_RECOVERY_REQUIRED", message="One or more confirmed sends need mailbox/receive recovery")
                emit_user(event_cb, "หยุดก่อน Batch ถัดไป • มีรายการส่งสำเร็จที่ต้องตรวจการรับใจซ้ำ")
                break
            if not cleanup_ok or not stage_cleanup_ok:
                paused_for_recovery = True
                self.repo.mark_job_paused(hj_id=hj_id, error_scope="receiver", error_code="FRIEND_CLEANUP_FAILED", message="Heart receive passed but receiver bulk friend cleanup failed")
                emit_user(event_cb, "รับใจสำเร็จแล้ว แต่ลบเพื่อนหลัง Batch ไม่สำเร็จ • หยุดก่อนเพื่อกันรายชื่อเต็ม")
                break
            # Never stop only because status says completed. A historical
            # Phase 5.4 SQL bug could leave partial jobs as completed (e.g.
            # 50/100). Progress is authoritative.
            final_requested = int(final_job.get("requested_hearts") or 0)
            final_completed = int(final_job.get("completed_hearts") or 0)
            if final_requested > 0 and final_completed >= final_requested:
                break

        final_job = self._job(hj_id)
        elapsed = max(0.001, time.monotonic() - started)
        completed = int(final_job.get("completed_hearts") or 0)
        remaining = max(0, int(final_job.get("requested_hearts") or 0) - completed)
        status = str(final_job.get("status") or "")
        ok = remaining == 0 and status == "completed"
        speed = max(0, completed - completed0) / elapsed
        if ok:
            self.repo.purge_receiver_job_vault(hj_id=hj_id)
        emit_rt(event_cb, "JOB_DONE", hj_id=hj_id, status=status, requested=requested, completed=completed, remaining=remaining, failed=total_failed, elapsed_seconds=round(elapsed, 2), hearts_per_second=round(speed, 3))
        emit_user(event_cb, f"JOB #{hj_id} จบ • {completed}/{requested} ใจ • {elapsed:.1f} วินาที")
        self.close(wait=True)
        eligible_end = self.repo.eligible_sender_counts(hj_id=hj_id, hr_id=hr_id)
        return {
            "ok": ok,
            "action": "heart-job-fast-batch-run",
            "hj_id": hj_id,
            "hr_id": hr_id,
            "status": status,
            "requested_hearts": requested,
            "completed_hearts": completed,
            "remaining_hearts": remaining,
            "failed_this_run": total_failed,
            "sender_account_disabled_this_run": 0,
            "local_auto_disable": False,
            "batch_size": batch_size,
            "sender_workers": sender_workers,
            "network_workers": network_workers,
            "warm_pool_target": warm_target,
            "warm_login_workers": warm_workers,
            "warm_hits_this_run": self._warm_hits,
            "cold_logins_this_run": self._cold_logins,
            "warm_pool_ready_end": len(self.warm_pool.available_ids(exclude_ids=set(self.repo.list_job_attempted_sender_ids(hj_id=hj_id)))) if self.warm_pool is not None else 0,
            "login_provider": "direct-http-exact-template",
            "browser_during_replay": False,
            "cooldown_seconds": cooldown_seconds,
            "no_eligible_sender": no_eligible,
            "eligible_sender_counts_end": eligible_end,
            "paused_for_recovery": paused_for_recovery,
            "stopped": stopped,
            "batch_count": len(batch_reports),
            "batches": batch_reports,
            "elapsed_ms": round(elapsed * 1000, 1),
            "hearts_per_second": round(speed, 3),
            "secretOutput": "NONE",
        }
