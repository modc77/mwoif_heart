from __future__ import annotations

from typing import Any

from mwoif.storage.vault import VaultBlob

from .database import MariaDb


class HeartRepository:
    def __init__(self, db: MariaDb) -> None:
        self.db = db

    def get_setting(self, key: str) -> str | None:
        row = self.db.fetch_one(
            "SELECT setting_value FROM heart_settings WHERE setting_key=%s",
            (key,),
        )
        return None if row is None else row.get("setting_value")

    def upsert_setting(self, key: str, value: str | None) -> None:
        self.db.execute(
            """
            INSERT INTO heart_settings (setting_key, setting_value)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)
            """,
            (key, value),
        )

    def upsert_receiver(self, *, email_hash: str, email_mask: str | None, source: str = "local", u_id: int | None = None) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO heart_receivers (u_id, source, email_hash, email_mask)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                email_mask=VALUES(email_mask),
                source=VALUES(source),
                u_id=COALESCE(VALUES(u_id), u_id),
                status='active'
            """,
            (u_id, source, email_hash, email_mask),
        )
        row = self.db.fetch_one(
            "SELECT * FROM heart_receivers WHERE email_hash=%s",
            (email_hash,),
        )
        if row is None:
            raise RuntimeError("receiver upsert succeeded but row was not found")
        return row

    def upsert_sender(self, *, label: str, email_hash: str, email_mask: str | None) -> dict[str, Any]:
        self.db.execute(
            """
            INSERT INTO heart_senders (label, email_hash, email_mask, provisioning_source, enabled)
            VALUES (%s, %s, %s, 'manual', 1)
            ON DUPLICATE KEY UPDATE
                label=VALUES(label),
                email_mask=VALUES(email_mask),
                provisioning_source='manual',
                enabled=1
            """,
            (label, email_hash, email_mask),
        )
        row = self.db.fetch_one(
            "SELECT * FROM heart_senders WHERE email_hash=%s",
            (email_hash,),
        )
        if row is None:
            raise RuntimeError("sender upsert succeeded but row was not found")
        return row

    def upsert_sender_vault(self, *, hs_id: int, blob: VaultBlob) -> None:
        self.db.execute(
            """
            INSERT INTO heart_sender_vault (hs_id, ciphertext, iv, auth_tag, key_version)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                ciphertext=VALUES(ciphertext),
                iv=VALUES(iv),
                auth_tag=VALUES(auth_tag),
                key_version=VALUES(key_version)
            """,
            (hs_id, blob.ciphertext, blob.iv, blob.auth_tag, blob.key_version),
        )

    def create_job(self, *, hr_id: int, requested_hearts: int, source: str = "local", u_id: int | None = None) -> dict[str, Any]:
        hj_id = self.db.insert_get_id(
            """
            INSERT INTO heart_jobs (u_id, hr_id, source, requested_hearts)
            VALUES (%s, %s, %s, %s)
            """,
            (u_id, hr_id, source, requested_hearts),
        )
        row = self.db.fetch_one("SELECT * FROM heart_jobs WHERE hj_id=%s", (hj_id,))
        if row is None:
            raise RuntimeError(f"job insert id={hj_id} succeeded but row was not found")
        return row

    def upsert_receiver_job_vault(self, *, hj_id: int, hr_id: int, blob: VaultBlob) -> None:
        self.db.execute(
            """
            INSERT INTO heart_receiver_job_vault (hj_id, hr_id, ciphertext, iv, auth_tag, key_version)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                ciphertext=VALUES(ciphertext),
                iv=VALUES(iv),
                auth_tag=VALUES(auth_tag),
                key_version=VALUES(key_version),
                credential_status='active',
                purge_after=NULL
            """,
            (hj_id, hr_id, blob.ciphertext, blob.iv, blob.auth_tag, blob.key_version),
        )

    def purge_receiver_job_vault(self, *, hj_id: int) -> int:
        return self.db.execute("DELETE FROM heart_receiver_job_vault WHERE hj_id=%s", (hj_id,))

    def create_round(self, *, hj_id: int, hs_id: int, hr_id: int, sequence_no: int) -> dict[str, Any]:
        hround_id = self.db.insert_get_id(
            """
            INSERT INTO heart_rounds (hj_id, hs_id, hr_id, sequence_no)
            VALUES (%s, %s, %s, %s)
            """,
            (hj_id, hs_id, hr_id, sequence_no),
        )
        row = self.db.fetch_one("SELECT * FROM heart_rounds WHERE hround_id=%s", (hround_id,))
        if row is None:
            raise RuntimeError(f"round insert id={hround_id} succeeded but row was not found")
        return row

    def update_round_step(
        self,
        *,
        hround_id: int,
        current_step: str,
        last_confirmed_step: str | None = None,
        status: str | None = None,
        send_outcome: str | None = None,
        cancel_locked: bool | None = None,
    ) -> None:
        sets = ["current_step=%s"]
        params: list[Any] = [current_step]
        if last_confirmed_step is not None:
            sets.append("last_confirmed_step=%s")
            params.append(last_confirmed_step)
        if status is not None:
            sets.append("status=%s")
            params.append(status)
        if send_outcome is not None:
            sets.append("send_outcome=%s")
            params.append(send_outcome)
        if cancel_locked is not None:
            sets.append("cancel_locked=%s")
            params.append(1 if cancel_locked else 0)
        params.append(hround_id)
        self.db.execute(f"UPDATE heart_rounds SET {', '.join(sets)} WHERE hround_id=%s", params)

    def mark_round_failed(
        self,
        *,
        hround_id: int,
        error_scope: str,
        error_stage: str,
        error_code: str,
        error_message: str,
        recovery_required: bool = False,
    ) -> None:
        self.db.execute(
            """
            UPDATE heart_rounds
            SET status=%s,
                current_step=%s,
                error_scope=%s,
                error_stage=%s,
                error_code=%s,
                error_message=%s,
                recovery_required=%s
            WHERE hround_id=%s
            """,
            (
                "recovery_pending" if recovery_required else "failed",
                "FAILED",
                error_scope,
                error_stage,
                error_code,
                error_message[:500],
                1 if recovery_required else 0,
                hround_id,
            ),
        )

    def log_event(
        self,
        *,
        event_type: str,
        step: str | None = None,
        success: bool = True,
        detail: str | None = None,
        hj_id: int | None = None,
        hround_id: int | None = None,
        hs_id: int | None = None,
        hr_id: int | None = None,
        error_scope: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO heart_events
                (hj_id, hround_id, hs_id, hr_id, event_type, step, success, error_scope, error_code, detail)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                hj_id,
                hround_id,
                hs_id,
                hr_id,
                event_type,
                step,
                1 if success else 0,
                error_scope,
                error_code,
                detail[:1000] if detail else None,
            ),
        )

    def create_worker_run(self, *, hj_id: int, worker_instance: str) -> dict[str, Any]:
        hwr_id = self.db.insert_get_id(
            """
            INSERT INTO heart_worker_runs (hj_id, worker_instance, status)
            VALUES (%s, %s, 'starting')
            """,
            (hj_id, worker_instance),
        )
        row = self.db.fetch_one("SELECT * FROM heart_worker_runs WHERE hwr_id=%s", (hwr_id,))
        if row is None:
            raise RuntimeError(f"worker run insert id={hwr_id} succeeded but row was not found")
        return row

    def list_receivers(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT hr_id, source, email_mask, member_seq, current_lv,
                   login_status, auth_status, session_status, status,
                   last_login_at, last_receive_at, last_error_stage, last_error_code
            FROM heart_receivers
            ORDER BY hr_id DESC
            LIMIT %s
            """,
            (limit,),
        )

    def list_senders(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT hs_id, label, email_mask, enabled, health_status,
                   login_status, auth_status, session_status, member_seq, current_lv,
                   next_available_at, last_login_at, last_send_at, total_rounds, total_pass, total_error,
                   last_error_stage, last_error_code
            FROM heart_senders
            ORDER BY hs_id DESC
            LIMIT %s
            """,
            (limit,),
        )

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT hj_id, source, u_id, hr_id, requested_hearts, completed_hearts,
                   successful_rounds, failed_rounds, status, stop_requested,
                   queued_at, started_at, completed_at, last_error_scope, last_error_code
            FROM heart_jobs
            ORDER BY hj_id DESC
            LIMIT %s
            """,
            (limit,),
        )

    def tail_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT he_id, hj_id, hround_id, hs_id, hr_id, event_type, step,
                   success, error_scope, error_code, detail, created_at
            FROM heart_events
            ORDER BY he_id DESC
            LIMIT %s
            """,
            (limit,),
        )


    def next_round_sequence(self, *, hj_id: int) -> int:
        row = self.db.fetch_one(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS n FROM heart_rounds WHERE hj_id=%s",
            (hj_id,),
        )
        return int((row or {"n": 1}).get("n") or 1)

    def mark_job_running(self, *, hj_id: int) -> None:
        self.db.execute(
            """
            UPDATE heart_jobs
            SET status='running',
                started_at=COALESCE(started_at, current_timestamp()),
                last_error_scope=NULL,
                last_error_code=NULL,
                last_error_message=NULL
            WHERE hj_id=%s
            """,
            (hj_id,),
        )

    def mark_round_passed(self, *, hround_id: int, last_confirmed_step: str = "REMOVE") -> None:
        self.db.execute(
            """
            UPDATE heart_rounds
            SET status='passed',
                current_step='PASS',
                last_confirmed_step=%s,
                heart_amount=1,
                completed_at=current_timestamp(),
                recovery_required=0,
                error_scope=NULL,
                error_stage=NULL,
                error_code=NULL,
                error_message=NULL
            WHERE hround_id=%s
            """,
            (last_confirmed_step, hround_id),
        )

    def mark_one_round_success(self, *, hj_id: int, hs_id: int, hr_id: int) -> None:
        self.db.execute(
            """
            UPDATE heart_senders
            SET last_send_at=current_timestamp(),
                last_success_at=current_timestamp(),
                total_rounds=total_rounds+1,
                total_pass=total_pass+1,
                health_status='ready',
                next_available_at=NULL,
                last_error_stage=NULL,
                last_error_code=NULL,
                last_error_message=NULL
            WHERE hs_id=%s
            """,
            (hs_id,),
        )
        self.db.execute(
            """
            UPDATE heart_receivers
            SET last_receive_at=current_timestamp(),
                last_error_stage=NULL,
                last_error_code=NULL,
                last_error_message=NULL
            WHERE hr_id=%s
            """,
            (hr_id,),
        )
        # IMPORTANT: do not calculate status in the same UPDATE after mutating
        # completed_hearts. MariaDB evaluates single-table SET assignments from
        # left to right, so the old Phase 5.4 statement could see the already
        # incremented value and mark a job completed one round too early.
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT requested_hearts, completed_hearts FROM heart_jobs WHERE hj_id=%s FOR UPDATE",
                        (hj_id,),
                    )
                    row = cur.fetchone() or {}
                    requested = int(row.get("requested_hearts") or 0)
                    current = int(row.get("completed_hearts") or 0)
                    new_completed = min(requested, current + 1) if requested > 0 else current + 1
                    new_status = "completed" if requested > 0 and new_completed >= requested else "running"
                    cur.execute(
                        """
                        UPDATE heart_jobs
                        SET completed_hearts=%s,
                            successful_rounds=successful_rounds+1,
                            status=%s,
                            completed_at=CASE WHEN %s='completed' THEN current_timestamp() ELSE NULL END,
                            last_error_scope=NULL,
                            last_error_code=NULL,
                            last_error_message=NULL
                        WHERE hj_id=%s
                        """,
                        (new_completed, new_status, new_status, hj_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def mark_batch_success(
        self,
        *,
        hj_id: int,
        hr_id: int,
        items: list[dict[str, Any]],
        cooldown_seconds: int,
        cleanup_ok: bool,
    ) -> int:
        """Commit a received batch in one DB transaction.

        Each item must contain hs_id, hround_id, sequence_no and lease_token.
        This avoids opening several MariaDB connections per heart and is the
        production path used by the Phase 5.3 fast engine.
        """
        if not items:
            return 0
        confirmed_step = "REMOVE" if cleanup_ok else "RECEIVE"
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        UPDATE heart_rounds
                        SET status='passed', current_step='PASS', last_confirmed_step=%s,
                            heart_amount=1, completed_at=current_timestamp(), recovery_required=0,
                            error_scope=NULL, error_stage=NULL, error_code=NULL, error_message=NULL
                        WHERE hround_id=%s
                        """,
                        [(confirmed_step, int(x["hround_id"])) for x in items],
                    )
                    cur.executemany(
                        """
                        UPDATE heart_senders
                        SET last_send_at=current_timestamp(), last_success_at=current_timestamp(),
                            total_rounds=total_rounds+1, total_pass=total_pass+1, health_status='ready',
                            next_available_at=NULL, last_error_stage=NULL, last_error_code=NULL, last_error_message=NULL
                        WHERE hs_id=%s
                        """,
                        [(int(x["hs_id"]),) for x in items],
                    )
                    cur.execute(
                        """
                        UPDATE heart_receivers
                        SET last_receive_at=current_timestamp(), last_error_stage=NULL,
                            last_error_code=NULL, last_error_message=NULL
                        WHERE hr_id=%s
                        """,
                        (hr_id,),
                    )
                    count = len(items)
                    # Lock the job row and calculate the next progress from the
                    # pre-update values. This avoids MariaDB left-to-right SET
                    # evaluation marking 50/100 as completed after a 50-heart batch.
                    cur.execute(
                        "SELECT requested_hearts, completed_hearts FROM heart_jobs WHERE hj_id=%s FOR UPDATE",
                        (hj_id,),
                    )
                    job_row = cur.fetchone() or {}
                    requested = int(job_row.get("requested_hearts") or 0)
                    current = int(job_row.get("completed_hearts") or 0)
                    new_completed = min(requested, current + count) if requested > 0 else current + count
                    new_status = "completed" if requested > 0 and new_completed >= requested else "running"
                    cur.execute(
                        """
                        UPDATE heart_jobs
                        SET completed_hearts=%s,
                            successful_rounds=successful_rounds+%s,
                            status=%s,
                            completed_at=CASE WHEN %s='completed' THEN current_timestamp() ELSE NULL END,
                            last_error_scope=NULL, last_error_code=NULL, last_error_message=NULL
                        WHERE hj_id=%s
                        """,
                        (new_completed, count, new_status, new_status, hj_id),
                    )
                    cur.executemany(
                        """
                        INSERT INTO heart_sender_receiver_cooldowns
                            (hs_id, hr_id, last_success_at, eligible_at, success_count)
                        VALUES (%s, %s, current_timestamp(), DATE_ADD(current_timestamp(), INTERVAL %s SECOND), 1)
                        ON DUPLICATE KEY UPDATE
                            last_success_at=current_timestamp(),
                            eligible_at=DATE_ADD(current_timestamp(), INTERVAL %s SECOND),
                            success_count=success_count+1
                        """,
                        [(int(x["hs_id"]), hr_id, cooldown_seconds, cooldown_seconds) for x in items],
                    )
                    cur.executemany(
                        """
                        UPDATE heart_job_sender_attempts
                        SET status='passed', hround_id=%s, sequence_no=%s, completed_at=current_timestamp(),
                            error_scope=NULL, error_stage=NULL, error_code=NULL, error_message=NULL
                        WHERE hj_id=%s AND hs_id=%s
                        """,
                        [(int(x["hround_id"]), int(x["sequence_no"]), hj_id, int(x["hs_id"])) for x in items],
                    )
                    cur.executemany(
                        "DELETE FROM heart_sender_leases WHERE hs_id=%s AND lease_token=%s",
                        [(int(x["hs_id"]), str(x["lease_token"])) for x in items],
                    )
                conn.commit()
                return len(items)
            except Exception:
                conn.rollback()
                raise

    def mark_batch_retryable_pre_send(
        self,
        *,
        hj_id: int,
        items: list[dict[str, Any]],
    ) -> int:
        """Persist ADD/ACCEPT misses but keep Sender retryable in this Local job.

        The round remains an audit record. The unique job-attempt row is marked
        failed with error_stage ADD/ACCEPT; Local eligibility SQL intentionally
        treats those two pre-send stages as retryable. No pair cooldown is added
        and the Sender is never disabled.
        """
        if not items:
            return 0
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    round_rows = [
                        (
                            str(x.get('error_scope') or 'system'),
                            str(x.get('error_stage') or 'UNKNOWN'),
                            str(x.get('error_code') or 'FAILED'),
                            str(x.get('error_message') or 'failed')[:500],
                            int(x.get('hround_id') or 0),
                        )
                        for x in items if int(x.get('hround_id') or 0) > 0
                    ]
                    if round_rows:
                        cur.executemany(
                            """
                            UPDATE heart_rounds
                            SET status='failed', current_step='FAILED',
                                error_scope=%s, error_stage=%s, error_code=%s, error_message=%s,
                                recovery_required=0
                            WHERE hround_id=%s
                            """,
                            round_rows,
                        )
                    cur.executemany(
                        """
                        UPDATE heart_job_sender_attempts
                        SET status='failed', hround_id=%s, sequence_no=%s, completed_at=current_timestamp(),
                            error_scope=%s, error_stage=%s, error_code=%s, error_message=%s
                        WHERE hj_id=%s AND hs_id=%s
                        """,
                        [
                            (
                                int(x.get('hround_id') or 0) or None,
                                int(x.get('sequence_no') or 0) or None,
                                str(x.get('error_scope') or 'system') if str(x.get('error_scope') or 'system') in {'sender','receiver','system'} else 'system',
                                str(x.get('error_stage') or 'UNKNOWN'),
                                str(x.get('error_code') or 'FAILED'),
                                str(x.get('error_message') or 'failed')[:500],
                                hj_id,
                                int(x['hs_id']),
                            )
                            for x in items
                        ],
                    )
                    cur.executemany(
                        """
                        UPDATE heart_senders
                        SET enabled=1, health_status='unknown', next_available_at=NULL,
                            last_error_stage=%s, last_error_code=%s, last_error_message=%s
                        WHERE hs_id=%s
                        """,
                        [
                            (
                                str(x.get('error_stage') or 'UNKNOWN'),
                                str(x.get('error_code') or 'FAILED'),
                                str(x.get('error_message') or 'failed')[:500],
                                int(x['hs_id']),
                            )
                            for x in items
                        ],
                    )
                    lease_rows = [
                        (int(x['hs_id']), str(x.get('lease_token') or ''))
                        for x in items if x.get('lease_token')
                    ]
                    if lease_rows:
                        cur.executemany(
                            "DELETE FROM heart_sender_leases WHERE hs_id=%s AND lease_token=%s",
                            lease_rows,
                        )
                conn.commit()
                return len(items)
            except Exception:
                conn.rollback()
                raise

    def mark_batch_failed(
        self,
        *,
        hj_id: int,
        items: list[dict[str, Any]],
    ) -> int:
        """Persist many failed sender attempts in one transaction.

        Phase 5.4.2 could spend tens of seconds opening several MariaDB
        connections for every transient ACCEPT failure.  Turbo batches may
        contain dozens of such failures, so persist them with executemany and
        release all leases in the same transaction.
        """
        if not items:
            return 0
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    round_rows = [
                        (
                            'recovery_pending' if bool(x.get('recovery_required')) else 'failed',
                            str(x.get('error_scope') or 'system'),
                            str(x.get('error_stage') or 'UNKNOWN'),
                            str(x.get('error_code') or 'FAILED'),
                            str(x.get('error_message') or 'failed')[:500],
                            1 if bool(x.get('recovery_required')) else 0,
                            int(x.get('hround_id') or 0),
                        )
                        for x in items if int(x.get('hround_id') or 0) > 0
                    ]
                    if round_rows:
                        cur.executemany(
                            """
                            UPDATE heart_rounds
                            SET status=%s, current_step='FAILED', error_scope=%s, error_stage=%s,
                                error_code=%s, error_message=%s, recovery_required=%s
                            WHERE hround_id=%s
                            """,
                            round_rows,
                        )

                    cur.executemany(
                        """
                        UPDATE heart_job_sender_attempts
                        SET status='failed', hround_id=%s, sequence_no=%s, completed_at=current_timestamp(),
                            error_scope=%s, error_stage=%s, error_code=%s, error_message=%s
                        WHERE hj_id=%s AND hs_id=%s
                        """,
                        [
                            (
                                int(x.get('hround_id') or 0) or None,
                                int(x.get('sequence_no') or 0) or None,
                                str(x.get('error_scope') or 'system') if str(x.get('error_scope') or 'system') in {'sender','receiver','system'} else 'system',
                                str(x.get('error_stage') or 'UNKNOWN'),
                                str(x.get('error_code') or 'FAILED'),
                                str(x.get('error_message') or 'failed')[:500],
                                hj_id,
                                int(x['hs_id']),
                            )
                            for x in items
                        ],
                    )

                    cur.executemany(
                        """
                        UPDATE heart_senders
                        SET total_rounds=total_rounds+1, total_error=total_error+1,
                            last_error_stage=%s, last_error_code=%s, last_error_message=%s
                        WHERE hs_id=%s
                        """,
                        [
                            (
                                str(x.get('error_stage') or 'UNKNOWN'),
                                str(x.get('error_code') or 'FAILED'),
                                str(x.get('error_message') or 'failed')[:500],
                                int(x['hs_id']),
                            )
                            for x in items
                        ],
                    )

                    last = items[-1]
                    cur.execute(
                        """
                        UPDATE heart_jobs
                        SET failed_rounds=failed_rounds+%s,
                            status=CASE WHEN stop_requested=1 THEN 'stopping' ELSE status END,
                            last_error_scope=%s, last_error_code=%s, last_error_message=%s
                        WHERE hj_id=%s
                        """,
                        (
                            len(items),
                            str(last.get('error_scope') or 'system') if str(last.get('error_scope') or 'system') in {'sender','receiver','system'} else 'system',
                            str(last.get('error_code') or 'FAILED'),
                            str(last.get('error_message') or 'failed')[:500],
                            hj_id,
                        ),
                    )

                    lease_rows = [
                        (int(x['hs_id']), str(x.get('lease_token') or ''))
                        for x in items if x.get('lease_token')
                    ]
                    if lease_rows:
                        cur.executemany(
                            "DELETE FROM heart_sender_leases WHERE hs_id=%s AND lease_token=%s",
                            lease_rows,
                        )
                conn.commit()
                return len(items)
            except Exception:
                conn.rollback()
                raise

    def mark_batch_send_confirmed(self, *, hround_ids: list[int]) -> int:
        """Checkpoint confirmed SEND state for many rounds in one transaction.

        This preserves crash recovery without opening one MariaDB connection per
        sender, which was a hidden Turbo latency source.
        """
        ids = [int(x) for x in hround_ids if int(x or 0) > 0]
        if not ids:
            return 0
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        UPDATE heart_rounds
                        SET current_step='MAILBOX', last_confirmed_step='SEND',
                            send_outcome='confirmed', cancel_locked=1
                        WHERE hround_id=%s
                        """,
                        [(x,) for x in ids],
                    )
                conn.commit()
                return len(ids)
            except Exception:
                conn.rollback()
                raise

    def mark_batch_cooldowns(
        self,
        *,
        hr_id: int,
        hs_ids: list[int],
        cooldown_seconds: int,
    ) -> int:
        ids = [int(x) for x in hs_ids if int(x or 0) > 0]
        if not ids:
            return 0
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO heart_sender_receiver_cooldowns
                            (hs_id, hr_id, last_success_at, eligible_at, success_count)
                        VALUES (%s, %s, current_timestamp(), DATE_ADD(current_timestamp(), INTERVAL %s SECOND), 1)
                        ON DUPLICATE KEY UPDATE
                            last_success_at=current_timestamp(),
                            eligible_at=DATE_ADD(current_timestamp(), INTERVAL %s SECOND),
                            success_count=success_count+1
                        """,
                        [(x, hr_id, cooldown_seconds, cooldown_seconds) for x in ids],
                    )
                conn.commit()
                return len(ids)
            except Exception:
                conn.rollback()
                raise

    def mark_one_round_failed(self, *, hj_id: int, hs_id: int, hr_id: int, error_scope: str, error_code: str, message: str) -> None:
        self.db.execute(
            """
            UPDATE heart_senders
            SET total_rounds=total_rounds+1,
                total_error=total_error+1,
                last_error_stage=%s,
                last_error_code=%s,
                last_error_message=%s
            WHERE hs_id=%s
            """,
            (error_scope, error_code, message[:500], hs_id),
        )
        self.db.execute(
            """
            UPDATE heart_jobs
            SET failed_rounds=failed_rounds+1,
                status=CASE WHEN stop_requested=1 THEN 'stopping' ELSE status END,
                last_error_scope=%s,
                last_error_code=%s,
                last_error_message=%s
            WHERE hj_id=%s
            """,
            (error_scope if error_scope in {"sender", "receiver", "system"} else "system", error_code, message[:500], hj_id),
        )

    def read_dashboard_counts(self) -> dict[str, Any]:
        receivers = self.db.fetch_one("SELECT COUNT(*) AS c FROM heart_receivers") or {"c": 0}
        senders = self.db.fetch_one("SELECT COUNT(*) AS c FROM heart_senders") or {"c": 0}
        jobs = self.db.fetch_all("SELECT status, COUNT(*) AS c FROM heart_jobs GROUP BY status ORDER BY status")
        vaults = self.db.fetch_one(
            """
            SELECT
              (SELECT COUNT(*) FROM heart_sender_vault) AS sender_vaults,
              (SELECT COUNT(*) FROM heart_receiver_job_vault) AS receiver_job_vaults
            """
        ) or {"sender_vaults": 0, "receiver_job_vaults": 0}
        return {
            "receivers": int(receivers["c"]),
            "senders": int(senders["c"]),
            "sender_vaults": int(vaults["sender_vaults"]),
            "receiver_job_vaults": int(vaults["receiver_job_vaults"]),
            "jobs": {str(row["status"]): int(row["c"]) for row in jobs},
        }

    def get_receiver(self, hr_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM heart_receivers WHERE hr_id=%s", (hr_id,))

    def get_sender(self, hs_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM heart_senders WHERE hs_id=%s", (hs_id,))

    def get_job(self, hj_id: int) -> dict[str, Any] | None:
        return self.db.fetch_one("SELECT * FROM heart_jobs WHERE hj_id=%s", (hj_id,))

    def get_sender_vault(self, hs_id: int) -> VaultBlob | None:
        row = self.db.fetch_one(
            "SELECT ciphertext, iv, auth_tag, key_version FROM heart_sender_vault WHERE hs_id=%s",
            (hs_id,),
        )
        if row is None:
            return None
        return VaultBlob(
            ciphertext=bytes(row["ciphertext"]),
            iv=bytes(row["iv"]),
            auth_tag=bytes(row["auth_tag"]),
            key_version=int(row.get("key_version") or 1),
        )

    def list_sender_identity_vaults(self, *, limit: int = 20000) -> dict[int, VaultBlob]:
        """Bulk-load sender identity vault blobs for Local UI display.

        The encrypted payload is decrypted only inside the operator's Local
        process. Passwords are never returned by this repository helper and the
        UI/controller only exposes the email field in memory.
        """
        limit = max(1, min(int(limit), 50000))
        rows = self.db.fetch_all(
            """
            SELECT hs_id, ciphertext, iv, auth_tag, key_version
            FROM heart_sender_vault
            ORDER BY hs_id DESC
            LIMIT %s
            """,
            (limit,),
        )
        out: dict[int, VaultBlob] = {}
        for row in rows:
            try:
                hs_id = int(row["hs_id"])
                out[hs_id] = VaultBlob(
                    ciphertext=bytes(row["ciphertext"]),
                    iv=bytes(row["iv"]),
                    auth_tag=bytes(row["auth_tag"]),
                    key_version=int(row.get("key_version") or 1),
                )
            except Exception:
                continue
        return out

    def get_receiver_job_vault(self, hj_id: int, hr_id: int) -> VaultBlob | None:
        row = self.db.fetch_one(
            """
            SELECT ciphertext, iv, auth_tag, key_version
            FROM heart_receiver_job_vault
            WHERE hj_id=%s AND hr_id=%s AND credential_status='active'
            """,
            (hj_id, hr_id),
        )
        if row is None:
            return None
        return VaultBlob(
            ciphertext=bytes(row["ciphertext"]),
            iv=bytes(row["iv"]),
            auth_tag=bytes(row["auth_tag"]),
            key_version=int(row.get("key_version") or 1),
        )

    def update_receiver_runtime_ok(self, *, hr_id: int, member_seq: int, current_lv: int) -> None:
        self.db.execute(
            """
            UPDATE heart_receivers
            SET login_status='ready', auth_status='valid', session_status='ready',
                member_seq=%s, current_lv=%s, last_login_at=current_timestamp(),
                last_error_stage=NULL, last_error_code=NULL, last_error_message=NULL
            WHERE hr_id=%s
            """,
            (member_seq, current_lv, hr_id),
        )

    def update_sender_runtime_ok(self, *, hs_id: int, member_seq: int, current_lv: int) -> None:
        self.db.execute(
            """
            UPDATE heart_senders
            SET login_status='ready', auth_status='valid', session_status='ready', health_status='ready',
                member_seq=%s, current_lv=%s, last_login_at=current_timestamp(),
                last_error_stage=NULL, last_error_code=NULL, last_error_message=NULL
            WHERE hs_id=%s
            """,
            (member_seq, current_lv, hs_id),
        )

    def update_receiver_runtime_error(self, *, hr_id: int, stage: str, code: str, message: str) -> None:
        self.db.execute(
            """
            UPDATE heart_receivers
            SET login_status='error', auth_status='error', session_status='error',
                last_error_stage=%s, last_error_code=%s, last_error_message=%s
            WHERE hr_id=%s
            """,
            (stage, code, message[:500], hr_id),
        )

    def update_sender_runtime_error(self, *, hs_id: int, stage: str, code: str, message: str) -> None:
        """Record a Local runtime error without auto-disabling the Sender.

        Local Control Center is operator-only. A transient login/template/session
        failure must remain diagnostic state only; the account stays selectable
        for later warm/login retries. Future Web policy can add stricter account
        quarantine separately.
        """
        self.db.execute(
            """
            UPDATE heart_senders
            SET enabled=1,
                login_status='error', auth_status='error', session_status='error', health_status='unknown',
                next_available_at=NULL,
                last_error_stage=%s, last_error_code=%s, last_error_message=%s
            WHERE hs_id=%s
            """,
            (stage, code, message[:500], hs_id),
        )

    def restore_local_auto_disabled_senders(self) -> int:
        """Force the operator-only Local sender pool open.

        Local mode never quarantines or disables a Sender. Historical
        enabled/health/backoff state from older phases is normalized before UI,
        warming and job selection. Diagnostic last_error_* fields are preserved
        so the operator can still see what failed without blocking reuse. Pair
        Sender->Receiver cooldowns remain in their dedicated cooldown table and
        are intentionally not touched here.
        """
        return self.db.execute(
            """
            UPDATE heart_senders
            SET enabled=1,
                health_status='unknown',
                next_available_at=NULL
            WHERE enabled<>1
               OR health_status NOT IN ('ready','unknown')
               OR health_status IS NULL
               OR next_available_at IS NOT NULL
            """
        )

    # ---------------- Phase 5.1 production-local foundation ----------------
    def check_production_tables(self) -> list[dict[str, Any]]:
        from mwoif.storage.production_schema import PRODUCTION_TABLES

        placeholders = ",".join(["%s"] * len(PRODUCTION_TABLES))
        rows = self.db.fetch_all(
            f"""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name IN ({placeholders})
            """,
            PRODUCTION_TABLES,
        )
        found = {str(row["table_name"]) for row in rows}
        return [{"table": name, "exists": name in found} for name in PRODUCTION_TABLES]

    def apply_production_schema(self) -> dict[str, Any]:
        from mwoif.storage.production_schema import DDL_STATEMENTS, MIGRATION_ID, PRODUCTION_TABLES, SETTING_DEFAULTS

        for sql in DDL_STATEMENTS:
            self.db.execute(sql)
        for key, value in SETTING_DEFAULTS.items():
            self.upsert_setting(key, value)
        # Backfill pair cooldowns from recent successful Phase 5.0 rounds so
        # a sender that just sent to the same receiver is not immediately
        # selected again after applying this migration.
        self.db.execute(
            """
            INSERT INTO heart_sender_receiver_cooldowns
                (hs_id, hr_id, last_success_at, eligible_at, success_count)
            SELECT hs_id, hr_id, MAX(COALESCE(completed_at, updated_at)),
                   DATE_ADD(MAX(COALESCE(completed_at, updated_at)), INTERVAL 3600 SECOND),
                   COUNT(*)
            FROM heart_rounds
            WHERE status='passed'
              AND COALESCE(completed_at, updated_at) > DATE_SUB(current_timestamp(), INTERVAL 1 HOUR)
            GROUP BY hs_id, hr_id
            ON DUPLICATE KEY UPDATE
                last_success_at=GREATEST(last_success_at, VALUES(last_success_at)),
                eligible_at=GREATEST(eligible_at, VALUES(eligible_at)),
                success_count=success_count+VALUES(success_count)
            """
        )
        self.db.execute(
            """
            INSERT INTO heart_prod_migrations (migration_id, description)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE applied_at=applied_at
            """,
            (MIGRATION_ID, "Phase 5.1 production local foundation tables"),
        )
        status = self.check_production_tables()
        missing = [row["table"] for row in status if not row["exists"]]
        return {
            "ok": not missing,
            "migration_id": MIGRATION_ID,
            "tables": status,
            "missing": missing,
            "table_count": len(PRODUCTION_TABLES),
            "secretOutput": "NONE",
        }

    def upsert_shared_sender_credential(self, *, credential_name: str, blob: VaultBlob) -> None:
        self.db.execute(
            """
            INSERT INTO heart_sender_shared_credentials
                (credential_name, ciphertext, iv, auth_tag, key_version, active)
            VALUES (%s, %s, %s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE
                ciphertext=VALUES(ciphertext),
                iv=VALUES(iv),
                auth_tag=VALUES(auth_tag),
                key_version=VALUES(key_version),
                active=1
            """,
            (credential_name, blob.ciphertext, blob.iv, blob.auth_tag, blob.key_version),
        )

    def get_shared_sender_credential(self, credential_name: str = "default") -> VaultBlob | None:
        row = self.db.fetch_one(
            """
            SELECT ciphertext, iv, auth_tag, key_version
            FROM heart_sender_shared_credentials
            WHERE credential_name=%s AND active=1
            """,
            (credential_name,),
        )
        if row is None:
            return None
        return VaultBlob(
            ciphertext=bytes(row["ciphertext"]),
            iv=bytes(row["iv"]),
            auth_tag=bytes(row["auth_tag"]),
            key_version=int(row.get("key_version") or 1),
        )

    def production_dashboard(self, *, hj_id: int | None = None) -> dict[str, Any]:
        prod_tables = self.check_production_tables()
        sender_counts = self.db.fetch_one(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled_count,
              SUM(CASE WHEN enabled=1 AND health_status IN ('ready','unknown') THEN 1 ELSE 0 END) AS selectable_count,
              SUM(CASE WHEN enabled=0 OR health_status IN ('error','disabled') THEN 1 ELSE 0 END) AS needs_attention_count
            FROM heart_senders
            """
        ) or {}
        lease_count = self.db.fetch_one("SELECT COUNT(*) AS c FROM heart_sender_leases WHERE leased_until > current_timestamp()") or {"c": 0}
        cooldown_count = self.db.fetch_one("SELECT COUNT(*) AS c FROM heart_sender_receiver_cooldowns WHERE eligible_at > current_timestamp()") or {"c": 0}
        shared = self.db.fetch_one("SELECT COUNT(*) AS c FROM heart_sender_shared_credentials WHERE active=1") or {"c": 0}
        out: dict[str, Any] = {
            "ok": all(bool(row["exists"]) for row in prod_tables),
            "production_tables": prod_tables,
            "senders": {
                "total": int(sender_counts.get("total") or 0),
                "enabled": int(sender_counts.get("enabled_count") or 0),
                "selectable": int(sender_counts.get("selectable_count") or 0),
                "needs_attention": int(sender_counts.get("needs_attention_count") or 0),
                "leased_active": int(lease_count.get("c") or 0),
                "cooldown_pairs_active": int(cooldown_count.get("c") or 0),
                "shared_credentials_active": int(shared.get("c") or 0),
            },
            "secretOutput": "NONE",
        }
        if hj_id is not None:
            rows = self.db.fetch_all(
                """
                SELECT status, COUNT(*) AS c
                FROM heart_job_sender_attempts
                WHERE hj_id=%s
                GROUP BY status
                ORDER BY status
                """,
                (hj_id,),
            )
            out["job_attempts"] = {str(row["status"]): int(row["c"]) for row in rows}
        return out

    def eligible_sender_counts(self, *, hj_id: int, hr_id: int) -> dict[str, Any]:
        row = self.db.fetch_one(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN s.enabled=1 AND s.health_status IN ('ready','unknown') THEN 1 ELSE 0 END) AS base_selectable,
              SUM(CASE WHEN l.hs_id IS NOT NULL AND l.leased_until > current_timestamp() THEN 1 ELSE 0 END) AS leased,
              SUM(CASE WHEN a.hs_id IS NOT NULL THEN 1 ELSE 0 END) AS already_attempted,
              SUM(CASE WHEN c.hs_id IS NOT NULL AND c.eligible_at > current_timestamp() THEN 1 ELSE 0 END) AS cooldown,
              SUM(CASE WHEN s.enabled=1 AND s.health_status IN ('ready','unknown')
                         AND (s.next_available_at IS NULL OR s.next_available_at <= current_timestamp())
                         AND (l.hs_id IS NULL OR l.leased_until <= current_timestamp())
                         AND a.hs_id IS NULL
                         AND (c.hs_id IS NULL OR c.eligible_at <= current_timestamp())
                       THEN 1 ELSE 0 END) AS eligible
            FROM heart_senders s
            LEFT JOIN heart_sender_leases l ON l.hs_id=s.hs_id
            LEFT JOIN heart_job_sender_attempts a
              ON a.hj_id=%s AND a.hs_id=s.hs_id
             AND NOT (a.status='failed' AND a.error_stage IN ('ADD','ACCEPT'))
            LEFT JOIN heart_sender_receiver_cooldowns c ON c.hs_id=s.hs_id AND c.hr_id=%s
            """,
            (hj_id, hr_id),
        ) or {}
        return {k: int(row.get(k) or 0) for k in ("total", "base_selectable", "leased", "already_attempted", "cooldown", "eligible")}

    def list_sender_warm_candidates(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Return enabled sender identities suitable for pre-login warming.

        This list is receiver-agnostic on purpose. Pair cooldown and active lease
        eligibility are enforced later when a job leases a warmed sender.
        """
        limit = max(1, min(int(limit), 5000))
        return self.db.fetch_all(
            """
            SELECT s.hs_id, s.label, s.email_mask, s.health_status, s.last_login_at
            FROM heart_senders s
            JOIN heart_sender_vault v ON v.hs_id=s.hs_id
            LEFT JOIN heart_sender_leases l
              ON l.hs_id=s.hs_id AND l.leased_until > current_timestamp()
            WHERE s.enabled=1
              AND s.health_status IN ('ready','unknown')
              AND (s.next_available_at IS NULL OR s.next_available_at <= current_timestamp())
              AND l.hs_id IS NULL
            ORDER BY (s.last_login_at IS NOT NULL), s.last_login_at ASC, s.hs_id ASC
            LIMIT %s
            """,
            (limit,),
        )

    def list_job_attempted_sender_ids(self, *, hj_id: int, limit: int = 10000) -> list[int]:
        limit = max(1, min(int(limit), 50000))
        rows = self.db.fetch_all(
            """
            SELECT hs_id
            FROM heart_job_sender_attempts
            WHERE hj_id=%s
              AND NOT (status='failed' AND error_stage IN ('ADD','ACCEPT'))
            ORDER BY hjsa_id ASC
            LIMIT %s
            """,
            (int(hj_id), limit),
        )
        return [int(r["hs_id"]) for r in rows if r.get("hs_id") is not None]

    def _lease_sender_select(
        self,
        *,
        hj_id: int,
        hr_id: int,
        worker_id: str,
        lease_seconds: int,
        count: int,
        hs_ids: list[int] | tuple[int, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Lease several eligible senders in one DB transaction.

        INSERT IGNORE + the hs_id primary key keeps this safe when a future web
        worker races another worker for the same sender. No schema change is
        required; this only batches the Phase 5.1 lease/attempt writes.
        """
        count = max(1, min(int(count), 500))
        lease_seconds = max(60, int(lease_seconds))
        wanted = [int(x) for x in (hs_ids or []) if int(x) > 0]
        if wanted:
            # Preserve randomness outside SQL without making FIELD() parameter
            # lists twice as large. The caller shuffles warm ids first.
            wanted = list(dict.fromkeys(wanted))[: max(count * 4, count)]
        import uuid
        batch_worker = f"{worker_id}-{uuid.uuid4().hex[:10]}"
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM heart_sender_leases WHERE leased_until <= current_timestamp()")
                    id_sql = ""
                    params: list[Any] = [hj_id, batch_worker, lease_seconds, hj_id, hr_id]
                    if wanted:
                        placeholders = ",".join(["%s"] * len(wanted))
                        id_sql = f" AND s.hs_id IN ({placeholders})"
                        params.extend(wanted)
                    params.append(count)
                    cur.execute(
                        f"""
                        INSERT IGNORE INTO heart_sender_leases
                            (hs_id, hj_id, worker_id, lease_token, leased_until)
                        SELECT s.hs_id, %s, %s, UUID(),
                               DATE_ADD(current_timestamp(), INTERVAL %s SECOND)
                        FROM heart_senders s
                        LEFT JOIN heart_sender_leases l
                          ON l.hs_id=s.hs_id AND l.leased_until > current_timestamp()
                        LEFT JOIN heart_job_sender_attempts a
                          ON a.hj_id=%s AND a.hs_id=s.hs_id
                         AND NOT (a.status='failed' AND a.error_stage IN ('ADD','ACCEPT'))
                        LEFT JOIN heart_sender_receiver_cooldowns c
                          ON c.hs_id=s.hs_id AND c.hr_id=%s
                         AND c.eligible_at > current_timestamp()
                        WHERE s.enabled=1
                          AND s.health_status IN ('ready','unknown')
                          AND (s.next_available_at IS NULL OR s.next_available_at <= current_timestamp())
                          AND l.hs_id IS NULL
                          AND a.hs_id IS NULL
                          AND c.hs_id IS NULL
                          {id_sql}
                        ORDER BY RAND()
                        LIMIT %s
                        """,
                        tuple(params),
                    )
                    cur.execute(
                        """
                        SELECT l.hs_id, l.hj_id, l.worker_id, l.lease_token, l.leased_until,
                               s.label, s.email_mask, s.health_status, s.enabled
                        FROM heart_sender_leases l
                        JOIN heart_senders s ON s.hs_id=l.hs_id
                        WHERE l.hj_id=%s AND l.worker_id=%s
                        ORDER BY l.hs_id
                        """,
                        (hj_id, batch_worker),
                    )
                    rows = [dict(x) for x in cur.fetchall()]
                    if rows:
                        cur.executemany(
                            """
                            INSERT INTO heart_job_sender_attempts
                                (hj_id, hs_id, hr_id, status, lease_token)
                            VALUES (%s, %s, %s, 'leased', %s)
                            ON DUPLICATE KEY UPDATE
                                status='leased', lease_token=VALUES(lease_token),
                                hround_id=NULL, sequence_no=NULL,
                                started_at=NULL, completed_at=NULL,
                                error_scope=NULL, error_stage=NULL, error_code=NULL, error_message=NULL
                            """,
                            [(hj_id, int(r["hs_id"]), hr_id, str(r["lease_token"])) for r in rows],
                        )
                conn.commit()
                return rows
            except Exception:
                conn.rollback()
                raise

    def lease_sender_ids(
        self, *, hj_id: int, hr_id: int, hs_ids: list[int] | tuple[int, ...],
        worker_id: str, lease_seconds: int, count: int | None = None,
    ) -> list[dict[str, Any]]:
        ids = [int(x) for x in hs_ids if int(x) > 0]
        if not ids:
            return []
        return self._lease_sender_select(
            hj_id=hj_id, hr_id=hr_id, worker_id=worker_id,
            lease_seconds=lease_seconds, count=int(count or len(ids)), hs_ids=ids,
        )

    def lease_random_senders(
        self, *, hj_id: int, hr_id: int, count: int, worker_id: str, lease_seconds: int,
    ) -> list[dict[str, Any]]:
        return self._lease_sender_select(
            hj_id=hj_id, hr_id=hr_id, worker_id=worker_id,
            lease_seconds=lease_seconds, count=count, hs_ids=None,
        )

    def create_rounds_batch(
        self, *, hj_id: int, hr_id: int, items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Create round/checkpoint rows for a batch on one MariaDB connection."""
        if not items:
            return []
        out: list[dict[str, Any]] = []
        with self.db.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COALESCE(MAX(sequence_no),0)+1 AS n FROM heart_rounds WHERE hj_id=%s", (hj_id,))
                    row = cur.fetchone() or {"n": 1}
                    seq = int(row.get("n") or 1)
                    for offset, item in enumerate(items):
                        hs_id = int(item["hs_id"])
                        lease_token = str(item["lease_token"])
                        sequence_no = seq + offset
                        cur.execute(
                            """
                            INSERT INTO heart_rounds
                                (hj_id, hs_id, hr_id, sequence_no, status, current_step, last_confirmed_step, send_outcome, started_at)
                            VALUES (%s,%s,%s,%s,'running','ADD','NONE','not_started',current_timestamp())
                            """,
                            (hj_id, hs_id, hr_id, sequence_no),
                        )
                        hround_id = int(cur.lastrowid or 0)
                        cur.execute(
                            """
                            UPDATE heart_job_sender_attempts
                            SET status='running', hround_id=%s, sequence_no=%s,
                                started_at=COALESCE(started_at,current_timestamp())
                            WHERE hj_id=%s AND hs_id=%s
                            """,
                            (hround_id, sequence_no, hj_id, hs_id),
                        )
                        cur.execute(
                            "UPDATE heart_sender_leases SET hround_id=%s WHERE hs_id=%s AND lease_token=%s",
                            (hround_id, hs_id, lease_token),
                        )
                        out.append({"hs_id": hs_id, "hround_id": hround_id, "sequence_no": sequence_no})
                conn.commit()
                return out
            except Exception:
                conn.rollback()
                raise

    def cleanup_expired_leases(self) -> int:
        return self.db.execute("DELETE FROM heart_sender_leases WHERE leased_until <= current_timestamp()")

    def lease_random_sender(self, *, hj_id: int, hr_id: int, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        import uuid

        self.cleanup_expired_leases()
        token = str(uuid.uuid4())
        affected = self.db.execute(
            """
            INSERT INTO heart_sender_leases (hs_id, hj_id, worker_id, lease_token, leased_until)
            SELECT s.hs_id, %s, %s, %s, DATE_ADD(current_timestamp(), INTERVAL %s SECOND)
            FROM heart_senders s
            LEFT JOIN heart_sender_leases l ON l.hs_id=s.hs_id AND l.leased_until > current_timestamp()
            LEFT JOIN heart_job_sender_attempts a
              ON a.hj_id=%s AND a.hs_id=s.hs_id
             AND NOT (a.status='failed' AND a.error_stage IN ('ADD','ACCEPT'))
            LEFT JOIN heart_sender_receiver_cooldowns c ON c.hs_id=s.hs_id AND c.hr_id=%s AND c.eligible_at > current_timestamp()
            WHERE s.enabled=1
              AND s.health_status IN ('ready','unknown')
              AND (s.next_available_at IS NULL OR s.next_available_at <= current_timestamp())
              AND l.hs_id IS NULL
              AND a.hs_id IS NULL
              AND c.hs_id IS NULL
            ORDER BY RAND()
            LIMIT 1
            """,
            (hj_id, worker_id, token, lease_seconds, hj_id, hr_id),
        )
        if affected <= 0:
            return None
        row = self.db.fetch_one(
            """
            SELECT l.hs_id, l.hj_id, l.worker_id, l.lease_token, l.leased_until,
                   s.label, s.email_mask, s.health_status, s.enabled
            FROM heart_sender_leases l
            JOIN heart_senders s ON s.hs_id=l.hs_id
            WHERE l.lease_token=%s
            """,
            (token,),
        )
        if not row:
            return None
        self.db.execute(
            """
            INSERT INTO heart_job_sender_attempts (hj_id, hs_id, hr_id, status, lease_token)
            VALUES (%s, %s, %s, 'leased', %s)
            ON DUPLICATE KEY UPDATE
                status='leased', lease_token=VALUES(lease_token),
                hround_id=NULL, sequence_no=NULL,
                started_at=NULL, completed_at=NULL,
                error_scope=NULL, error_stage=NULL, error_code=NULL, error_message=NULL
            """,
            (hj_id, int(row["hs_id"]), hr_id, token),
        )
        return row

    def attach_lease_round(self, *, hs_id: int, lease_token: str, hround_id: int) -> None:
        self.db.execute(
            "UPDATE heart_sender_leases SET hround_id=%s WHERE hs_id=%s AND lease_token=%s",
            (hround_id, hs_id, lease_token),
        )
        self.db.execute(
            "UPDATE heart_job_sender_attempts SET hround_id=%s WHERE hs_id=%s AND lease_token=%s",
            (hround_id, hs_id, lease_token),
        )

    def mark_sender_attempt_running(self, *, hj_id: int, hs_id: int, hround_id: int | None, sequence_no: int | None) -> None:
        self.db.execute(
            """
            UPDATE heart_job_sender_attempts
            SET status='running', hround_id=%s, sequence_no=%s, started_at=COALESCE(started_at, current_timestamp())
            WHERE hj_id=%s AND hs_id=%s
            """,
            (hround_id, sequence_no, hj_id, hs_id),
        )

    def mark_sender_attempt_passed(self, *, hj_id: int, hs_id: int, hround_id: int | None, sequence_no: int | None) -> None:
        self.db.execute(
            """
            UPDATE heart_job_sender_attempts
            SET status='passed', hround_id=%s, sequence_no=%s, completed_at=current_timestamp(),
                error_scope=NULL, error_stage=NULL, error_code=NULL, error_message=NULL
            WHERE hj_id=%s AND hs_id=%s
            """,
            (hround_id, sequence_no, hj_id, hs_id),
        )

    def mark_sender_attempt_failed(
        self,
        *,
        hj_id: int,
        hs_id: int,
        hround_id: int | None,
        sequence_no: int | None,
        error_scope: str,
        error_stage: str,
        error_code: str,
        error_message: str,
    ) -> None:
        self.db.execute(
            """
            UPDATE heart_job_sender_attempts
            SET status='failed', hround_id=%s, sequence_no=%s, completed_at=current_timestamp(),
                error_scope=%s, error_stage=%s, error_code=%s, error_message=%s
            WHERE hj_id=%s AND hs_id=%s
            """,
            (hround_id, sequence_no, error_scope if error_scope in {"sender", "receiver", "system"} else "system", error_stage, error_code, error_message[:500], hj_id, hs_id),
        )

    def release_sender_lease(self, *, hs_id: int, lease_token: str | None = None) -> int:
        if lease_token:
            return self.db.execute("DELETE FROM heart_sender_leases WHERE hs_id=%s AND lease_token=%s", (hs_id, lease_token))
        return self.db.execute("DELETE FROM heart_sender_leases WHERE hs_id=%s", (hs_id,))

    def mark_sender_receiver_cooldown(self, *, hs_id: int, hr_id: int, cooldown_seconds: int) -> None:
        self.db.execute(
            """
            INSERT INTO heart_sender_receiver_cooldowns (hs_id, hr_id, last_success_at, eligible_at, success_count)
            VALUES (%s, %s, current_timestamp(), DATE_ADD(current_timestamp(), INTERVAL %s SECOND), 1)
            ON DUPLICATE KEY UPDATE
                last_success_at=current_timestamp(),
                eligible_at=DATE_ADD(current_timestamp(), INTERVAL %s SECOND),
                success_count=success_count+1
            """,
            (hs_id, hr_id, cooldown_seconds, cooldown_seconds),
        )

    def disable_sender_needs_attention(self, *, hs_id: int, stage: str, code: str, message: str) -> None:
        """Local compatibility shim: record diagnostics, never disable Sender.

        Kept under the historical method name so older Local call sites remain
        compatible. Future Web policy may implement quarantine separately.
        """
        self.db.execute(
            """
            UPDATE heart_senders
            SET enabled=1,
                health_status='unknown',
                next_available_at=NULL,
                login_status=CASE WHEN %s LIKE 'LOGIN%%' OR %s LIKE 'HTTP_TEMPLATE%%' THEN 'error' ELSE login_status END,
                last_error_stage=%s,
                last_error_code=%s,
                last_error_message=%s
            WHERE hs_id=%s
            """,
            (stage, stage, stage, code, message[:500], hs_id),
        )

    def mark_job_paused(self, *, hj_id: int, error_scope: str, error_code: str, message: str) -> None:
        self.db.execute(
            """
            UPDATE heart_jobs
            SET status='paused',
                last_error_scope=%s,
                last_error_code=%s,
                last_error_message=%s
            WHERE hj_id=%s
            """,
            (error_scope if error_scope in {"sender", "receiver", "system"} else "system", error_code, message[:500], hj_id),
        )

    def request_job_stop(self, *, hj_id: int) -> None:
        self.db.execute(
            """
            UPDATE heart_jobs
            SET stop_requested=1,
                stop_requested_at=current_timestamp(),
                status=CASE WHEN status='running' THEN 'stopping' ELSE status END
            WHERE hj_id=%s
            """,
            (hj_id,),
        )

    def resume_job(self, *, hj_id: int) -> None:
        self.db.execute(
            """
            UPDATE heart_jobs
            SET stop_requested=0,
                stop_requested_at=NULL,
                status=CASE
                    WHEN completed_hearts >= requested_hearts THEN 'completed'
                    WHEN completed_hearts < requested_hearts AND status IN ('paused','stopping','queued','completed') THEN 'queued'
                    ELSE status
                END,
                completed_at=CASE
                    WHEN completed_hearts >= requested_hearts THEN completed_at
                    ELSE NULL
                END
            WHERE hj_id=%s
            """,
            (hj_id,),
        )

    def mark_job_stopped_after_batch(self, *, hj_id: int) -> None:
        self.db.execute(
            """
            UPDATE heart_jobs
            SET status=CASE WHEN completed_hearts >= requested_hearts THEN 'completed' ELSE 'paused' END,
                last_error_scope=NULL,
                last_error_code='USER_STOPPED',
                last_error_message='Stopped after active batch completed'
            WHERE hj_id=%s
            """,
            (hj_id,),
        )

    def mark_job_cancelled(self, *, hj_id: int, message: str = "cancelled before run") -> None:
        self.db.execute(
            """
            UPDATE heart_jobs
            SET status='cancelled',
                stop_requested=1,
                stop_requested_at=current_timestamp(),
                completed_at=current_timestamp(),
                last_error_scope=NULL,
                last_error_code=NULL,
                last_error_message=%s
            WHERE hj_id=%s
            """,
            (message[:500], hj_id),
        )
