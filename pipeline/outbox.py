"""M7 simulated integration outbox helpers.

The outbox records writeback intent only. It never calls real ERP/TMS/QMS
systems and never sends network requests.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

POLICY_VERSION = "M7-simulated-outbox-v1"


def ensure_integration_outbox(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS integration_outbox (
            idempotency_key TEXT PRIMARY KEY,
            target_system TEXT NOT NULL,
            action_name TEXT NOT NULL,
            target_object TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            UNIQUE (target_system, action_name, target_object)
        )"""
    )


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _idempotency_key(
    target_system: str,
    action_name: str,
    target_object: str,
) -> str:
    entropy = f"{target_system}|{action_name}|{target_object}"
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:32].upper()
    return f"OUT-{digest}"


def enqueue_writeback(
    conn: sqlite3.Connection,
    target_system: str,
    action_name: str,
    target_object: str,
    payload: dict[str, Any],
    *,
    as_of: str,
) -> str:
    """Record one simulated writeback request and return its idempotency key.

    审计时间戳基于显式 as_of（D8），不用系统时钟。
    """
    payload_json = _canonical_payload(payload)
    key = _idempotency_key(target_system, action_name, target_object)
    created_at = f"{as_of}T00:00:00Z"
    row = (
        key,
        target_system,
        action_name,
        target_object,
        payload_json,
        "pending",
        0,
        None,
        created_at,
        created_at,
        POLICY_VERSION,
    )

    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        ensure_integration_outbox(conn)
        existing = conn.execute(
            """SELECT idempotency_key, target_system, action_name, target_object, payload_json
               FROM integration_outbox
               WHERE target_system=? AND action_name=? AND target_object=?""",
            (target_system, action_name, target_object),
        ).fetchone()
        if existing:
            existing_key = existing[0]
            existing_values = tuple(existing[1:])
            if existing_values != (target_system, action_name, target_object, payload_json):
                raise ValueError(f"integration_outbox_conflict:{existing_key}")
            key = existing_key
        else:
            conn.execute(
                """INSERT INTO integration_outbox (
                    idempotency_key, target_system, action_name, target_object,
                    payload_json, status, attempt_count, last_error, created_at,
                    updated_at, policy_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
        if owns_transaction:
            conn.commit()
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise
    return key
