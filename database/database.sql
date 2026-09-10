-- M WOIF HEART V3
-- Heart subsystem tables for existing database: mwoifmod_license_keys09
-- Target: MariaDB 10.6+ / InnoDB / utf8mb4
-- Scope: create only heart_* tables. Existing website/license tables are not modified.

SET NAMES utf8mb4;
SET time_zone = '+00:00';
START TRANSACTION;

CREATE TABLE IF NOT EXISTS `heart_receivers` (
  `hr_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `u_id` bigint(20) UNSIGNED DEFAULT NULL,
  `source` enum('local','web') NOT NULL DEFAULT 'local',
  `email_hash` char(64) NOT NULL,
  `email_mask` varchar(190) DEFAULT NULL,
  `member_seq` bigint(20) UNSIGNED DEFAULT NULL,
  `current_lv` int(10) UNSIGNED DEFAULT NULL,
  `login_status` enum('unknown','pending','ready','error') NOT NULL DEFAULT 'unknown',
  `auth_status` enum('unknown','missing','valid','expired','error') NOT NULL DEFAULT 'unknown',
  `session_status` enum('unknown','missing','ready','invalid','error') NOT NULL DEFAULT 'unknown',
  `status` enum('active','disabled') NOT NULL DEFAULT 'active',
  `last_login_at` datetime DEFAULT NULL,
  `last_receive_at` datetime DEFAULT NULL,
  `last_error_stage` varchar(64) DEFAULT NULL,
  `last_error_code` varchar(96) DEFAULT NULL,
  `last_error_message` varchar(500) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`hr_id`),
  UNIQUE KEY `uq_heart_receivers_email_hash` (`email_hash`),
  KEY `idx_heart_receivers_user` (`u_id`),
  KEY `idx_heart_receivers_status` (`status`,`login_status`,`auth_status`,`session_status`),
  KEY `idx_heart_receivers_last_receive` (`last_receive_at`),
  CONSTRAINT `fk_heart_receivers_user`
    FOREIGN KEY (`u_id`) REFERENCES `users` (`u_id`)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_senders` (
  `hs_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `label` varchar(80) NOT NULL,
  `email_hash` char(64) NOT NULL,
  `email_mask` varchar(190) DEFAULT NULL,
  `provisioning_source` varchar(16) NOT NULL DEFAULT 'manual',
  `enabled` tinyint(1) NOT NULL DEFAULT 1,
  `health_status` enum('unknown','ready','cooldown','error','disabled') NOT NULL DEFAULT 'unknown',
  `login_status` enum('unknown','pending','ready','error') NOT NULL DEFAULT 'unknown',
  `auth_status` enum('unknown','missing','valid','expired','error') NOT NULL DEFAULT 'unknown',
  `session_status` enum('unknown','missing','ready','invalid','error') NOT NULL DEFAULT 'unknown',
  `member_seq` bigint(20) UNSIGNED DEFAULT NULL,
  `current_lv` int(10) UNSIGNED DEFAULT NULL,
  `next_available_at` datetime DEFAULT NULL,
  `last_login_at` datetime DEFAULT NULL,
  `last_send_at` datetime DEFAULT NULL,
  `last_success_at` datetime DEFAULT NULL,
  `total_rounds` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  `total_pass` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  `total_error` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  `last_error_stage` varchar(64) DEFAULT NULL,
  `last_error_code` varchar(96) DEFAULT NULL,
  `last_error_message` varchar(500) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`hs_id`),
  UNIQUE KEY `uq_heart_senders_email_hash` (`email_hash`),
  UNIQUE KEY `uq_heart_senders_label` (`label`),
  KEY `idx_heart_senders_ready` (`enabled`,`health_status`,`next_available_at`),
  KEY `idx_heart_senders_status` (`login_status`,`auth_status`,`session_status`),
  KEY `idx_heart_senders_last_success` (`last_success_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_sender_vault` (
  `hsv_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `hs_id` bigint(20) UNSIGNED NOT NULL,
  `ciphertext` longblob NOT NULL,
  `iv` varbinary(12) NOT NULL,
  `auth_tag` varbinary(16) NOT NULL,
  `key_version` smallint(5) UNSIGNED NOT NULL DEFAULT 1,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`hsv_id`),
  UNIQUE KEY `uq_heart_sender_vault_sender` (`hs_id`),
  KEY `idx_heart_sender_vault_version` (`key_version`),
  CONSTRAINT `fk_heart_sender_vault_sender`
    FOREIGN KEY (`hs_id`) REFERENCES `heart_senders` (`hs_id`)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_jobs` (
  `hj_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `u_id` bigint(20) UNSIGNED DEFAULT NULL,
  `hr_id` bigint(20) UNSIGNED NOT NULL,
  `source` enum('local','web') NOT NULL DEFAULT 'local',
  `requested_hearts` bigint(20) UNSIGNED NOT NULL,
  `completed_hearts` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  `successful_rounds` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  `failed_rounds` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  `status` enum('queued','running','stopping','paused','completed','failed','cancelled') NOT NULL DEFAULT 'queued',
  `stop_requested` tinyint(1) NOT NULL DEFAULT 0,
  `stop_requested_at` datetime DEFAULT NULL,
  `last_error_scope` enum('sender','receiver','system') DEFAULT NULL,
  `last_error_code` varchar(96) DEFAULT NULL,
  `last_error_message` varchar(500) DEFAULT NULL,
  `queued_at` datetime NOT NULL DEFAULT current_timestamp(),
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`hj_id`),
  KEY `idx_heart_jobs_user` (`u_id`,`created_at`),
  KEY `idx_heart_jobs_receiver` (`hr_id`,`created_at`),
  KEY `idx_heart_jobs_status` (`status`,`created_at`),
  KEY `idx_heart_jobs_queue` (`status`,`queued_at`,`hj_id`),
  CONSTRAINT `fk_heart_jobs_user`
    FOREIGN KEY (`u_id`) REFERENCES `users` (`u_id`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_jobs_receiver`
    FOREIGN KEY (`hr_id`) REFERENCES `heart_receivers` (`hr_id`)
    ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_receiver_job_vault` (
  `hrjv_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `hj_id` bigint(20) UNSIGNED NOT NULL,
  `hr_id` bigint(20) UNSIGNED NOT NULL,
  `ciphertext` longblob NOT NULL,
  `iv` varbinary(12) NOT NULL,
  `auth_tag` varbinary(16) NOT NULL,
  `key_version` smallint(5) UNSIGNED NOT NULL DEFAULT 1,
  `credential_status` enum('active','purge_pending') NOT NULL DEFAULT 'active',
  `purge_after` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`hrjv_id`),
  UNIQUE KEY `uq_heart_receiver_job_vault_job` (`hj_id`),
  KEY `idx_heart_receiver_job_vault_receiver` (`hr_id`),
  KEY `idx_heart_receiver_job_vault_purge` (`credential_status`,`purge_after`),
  CONSTRAINT `fk_heart_receiver_job_vault_job`
    FOREIGN KEY (`hj_id`) REFERENCES `heart_jobs` (`hj_id`)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_receiver_job_vault_receiver`
    FOREIGN KEY (`hr_id`) REFERENCES `heart_receivers` (`hr_id`)
    ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_rounds` (
  `hround_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `hj_id` bigint(20) UNSIGNED NOT NULL,
  `hs_id` bigint(20) UNSIGNED NOT NULL,
  `hr_id` bigint(20) UNSIGNED NOT NULL,
  `sequence_no` bigint(20) UNSIGNED NOT NULL,
  `status` enum('created','running','passed','failed','recovery_pending','cancelled') NOT NULL DEFAULT 'created',
  `current_step` varchar(64) NOT NULL DEFAULT 'CREATED',
  `last_confirmed_step` varchar(64) DEFAULT NULL,
  `send_outcome` enum('not_started','confirmed','unknown','failed') NOT NULL DEFAULT 'not_started',
  `heart_amount` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  `cancel_locked` tinyint(1) NOT NULL DEFAULT 0,
  `retry_count` int(10) UNSIGNED NOT NULL DEFAULT 0,
  `recovery_required` tinyint(1) NOT NULL DEFAULT 0,
  `error_scope` enum('sender','receiver','system') DEFAULT NULL,
  `error_stage` varchar(64) DEFAULT NULL,
  `error_code` varchar(96) DEFAULT NULL,
  `error_message` varchar(500) DEFAULT NULL,
  `started_at` datetime DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`hround_id`),
  UNIQUE KEY `uq_heart_round_job_sequence` (`hj_id`,`sequence_no`),
  KEY `idx_heart_round_job_status` (`hj_id`,`status`,`sequence_no`),
  KEY `idx_heart_round_sender` (`hs_id`,`created_at`),
  KEY `idx_heart_round_receiver` (`hr_id`,`created_at`),
  KEY `idx_heart_round_recovery` (`recovery_required`,`status`,`updated_at`),
  KEY `idx_heart_round_error` (`error_scope`,`error_stage`,`error_code`,`created_at`),
  CONSTRAINT `fk_heart_round_job`
    FOREIGN KEY (`hj_id`) REFERENCES `heart_jobs` (`hj_id`)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_round_sender`
    FOREIGN KEY (`hs_id`) REFERENCES `heart_senders` (`hs_id`)
    ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_round_receiver`
    FOREIGN KEY (`hr_id`) REFERENCES `heart_receivers` (`hr_id`)
    ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_events` (
  `he_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `hj_id` bigint(20) UNSIGNED DEFAULT NULL,
  `hround_id` bigint(20) UNSIGNED DEFAULT NULL,
  `hs_id` bigint(20) UNSIGNED DEFAULT NULL,
  `hr_id` bigint(20) UNSIGNED DEFAULT NULL,
  `event_type` varchar(80) NOT NULL,
  `step` varchar(64) DEFAULT NULL,
  `success` tinyint(1) NOT NULL DEFAULT 1,
  `error_scope` enum('sender','receiver','system') DEFAULT NULL,
  `error_code` varchar(96) DEFAULT NULL,
  `detail` varchar(1000) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`he_id`),
  KEY `idx_heart_events_job_time` (`hj_id`,`created_at`),
  KEY `idx_heart_events_round_time` (`hround_id`,`created_at`),
  KEY `idx_heart_events_sender_time` (`hs_id`,`created_at`),
  KEY `idx_heart_events_receiver_time` (`hr_id`,`created_at`),
  KEY `idx_heart_events_error_time` (`error_code`,`created_at`),
  KEY `idx_heart_events_type_time` (`event_type`,`created_at`),
  CONSTRAINT `fk_heart_events_job`
    FOREIGN KEY (`hj_id`) REFERENCES `heart_jobs` (`hj_id`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_events_round`
    FOREIGN KEY (`hround_id`) REFERENCES `heart_rounds` (`hround_id`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_events_sender`
    FOREIGN KEY (`hs_id`) REFERENCES `heart_senders` (`hs_id`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_events_receiver`
    FOREIGN KEY (`hr_id`) REFERENCES `heart_receivers` (`hr_id`)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_worker_runs` (
  `hwr_id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `hj_id` bigint(20) UNSIGNED NOT NULL,
  `worker_instance` varchar(96) NOT NULL,
  `status` enum('starting','running','stopping','stopped','crashed','failed') NOT NULL DEFAULT 'starting',
  `current_round_id` bigint(20) UNSIGNED DEFAULT NULL,
  `current_sender_id` bigint(20) UNSIGNED DEFAULT NULL,
  `stop_requested` tinyint(1) NOT NULL DEFAULT 0,
  `started_at` datetime NOT NULL DEFAULT current_timestamp(),
  `heartbeat_at` datetime NOT NULL DEFAULT current_timestamp(),
  `stopped_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`hwr_id`),
  KEY `idx_heart_worker_job_status` (`hj_id`,`status`,`heartbeat_at`),
  KEY `idx_heart_worker_heartbeat` (`status`,`heartbeat_at`),
  KEY `idx_heart_worker_round` (`current_round_id`),
  KEY `idx_heart_worker_sender` (`current_sender_id`),
  CONSTRAINT `fk_heart_worker_job`
    FOREIGN KEY (`hj_id`) REFERENCES `heart_jobs` (`hj_id`)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_worker_round`
    FOREIGN KEY (`current_round_id`) REFERENCES `heart_rounds` (`hround_id`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_heart_worker_sender`
    FOREIGN KEY (`current_sender_id`) REFERENCES `heart_senders` (`hs_id`)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `heart_settings` (
  `setting_key` varchar(100) NOT NULL,
  `setting_value` text DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`setting_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

COMMIT;
