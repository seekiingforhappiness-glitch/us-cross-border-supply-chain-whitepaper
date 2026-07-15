"""M7 outbox tests: python3 -m app.test_outbox"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import yaml

from .actions import assign_task, approve_mitigation, propose_mitigation
from .integration_actions import mark_outbox_failed, mark_outbox_succeeded
from pipeline.outbox import ensure_integration_outbox, enqueue_writeback

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_integration_outbox(conn)
    return conn


def scalar(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_enqueue_writeback_is_idempotent():
    conn = make_conn()
    payload_a = {"date": "2026-08-27", "notify_customer": True}
    payload_b = {"notify_customer": True, "date": "2026-08-27"}
    key1 = enqueue_writeback(conn, "oms_simulated", "update_promise_date", "SOL-1", payload_a, as_of="2026-08-08")
    key2 = enqueue_writeback(conn, "oms_simulated", "update_promise_date", "SOL-1", payload_b, as_of="2026-08-08")
    row = conn.execute(
        """SELECT payload_json, status, attempt_count, policy_version
           FROM integration_outbox WHERE idempotency_key=?""",
        (key1,),
    ).fetchone()
    check("enqueue returns same deterministic key for semantic same payload", key1 == key2)
    check("enqueue stores one row only", scalar(conn, "SELECT count(*) FROM integration_outbox") == 1)
    check("payload_json is canonical sort_keys JSON",
          row["payload_json"] == json.dumps(payload_a, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")))
    check("new outbox row starts pending with zero attempts",
          row["status"] == "pending" and row["attempt_count"] == 0
          and row["policy_version"] == "M7-simulated-outbox-v1")
    conflict = None
    try:
        enqueue_writeback(conn, "oms_simulated", "update_promise_date", "SOL-1",
                          {"date": "2026-08-28", "notify_customer": True}, as_of="2026-08-08")
    except ValueError as exc:
        conflict = str(exc)
    check("same business target with different payload is rejected",
          conflict and conflict.startswith("integration_outbox_conflict:"), str(conflict))
    check("payload conflict does not create another outbox row",
          scalar(conn, "SELECT count(*) FROM integration_outbox") == 1)


def test_mark_outbox_statuses():
    conn = make_conn()
    key = enqueue_writeback(conn, "oms_simulated", "update_promise_date", "SOL-1",
                            {"date": "2026-08-27"}, as_of="2026-08-08")
    succeeded = mark_outbox_succeeded(conn, key, as_of="2026-08-08")
    row = conn.execute(
        "SELECT status, attempt_count, last_error, updated_at FROM integration_outbox WHERE idempotency_key=?",
        (key,),
    ).fetchone()
    check("mark_outbox_succeeded returns ok", succeeded["ok"])
    check("success status increments attempt and clears error",
          tuple(row) == ("succeeded", 1, None, "2026-08-08T00:00:00Z"), tuple(row))

    repeated_success = mark_outbox_succeeded(conn, key, as_of="2026-08-09")
    row = conn.execute(
        "SELECT status, attempt_count, last_error, updated_at FROM integration_outbox WHERE idempotency_key=?",
        (key,),
    ).fetchone()
    check("repeated success is idempotent no-op", repeated_success["ok"])
    check("repeated success does not increment attempt",
          tuple(row) == ("succeeded", 1, None, "2026-08-08T00:00:00Z"), tuple(row))
    failed_after_success = mark_outbox_failed(conn, key, "simulated timeout", as_of="2026-08-09")
    check("succeeded outbox cannot be marked failed",
          not failed_after_success["ok"] and failed_after_success["error"] == "outbox_already_succeeded",
          failed_after_success)
    missing = mark_outbox_succeeded(conn, "OUT-MISSING", as_of="2026-08-08")
    check("missing outbox key returns testable error",
          not missing["ok"] and missing["error"] == "outbox_row_not_found", missing)

    failed_key = enqueue_writeback(conn, "oms_simulated", "update_promise_date", "SOL-2",
                                   {"date": "2026-08-29"}, as_of="2026-08-08")
    failed = mark_outbox_failed(conn, failed_key, "simulated timeout", as_of="2026-08-09")
    failed_row = conn.execute(
        "SELECT status, attempt_count, last_error, updated_at FROM integration_outbox WHERE idempotency_key=?",
        (failed_key,),
    ).fetchone()
    check("pending outbox can be marked failed", failed["ok"])
    check("failure status records error and increments attempt",
          tuple(failed_row) == ("failed", 1, "simulated timeout", "2026-08-09T00:00:00Z"),
          tuple(failed_row))


def test_approved_reschedule_enqueues_one_writeback():
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    as_of = cfg["window"]["as_of"]
    tmp = Path(tempfile.mkdtemp()) / "outbox-loop.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row

    risk = conn.execute(
        "SELECT * FROM risk_events WHERE shipment_id='SHP-2026-0099' AND rule_id='R1'"
    ).fetchone()
    if risk is None or risk["status"] != "open":
        print("前置不满足：请先运行 python3 -m pipeline.build_ontology && python3 -m engine.detect")
        sys.exit(2)

    assigned = assign_task(conn, risk["risk_event_id"], "ops", "P1", as_of,
                           actor="daniel", role="ops", as_of=as_of)
    proposed = propose_mitigation(
        conn,
        assigned["object_id"],
        "reschedule",
        {"new_promise_date": "2026-08-27", "notify_customer": True},
        actor="daniel",
        role="ops",
        as_of=as_of,
    )
    approved = approve_mitigation(conn, assigned["object_id"], "approved", "同意改期并通知客户",
                                  actor="manager-li", role="manager", as_of=as_of)
    rows = list(conn.execute("SELECT * FROM integration_outbox ORDER BY idempotency_key"))
    check("approved reschedule flow succeeds",
          assigned["ok"] and proposed["ok"] and approved["ok"])
    check("approved reschedule creates exactly one simulated writeback", len(rows) == 1,
          [dict(r) for r in rows])

    if rows:
        payload = json.loads(rows[0]["payload_json"])
        affected = sorted(json.loads(risk["affected_so_line_ids"]))
        replay_key = enqueue_writeback(
            conn,
            rows[0]["target_system"],
            rows[0]["action_name"],
            rows[0]["target_object"],
            payload,
            as_of=as_of,
        )
        check("reschedule outbox payload names affected SO lines and promise date",
              payload["so_line_ids"] == affected
              and payload["new_promise_date"] == "2026-08-27"
              and payload["notify_customer"] is True,
              payload)
        check("manual replay of same writeback keeps same idempotency key",
              replay_key == rows[0]["idempotency_key"])
        check("manual replay does not duplicate writeback row",
              scalar(conn, "SELECT count(*) FROM integration_outbox") == 1)


def test_approve_mitigation_survives_outbox_conflict():
    """fix-3：outbox 入队冲突时 approve_mitigation 不得穿透异常，须回滚并结构化失败返回。"""
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    as_of = cfg["window"]["as_of"]
    tmp = Path(tempfile.mkdtemp()) / "outbox-conflict.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row

    risk = conn.execute(
        "SELECT * FROM risk_events WHERE shipment_id='SHP-2026-0099' AND rule_id='R1'"
    ).fetchone()
    if risk is None or risk["status"] != "open":
        print("前置不满足：请先运行 python3 -m pipeline.build_ontology && python3 -m engine.detect")
        sys.exit(2)

    assigned = assign_task(conn, risk["risk_event_id"], "ops", "P1", as_of,
                           actor="daniel", role="ops", as_of=as_of)
    task_id = assigned["object_id"]
    propose_mitigation(conn, task_id, "reschedule",
                       {"new_promise_date": "2026-08-27", "notify_customer": True},
                       actor="daniel", role="ops", as_of=as_of)
    # 预置同一 Task 目标、不同 payload 的 outbox 行 → 逼近 approve 时 enqueue 抛 conflict
    enqueue_writeback(conn, "oms_simulated", "update_promise_date", f"Task:{task_id}",
                      {"sabotage": True}, as_of=as_of)

    raised = None
    try:
        approved = approve_mitigation(conn, task_id, "approved", "改期",
                                      actor="manager-li", role="manager", as_of=as_of)
    except Exception as exc:  # noqa: BLE001 —— 契约就是不该抛
        raised, approved = exc, None
    check("approve 不因 outbox 冲突穿透异常（从不抛异常契约）", raised is None, repr(raised))
    check("approve 在 outbox 冲突时返回 ok=False", approved is not None and not approved["ok"], approved)

    task_after = conn.execute(
        "SELECT status, approval_status FROM tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    check("失败审批回滚：task 未进入 done/approved（原子性）",
          task_after and task_after["approval_status"] == "pending"
          and task_after["status"] != "done",
          tuple(task_after) if task_after else None)
    check("失败审批未新增 outbox 行（仅预置的 sabotage 行）",
          scalar(conn, "SELECT count(*) FROM integration_outbox") == 1)
    fail_log = conn.execute(
        """SELECT result FROM action_log WHERE action='ApproveMitigation'
           AND target_object_id=? ORDER BY log_id DESC LIMIT 1""", (task_id,)
    ).fetchone()
    check("失败审批写了 rejected 审计留痕",
          fail_log and fail_log["result"].startswith("rejected:"),
          dict(fail_log) if fail_log else None)


def main():
    print("== enqueue idempotency ==")
    test_enqueue_writeback_is_idempotent()
    print("== outbox status actions ==")
    test_mark_outbox_statuses()
    print("== approved reschedule writeback ==")
    test_approved_reschedule_enqueues_one_writeback()
    print("== approve 冲突不穿透（fix-3）==")
    test_approve_mitigation_survives_outbox_conflict()
    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
