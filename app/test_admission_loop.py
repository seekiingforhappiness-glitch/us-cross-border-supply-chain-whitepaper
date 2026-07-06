"""V3 准入闭环无头测试：python3 -m app.test_admission_loop

在 ontology.sqlite 临时副本上验证 demo-assertions-admission 的 AA/AB/AC 段
（AC2 字段脱敏留 UI 走查；AC4 已由 SQL 验证；AC5=全量回归另跑）。
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import yaml

from .admission_actions import (create_admission_case, run_compliance_precheck,
                                build_logistics_plan, calculate_cost_scenario,
                                approve_quote_decision, reject_or_request_more_info)

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


DAP_PLAN = {"plan_name": "海运整柜/DAP", "route_type": "ocean_fcl", "incoterm": "DAP",
            "origin_port_locode": "CNYTN", "destination_port_locode": "USLAX",
            "us_warehouse_region": "west", "last_mile_method": "UPS",
            "estimated_transit_days": 32, "sla_risk": "low"}
COSTS = {"product_cost_usd": 5000, "first_mile_cost_usd": 300, "international_freight_usd": 1800,
         "duty_tax_usd": 650, "customs_brokerage_usd": 180, "warehouse_cost_usd": 400,
         "last_mile_cost_usd": 900, "returns_allowance_usd": 150, "risk_buffer_usd": 250}


def main():
    as_of = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))["window"]["as_of"]
    tmp = Path(tempfile.mkdtemp()) / "adm.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    def q1(sql, *a):
        return con.execute(sql, a).fetchone()

    kid = q1("""SELECT sku_id FROM skus WHERE sku_status='candidate'
                AND sku_id NOT IN ('SKU-9001','SKU-9002','SKU-9003','SKU-9004','SKU-9005')
                ORDER BY sku_id""")["sku_id"]

    print("== AA. happy path（新建案件全流程）==")
    r = create_admission_case(con, "CUS-0007", kid, "ddp_quote", "DDP", "2026-10-01", 1200,
                              actor="daniel", role="sales", as_of=as_of)
    aid = r["object_id"]
    bad = create_admission_case(con, "CUS-0007", "SKU-0001", "new_sku", "FOB", "2026-10-01", 500,
                                actor="daniel", role="sales", as_of=as_of)
    check("AA1 建案 draft + active SKU 建案被拒", r["ok"]
          and q1("SELECT status FROM admission_cases WHERE admission_case_id=?", aid)["status"] == "draft"
          and not bad["ok"])
    r = run_compliance_precheck(con, aid, [
        {"finding_title": "HTS 归类", "finding_type": "hts", "severity": "low",
         "hts_candidate": "8504.40.95", "evidence_status": "verified", "recommendation": "accept"},
        {"finding_title": "FCC", "finding_type": "pga", "severity": "low", "pga_agency": "FCC",
         "evidence_status": "verified", "recommendation": "accept"}],
        actor="daniel", role="compliance", as_of=as_of)
    case = q1("SELECT * FROM admission_cases WHERE admission_case_id=?", aid)
    check("AA2 预审：2 findings 入库 / in_precheck / risk=low", r["ok"]
          and case["status"] == "in_precheck" and case["risk_level"] == "low")
    r = build_logistics_plan(con, aid, {**DAP_PLAN, "plan_name": "海运整柜/DDP", "incoterm": "DDP"},
                             actor="daniel", role="ops", as_of=as_of)
    pid = r["object_id"]
    check("AA3 DDP 方案通过门禁（has_ior+HTS）/ plan_ready", r["ok"]
          and q1("SELECT status FROM admission_cases WHERE admission_case_id=?", aid)["status"] == "plan_ready")
    sids = []
    for stype, quote in (("conservative", 10200), ("base", 10600), ("optimistic", 11000)):
        r = calculate_cost_scenario(con, pid, {"scenario_type": stype, "quote_price_usd": quote, **COSTS},
                                    actor="daniel", role="finance", as_of=as_of)
        sids.append(r["object_id"])
    s = q1("SELECT * FROM cost_scenarios WHERE cost_scenario_id=?", sids[1])
    check("AA4 三情景入库 / 毛利=报价−成本（10600−9630=970）/ priced",
          all(sids) and abs(s["gross_margin_usd"] - 970.0) < 0.01
          and q1("SELECT status FROM admission_cases WHERE admission_case_id=?", aid)["status"] == "priced")
    r = approve_quote_decision(con, aid, pid, sids[1], "approve", "三情景毛利为正，DDP 条件齐备",
                               "", actor="manager-li", role="manager", as_of=as_of)
    case = q1("SELECT * FROM admission_cases WHERE admission_case_id=?", aid)
    check("AA5 批准 approved + 理由非空", r["ok"] and case["status"] == "approved"
          and case["decision_reason"] != "")
    check("AA6 E1 咬合：SKU candidate→active",
          q1("SELECT sku_status FROM skus WHERE sku_id=?", kid)["sku_status"] == "active")
    chain = [r["action"] for r in con.execute(
        """SELECT action FROM action_log WHERE target_object_id=? AND result='ok'
           ORDER BY log_id""", (aid,))]
    check("AA7 全链审计 B1→B2→B3→B4×3→B5 时序完整",
          chain == ["CreateAdmissionCase", "RunCompliancePrecheck", "BuildLogisticsPlan",
                    "CalculateCostScenario", "CalculateCostScenario", "CalculateCostScenario",
                    "ApproveQuoteDecision"], str(chain))

    print("== AB. 门禁反断言 ==")
    b = q1("""SELECT p.logistics_plan_id, s.cost_scenario_id FROM logistics_plans p
              JOIN cost_scenarios s ON s.logistics_plan_id=p.logistics_plan_id
              WHERE p.admission_case_id='AC-2026-0032' LIMIT 1""")
    r = approve_quote_decision(con, "AC-2026-0032", b["logistics_plan_id"], b["cost_scenario_id"],
                               "approve", "试图带 critical 批准", "", actor="manager-li",
                               role="manager", as_of=as_of)
    check("AB1 G1：critical 未核实 → 批准被拒", not r["ok"] and "G1" in r["error"])
    r = build_logistics_plan(con, "AC-2026-0033", {**DAP_PLAN, "incoterm": "DDP"},
                             actor="daniel", role="ops", as_of=as_of)
    check("AB2a G2：needs_partner 客户建 DDP 方案被拒", not r["ok"] and "IOR" in r["error"])
    r = build_logistics_plan(con, "AC-2026-0033", DAP_PLAN, actor="daniel", role="ops", as_of=as_of)
    check("AB2b 同案件 DAP 方案放行", r["ok"])
    r = approve_quote_decision(con, "AC-2026-0034", "LP-00001", "CS-00001", "approve", "跳步批准",
                               "", actor="manager-li", role="manager", as_of=as_of)
    check("AB3 G3：needs_more_info 案件批准被拒", not r["ok"] and "G3" in r["error"])
    a_plan = q1("""SELECT logistics_plan_id FROM logistics_plans
                   WHERE admission_case_id='AC-2026-0031' AND incoterm='DDP'""")
    r = calculate_cost_scenario(con, a_plan["logistics_plan_id"],
                                {"scenario_type": "base", "quote_price_usd": 9000,
                                 **{**COSTS, "duty_tax_usd": 0}},
                                actor="daniel", role="finance", as_of=as_of)
    check("AB4 DDP 成本门禁：duty_tax=0 被拒", not r["ok"] and "duty_tax" in r["error"])
    r = reject_or_request_more_info(con, "AC-2026-0033", "reject", [], "",
                                    actor="manager-li", role="manager", as_of=as_of)
    r2 = reject_or_request_more_info(con, "AC-2026-0033", "more_info", [], "",
                                     actor="daniel", role="compliance", as_of=as_of)
    check("AB5 B6 校验：无理由拒接被拒 / 无清单补资料被拒", not r["ok"] and not r2["ok"])
    # AB6 回流：AC-2026-0034 补件 → 走到终态
    r = run_compliance_precheck(con, "AC-2026-0034", [
        {"finding_title": "FDA 补件", "finding_type": "pga", "severity": "medium",
         "pga_agency": "FDA", "evidence_status": "verified", "recommendation": "accept"}],
        actor="daniel", role="compliance", as_of=as_of)
    r2 = build_logistics_plan(con, "AC-2026-0034", DAP_PLAN, actor="daniel", role="ops", as_of=as_of)
    r3 = calculate_cost_scenario(con, r2["object_id"], {"scenario_type": "base",
                                 "quote_price_usd": 12000, **COSTS},
                                 actor="daniel", role="finance", as_of=as_of)
    r4 = approve_quote_decision(con, "AC-2026-0034", r2["object_id"], r3["object_id"],
                                "approve", "补件后齐备", "", actor="manager-li", role="manager",
                                as_of=as_of)
    check("AB6 回流：needs_more_info→in_precheck→…→approved", all(x["ok"] for x in (r, r2, r3, r4)))
    # AC-DEMO-E：毛利为负如实呈现，经理附条件批准（门禁真值第 7 行）
    e = q1("""SELECT p.logistics_plan_id, s.cost_scenario_id FROM logistics_plans p
              JOIN cost_scenarios s ON s.logistics_plan_id=p.logistics_plan_id
              WHERE p.admission_case_id='AC-2026-0035' AND s.scenario_type='base'""")
    r = approve_quote_decision(con, "AC-2026-0035", e["logistics_plan_id"], e["cost_scenario_id"],
                               "quote_with_conditions", "conservative 毛利为负",
                               "月单量须≥3000 或上调报价 8%", actor="manager-li", role="manager",
                               as_of=as_of)
    check("AB7 毛利为负 → quote_with_conditions 附条件放行", r["ok"]
          and q1("SELECT status FROM admission_cases WHERE admission_case_id='AC-2026-0035'")["status"]
          == "quote_with_conditions")

    print("== AC. 权限断言 ==")
    r = approve_quote_decision(con, aid, pid, sids[1], "approve", "越权", "",
                               actor="daniel", role="sales", as_of=as_of)
    denied = q1("""SELECT count(*) c FROM action_log WHERE action='ApproveQuoteDecision'
                   AND result LIKE 'denied%'""")["c"]
    check("AC1 sales 审批被拒且审计留痕", not r["ok"] and denied >= 1)
    r = reject_or_request_more_info(con, "AC-2026-0004", "reject", [], "合规不达标",
                                    actor="daniel", role="compliance", as_of=as_of)
    check("AC3 compliance 拒接被拒（仅经理可拒）", not r["ok"])

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试；AC2 字段脱敏与 AC4/AC5 分别由 UI 走查、SQL 验证、全量回归覆盖）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
