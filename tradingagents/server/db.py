"""SQLite-backed jobs table for the web server.

Single-process, single-writer. Opens a fresh connection per call so cross-thread
use (the asyncio.to_thread runner writes; the FastAPI handlers read) is safe
without check_same_thread juggling.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    status        TEXT NOT NULL,
    current_step  TEXT,
    progress_pct  INTEGER,
    error         TEXT,
    report_path   TEXT,
    config_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columns added after the initial schema shipped. Each tuple is
# (column_name, SQL type). init_db idempotently ADDs anything missing.
_LATER_COLUMNS: list[tuple[str, str]] = [
    ("prompt_tokens", "INTEGER"),
    ("completion_tokens", "INTEGER"),
    ("estimated_cost_usd", "REAL"),
]


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for col, sql_type in _LATER_COLUMNS:
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {sql_type}")


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # No WAL: when the DB lives on an SMB share (Azure Files), the WAL sidecar
    # needs locking semantics SMB doesn't implement, and `PRAGMA journal_mode=WAL`
    # fails with "database is locked". The default rollback journal works
    # everywhere, and busy_timeout covers the single-writer/multi-reader case.
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["config"] = json.loads(d.pop("config_json"))
    return d


def create_job(
    db_path: Path,
    *,
    job_id: str,
    ticker: str,
    analysis_date: str,
    config: dict[str, Any],
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, ticker, analysis_date, status, config_json, created_at)
            VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (job_id, ticker, analysis_date, json.dumps(config), _now()),
        )


def get_job(db_path: Path, job_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _row_to_dict(row)


def list_jobs(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]  # type: ignore[misc]


def mark_running(db_path: Path, job_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE job_id=?",
            (_now(), job_id),
        )


def mark_done(db_path: Path, job_id: str, *, report_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs SET status='done', finished_at=?, report_path=?, current_step=NULL
             WHERE job_id=?
            """,
            (_now(), report_path, job_id),
        )


def mark_failed(db_path: Path, job_id: str, *, error: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', finished_at=?, error=? WHERE job_id=?",
            (_now(), error, job_id),
        )


def set_current_step(db_path: Path, job_id: str, step: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET current_step=? WHERE job_id=?",
            (step, job_id),
        )


def snapshot_db(src: Path, dst: Path) -> None:
    """Take a consistent point-in-time copy of the jobs DB via SQLite's
    backup API.

    Used by the web service to copy a fast ephemeral DB (e.g. /tmp/) onto a
    durable but lock-hostile Azure Files SMB share. The backup API is safe
    to call while writers are active — it takes a B-tree page-level snapshot
    without blocking ongoing transactions. The destination is a plain file
    written byte-by-byte, so no fcntl locks are involved on dst.

    Parent of ``dst`` is created if missing.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def set_telemetry(
    db_path: Path,
    job_id: str,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    estimated_cost_usd: float | None,
) -> None:
    """Record aggregate LLM usage + cost estimate for a finished job."""
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
               SET prompt_tokens = ?,
                   completion_tokens = ?,
                   estimated_cost_usd = ?
             WHERE job_id = ?
            """,
            (prompt_tokens, completion_tokens, estimated_cost_usd, job_id),
        )
