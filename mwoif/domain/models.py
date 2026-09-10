from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import (
    AccountStatus,
    AuthStatus,
    ErrorScope,
    JobStatus,
    LoginStatus,
    RoundStatus,
    RoundStep,
    SendOutcome,
    SenderHealth,
    SessionStatus,
    Source,
)


@dataclass(slots=True)
class Receiver:
    hr_id: int | None
    source: Source
    email_hash: str
    email_mask: str | None
    u_id: int | None = None
    member_seq: int | None = None
    current_lv: int | None = None
    login_status: LoginStatus = LoginStatus.UNKNOWN
    auth_status: AuthStatus = AuthStatus.UNKNOWN
    session_status: SessionStatus = SessionStatus.UNKNOWN
    status: AccountStatus = AccountStatus.ACTIVE


@dataclass(slots=True)
class Sender:
    hs_id: int | None
    label: str
    email_hash: str
    email_mask: str | None
    enabled: bool = True
    health_status: SenderHealth = SenderHealth.UNKNOWN
    login_status: LoginStatus = LoginStatus.UNKNOWN
    auth_status: AuthStatus = AuthStatus.UNKNOWN
    session_status: SessionStatus = SessionStatus.UNKNOWN
    member_seq: int | None = None
    current_lv: int | None = None
    next_available_at: datetime | None = None


@dataclass(slots=True)
class HeartJob:
    hj_id: int | None
    hr_id: int
    requested_hearts: int
    source: Source = Source.LOCAL
    u_id: int | None = None
    completed_hearts: int = 0
    successful_rounds: int = 0
    failed_rounds: int = 0
    status: JobStatus = JobStatus.QUEUED
    stop_requested: bool = False


@dataclass(slots=True)
class HeartRound:
    hround_id: int | None
    hj_id: int
    hs_id: int
    hr_id: int
    sequence_no: int
    status: RoundStatus = RoundStatus.CREATED
    current_step: RoundStep = RoundStep.CREATED
    last_confirmed_step: RoundStep | None = None
    send_outcome: SendOutcome = SendOutcome.NOT_STARTED
    heart_amount: int = 0
    cancel_locked: bool = False
    retry_count: int = 0
    recovery_required: bool = False
    error_scope: ErrorScope | None = None
    error_stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None
