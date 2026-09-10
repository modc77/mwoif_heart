from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mwoif.core.config import AppConfig
from mwoif.storage.database import MariaDb, TableStatus
from mwoif.storage.repository import HeartRepository


@dataclass(frozen=True, slots=True)
class HealthReport:
    ok: bool
    database: dict[str, Any]
    tables: list[TableStatus]
    dashboard: dict[str, Any] | None

    @property
    def missing_tables(self) -> list[str]:
        return [item.table for item in self.tables if not item.exists]


def build_database(config: AppConfig) -> MariaDb:
    return MariaDb(config.database)


def build_repository(config: AppConfig) -> HeartRepository:
    return HeartRepository(build_database(config))


def check_health(config: AppConfig, *, include_dashboard: bool = True) -> HealthReport:
    db = build_database(config)
    db_info = db.ping()
    table_status = db.check_heart_tables()
    missing = [item.table for item in table_status if not item.exists]
    dashboard = None
    if include_dashboard and not missing:
        dashboard = HeartRepository(db).read_dashboard_counts()
    return HealthReport(ok=not missing, database=db_info, tables=table_status, dashboard=dashboard)


def initialize_settings(config: AppConfig) -> None:
    repo = build_repository(config)
    defaults = {
        "schema.version": "v1",
        "worker.mode": "sequential",
        "retry.network.max_attempts": "3",
        "credential.receiver.purge_on_complete": "1",
        "secret.output": "NONE",
    }
    for key, value in defaults.items():
        repo.upsert_setting(key, value)
