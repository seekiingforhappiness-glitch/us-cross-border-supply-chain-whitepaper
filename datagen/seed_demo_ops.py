"""enrich demo：运营快照种子（build_ontology + engine.detect 之后运行）。

为什么这样建（≤5 行）：
- 控制塔的 RBAC/数据范围/KPI 管道已做真，但跑在空 tasks 上 → 队列空、切角色无差别、KPI 一排 0。
- 本脚本只写「运营状态表」tasks（+ 轻量 action_log 留痕），造多实名 owner、混合 SLA、逾期升级的真实快照。
- 铁律：绝不碰 ground truth（expected_risk_events / injected_noise_log）与 risk_events 对象表；
  留 SHP-2026-0099 的 R1 风险 open 且不派单（Daniel 手工走查专用）。
- 幂等：确定性 task_id + 每次先清自己造的种子（policy_version / actor 标记）再造；as_of 显式取配置（D8）。
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
from datetime import date, timedelta

import yaml

try:  # 包上下文（python3 -m datagen.seed_demo_ops）
    from app.actions import DEMO_ROSTER
    from app.action_context import next_stable_id
    from app.work_queue import sla_state
except ImportError:  # 直接脚本执行兜底
    from actions import DEMO_ROSTER  # type: ignore
    from action_context import next_stable_id  # type: ignore
    from work_queue import sla_state  # type: ignore

DB = "data/ontology.sqlite"
CONFIG = "config/datagen.yaml"

# 种子标记：cleanup 只删本脚本造的行，绝不误伤真实 assign_task（其 policy_version 为 M2-demo-work-queue-v1）
SEED_POLICY_VERSION = "demo-ops-seed-v1"
SEED_ACTOR = "seed_demo_ops"
# Daniel 手工走查专用风险所在 shipment——种子不占用其任何风险（铁律 + test_closed_loop/test_outbox 依赖其 open 未派单）
PROTECTED_SHIPMENT = "SHP-2026-0099"
# 设计案例 CD-A..CD-E / AC 的 shipment——action-chain 测试（test_cost_loop/test_closed_loop）以其
# 风险为 open 未派单的 fixture；种子一并避让，既不撞测试也让设计案例对 Daniel 走查保持原始态。
RESERVED_SHIPMENTS = {
    PROTECTED_SHIPMENT,
    "SHP-2026-0001", "SHP-2026-0002", "SHP-2026-0009", "SHP-2026-0010", "SHP-2026-0012",
}

SEV_TO_PRIORITY = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P3"}
CONTROL_TOWER_RULES = ("R1", "R2", "R3")
COST_RULES = ("R4", "R5", "R6")
TERMINAL_STATUSES = ("done", "cancelled")

# 派单对象（实名 owner）——与 DEMO_ROSTER 一致；u-ops-us 拿最多，驱动「ops 我的任务」非空且严格少于 manager 全量
CONTROL_TOWER_OWNERS = [
    ("u-ops-us", "ops", "US"),
    ("u-ops-us", "ops", "US"),
    ("u-cs-us", "cs", "US"),
    ("u-ops-us-amelia", "ops", "US"),
    ("u-ops-cn-lin", "ops", "CN"),
    ("u-ops-us", "ops", "US"),
    ("u-cs-us-priya", "cs", "US"),
    ("u-ops-us-diego", "ops", "US"),
    ("u-ops-us", "ops", "US"),
    ("u-ops-cn", "ops", "CN"),
    ("u-cs-us", "cs", "US"),
    ("u-ops-us", "ops", "US"),
    ("u-ops-us-amelia", "ops", "US"),
    ("u-ops-us", "ops", "US"),
]
COST_OWNERS = [
    ("u-fin-us", "finance", "US"),
    ("u-fin-us-marcus", "finance", "US"),
    ("u-fin-us", "finance", "US"),
    ("u-fin-us-marcus", "finance", "US"),
    ("u-fin-us", "finance", "US"),
    ("u-fin-us-marcus", "finance", "US"),
]


def _team_id(role: str, region: str) -> str:
    return f"team-{role}-{region.lower()}"


def _sla_bucket(i: int) -> str:
    """按索引确定 SLA 桶，保证三态齐全（~20% overdue、~20% due_today、其余 open）。"""
    if i % 5 == 0:
        return "overdue"
    if i % 5 == 2:
        return "due_today"
    return "open"


def _status_for(i: int) -> str:
    """少量任务推进到不同状态，展示混合任务态；其余 assigned。"""
    if i in (8, 16):
        return "done"
    if i in (3, 9, 13):
        return "in_progress"
    return "assigned"


def _due_at(bucket: str, as_of: date, rng: random.Random) -> str:
    """SLA 桶 → due_at（UTC ISO8601）。open 用固定种子 jitter 2-5 天，可复现。"""
    if bucket == "overdue":
        d = as_of - timedelta(days=3)
    elif bucket == "due_today":
        d = as_of
    else:
        d = as_of + timedelta(days=2 + rng.randint(0, 3))
    return f"{d.isoformat()}T00:00:00Z"


def _proposal(as_of: date, value_usd: float) -> tuple[str, str]:
    """给 in_progress/done 任务一个可信的处置提案（不产生行级副作用、不改 risk）。"""
    params = {
        "new_mode": "air",
        "est_cost_usd": round(float(value_usd or 0) * 0.05 + 800, 2),
        "expected_new_eta": (as_of + timedelta(days=7)).isoformat(),
    }
    return "expedite", json.dumps(params, ensure_ascii=False)


def _select_risks(cur):
    """读现有 risk_events（避让全部 reserved shipment），按 rule 分控制塔/费用两组，稳定排序。"""
    rows = cur.execute(
        """SELECT risk_event_id, type, rule_id, severity, shipment_id, affected_value_usd
           FROM risk_events ORDER BY rule_id, risk_event_id"""
    ).fetchall()
    rows = [r for r in rows if r["shipment_id"] not in RESERVED_SHIPMENTS]
    ct = [r for r in rows if r["rule_id"] in CONTROL_TOWER_RULES]
    cost = [r for r in rows if r["rule_id"] in COST_RULES]
    return ct, cost


def _cleanup(cur):
    """幂等前置：只删本脚本造的 tasks 与 action_log。"""
    cur.execute("DELETE FROM tasks WHERE policy_version = ?", (SEED_POLICY_VERSION,))
    cur.execute("DELETE FROM action_log WHERE actor = ?", (SEED_ACTOR,))


def _build_plan(ct, cost, n_ct, n_cost):
    """把选中的风险与 (owner, sla, status) 计划 zip 成派单计划；全程确定性。"""
    picks = list(ct[:n_ct]) + list(cost[:n_cost])
    owners = (CONTROL_TOWER_OWNERS[:n_ct] + COST_OWNERS[:n_cost])
    plan = []
    for i, (risk, owner) in enumerate(zip(picks, owners)):
        plan.append({"i": i, "risk": risk, "owner": owner,
                     "bucket": _sla_bucket(i), "status": _status_for(i)})
    return plan


def run(db_path: str = DB, config_path: str = CONFIG, as_of: str | None = None) -> dict:
    """造运营快照，返回统计 dict。幂等：可重复调用不产生重复/翻倍。"""
    cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
    as_of_str = as_of or cfg["window"]["as_of"]   # D8：显式 as_of，禁系统时钟
    as_of_date = date.fromisoformat(as_of_str)
    dcfg = cfg.get("demo_ops", {}) or {}
    rng = random.Random(dcfg.get("seed", 20260808))
    n_ct = int(dcfg.get("n_control_tower", 14))
    n_cost = int(dcfg.get("n_cost", 6))
    ts = f"{as_of_str}T00:00:00Z"

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    ct, cost = _select_risks(cur)
    plan = _build_plan(ct, cost, n_ct, n_cost)

    _cleanup(cur)
    sla_dist, owner_dist, status_dist, escalations = {}, {}, {}, 0

    for item in plan:
        risk, (uid, role, region) = item["risk"], item["owner"]
        rid = risk["risk_event_id"]
        task_id = next_stable_id("TSK", f"{rid}|demo-ops-seed")
        due_at = _due_at(item["bucket"], as_of_date, rng)
        state = sla_state(due_at, as_of_str)            # 单一真相：从 due_at 反算 SLA
        status = item["status"]
        esc = 1 if state == "overdue" and status not in TERMINAL_STATUSES else 0
        priority = SEV_TO_PRIORITY.get(risk["severity"], "P3")
        title = f"处置 {risk['type']} @ {risk['shipment_id']}"

        proposed_action = proposal_params = approval_status = None
        approved_by_role = action_taken = proposal_actor_id = proposal_actor_role = None
        if status in ("in_progress", "done"):
            proposed_action, proposal_params = _proposal(as_of_date, risk["affected_value_usd"])
            proposal_actor_id, proposal_actor_role = uid, role
            if status == "in_progress":
                approval_status = "pending"
            else:  # done：展示已完成的 maker-checker 一轮（仅任务态，不动 risk）
                approval_status = "approved"
                approved_by_role = "manager"
                action_taken = f"{proposed_action} approved: {proposal_params}"

        cur.execute(
            """INSERT INTO tasks
               (task_id, risk_event_id, title, assignee_role, priority, due_at,
                proposed_action, proposal_params, approval_status, approved_by_role,
                action_taken, status, assigned_by_actor_id, proposal_actor_id,
                proposal_actor_role, assignee_user_id, assignee_team_id, sla_state,
                escalation_level, policy_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, rid, title, role, priority, due_at, proposed_action, proposal_params,
             approval_status, approved_by_role, action_taken, status, "u-ops-us",
             proposal_actor_id, proposal_actor_role, uid, _team_id(role, region),
             state, esc, SEED_POLICY_VERSION),
        )
        cur.execute(
            """INSERT INTO action_log (actor, role, action, target_object_id, params_json,
               as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
            (SEED_ACTOR, "system", "AssignTask", task_id,
             json.dumps({"risk_event_id": rid, "assignee_user_id": uid,
                         "sla_state": state, "escalation_level": esc}, ensure_ascii=False),
             as_of_str, ts, "ok"),
        )
        sla_dist[state] = sla_dist.get(state, 0) + 1
        owner_dist[uid] = owner_dist.get(uid, 0) + 1
        status_dist[status] = status_dist.get(status, 0) + 1
        escalations += esc

    con.commit()
    con.close()
    return {"as_of": as_of_str, "tasks": len(plan), "sla_dist": sla_dist,
            "owner_dist": owner_dist, "status_dist": status_dist,
            "escalations": escalations, "protected_shipment": PROTECTED_SHIPMENT}


def main():
    ap = argparse.ArgumentParser(description="enrich demo 运营快照种子（只写 tasks/action_log）")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--as-of", default=None, help="默认取 config window.as_of（D8：禁系统时钟）")
    args = ap.parse_args()
    stats = run(args.db, args.config, args.as_of)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\n造了 {stats['tasks']} 个 task | as_of={stats['as_of']}")
    print(f"  SLA 分布   : {stats['sla_dist']}（escalation_level=1 共 {stats['escalations']} 个）")
    print(f"  owner 分布 : {stats['owner_dist']}")
    print(f"  状态 分布  : {stats['status_dist']}")
    print(f"  保护未派单 : {PROTECTED_SHIPMENT} 的 R1 风险仍 open（Daniel 手工走查专用）")


if __name__ == "__main__":
    main()
