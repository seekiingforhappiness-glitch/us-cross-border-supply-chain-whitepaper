"""口径收敛属性测试：python3 -m app.test_scope_parity

证明「agent 数据范围 == UI 数据范围」（逐角色、逐字一致）——Foundry 理想：一个角色在 UI
看得到什么，它的对象级 agent 就看得到什么，一分不多一分不少。对同一对象、同一 role，比较
agent 读工具（agent/tools.AgentSession）与 UI 富工作台（app/object_workbench.build_*）的脱敏结论：

① 发票成本：get_invoice_context ⟺ build_invoice_workbench（total + 逐行金额/基准/差异）
② 客户等级：get_impact_chain ⟺ build_workbench（受影响行 tier）
③ 准入成本：get_admission_context ⟺ build_admission_workbench（成本情景报价/毛利）

覆盖 ops/finance/manager 三角色（tier 另加 cs 佐证）。核心断言：**同一 role 下两侧掩码判定完全相同**，
且方向正确（ops 掩码成本、finance/manager 见成本；ops/finance 掩码 tier、cs/manager 见 tier）。

只读断言 + 临时副本（绝不污染 data/ontology.sqlite）。不改任何 ROLE_PERMS/对象/规则/KPI。
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import object_workbench as owb
from .actions import ROLE_PERMS
from agent.tools import AgentSession, MASK

FAILS = []
INV = "INV-2026-00001"   # SHP-2026-0001，有账单行金额/基准/差异（费用对账锚点）
RID = "RSK-0068"         # SHP-2026-0099 R1，受影响行 SOL-0188-1，客户 CUS-0007 tier=A
AID = "AC-2026-0031"     # priced DDP 案，成本情景 CS-00034/35/36

EXPECTED_ROLE_PERMS = {  # manual §6 + cost-manual §5 + P4（ProposeMitigation +procurement）
    "AssignTask": {"ops", "system"},
    "ProposeMitigation": {"ops", "cs", "finance", "procurement"},
    "ApproveMitigation": {"manager"},
    "CloseRiskEvent": {"ops"},
}


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def masked(v):
    return v == MASK


def main():
    tmp = Path(tempfile.mkdtemp()) / "parity.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    def agent(role):
        return AgentSession(db_path=str(tmp), role=role)

    print("== ① 发票成本脱敏：get_invoice_context ⟺ build_invoice_workbench ==")
    for role in ("ops", "finance", "manager"):
        a = agent(role).dispatch("get_invoice_context", {"invoice_id": INV})
        u = owb.build_invoice_workbench(con, INV, role)
        check(f"① [{role}] 无 error", "error" not in a and "error" not in u,
              str(a.get("error") or u.get("error")))
        a_total_masked, u_total_masked = masked(a["invoice"]["total_usd"]), masked(u["invoice"]["total_usd"])
        check(f"① [{role}] total_usd 掩码判定 agent==UI（逐字一致）",
              a_total_masked == u_total_masked, f"agent={a_total_masked} UI={u_total_masked}")
        # 逐行对齐（按 invoice_line_id）比较 amount/baseline/diff 的掩码判定
        u_by_id = {l["invoice_line_id"]: l for l in u["lines"]}
        line_parity = True
        for al in a["lines"]:
            ul = u_by_id.get(al["invoice_line_id"])
            if ul is None:
                line_parity = False
                break
            for f in ("amount_usd", "baseline_usd", "diff_usd"):
                if masked(al.get(f)) != masked(ul.get(f)):
                    line_parity = False
        check(f"① [{role}] 逐行金额/基准/差异掩码判定 agent==UI", line_parity)
        # 方向：ops 两侧都掩码；finance/manager 两侧都见真数
        if role == "ops":
            check("① [ops] 成本对 agent 与 UI 双双掩码（一分不多）",
                  a_total_masked and u_total_masked
                  and all(masked(l["amount_usd"]) for l in a["lines"])
                  and all(masked(l["amount_usd"]) for l in u["lines"]))
        else:
            check(f"① [{role}] 成本对 agent 与 UI 双双可见（一分不少）",
                  not a_total_masked and not u_total_masked
                  and all(isinstance(l["amount_usd"], (int, float)) for l in a["lines"])
                  and all(isinstance(l["amount_usd"], (int, float)) for l in u["lines"]))

    print("== ② 客户等级脱敏：get_impact_chain ⟺ build_workbench ==")
    for role in ("ops", "finance", "manager", "cs"):
        a = agent(role).dispatch("get_impact_chain", {"risk_event_id": RID})
        u = owb.build_workbench(con, RID, role)
        a_line = next((x for x in a["affected"] if x["so_line_id"] == "SOL-0188-1"), None)
        u_line = next((x for x in u["affected_lines"] if x["so_line_id"] == "SOL-0188-1"), None)
        check(f"② [{role}] 锚点行 SOL-0188-1 两侧都在", a_line is not None and u_line is not None)
        a_tier_masked, u_tier_masked = masked(a_line["tier"]), masked(u_line["tier"])
        check(f"② [{role}] tier 掩码判定 agent==UI（逐字一致）",
              a_tier_masked == u_tier_masked, f"agent={a_line['tier']} UI={u_line['tier']}")
        # 方向：ops/finance 掩码（非 cs/manager）；cs/manager 见真 tier（=A）
        if role in ("ops", "finance"):
            check(f"② [{role}] tier 对 agent 与 UI 双双掩码", a_tier_masked and u_tier_masked)
        else:
            check(f"② [{role}] tier 对 agent 与 UI 双双可见（=A）",
                  a_line["tier"] == "A" and u_line["tier"] == "A",
                  f"agent={a_line['tier']} UI={u_line['tier']}")

    print("== ③ 准入成本脱敏：get_admission_context ⟺ build_admission_workbench ==")
    for role in ("ops", "finance", "manager"):
        a = agent(role).dispatch("get_admission_context", {"admission_case_id": AID})
        u = owb.build_admission_workbench(con, AID, role)
        check(f"③ [{role}] 成本情景两侧都非空",
              bool(a["cost_scenarios"]) and bool(u["cost_scenarios"]))
        u_by_id = {s["cost_scenario_id"]: s for s in u["cost_scenarios"]}
        parity, direction_ok = True, True
        for asc in a["cost_scenarios"]:
            usc = u_by_id.get(asc["cost_scenario_id"])
            if usc is None:
                parity = False
                break
            for f in ("quote_price_usd", "gross_margin_usd", "gross_margin_rate"):
                if masked(asc.get(f)) != masked(usc.get(f)):
                    parity = False
                want_masked = (role == "ops")
                if masked(asc.get(f)) != want_masked:
                    direction_ok = False
        check(f"③ [{role}] 报价/毛利/毛利率掩码判定 agent==UI（逐字一致）", parity)
        check(f"③ [{role}] 方向正确（{'ops 掩码' if role == 'ops' else role + ' 可见'}）", direction_ok)

    print("== ④ ROLE_PERMS 未改动（硬 gate 未削弱）==")
    check("④ ROLE_PERMS 与基线完全一致", ROLE_PERMS == EXPECTED_ROLE_PERMS, str(ROLE_PERMS))

    con.close()
    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔（agent 数据范围 == UI 数据范围）' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试，data/ontology.sqlite 未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
