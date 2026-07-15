from __future__ import annotations

import sqlite3

from app.action_context import Actor, ApprovalPolicy, next_stable_id, transaction
from app.actions import assign_task, approve_mitigation, can_approve_actor, propose_mitigation


def test_manager_cannot_approve_own_proposal() -> None:
    proposer = Actor(actor_id="u-ops-001", role="ops", display_name="Ops One")
    approver = Actor(actor_id="u-ops-001", role="manager", display_name="Ops One")
    allowed, reason = ApprovalPolicy(policy_version="M1").can_approve(
        proposer=proposer,
        approver=approver,
    )
    assert allowed is False
    assert reason == "maker_checker_violation"


def test_different_manager_can_approve() -> None:
    proposer = Actor(actor_id="u-ops-001", role="ops", display_name="Ops One")
    approver = Actor(actor_id="u-mgr-001", role="manager", display_name="Manager One")
    allowed, reason = ApprovalPolicy(policy_version="M1").can_approve(
        proposer=proposer,
        approver=approver,
    )
    assert allowed is True
    assert reason == "approved_by_manager"


def test_non_manager_policy_requires_manager_role() -> None:
    proposer = Actor(actor_id="u-ops-001", role="ops", display_name="Ops One")
    approver = Actor(actor_id="u-cs-001", role="cs", display_name="CS One")
    allowed, reason = ApprovalPolicy(policy_version="M1").can_approve(
        proposer=proposer,
        approver=approver,
    )
    assert allowed is False
    assert reason == "manager_role_required"


def test_next_stable_id_does_not_depend_on_row_count() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table task(task_id text primary key)")
    first = next_stable_id(prefix="TSK", entropy="risk-1|assign|2026-07-07")
    second = next_stable_id(prefix="TSK", entropy="risk-1|assign|2026-07-07")
    assert first == second
    conn.execute("insert into task(task_id) values (?)", (first,))
    third = next_stable_id(prefix="TSK", entropy="risk-2|assign|2026-07-07")
    assert third != first


def test_transaction_rolls_back_on_failure() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table audit_log(action_id text primary key, result text)")
    try:
        with transaction(conn):
            conn.execute(
                "insert into audit_log(action_id, result) values (?, ?)",
                ("ACT-1", "started"),
            )
            raise RuntimeError("forced failure")
    except RuntimeError:
        pass
    rows = conn.execute("select * from audit_log").fetchall()
    assert rows == []


def test_nested_transaction_rolls_back_only_inner_failed_block() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table audit_log(action_id text primary key, result text)")
    conn.execute(
        "insert into audit_log(action_id, result) values (?, ?)",
        ("OUTER-1", "started"),
    )
    assert conn.in_transaction is True
    try:
        with transaction(conn):
            conn.execute(
                "insert into audit_log(action_id, result) values (?, ?)",
                ("INNER-1", "started"),
            )
            raise RuntimeError("forced inner failure")
    except RuntimeError:
        pass

    assert conn.in_transaction is True
    conn.commit()
    rows = conn.execute("select * from audit_log").fetchall()
    assert rows == [("OUTER-1", "started")]


def test_can_approve_actor_helper_preserves_legacy_unknown_proposer() -> None:
    allowed, reason = can_approve_actor(
        proposer_actor_id=None,
        approver_actor_id="u-mgr-001",
        approver_role="manager",
    )
    assert allowed is True
    assert reason == "legacy_role_only"


def test_can_approve_actor_helper_blocks_same_named_actor() -> None:
    allowed, reason = can_approve_actor(
        proposer_actor_id="u-ops-001",
        approver_actor_id="u-ops-001",
        approver_role="manager",
    )
    assert allowed is False
    assert reason == "maker_checker_violation"


def test_can_approve_actor_helper_requires_manager_role() -> None:
    allowed, reason = can_approve_actor(
        proposer_actor_id="u-ops-001",
        approver_actor_id="u-cs-001",
        approver_role="cs",
    )
    assert allowed is False
    assert reason == "manager_role_required"


