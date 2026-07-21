"""Append-only action_log (audit trail).

Every write that matters records an action_log row with the acting actor,
an explicit `as_of` (never the system clock), and a JSON payload snapshot.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from governance.transaction import Actor, next_stable_id


def ensure_action_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS action_log (
            action_id TEXT PRIMARY KEY,
            actor_id TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            action_name TEXT NOT NULL,
            target_table TEXT,
            target_id TEXT,
            payload_json TEXT,
            as_of_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )


def record_action(
    conn: sqlite3.Connection,
    actor: Actor,
    action_name: str,
    *,
    as_of: str,
    target_table: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    """Append one audit row. `as_of` is explicit (D8), created_at derives from it.

    Assumes caller holds an open transaction (governance.transaction).
    """
    ensure_action_log(conn)
    payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    entropy = f"{actor.actor_id}|{action_name}|{target_table}|{target_id}|{as_of}"
    action_id = next_stable_id("ACT", entropy)
    created_at = f"{as_of}T00:00:00Z"
    conn.execute(
        """INSERT OR REPLACE INTO action_log (
            action_id, actor_id, actor_role, action_name, target_table,
            target_id, payload_json, as_of_date, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            action_id,
            actor.actor_id,
            actor.role,
            action_name,
            target_table,
            target_id,
            payload_json,
            as_of,
            created_at,
        ),
    )
    return action_id
