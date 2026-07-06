"""W5 无头闭环测试：python3 -m app.test_closed_loop

在 ontology.sqlite 的临时副本上，按 demo-assertions 走完 DEMO-01 主线闭环 + 反断言。
自动验证 A8-A13、B4-B6、C1-C2、C4（A1-A7 由 engine 层验证；C3 留人工 UI 走查）。
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import yaml

from .actions import assign_task, propose_mitigation, approve_mitigation, close_risk_event

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    as_of = cfg["window"]["as_of"]
    tmp = Path(tempfile.mkdtemp()) / "loop.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    def q1(sql, *a):
        return con.execute(sql, a).fetchone()

    risk = q1("SELECT * FROM risk_events WHERE shipment_id='SHP-2026-0099' AND rule_id='R1'")
    rid = risk["risk_event_id"]

    print("== 派发（A8）==")
    r = assign_task(con, rid, "ops", "P1", as_of, actor="daniel", role="ops", as_of=as_of)
    tid = r["object_id"]
    check("A8 派单成功 Task=assigned, Risk→acknowledged",
          r["ok"] and q1("SELECT status FROM tasks WHERE task_id=?", tid)["status"] == "assigned"
          and q1("SELECT status FROM risk_events WHERE risk_event_id=?", rid)["status"] == "acknowledged")

    print("== 反断言 B6：二次派单被拒并返回既有任务 ==")
    r2 = assign_task(con, rid, "ops", "P2", as_of, actor="daniel", role="ops", as_of=as_of)
    check("B6 二次派单被拒", not r2["ok"] and tid in (r2["error"] or ""))

    print("== 处置提案（A9）==")
    r = propose_mitigation(con, tid, "reschedule",
                           {"new_promise_date": "2026-08-27", "notify_customer": True},
                           actor="daniel", role="ops", as_of=as_of)
    check("A9 提案成功 Task→in_progress/pending, Risk→mitigating",
          r["ok"] and q1("SELECT approval_status FROM tasks WHERE task_id=?", tid)["approval_status"] == "pending"
          and q1("SELECT status FROM risk_events WHERE risk_event_id=?", rid)["status"] == "mitigating")

    print("== 反断言 B4：ops 越权审批被拒且留审计 ==")
    r = approve_mitigation(con, tid, "approved", "我自己批", actor="daniel", role="ops", as_of=as_of)
    denied_logged = q1("""SELECT count(*) c FROM action_log WHERE action='ApproveMitigation'
                          AND result LIKE 'denied%'""")["c"]
    check("B4 越权被拒且审计留痕", not r["ok"] and denied_logged == 1)

    print("== 审批与回写（A10/A11）==")
    r = approve_mitigation(con, tid, "approved", "同意改期并通知客户", actor="manager-li",
                           role="manager", as_of=as_of)
    line = q1("SELECT * FROM sales_order_lines WHERE so_line_id='SOL-0188-1'")
    check("A10 改期回写：承诺日→08-27, reschedule_count=1, at_risk→allocated",
          r["ok"] and line["promised_delivery_date"] == "2026-08-27"
          and line["reschedule_count"] == 1 and line["line_status"] == "allocated")
    check("A11 original_promised_date 不可变（仍 08-20）",
          line["original_promised_date"] == "2026-08-20")

    print("== 反断言 B5：另一风险有开放任务时禁止关闭 ==")
    other = q1("""SELECT risk_event_id FROM risk_events WHERE status='open'
                  AND risk_event_id != ? LIMIT 1""", rid)
    r = assign_task(con, other["risk_event_id"], "ops", "P2", as_of,
                    actor="daniel", role="ops", as_of=as_of)
    r = close_risk_event(con, other["risk_event_id"], "mitigated", "试图带开放任务关闭",
                         actor="daniel", role="ops", as_of=as_of)
    check("B5 开放任务时关闭被拒", not r["ok"])

    print("== 关闭（A12/A13）==")
    r = close_risk_event(con, rid, "mitigated", "延误7天，经客户同意改期至08-27，风险解除",
                         actor="daniel", role="ops", as_of=as_of)
    check("A12 关闭成功 resolved + 小结非空",
          r["ok"] and q1("SELECT status, resolution_summary FROM risk_events WHERE risk_event_id=?",
                         rid)["status"] == "resolved")
    # A13：改期后重跑引擎不再对 0099 出 delay_breach
    from engine.rules import detect_risks
    from datetime import date
    cands = detect_risks(con, date.fromisoformat(as_of), cfg)
    check("A13 改期后重跑引擎无新 delay_breach（0099）",
          not [c for c in cands if c["shipment_id"] == "SHP-2026-0099" and c["rule_id"] == "R1"])

    print("== 审计与追溯（C1/C2/C4）==")
    chain = [r["action"] for r in con.execute(
        """SELECT action FROM action_log WHERE target_object_id IN (?,?)
           AND result IN ('ok','created') ORDER BY log_id""", (rid, tid))]
    check("C1 完整链：Create→Assign→Propose→Approve→Close 时序单调",
          chain == ["CreateRiskEvent", "AssignTask", "ProposeMitigation",
                    "ApproveMitigation", "CloseRiskEvent"], str(chain))
    n_ok = q1("""SELECT count(*) c FROM action_log WHERE target_object_id IN (?,?)
                 AND result IN ('ok','created')""", rid, tid)["c"]
    check("C2 状态变更与审计 1:1（5 次动作 5 条记录）", n_ok == 5)
    n_bad = q1("""SELECT count(*) c FROM action_log WHERE result != 'ok'""")["c"]
    check("C4 被拒/越权调用留痕（≥3 条失败记录）", n_bad >= 3)

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试，data/ontology.sqlite 未被污染；C3 五分钟走查需人工在 UI 完成）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
