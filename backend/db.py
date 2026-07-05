from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "proofpilot.sqlite3"
DB_PATH = Path(os.environ.get("PROOFPILOT_DB", DEFAULT_DB_PATH))


@dataclass
class ProofAttempt:
    id: str
    title: str
    statement: str
    code: str
    status: str
    created_at: float
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairAttempt:
    id: str
    proof_attempt_id: str
    prompt: str
    candidate_code: str
    provider: str
    model: str
    round_index: int
    status: str
    result: dict[str, Any]
    created_at: float


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proof_attempts (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                statement TEXT NOT NULL,
                code TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proof_attempts_created_at "
            "ON proof_attempts(created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_attempts (
                id TEXT PRIMARY KEY,
                proof_attempt_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                candidate_code TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT 'local',
                model TEXT NOT NULL DEFAULT 'deterministic-rfl',
                round_index INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY (proof_attempt_id) REFERENCES proof_attempts(id)
            )
            """
        )
        _ensure_column(conn, "repair_attempts", "candidate_code", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "repair_attempts", "provider", "TEXT NOT NULL DEFAULT 'local'")
        _ensure_column(conn, "repair_attempts", "model", "TEXT NOT NULL DEFAULT 'deterministic-rfl'")
        _ensure_column(conn, "repair_attempts", "round_index", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "repair_attempts", "result_json", "TEXT NOT NULL DEFAULT '{}'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repair_attempts_proof_attempt_id "
            "ON repair_attempts(proof_attempt_id, created_at DESC)"
        )


def create_attempt(
    *,
    attempt_id: str,
    title: str,
    statement: str,
    code: str,
    status: str,
    result: dict[str, Any],
) -> ProofAttempt:
    attempt = ProofAttempt(
        id=attempt_id,
        title=title,
        statement=statement,
        code=code,
        status=status,
        created_at=time.time(),
        result=result,
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO proof_attempts (
                id, title, statement, code, status, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.id,
                attempt.title,
                attempt.statement,
                attempt.code,
                attempt.status,
                json.dumps(attempt.result),
                attempt.created_at,
            ),
        )
    return attempt


def list_attempts(limit: int = 50) -> list[ProofAttempt]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, statement, code, status, result_json, created_at
            FROM proof_attempts
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_attempt(row) for row in rows]


def get_attempt(attempt_id: str) -> ProofAttempt | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, title, statement, code, status, result_json, created_at
            FROM proof_attempts
            WHERE id = ?
            """,
            (attempt_id,),
        ).fetchone()
    return _row_to_attempt(row) if row is not None else None


def create_repair_attempt(
    *,
    repair_id: str,
    proof_attempt_id: str,
    prompt: str,
    candidate_code: str,
    provider: str,
    model: str,
    round_index: int,
    status: str,
    result: dict[str, Any],
) -> RepairAttempt:
    repair = RepairAttempt(
        id=repair_id,
        proof_attempt_id=proof_attempt_id,
        prompt=prompt,
        candidate_code=candidate_code,
        provider=provider,
        model=model,
        round_index=round_index,
        status=status,
        result=result,
        created_at=time.time(),
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO repair_attempts (
                id, proof_attempt_id, prompt, candidate_code, provider, model, round_index, status, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repair.id,
                repair.proof_attempt_id,
                repair.prompt,
                repair.candidate_code,
                repair.provider,
                repair.model,
                repair.round_index,
                repair.status,
                json.dumps(repair.result),
                repair.created_at,
            ),
        )
    return repair


def list_repair_attempts(proof_attempt_id: str) -> list[RepairAttempt]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, proof_attempt_id, prompt, candidate_code, provider, model, round_index, status, result_json, created_at
            FROM repair_attempts
            WHERE proof_attempt_id = ?
            ORDER BY created_at DESC
            """,
            (proof_attempt_id,),
        ).fetchall()
    return [_row_to_repair_attempt(row) for row in rows]


def get_metrics() -> dict[str, Any]:
    with _connect() as conn:
        proof_counts = _count_by_status(conn, "proof_attempts")
        repair_counts = _count_by_status(conn, "repair_attempts")
        provider_counts = _count_by_column(conn, "repair_attempts", "provider")
        proof_total = sum(proof_counts.values())
        repair_total = sum(repair_counts.values())
        verified_proofs = proof_counts.get("verified", 0)
        verified_repairs = repair_counts.get("candidate_verified", 0)
        avg_proof_ms = _avg_elapsed_ms(conn, "proof_attempts")
        avg_repair_ms = _avg_elapsed_ms(conn, "repair_attempts")

    return {
        "proofs": {
            "total": proof_total,
            "verified": verified_proofs,
            "failed": proof_counts.get("failed", 0),
            "rejected": proof_counts.get("rejected", 0),
            "timeout": proof_counts.get("timeout", 0),
            "tool_missing": proof_counts.get("tool_missing", 0),
            "verification_rate": verified_proofs / proof_total if proof_total else 0,
            "avg_elapsed_ms": avg_proof_ms,
        },
        "repairs": {
            "total": repair_total,
            "verified": verified_repairs,
            "failed": repair_counts.get("candidate_failed", 0),
            "success_rate": verified_repairs / repair_total if repair_total else 0,
            "avg_elapsed_ms": avg_repair_ms,
            "providers": provider_counts,
        },
    }


def reset_dev_data() -> dict[str, int]:
    with _connect() as conn:
        repair_count = conn.execute("SELECT COUNT(*) AS count FROM repair_attempts").fetchone()["count"]
        proof_count = conn.execute("SELECT COUNT(*) AS count FROM proof_attempts").fetchone()["count"]
        conn.execute("DELETE FROM repair_attempts")
        conn.execute("DELETE FROM proof_attempts")
    return {
        "proof_attempts_deleted": proof_count,
        "repair_attempts_deleted": repair_count,
    }


def attempt_to_dict(attempt: ProofAttempt) -> dict[str, Any]:
    return asdict(attempt)


def repair_to_dict(repair: RepairAttempt) -> dict[str, Any]:
    return asdict(repair)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _count_by_status(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    rows = conn.execute(f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status").fetchall()
    return {row["status"]: row["count"] for row in rows}


def _count_by_column(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    rows = conn.execute(f"SELECT {column}, COUNT(*) AS count FROM {table} GROUP BY {column}").fetchall()
    return {row[column]: row["count"] for row in rows}


def _avg_elapsed_ms(conn: sqlite3.Connection, table: str) -> int:
    rows = conn.execute(f"SELECT result_json FROM {table}").fetchall()
    elapsed_values = []
    for row in rows:
        result = json.loads(row["result_json"])
        elapsed_ms = result.get("elapsed_ms")
        if isinstance(elapsed_ms, int | float):
            elapsed_values.append(elapsed_ms)
    return round(sum(elapsed_values) / len(elapsed_values)) if elapsed_values else 0


def _row_to_attempt(row: sqlite3.Row) -> ProofAttempt:
    return ProofAttempt(
        id=row["id"],
        title=row["title"],
        statement=row["statement"],
        code=row["code"],
        status=row["status"],
        created_at=row["created_at"],
        result=json.loads(row["result_json"]),
    )


def _row_to_repair_attempt(row: sqlite3.Row) -> RepairAttempt:
    return RepairAttempt(
        id=row["id"],
        proof_attempt_id=row["proof_attempt_id"],
        prompt=row["prompt"],
        candidate_code=row["candidate_code"],
        provider=row["provider"],
        model=row["model"],
        round_index=row["round_index"],
        status=row["status"],
        result=json.loads(row["result_json"]),
        created_at=row["created_at"],
    )

