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

    def mark_round_passed(self, *, hround_id: int) -> None:
        self.db.execute(
            """
            UPDATE heart_rounds
            SET status='passed',
                current_step='PASS',
                last_confirmed_step='REMOVE',
                heart_amount=1,
                completed_at=current_timestamp(),
                recovery_required=0,
                error_scope=NULL,
                error_stage=NULL,
                error_code=NULL,
                error_message=NULL
            WHERE hround_id=%s
            """,
            (hround_id,),
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
        self.db.execute(
            """
            UPDATE heart_jobs
            SET completed_hearts=completed_hearts+1,
                successful_rounds=successful_rounds+1,
                status=CASE
                    WHEN completed_hearts+1 >= requested_hearts THEN 'completed'
                    ELSE 'running'
                END,
                completed_at=CASE
                    WHEN completed_hearts+1 >= requested_hearts THEN current_timestamp()
                    ELSE completed_at
                END,
                last_error_scope=NULL,
                last_error_code=NULL,
                last_error_message=NULL
            WHERE hj_id=%s
            """,
            (hj_id,),
        )

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
        self.db.execute(
            """
            UPDATE heart_senders
            SET login_status='error', auth_status='error', session_status='error', health_status='error',
                last_error_stage=%s, last_error_code=%s, last_error_message=%s
            WHERE hs_id=%s
            """,
            (stage, code, message[:500], hs_id),
        )
