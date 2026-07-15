"""M6 DQ issue loop test: python3 -m app.test_dq_loop"""
from __future__ import annotations

import sqlite3
import sys

from app.dq_actions import assign_dq_issue, close_dq_issue
from pipeline.dq_issues import create_dq_issue

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def connect():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """create table dq_issues (
            dq_issue_id text primary key,
            source_table text not null,
            source_record_id text not null,
            issue_type text not null,
            severity text not null,
            status text not null,
            assignee_user_id text,
            resolution text,
            detail_json text,
            created_at text,
            closed_at text,
            policy_version text
        )"""
    )
    conn.execute(
        """create table action_log (
            log_id integer primary key autoincrement,
            actor text,
            role text,
            action text,
            target_object_id text,
            params_json text,
            as_of_date text,
            timestamp text,
            result text
        )"""
    )
    return conn


def test_create_assign_close():
    conn = connect()
    issue_id = create_dq_issue(
        conn,
        "unresolved_milestones",
        "MS-001",
        "unresolved_reference",
        "medium",
        detail_json={"milestone_id": "MS-001", "source_system": "carrier_portal"},
    )
    assigned = assign_dq_issue(conn, issue_id, "u-ops-cn")
    assigned_row = conn.execute(
        "select status, assignee_user_id from dq_issues where dq_issue_id=?",
        (issue_id,),
    ).fetchone()
    closed = close_dq_issue(conn, issue_id, "Carrier event has a bad booking reference; source repair deferred.")
    row = conn.execute("select * from dq_issues where dq_issue_id=?", (issue_id,)).fetchone()
    actions = [
        r["action"]
        for r in conn.execute("select action from action_log where target_object_id=? order by log_id", (issue_id,))
    ]
    check("create -> assign -> close succeeds", assigned["ok"] and closed["ok"])
    check("assign moves issue to assigned",
          assigned_row["status"] == "assigned" and assigned_row["assignee_user_id"] == "u-ops-cn",
          str(dict(assigned_row)))
    check(
        "status/assignee/resolution persisted",
        row["status"] == "closed"
        and row["assignee_user_id"] == "u-ops-cn"
        and row["resolution"].startswith("Carrier event"),
        str(dict(row)),
    )
    check("assign/close action_log persisted in order", actions == ["AssignDQIssue", "CloseDQIssue"], str(actions))


def test_create_idempotent():
    conn = connect()
    first = create_dq_issue(conn, "unresolved_milestones", "MS-001", "unresolved_reference", "medium")
    second = create_dq_issue(conn, "unresolved_milestones", "MS-001", "unresolved_reference", "medium")
    count = conn.execute("select count(*) from dq_issues").fetchone()[0]
    check("duplicate create returns same id", first == second)
    check("duplicate create is insert-or-ignore idempotent", count == 1, f"count={count}")


def test_create_conflict_rejected():
    conn = connect()
    issue_id = create_dq_issue(
        conn,
        "unresolved_milestones",
        "MS-001",
        "unresolved_reference",
        "medium",
        detail_json={"source_system": "carrier_portal"},
    )
    conflict = None
    try:
        create_dq_issue(
            conn,
            "unresolved_milestones",
            "MS-001",
            "unresolved_reference",
            "high",
            detail_json={"source_system": "erp"},
        )
    except ValueError as exc:
        conflict = str(exc)
    row = conn.execute("select severity, detail_json from dq_issues where dq_issue_id=?", (issue_id,)).fetchone()
    count = conn.execute("select count(*) from dq_issues").fetchone()[0]
    check("conflicting duplicate create is rejected", conflict and conflict.startswith("dq_issue_conflict:"),
          str(conflict))
    check("conflicting duplicate preserves existing issue",
          count == 1 and row["severity"] == "medium" and "carrier_portal" in row["detail_json"],
          str(dict(row)))


def test_assign_audit_failure_rolls_back():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """create table dq_issues (
            dq_issue_id text primary key,
            source_table text not null,
            source_record_id text not null,
            issue_type text not null,
            severity text not null,
            status text not null,
            assignee_user_id text,
            resolution text,
            detail_json text,
            created_at text,
            closed_at text,
            policy_version text
        )"""
    )
    issue_id = create_dq_issue(conn, "unresolved_milestones", "MS-001", "unresolved_reference", "medium")
    conn.commit()
    assigned = assign_dq_issue(conn, issue_id, "u-ops-cn")
    row = conn.execute(
        "select status, coalesce(assignee_user_id, '') from dq_issues where dq_issue_id=?",
        (issue_id,),
    ).fetchone()
    check("assign rejects when audit write fails",
          assigned["ok"] is False and str(assigned["error"]).startswith("sqlite_error:"),
          str(assigned))
    check("assign rollback keeps issue open", row[0] == "open" and row[1] == "", str(row))
    check("assign rollback closes transaction", conn.in_transaction is False)


def test_invalid_close_audited():
    conn = connect()
    issue_id = create_dq_issue(conn, "unresolved_milestones", "MS-001", "unresolved_reference", "medium")
    empty = close_dq_issue(conn, issue_id, "")
    ok_close = close_dq_issue(conn, issue_id, "Handled in simulated DQ queue.")
    already = close_dq_issue(conn, issue_id, "second close")
    rejected = conn.execute(
        """select count(*) from action_log
           where action='CloseDQIssue' and result like 'rejected:%'"""
    ).fetchone()[0]
    check("empty resolution is rejected", empty["ok"] is False and "resolution_required" in empty["error"])
    check("already closed is rejected", ok_close["ok"] and already["ok"] is False
          and "dq_issue_already_closed" in already["error"])
    check("invalid closes write rejected audit", rejected == 2, f"rejected={rejected}")


def main():
    print("== M6 DQ issue loop ==")
    test_create_assign_close()
    test_create_idempotent()
    test_create_conflict_rejected()
    test_assign_audit_failure_rolls_back()
    test_invalid_close_audited()
    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
