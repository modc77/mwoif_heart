-- M WOIF HEART V3 Phase 5.1 Production Local Foundation
-- Scope: add heart_* production-local tables only. Existing web/license tables are not modified.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS heart_prod_migrations (
      migration_id varchar(96) NOT NULL,
      description varchar(255) NOT NULL,
      applied_at datetime NOT NULL DEFAULT current_timestamp(),
      PRIMARY KEY (migration_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS heart_sender_shared_credentials (
      hssc_id bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
      credential_name varchar(64) NOT NULL DEFAULT 'default',
      ciphertext longblob NOT NULL,
      iv varbinary(12) NOT NULL,
      auth_tag varbinary(16) NOT NULL,
      key_version smallint(5) UNSIGNED NOT NULL DEFAULT 1,
      active tinyint(1) NOT NULL DEFAULT 1,
      created_at datetime NOT NULL DEFAULT current_timestamp(),
      updated_at datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
      PRIMARY KEY (hssc_id),
      UNIQUE KEY uq_heart_sender_shared_credential_name (credential_name),
      KEY idx_heart_sender_shared_active (active, credential_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS heart_sender_leases (
      hs_id bigint(20) UNSIGNED NOT NULL,
      hj_id bigint(20) UNSIGNED NOT NULL,
      hround_id bigint(20) UNSIGNED DEFAULT NULL,
      worker_id varchar(96) NOT NULL,
      lease_token char(36) NOT NULL,
      leased_until datetime NOT NULL,
      created_at datetime NOT NULL DEFAULT current_timestamp(),
      updated_at datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
      PRIMARY KEY (hs_id),
      UNIQUE KEY uq_heart_sender_leases_token (lease_token),
      KEY idx_heart_sender_leases_job (hj_id, leased_until),
      KEY idx_heart_sender_leases_expiry (leased_until),
      CONSTRAINT fk_heart_sender_leases_sender
        FOREIGN KEY (hs_id) REFERENCES heart_senders (hs_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
      CONSTRAINT fk_heart_sender_leases_job
        FOREIGN KEY (hj_id) REFERENCES heart_jobs (hj_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
      CONSTRAINT fk_heart_sender_leases_round
        FOREIGN KEY (hround_id) REFERENCES heart_rounds (hround_id)
        ON DELETE SET NULL ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS heart_sender_receiver_cooldowns (
      hsrc_id bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
      hs_id bigint(20) UNSIGNED NOT NULL,
      hr_id bigint(20) UNSIGNED NOT NULL,
      last_success_at datetime NOT NULL,
      eligible_at datetime NOT NULL,
      success_count bigint(20) UNSIGNED NOT NULL DEFAULT 1,
      created_at datetime NOT NULL DEFAULT current_timestamp(),
      updated_at datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
      PRIMARY KEY (hsrc_id),
      UNIQUE KEY uq_heart_sender_receiver_pair (hs_id, hr_id),
      KEY idx_heart_cooldown_receiver_eligible (hr_id, eligible_at),
      KEY idx_heart_cooldown_sender_eligible (hs_id, eligible_at),
      CONSTRAINT fk_heart_cooldown_sender
        FOREIGN KEY (hs_id) REFERENCES heart_senders (hs_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
      CONSTRAINT fk_heart_cooldown_receiver
        FOREIGN KEY (hr_id) REFERENCES heart_receivers (hr_id)
        ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS heart_job_sender_attempts (
      hjsa_id bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
      hj_id bigint(20) UNSIGNED NOT NULL,
      hs_id bigint(20) UNSIGNED NOT NULL,
      hr_id bigint(20) UNSIGNED NOT NULL,
      hround_id bigint(20) UNSIGNED DEFAULT NULL,
      sequence_no bigint(20) UNSIGNED DEFAULT NULL,
      status enum('leased','running','passed','failed','skipped','cooldown') NOT NULL DEFAULT 'leased',
      lease_token char(36) DEFAULT NULL,
      error_scope enum('sender','receiver','system') DEFAULT NULL,
      error_stage varchar(64) DEFAULT NULL,
      error_code varchar(96) DEFAULT NULL,
      error_message varchar(500) DEFAULT NULL,
      started_at datetime DEFAULT NULL,
      completed_at datetime DEFAULT NULL,
      created_at datetime NOT NULL DEFAULT current_timestamp(),
      updated_at datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
      PRIMARY KEY (hjsa_id),
      UNIQUE KEY uq_heart_job_sender_once (hj_id, hs_id),
      KEY idx_heart_attempt_job_status (hj_id, status, created_at),
      KEY idx_heart_attempt_sender (hs_id, created_at),
      KEY idx_heart_attempt_round (hround_id),
      CONSTRAINT fk_heart_attempt_job
        FOREIGN KEY (hj_id) REFERENCES heart_jobs (hj_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
      CONSTRAINT fk_heart_attempt_sender
        FOREIGN KEY (hs_id) REFERENCES heart_senders (hs_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
      CONSTRAINT fk_heart_attempt_receiver
        FOREIGN KEY (hr_id) REFERENCES heart_receivers (hr_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
      CONSTRAINT fk_heart_attempt_round
        FOREIGN KEY (hround_id) REFERENCES heart_rounds (hround_id)
        ON DELETE SET NULL ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS heart_job_runtime_locks (
      hj_id bigint(20) UNSIGNED NOT NULL,
      worker_id varchar(96) NOT NULL,
      lock_token char(36) NOT NULL,
      locked_until datetime NOT NULL,
      heartbeat_at datetime NOT NULL DEFAULT current_timestamp(),
      created_at datetime NOT NULL DEFAULT current_timestamp(),
      updated_at datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
      PRIMARY KEY (hj_id),
      UNIQUE KEY uq_heart_job_runtime_locks_token (lock_token),
      KEY idx_heart_job_runtime_locks_expiry (locked_until),
      CONSTRAINT fk_heart_job_runtime_locks_job
        FOREIGN KEY (hj_id) REFERENCES heart_jobs (hj_id)
        ON DELETE CASCADE ON UPDATE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Backfill 1-hour cooldowns from recent successful Phase 5.0 rounds.
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
    success_count=success_count+VALUES(success_count);

INSERT INTO heart_settings (setting_key, setting_value) VALUES ('production.foundation.version', '5.1') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
INSERT INTO heart_settings (setting_key, setting_value) VALUES ('sender.selection.mode', 'random') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
INSERT INTO heart_settings (setting_key, setting_value) VALUES ('sender.cooldown.seconds', '3600') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
INSERT INTO heart_settings (setting_key, setting_value) VALUES ('sender.lease.seconds', '900') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
INSERT INTO heart_settings (setting_key, setting_value) VALUES ('sender.workers.default', '10') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
INSERT INTO heart_settings (setting_key, setting_value) VALUES ('receiver.actor.mode', 'single') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
INSERT INTO heart_settings (setting_key, setting_value) VALUES ('login.provider.primary', 'direct-http-exact-template') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);

INSERT INTO heart_prod_migrations (migration_id, description) VALUES ('20260911_p51_prod_foundation', 'Phase 5.1 production local foundation tables') ON DUPLICATE KEY UPDATE applied_at=applied_at;
