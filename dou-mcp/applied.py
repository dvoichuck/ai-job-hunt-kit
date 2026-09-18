"""SQLite log of DOU / external-ATS applications to avoid duplicates."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "applied.db"

_conn: sqlite3.Connection | None = None


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applied (
                job_key TEXT PRIMARY KEY,
                ats TEXT,
                url TEXT,
                status TEXT,
                note TEXT,
                applied_at TEXT
            )
            """
        )
        _conn.commit()
    return _conn


def is_applied(job_key: str) -> bool:
    row = connect().execute(
        "SELECT 1 FROM applied WHERE job_key = ? AND status IN ('submitted', 'needs_captcha')",
        (job_key,),
    ).fetchone()
    return row is not None


def mark(
    job_key: str,
    *,
    ats: str,
    url: str,
    status: str,
    note: str = "",
) -> None:
    connect().execute(
        """
        INSERT INTO applied (job_key, ats, url, status, note, applied_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_key) DO UPDATE SET
            ats = excluded.ats,
            url = excluded.url,
            status = excluded.status,
            note = excluded.note,
            applied_at = excluded.applied_at
        """,
        (job_key, ats, url, status, note, datetime.now(timezone.utc).isoformat()),
    )
    connect().commit()


def get(job_key: str) -> dict[str, Any] | None:
    row = connect().execute(
        "SELECT * FROM applied WHERE job_key = ?", (job_key,)
    ).fetchone()
    return dict(row) if row else None
