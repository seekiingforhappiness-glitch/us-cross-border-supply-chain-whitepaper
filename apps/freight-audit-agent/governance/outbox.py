"""dispute_outbox — draft-not-send writeback ledger for dispute drafts.

A confirmed discrepancy can be turned into a *dispute draft* (a claim letter /
recovery request). The outbox records intent only: it never sends email, never
calls a carrier API, never touches a payment system. Human authorization only
flips a status column.

Discipline (adapted from pipeline/outbox.py):
- idempotency key = sha256(target_system|action_name|target_object)
- status machine: pending -> authorized -> succeeded | failed
- attempt_count, explicit as_of (no system clock), last_error
- conflict detection (same key, different payload)
- nested-transaction detection (participate vs own the txn)
- NEVER raises: every call returns a structured OutboxResult.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "freight-audit-dispute-outbox-v1"

_TERMINAL = {"succeeded", "failed"}
_ALLOWED_TRANSITIONS = {
    "pending": {"authorized"},
    "authorized": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": {"authorized"},  # allow retry after a failed attempt
}


@dataclass(frozen=True)
class OutboxResult:
    ok: bool
    status: str          # enqueued | exists | conflict | transitioned | not_found | error | invalid_transition
    key: str | None
    detail: str = ""


def ensure_dispute_outbox(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dispute_outbox (
            idempotency_key TEXT PRIMARY KEY,
            target_system TEXT NOT NULL,
            action_name TEXT NOT NULL,
            target_object TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            proposer_id TEXT,
            authorizer_id TEXT,
            last_error TEXT,
            as_of_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            UNIQUE (target_system, action_name, target_object)
        )"""
    )


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def idempotency_key(target_system: str, action_name: str, target_object: str) -> str:
    entropy = f"{target_system}|{action_name}|{target_object}"
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:32].upper()
    return f"DSP-{digest}"


def enqueue_dispute(
    conn: sqlite3.Connection,
    target_system: str,
    action_name: str,
    target_object: str,
    payload: dict[str, Any],
    *,
    as_of: str,
    proposer_id: str,
) -> OutboxResult:
    """Enqueue one dispute draft (status=pending). Never raises."""
    key = idempotency_key(target_system, action_name, target_object)
    owns_transaction = not conn.in_transaction
    try:
        payload_json = _canonical(payload)
        created_at = f"{as_of}T00:00:00Z"
        if owns_transaction:
            conn.execute("BEGIN")
        ensure_dispute_outbox(conn)
        existing = conn.execute(
            "SELECT idempotency_key, payload_json FROM dispute_outbox WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if existing is not None:
            if existing[1] != payload_json:
                if owns_transaction:
                    conn.rollback()
                return OutboxResult(False, "conflict", key, "same key, different payload")
            if owns_transaction:
                conn.commit()
            return OutboxResult(True, "exists", key, "idempotent no-op")
        conn.execute(
            """INSERT INTO dispute_outbox (
                idempotency_key, target_system, action_name, target_object,
                payload_json, status, attempt_count, proposer_id, authorizer_id,
                last_error, as_of_date, created_at, updated_at, policy_version
            ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, ?, ?, ?, ?)""",
            (
                key, target_system, action_name, target_object, payload_json,
                proposer_id, as_of, created_at, created_at, POLICY_VERSION,
            ),
        )
        if owns_transaction:
            conn.commit()
        return OutboxResult(True, "enqueued", key, "pending")
    except Exception as exc:  # never raises
        if owns_transaction and conn.in_transaction:
            conn.rollback()
        return OutboxResult(False, "error", key, f"{type(exc).__name__}: {exc}")


def transition_dispute(
    conn: sqlite3.Connection,
    key: str,
    new_status: str,
    *,
    as_of: str,
    actor_id: str,
    last_error: str | None = None,
) -> OutboxResult:
    """Advance the status machine. Enforces allowed transitions. Never raises.

    pending -> authorized (records authorizer_id; caller enforces maker-checker)
    authorized -> succeeded | failed (increments attempt_count)
    failed -> authorized (retry)
    """
    owns_transaction = not conn.in_transaction
    try:
        updated_at = f"{as_of}T00:00:00Z"
        if owns_transaction:
            conn.execute("BEGIN")
        ensure_dispute_outbox(conn)
        row = conn.execute(
            "SELECT status, attempt_count FROM dispute_outbox WHERE idempotency_key=?",
            (key,),
        ).fetchone()
        if row is None:
            if owns_transaction:
                conn.rollback()
            return OutboxResult(False, "not_found", key, "no such dispute")
        cur_status = row[0]
        if new_status not in _ALLOWED_TRANSITIONS.get(cur_status, set()):
            if owns_transaction:
                conn.rollback()
            return OutboxResult(False, "invalid_transition", key, f"{cur_status}->{new_status}")
        attempt = row[1] + (1 if new_status in _TERMINAL else 0)
        if new_status == "authorized":
            conn.execute(
                """UPDATE dispute_outbox
                   SET status=?, authorizer_id=?, updated_at=?, last_error=NULL
                   WHERE idempotency_key=?""",
                (new_status, actor_id, updated_at, key),
            )
        else:
            conn.execute(
                """UPDATE dispute_outbox
                   SET status=?, attempt_count=?, updated_at=?, last_error=?
                   WHERE idempotency_key=?""",
                (new_status, attempt, updated_at, last_error, key),
            )
        if owns_transaction:
            conn.commit()
        return OutboxResult(True, "transitioned", key, f"{cur_status}->{new_status}")
    except Exception as exc:  # never raises
        if owns_transaction and conn.in_transaction:
            conn.rollback()
        return OutboxResult(False, "error", key, f"{type(exc).__name__}: {exc}")
