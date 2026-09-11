from __future__ import annotations

import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from mwoif.application.login_session_manager import LoginSessionManager, LoginSessionResult
from mwoif.auth.models import jwt_exp
from mwoif.core.config import AppConfig
from mwoif.storage.repository import HeartRepository
from mwoif.storage.vault import CredentialVault

Event = Callable[[str], None]


@dataclass(slots=True)
class WarmEntry:
    hs_id: int
    login: LoginSessionResult
    warmed_at: float
    token_exp: int | None
    uses: int = 0
    last_used_at: float = 0.0


class WarmSenderPool:
    """Process-local sender auth/session cache for Local/Web worker prototypes.

    The pool does not bypass DB leases or sender+receiver cooldowns.  It only
    removes login/initMember3 from the customer's critical path.  A job still
    has to lease a warmed sender successfully before it may use that session.
    """

    def __init__(self, config: AppConfig, repo: HeartRepository, vault: CredentialVault, *, max_workers: int = 20) -> None:
        self.config = config
        self.repo = repo
        self.vault = vault
        self.login_manager = LoginSessionManager(config, repo, vault)
        self._lock = threading.RLock()
        self._entries: dict[int, WarmEntry] = {}
        self._warming_ids: set[int] = set()
        self._executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="p54-warm")
        self._background_lock = threading.Lock()
        self._background_thread: threading.Thread | None = None
        self._last_error: str | None = None
        self._last_warm_at: float | None = None
        self._closed = False

    @staticmethod
    def _fatal(exc: BaseException) -> bool:
        # Background warming must never quarantine an account on a transient
        # network/auth/session failure. Job execution remains the authority for
        # NEEDS_ATTENTION. Missing/corrupt local credential data is reported but
        # also not auto-disabled here so an operator can fix the shared vault.
        return False

    def _max_age_seconds(self) -> int:
        # Runtime observation from V1 was about 30 minutes. Keep a safety
        # margin and recycle before the session becomes questionable.
        raw = os.getenv("MWOIF_HEART_WARM_MAX_AGE_SECONDS", "1500")
        try:
            return max(300, min(3600, int(raw)))
        except (TypeError, ValueError):
            return 1500

    def _valid_entry(self, entry: WarmEntry, *, safety_seconds: int = 120) -> bool:
        if not entry.login.auth.ready or not entry.login.session.established:
            return False
        now = time.time()
        if now - float(entry.warmed_at or 0.0) >= self._max_age_seconds():
            return False
        exp = entry.token_exp
        if exp is None:
            return True
        return exp > int(now) + max(30, int(safety_seconds))

    def prune(self) -> int:
        removed = 0
        with self._lock:
            for hs_id, entry in list(self._entries.items()):
                if not self._valid_entry(entry):
                    self._entries.pop(hs_id, None)
                    removed += 1
        return removed

    def status(self) -> dict[str, Any]:
        self.prune()
        with self._lock:
            now = time.time()
            ages = [max(0.0, now - float(entry.warmed_at or now)) for entry in self._entries.values()]
            return {
                "ok": True,
                "ready": len(self._entries),
                "warming": len(self._warming_ids),
                "last_warm_at": self._last_warm_at,
                "last_error": self._last_error,
                "max_age_seconds": self._max_age_seconds(),
                "oldest_age_seconds": round(max(ages), 1) if ages else 0.0,
                "sender_ids": list(self._entries.keys())[:500],
                "secretOutput": "NONE",
            }

    def available_ids(self, *, exclude_ids: set[int] | list[int] | tuple[int, ...] | None = None) -> list[int]:
        self.prune()
        excluded = {int(x) for x in (exclude_ids or [])}
        with self._lock:
            ids = [hs_id for hs_id in self._entries.keys() if hs_id not in excluded]
        random.shuffle(ids)
        return ids

    def get(self, hs_id: int) -> LoginSessionResult | None:
        self.prune()
        with self._lock:
            entry = self._entries.get(int(hs_id))
            if not entry or not self._valid_entry(entry):
                return None
            entry.uses += 1
            entry.last_used_at = time.time()
            return entry.login

    def drop(self, hs_id: int) -> None:
        with self._lock:
            self._entries.pop(int(hs_id), None)
            self._warming_ids.discard(int(hs_id))

    def _warm_one(self, hs_id: int, template_hs_id: int, event_cb: Event | None) -> tuple[int, LoginSessionResult | None, BaseException | None]:
        try:
            login = self.login_manager.login_sender_http_template(
                hs_id=int(hs_id),
                template_hs_id=int(template_hs_id),
                event_cb=event_cb,
            )
            return int(hs_id), login, None
        except BaseException as exc:  # returned to coordinator; never leaks secrets
            return int(hs_id), None, exc

    def warm(
        self,
        *,
        target: int,
        workers: int,
        template_hs_id: int,
        event_cb: Event | None = None,
        exclude_ids: set[int] | list[int] | tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        target = max(0, min(1000, int(target)))
        workers = max(1, min(50, int(workers)))
        if target <= 0:
            return self.status()
        # Operator-only Local policy: no sender is quarantined/disabled by
        # historical health state. Warm failures simply stay not-warm and can
        # be retried on the next refill cycle.
        self.repo.restore_local_auto_disabled_senders()
        self.prune()
        excluded = {int(x) for x in (exclude_ids or [])}
        with self._lock:
            usable_before = sum(1 for hs_id in self._entries if hs_id not in excluded)
            total_before = len(self._entries)
            need = max(0, target - usable_before)
        if need <= 0:
            return self.status()

        # Pull a reserve of candidates. Only ``workers`` are in flight at a
        # time; when one fails we immediately feed the next candidate until
        # the target is actually reached or the candidate pool is exhausted.
        # This avoids the old behavior where one bad account could leave a
        # 100-target pool at 99 until the next maintenance tick.
        candidates = self.repo.list_sender_warm_candidates(limit=max(target * 3, target + 100))
        with self._lock:
            existing = set(self._entries) | set(self._warming_ids) | excluded
        candidate_ids = [int(r["hs_id"]) for r in candidates if int(r["hs_id"]) not in existing]
        random.shuffle(candidate_ids)
        if not candidate_ids:
            return self.status()

        if event_cb:
            event_cb(f"P54 WARM START target={target} usable={usable_before} total={total_before} candidates={len(candidate_ids)} workers={workers} secretOutput=NONE")
            event_cb(f'@RT {{"event":"WARM_POOL","status":"running","ready":{usable_before},"total_ready":{total_before},"target":{target},"warming":0}}')

        passed = 0
        failed = 0
        disabled = 0
        iterator = iter(candidate_ids)
        pending: dict[Any, int] = {}
        sem = threading.Semaphore(workers)

        def guarded_warm(hs_id: int):
            with sem:
                return self._warm_one(hs_id, int(template_hs_id), event_cb)

        def submit_next() -> bool:
            try:
                hs_id = next(iterator)
            except StopIteration:
                return False
            with self._lock:
                # Another warm cycle/job may have populated it while queued.
                if hs_id in self._entries or hs_id in self._warming_ids:
                    return submit_next()
                self._warming_ids.add(hs_id)
            fut = self._executor.submit(guarded_warm, hs_id)
            pending[fut] = hs_id
            return True

        for _ in range(min(workers, len(candidate_ids), need)):
            if not submit_next():
                break

        while pending:
            # Process one completed future at a time so a failed slot can be
            # refilled immediately without scheduling hundreds of excess logins.
            future = next(as_completed(list(pending.keys())))
            hs_id = pending.pop(future)
            try:
                _id, login, exc = future.result()
            except BaseException as caught:  # defensive; never expose secret values
                login, exc = None, caught
            with self._lock:
                self._warming_ids.discard(hs_id)

            if login is not None:
                exp = jwt_exp(login.auth.game_access_token)
                now = time.time()
                with self._lock:
                    self._entries[hs_id] = WarmEntry(hs_id=hs_id, login=login, warmed_at=now, token_exp=exp, last_used_at=now)
                passed += 1
            else:
                failed += 1
                self.drop(hs_id)

            with self._lock:
                total_now = len(self._entries)
                usable_now = sum(1 for sender_id in self._entries if sender_id not in excluded)
                warming_now = len(self._warming_ids)
            if event_cb:
                event_cb(f'@RT {{"event":"WARM_POOL","status":"running","ready":{usable_now},"total_ready":{total_now},"target":{target},"warming":{warming_now}}}')

            if usable_now < target:
                submit_next()
            # If the target is reached, let already-running logins finish. Their
            # sessions remain useful as a small ready reserve, but do not submit
            # any new candidates.

        with self._lock:
            self._last_warm_at = time.time()
            total_ready = len(self._entries)
            usable_ready = sum(1 for hs_id in self._entries if hs_id not in excluded)
            self._last_error = None if usable_ready else "NO_WARMABLE_SENDER"
            warming = len(self._warming_ids)
        if event_cb:
            event_cb(f"P54 WARM DONE usable={usable_ready}/{target} total={total_ready} passed={passed} failed={failed} disabled={disabled} secretOutput=NONE")
            event_cb(f'@RT {{"event":"WARM_POOL","status":"ready","ready":{usable_ready},"total_ready":{total_ready},"target":{target},"warming":{warming}}}')
        return {
            "ok": usable_ready > 0,
            "ready": usable_ready,
            "total_ready": total_ready,
            "target": target,
            "passed_this_run": passed,
            "failed_this_run": failed,
            "disabled_this_run": disabled,
            "warming": warming,
            "secretOutput": "NONE",
        }

    def close(self, *, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._executor.shutdown(wait=bool(wait), cancel_futures=True)
        except TypeError:  # Python compatibility
            self._executor.shutdown(wait=bool(wait))

    def ensure_async(
        self, *, target: int, workers: int, template_hs_id: int, event_cb: Event | None = None,
        exclude_ids: set[int] | list[int] | tuple[int, ...] | None = None,
    ) -> bool:
        """Start one background refill if another refill is not already active.

        ``exclude_ids`` is job-scoped. It lets a 1,000-heart job keep 100+
        *usable* warmed senders ahead even after earlier batches consumed other
        warm sessions. The excluded sessions stay cached for other receivers.
        """
        self.prune()
        with self._lock:
            if self._closed:
                return False
        excluded = {int(x) for x in (exclude_ids or [])}
        with self._lock:
            usable = sum(1 for hs_id in self._entries if hs_id not in excluded)
            if usable >= int(target):
                return False
        with self._background_lock:
            if self._background_thread is not None and self._background_thread.is_alive():
                return False

            def run() -> None:
                try:
                    self.warm(
                        target=target, workers=workers, template_hs_id=template_hs_id,
                        event_cb=event_cb, exclude_ids=excluded,
                    )
                except Exception as exc:
                    with self._lock:
                        self._last_error = f"{type(exc).__name__}: {exc}"

            self._background_thread = threading.Thread(target=run, name="p54-warm-refill", daemon=True)
            self._background_thread.start()
            return True
