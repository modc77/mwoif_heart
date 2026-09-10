from __future__ import annotations

from dataclasses import dataclass

from .enums import ErrorScope, RoundStep


@dataclass(slots=True)
class ErrorInfo:
    scope: ErrorScope
    code: str
    message: str
    stage: str | None = None


class MwoifHeartError(Exception):
    code = "MWOIF_HEART_ERROR"
    scope = ErrorScope.SYSTEM

    def __init__(self, message: str, *, stage: str | RoundStep | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage.value if isinstance(stage, RoundStep) else stage

    def to_info(self) -> ErrorInfo:
        return ErrorInfo(scope=self.scope, code=self.code, message=self.message, stage=self.stage)


class ConfigError(MwoifHeartError):
    code = "CONFIG_ERROR"


class DatabaseError(MwoifHeartError):
    code = "DATABASE_ERROR"


class CredentialError(MwoifHeartError):
    code = "CREDENTIAL_ERROR"


class AuthError(MwoifHeartError):
    code = "AUTH_ERROR"


class SessionError(MwoifHeartError):
    code = "SESSION_ERROR"


class SenderError(MwoifHeartError):
    code = "SENDER_ERROR"
    scope = ErrorScope.SENDER


class ReceiverError(MwoifHeartError):
    code = "RECEIVER_ERROR"
    scope = ErrorScope.RECEIVER


class ProtocolError(MwoifHeartError):
    code = "PROTOCOL_ERROR"