def test_same_named_actor_cannot_approve_known_mitigation_proposal() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""create table risk_events(
        risk_event_id text primary key,
        type text,
        rule_id text,
        severity text,
        shipment_id text,
        affected_so_line_ids text,
        affected_value_usd real,
        detected_at text,
        root_cause text,
        status text,
        resolved_at text,
        outcome text,
        resolution_summary text,
        affected_invoice_line_ids text
    )""")
    conn.execute("""create table tasks(
        task_id text primary key,
        risk_event_id text,
        title text,
        assignee_role text,
        priority text,
        due_at text,
        proposed_action text,
        proposal_params text,
        approval_status text,
        approved_by_role text,
        action_taken text,
        status text
    )""")
    conn.execute("""create table action_log(
        log_id integer primary key autoincrement,
        actor text,
        role text,
        action text,
        target_object_id text,
        params_json text,
        as_of_date text,
        timestamp text,
        result text
    )""")
    conn.execute("""insert into risk_events values (
        'RSK-1', 'delay_breach', 'R1', 'high', 'SHP-1', '[]', 0,
        '2026-07-07', 'test risk', 'open', null, null, null, null
    )""")
    conn.commit()

    assigned = assign_task(
        conn, "RSK-1", "ops", "P1", "2026-07-06",
        actor="u-ops-001", role="ops", as_of="2026-07-07",
    )
    assert assigned["ok"] is True
    assigned_task = conn.execute(
        """select assignee_user_id, assignee_team_id, sla_state, escalation_level,
                  policy_version
           from tasks where task_id=?""",
        (assigned["object_id"],),
    ).fetchone()
    assert assigned_task["assignee_user_id"] == "u-ops-us"
    assert assigned_task["assignee_team_id"] == "team-ops-us"
    assert assigned_task["sla_state"] == "overdue"
    assert assigned_task["escalation_level"] == 1
    assert assigned_task["policy_version"] == "M2-demo-work-queue-v1"
    proposed = propose_mitigation(
        conn, assigned["object_id"], "accept_delay", {"reason": "test"},
        actor="u-ops-001", role="ops", as_of="2026-07-07",
    )
    assert proposed["ok"] is True

    approved = approve_mitigation(
        conn, assigned["object_id"], "approved", "same actor tries manager approval",
        actor="u-ops-001", role="manager", as_of="2026-07-07",
    )
    task = conn.execute(
        "select status, approval_status, proposal_actor_id from tasks where task_id=?",
        (assigned["object_id"],),
    ).fetchone()
    rejected = conn.execute("""select count(*) from action_log
                               where action='ApproveMitigation'
                               and result like 'rejected:%maker_checker_violation%'""").fetchone()[0]

    assert approved["ok"] is False
    assert "maker_checker_violation" in approved["error"]
    assert task["status"] == "in_progress"
    assert task["approval_status"] == "pending"
    assert task["proposal_actor_id"] == "u-ops-001"
    assert rejected == 1


def test_assign_task_does_not_hide_bad_shipment_schema() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""create table risk_events(
        risk_event_id text primary key,
        type text,
        rule_id text,
        severity text,
        shipment_id text,
        affected_so_line_ids text,
        affected_value_usd real,
        detected_at text,
        root_cause text,
        status text,
        resolved_at text,
        outcome text,
        resolution_summary text,
        affected_invoice_line_ids text
    )""")
    conn.execute("""create table tasks(
        task_id text primary key,
        risk_event_id text,
        title text,
        assignee_role text,
        priority text,
        due_at text,
        proposed_action text,
        proposal_params text,
        approval_status text,
        approved_by_role text,
        action_taken text,
        status text
    )""")
    conn.execute("""create table action_log(
        log_id integer primary key autoincrement,
        actor text,
        role text,
        action text,
        target_object_id text,
        params_json text,
        as_of_date text,
        timestamp text,
        result text
    )""")
    conn.execute("create table shipments(shipment_id text primary key)")
    conn.execute("""insert into risk_events values (
        'RSK-1', 'delay_breach', 'R1', 'high', 'SHP-1', '[]', 0,
        '2026-07-07', 'test risk', 'open', null, null, null, null
    )""")
    conn.commit()

    try:
        assign_task(
            conn, "RSK-1", "ops", "P1", "2026-07-07",
            actor="u-ops-001", role="ops", as_of="2026-07-07",
        )
    except sqlite3.OperationalError as exc:
        assert "destination_port_locode" in str(exc)
    else:
        assert False, "bad shipments schema should not silently fall back to demo region"


if __name__ == "__main__":
    test_manager_cannot_approve_own_proposal()
    test_different_manager_can_approve()
    test_non_manager_policy_requires_manager_role()
    test_next_stable_id_does_not_depend_on_row_count()
    test_transaction_rolls_back_on_failure()
    test_nested_transaction_rolls_back_only_inner_failed_block()
    test_can_approve_actor_helper_preserves_legacy_unknown_proposer()
    test_can_approve_actor_helper_blocks_same_named_actor()
    test_can_approve_actor_helper_requires_manager_role()
    test_same_named_actor_cannot_approve_known_mitigation_proposal()
    test_assign_task_does_not_hide_bad_shipment_schema()
    print("ok")
