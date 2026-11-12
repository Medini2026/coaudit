import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from coaudit.contracts import (
    AuditEntry,
    Decision,
    MetricName,
    ReviewItem,
    ReviewStatus,
)

AUDIT_TABLE = "audit_log"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    thread_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    status TEXT NOT NULL,
    fault_switch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    company TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    original_value REAL NOT NULL,
    final_value REAL,
    decision TEXT NOT NULL,
    reviewer_notes TEXT NOT NULL DEFAULT '',
    reviewer_id TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TABLE IF NOT EXISTS step_ledger (
    thread_id TEXT NOT NULL,
    step TEXT NOT NULL,
    runs INTEGER NOT NULL,
    PRIMARY KEY (thread_id, step)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

BLOCKED_ON_AUDIT_TABLE = (
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DROP_TABLE,
)


class ReviewNotFoundError(Exception):
    pass


class ReviewAlreadyDecidedError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def guard_audit_log(action, first, second, database, source) -> int:
    if action in BLOCKED_ON_AUDIT_TABLE and first == AUDIT_TABLE:
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_ALTER_TABLE) and second == AUDIT_TABLE:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def to_review_item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        id=row["id"],
        thread_id=row["thread_id"],
        ticker=row["ticker"],
        metric_name=MetricName(row["metric_name"]),
        reason=row["reason"],
        status=ReviewStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def to_audit_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        audit_id=row["audit_id"],
        thread_id=row["thread_id"],
        company=row["company"],
        metric_name=MetricName(row["metric_name"]),
        original_value=row["original_value"],
        final_value=row["final_value"],
        decision=Decision(row["decision"]),
        reviewer_notes=row["reviewer_notes"],
        reviewer_id=row["reviewer_id"],
        simulated=bool(row["simulated"]),
        timestamp=datetime.fromisoformat(row["timestamp"]),
    )


class Store:
    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(SCHEMA)
        self._connection.set_authorizer(guard_audit_log)

    def close(self) -> None:
        self._connection.close()

    def create_run(self, thread_id: str, ticker: str, fault_switch: bool) -> None:
        stamp = now_iso()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO runs (thread_id, ticker, status, fault_switch, created_at, updated_at)"
                " VALUES (?, ?, 'RUNNING', ?, ?, ?)",
                (thread_id, ticker, int(fault_switch), stamp, stamp),
            )

    def update_run(self, thread_id: str, status: str, error: Optional[str] = None) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE runs SET status = ?, error = ?, updated_at = ? WHERE thread_id = ?",
                (status, error, now_iso(), thread_id),
            )

    def get_run(self, thread_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runs WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_review_items(
        self, thread_id: str, ticker: str, items: list[tuple[str, str]]
    ) -> None:
        stamp = now_iso()
        with self._lock, self._connection:
            for metric_name, reason in items:
                self._connection.execute(
                    "INSERT OR IGNORE INTO review_items"
                    " (id, thread_id, ticker, metric_name, reason, status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 'PENDING', ?)",
                    (f"{thread_id}:{metric_name}", thread_id, ticker, metric_name, reason, stamp),
                )

    def get_review(self, review_id: str) -> Optional[ReviewItem]:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM review_items WHERE id = ?", (review_id,)
            ).fetchone()
        return to_review_item(row) if row else None

    def list_reviews(
        self, status: Optional[ReviewStatus] = None, thread_id: Optional[str] = None
    ) -> list[ReviewItem]:
        query = "SELECT * FROM review_items WHERE 1 = 1"
        parameters: list[Any] = []
        if status is not None:
            query += " AND status = ?"
            parameters.append(status.value)
        if thread_id is not None:
            query += " AND thread_id = ?"
            parameters.append(thread_id)
        query += " ORDER BY created_at, id"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [to_review_item(row) for row in rows]

    def count_reviews(self, thread_id: str, status: ReviewStatus) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS total FROM review_items WHERE thread_id = ? AND status = ?",
                (thread_id, status.value),
            ).fetchone()
        return int(row["total"])

    def record_decision(self, review_id: str, entry: AuditEntry) -> AuditEntry:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT status FROM review_items WHERE id = ?", (review_id,)
            ).fetchone()
            if row is None:
                raise ReviewNotFoundError(review_id)
            if row["status"] != ReviewStatus.PENDING.value:
                raise ReviewAlreadyDecidedError(review_id)
            cursor = self._connection.execute(
                "INSERT INTO audit_log (thread_id, company, metric_name, original_value,"
                " final_value, decision, reviewer_notes, reviewer_id, simulated, timestamp)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.thread_id,
                    entry.company,
                    entry.metric_name.value,
                    entry.original_value,
                    entry.final_value,
                    entry.decision.value,
                    entry.reviewer_notes,
                    entry.reviewer_id,
                    int(entry.simulated),
                    entry.timestamp.isoformat(),
                ),
            )
            self._connection.execute(
                "UPDATE review_items SET status = 'DONE', decided_at = ? WHERE id = ?",
                (now_iso(), review_id),
            )
            return entry.model_copy(update={"audit_id": cursor.lastrowid})

    def list_audit(self, thread_id: Optional[str] = None) -> list[AuditEntry]:
        query = "SELECT * FROM audit_log"
        parameters: list[Any] = []
        if thread_id is not None:
            query += " WHERE thread_id = ?"
            parameters.append(thread_id)
        query += " ORDER BY audit_id"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [to_audit_entry(row) for row in rows]

    def record_step(self, thread_id: str, step: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO step_ledger (thread_id, step, runs) VALUES (?, ?, 1)"
                " ON CONFLICT(thread_id, step) DO UPDATE SET runs = runs + 1",
                (thread_id, step),
            )

    def get_ledger(self, thread_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT step, runs FROM step_ledger WHERE thread_id = ?", (thread_id,)
            ).fetchall()
        return {row["step"]: int(row["runs"]) for row in rows}

    def get_setting(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
