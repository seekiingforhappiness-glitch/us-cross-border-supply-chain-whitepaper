"""运营快照种子测试：python3 -m datagen.test_seed_demo_ops

对真实库运行 seed_demo_ops（幂等，可重复跑），断言：
① 种子后 tasks>0 且 SLA 三态齐全、≥1 个 escalation_level=1
② 任务分布在 ≥3 个不同 assignee_user_id
③ SHP-2026-0099 的 R1 风险仍 open、无 task（Daniel 手工走查专用）
④ 幂等：连跑两次 seed，task 数不翻倍
⑤ 种子只动 tasks/action_log —— expected_risk_events 行数、risk_events 计数不变
"""
import sqlite3
import sys

from datagen.seed_demo_ops import PROTECTED_SHIPMENT, SEED_POLICY_VERSION, run

DB = "data/ontology.sqlite"
TRUTH = "data/truth/expected_risk_events.csv"
FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _con():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def main():
    # 基线：ground truth 行数 + risk_events 计数（种子跑完后必须不变）
    truth_before = sum(1 for _ in open(TRUTH, encoding="utf-8"))
    con = _con()
    risk_before = con.execute("SELECT count(*) c FROM risk_events").fetchone()["c"]
    con.close()

    print("== 运行种子（第 1 次）==")
    stats = run()
    print(f"  seed 统计: tasks={stats['tasks']} sla={stats['sla_dist']} "
          f"escalations={stats['escalations']}")

    con = _con()
    tasks = [dict(r) for r in con.execute("SELECT * FROM tasks")]
    seed_tasks = [t for t in tasks if t["policy_version"] == SEED_POLICY_VERSION]

    print("== ① tasks 非空 + SLA 三态齐全 + ≥1 escalation ==")
    check("tasks 数 > 0", len(seed_tasks) > 0, str(len(seed_tasks)))
    sla_states = {t["sla_state"] for t in seed_tasks}
    check("SLA 三态齐全（open/due_today/overdue）",
          {"open", "due_today", "overdue"} <= sla_states, str(sorted(sla_states)))
    esc = [t for t in seed_tasks if t["escalation_level"] == 1]
    check("≥1 个 escalation_level=1", len(esc) >= 1, str(len(esc)))
    check("升级任务均为 overdue 且非终态",
          all(t["sla_state"] == "overdue" and t["status"] not in ("done", "cancelled")
              for t in esc))

    print("== ② 任务分布在 ≥3 个不同 assignee_user_id ==")
    assignees = {t["assignee_user_id"] for t in seed_tasks}
    check("≥3 个不同 assignee_user_id", len(assignees) >= 3, str(sorted(assignees)))
    check("含至少 1 个 CN owner（team-*-cn）",
          any((t["assignee_team_id"] or "").endswith("-cn") for t in seed_tasks),
          str(sorted({t["assignee_team_id"] for t in seed_tasks})))

    print("== ③ SHP-2026-0099 的 R1 风险仍 open、无 task ==")
    prot_risk = con.execute(
        "SELECT * FROM risk_events WHERE shipment_id=? AND rule_id='R1'",
        (PROTECTED_SHIPMENT,)).fetchone()
    check("受保护风险存在且仍 open",
          prot_risk is not None and prot_risk["status"] == "open",
          prot_risk["status"] if prot_risk else "缺失")
    prot_tasks = con.execute(
        "SELECT count(*) c FROM tasks WHERE risk_event_id=?",
        (prot_risk["risk_event_id"],)).fetchone()["c"] if prot_risk else -1
    check("受保护风险无任何 task", prot_tasks == 0, str(prot_tasks))
    con.close()

    print("== ④ 幂等：连跑两次 seed，task 数不翻倍 ==")
    n1 = len(seed_tasks)
    run()  # 第 2 次
    con = _con()
    n2 = con.execute("SELECT count(*) c FROM tasks WHERE policy_version=?",
                     (SEED_POLICY_VERSION,)).fetchone()["c"]
    risk_after = con.execute("SELECT count(*) c FROM risk_events").fetchone()["c"]
    con.close()
    check("两次 seed 后种子 task 数相等（不翻倍）", n1 == n2, f"{n1} -> {n2}")

    print("== ⑤ 种子只动 tasks/action_log：ground truth 与 risk_events 不变 ==")
    truth_after = sum(1 for _ in open(TRUTH, encoding="utf-8"))
    check("expected_risk_events 行数不变", truth_before == truth_after,
          f"{truth_before} -> {truth_after}")
    check("risk_events 计数不变（未碰对象表）", risk_before == risk_after,
          f"{risk_before} -> {risk_after}")

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
