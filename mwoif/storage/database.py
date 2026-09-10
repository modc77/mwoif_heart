from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from mwoif.core.config import DatabaseConfig
from mwoif.domain.errors import DatabaseError
from mwoif.storage.production_schema import PRODUCTION_TABLES

BASE_HEART_TABLES: tuple[str, ...] = (
    "heart_receivers",
    "heart_senders",
    "heart_sender_vault",
    "heart_jobs",
    "heart_receiver_job_vault",
    "heart_rounds",
    "heart_events",
    "heart_worker_runs",
    "heart_settings",
)

HEART_TABLES: tuple[str, ...] = BASE_HEART_TABLES + PRODUCTION_TABLES


@dataclass(frozen=True, slots=True)
class TableStatus:
    table: str
    exists: bool


class MariaDb:
    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config

    def _connect(self):  # intentionally untyped; pymysql is optional until requirements are installed
        try:
            import pymysql
        except ImportError as exc:
            raise DatabaseError(
                "Missing dependency pymysql. Run: pip install -r requirements.txt",
                stage="DB_IMPORT",
            ) from exc

        try:
            return pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database=self.config.database,
                charset=self.config.charset,
                connect_timeout=self.config.connect_timeout,
                read_timeout=self.config.read_timeout,
                write_timeout=self.config.write_timeout,
                autocommit=False,
                cursorclass=pymysql.cursors.DictCursor,
            )
        except Exception as exc:  # pragma: no cover - depends on local DB
            raise DatabaseError(str(exc), stage="DB_CONNECT") from exc

    @contextmanager
    def connection(self) -> Iterator[Any]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def ping(self) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DATABASE() AS db_name, VERSION() AS db_version")
                row = cur.fetchone()
            conn.commit()
        return dict(row or {})

    def fetch_one(self, sql: str, params: Iterable[Any] | dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: Iterable[Any] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.commit()
        return [dict(row) for row in rows]

    def execute(self, sql: str, params: Iterable[Any] | dict[str, Any] | None = None) -> int:
        with self.connection() as conn:
            try:
                with conn.cursor() as cur:
                    affected = cur.execute(sql, params)
                conn.commit()
                return int(affected)
            except Exception:
                conn.rollback()
                raise

    def insert_get_id(self, sql: str, params: Iterable[Any] | dict[str, Any] | None = None) -> int:
        """Execute one INSERT and return its auto-increment id from the same connection.

        LAST_INSERT_ID() is connection-scoped in MariaDB/MySQL. The repository must not
        call fetch_one("SELECT LAST_INSERT_ID()") after execute(), because execute() opens
        and closes its own connection.
        """
        with self.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    inserted_id = int(cur.lastrowid or 0)
                conn.commit()
                if inserted_id <= 0:
                    raise DatabaseError("insert succeeded but no auto-increment id was returned", stage="DB_INSERT_ID")
                return inserted_id
            except Exception:
                conn.rollback()
                raise

    def check_heart_tables(self) -> list[TableStatus]:
        placeholders = ",".join(["%s"] * len(HEART_TABLES))
        rows = self.fetch_all(
            f"""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name IN ({placeholders})
            """,
            HEART_TABLES,
        )
        found = {str(row["table_name"]) for row in rows}
        return [TableStatus(table=name, exists=name in found) for name in HEART_TABLES]
