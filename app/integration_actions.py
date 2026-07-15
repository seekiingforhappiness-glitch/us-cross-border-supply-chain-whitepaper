"""M7 actions for the simulated integration outbox."""
from __future__ import annotations

import sqlite3

try:
    from .action_context import transaction
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from action_context import transaction

from pipeline.outbox import ensure_integration_outbox


def _res(ok, object_id=None, side_effects=None, error=None):
    return {"ok": ok, "object_id": object_id, "side_effects": side_effects or [], "error": error}


def _mark_outbox(
    conn: sqlite3.Connection,
    idempotency_key: str,
    *,
    status: str,
    error: str | None,
    as_of: str,
):
    ensure_integration_outbox(conn)
    row = conn.execute(
        "SELECT status FROM integration_outbox WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if not row:
        return _res(False, idempotency_key, error="outbox_row_not_found")
    current_status = row[0]
    if current_status == "succeeded":
        if status == "succeeded":
            return _res(True, idempotency_key, [f"Outbox {idempotency_key}: already succeeded"])
        return _res(False, idempotency_key, error="outbox_already_succeeded")

    with transaction(conn):
        conn.execute(
            """UPDATE integration_outbox
               SET status=?, attempt_count=attempt_count+1, last_error=?, updated_at=?
               WHERE idempotency_key=?""",
            (status, error, f"{as_of}T00:00:00Z", idempotency_key),
        )
    return _res(True, idempotency_key, [f"Outbox {idempotency_key}: {current_status}→{status}"])


def mark_outbox_succeeded(
    conn: sqlite3.Connection,
    idempotency_key: str,
    *,
    as_of: str = "2026-08-08",
):
    return _mark_outbox(conn, idempotency_key, status="succeeded", error=None, as_of=as_of)


def mark_outbox_failed(
    conn: sqlite3.Connection,
    idempotency_key: str,
    error: str,
    *,
    as_of: str = "2026-08-08",
):
    message = (error or "").strip()
    if not message:
        return _res(False, idempotency_key, error="error_required")
    return _mark_outbox(conn, idempotency_key, status="failed", error=message, as_of=as_of)
