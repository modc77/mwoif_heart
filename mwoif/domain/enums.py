from __future__ import annotations

from enum import StrEnum


class Source(StrEnum):
    LOCAL = "local"
    WEB = "web"


class LoginStatus(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    READY = "ready"
    ERROR = "error"


class AuthStatus(StrEnum):
    UNKNOWN = "unknown"
    MISSING = "missing"
    VALID = "valid"
    EXPIRED = "expired"
    ERROR = "error"


class SessionStatus(StrEnum):
    UNKNOWN = "unknown"
    MISSING = "missing"
    READY = "ready"
    INVALID = "invalid"
    ERROR = "error"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class SenderHealth(StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    COOLDOWN = "cooldown"
    ERROR = "error"
    DISABLED = "disabled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RoundStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    RECOVERY_PENDING = "recovery_pending"
    CANCELLED = "cancelled"


class RoundStep(StrEnum):
    CREATED = "CREATED"
    ENSURE_RECEIVER = "ENSURE_RECEIVER"
    ENSURE_SENDER = "ENSURE_SENDER"
    ADD = "ADD"
    ACCEPT = "ACCEPT"
    READY_TO_SEND = "READY_TO_SEND"
    SEND = "SEND"
    READ_MAILBOX = "READ_MAILBOX"
    RECEIVE = "RECEIVE"
    REMOVE = "REMOVE"
    PASS = "PASS"
    FAILED = "FAILED"


class SendOutcome(StrEnum):
    NOT_STARTED = "not_started"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    FAILED = "failed"


class ErrorScope(StrEnum):
    SENDER = "sender"
    RECEIVER = "receiver"
    SYSTEM = "system"
