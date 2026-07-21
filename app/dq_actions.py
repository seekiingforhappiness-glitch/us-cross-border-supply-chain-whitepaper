"""M6 DQ issue actions: assign and close.

The actions are deliberately local to avoid coupling the DQ loop to the risk
mitigation action module. They follow the same return shape and audit discipline.
"""
from __future__ import annotations

import json
import sqlite3

try:
    from .action_context import transaction
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from action_context import transaction

ALLOWED_ROLES = {"ops", "manager", "system"}


def _log(cur, actor, role, action, target, params, as_of, result):
    cur.execute(
        """INSERT INTO action_log (actor, role, action, target_object_id, params_json,
           as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
        (
            actor,
            role,
            action,
            target,
            json.dumps(params, ensure_ascii=False),
            as_of,
            f"{as_of}T00:00:00Z",
            result,
        ),
    )


def _res(ok, object_id=None, side_effects=None, error=None):
    return {"ok": ok, "object_id": object_id, "side_effects": side_effects or [], "error": error}


def _fail(conn, action, target, actor, role, as_of, message, params=None):
    cur = conn.cursor()
    with transaction(conn):
        _log(cur, actor, role, action, target, params or {}, as_of, f"rejected: {message}")
    return _res(False, target, error=message)


def _issue(cur, dq_issue_id):
    result = cur.execute("SELECT * FROM dq_issues WHERE dq_issue_id=?", (dq_issue_id,))
    row = result.fetchone()
    if not row:
        return None
    columns = [desc[0] for desc in result.description]
    return {column: row[index] for index, column in enumerate(columns)}


def assign_dq_issue(
    conn: sqlite3.Connection,
    dq_issue_id: str,
    assignee_user_id: str,
    *,
    actor: str = "u-ops-us",
    role: str = "ops",
    as_of: str = "2026-08-08",
):
    try:
        action = "AssignDQIssue"
        if role not in ALLOWED_ROLES:
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "role_not_permitted")
        if not assignee_user_id or not assignee_user_id.strip():
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "assignee_user_id_required")

        cur = conn.cursor()
        issue = _issue(cur, dq_issue_id)
        if not issue:
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "dq_issue_not_found")
        if issue["status"] == "closed":
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "dq_issue_already_closed")

        previous_status = issue["status"]
        with transaction(conn):
            cur.execute(
                "UPDATE dq_issues SET status='assigned', assignee_user_id=? WHERE dq_issue_id=?",
                (assignee_user_id.strip(), dq_issue_id),
            )
            _log(
                cur,
                actor,
                role,
                action,
                dq_issue_id,
                {"assignee_user_id": assignee_user_id.strip()},
                as_of,
                "ok",
            )
        return _res(True, dq_issue_id, [f"DQ issue {dq_issue_id}: {previous_status}→assigned",
                                        f"assigned to {assignee_user_id.strip()}"])
    except sqlite3.Error as exc:
        return _res(False, dq_issue_id, error=f"sqlite_error:{exc}")


def close_dq_issue(
    conn: sqlite3.Connection,
    dq_issue_id: str,
    resolution: str,
    *,
    actor: str = "u-ops-us",
    role: str = "ops",
    as_of: str = "2026-08-08",
):
    try:
        action = "CloseDQIssue"
        if role not in ALLOWED_ROLES:
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "role_not_permitted")
        if not resolution or not resolution.strip():
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "resolution_required")

        cur = conn.cursor()
        issue = _issue(cur, dq_issue_id)
        if not issue:
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "dq_issue_not_found")
        if issue["status"] == "closed":
            return _fail(conn, action, dq_issue_id, actor, role, as_of, "dq_issue_already_closed")

        previous_status = issue["status"]
        with transaction(conn):
            cur.execute(
                """UPDATE dq_issues
                   SET status='closed', resolution=?, closed_at=?
                   WHERE dq_issue_id=?""",
                (resolution.strip(), f"{as_of}T00:00:00Z", dq_issue_id),
            )
            _log(
                cur,
                actor,
                role,
                action,
                dq_issue_id,
                {"resolution": resolution.strip()},
                as_of,
                "ok",
            )
        return _res(True, dq_issue_id, [f"DQ issue {dq_issue_id}: {previous_status}→closed"])
    except sqlite3.Error as exc:
        return _res(False, dq_issue_id, error=f"sqlite_error:{exc}")
