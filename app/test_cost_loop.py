"""X3 费用闭环无头测试：python3 -m app.test_cost_loop

在 ontology.sqlite 临时副本上验证 demo-assertions-cost 的 XC 段（同一风险队列复用
控制塔动作层：AssignTask/ProposeMitigation/ApproveMitigation/CloseRiskEvent + G4 门禁 +
发票状态回写）。模式仿 app/test_closed_loop / test_admission_loop。
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
    tmp = Path(tempfile.mkdtemp()) / "cost.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    def q1(sql, *a):
        return con.execute(sql, a).fetchone()

    # 前置守卫：CD-A 的 R4 风险必须存在且 open
    cda = q1("SELECT * FROM risk_events WHERE shipment_id='SHP-2026-0001' AND rule_id='R4'")
    if cda is None or cda["status"] != "open":
        print("前置不满足：CD-A 的 R4 风险不存在或非 open。\n"
              "请先重建：python3 -m pipeline.build_ontology && python3 -m engine.detect")
        sys.exit(2)
    cda_rid = cda["risk_event_id"]
    cda_val = cda["affected_value_usd"]

    print("== XC1 费用异常与控制塔风险同处一个队列 ==")
    logi = q1("SELECT count(*) c FROM risk_events WHERE rule_id IN ('R1','R2','R3')")["c"]
    cost = q1("SELECT count(*) c FROM risk_events WHERE rule_id IN ('R4','R5','R6')")["c"]
    check("XC1 risk_events 同时含 R1-R3 与 R4-R6（同队列，F1）", logi > 0 and cost > 0,
          f"logistics={logi} cost={cost}")

    print("== XC2 前半 CD-A dispute 全链：assign→propose(finance)→approve→发票 disputed→close ==")
    r = assign_task(con, cda_rid, "ops", "P1", as_of, actor="daniel", role="ops", as_of=as_of)
    cda_tid = r["object_id"]
    check("XC2a 派单成功（ops）", r["ok"] and cda_tid)
    r = propose_mitigation(con, cda_tid, "dispute",
                           {"reason": "OFT 超基准 30%", "disputed_amount_usd": cda_val},
                           actor="fin-anna", role="finance", as_of=as_of)
    check("XC2b finance 提 dispute 成功（P3）", r["ok"])
    r = approve_mitigation(con, cda_tid, "approved", "同意进入争议", actor="manager-li",
                           role="manager", as_of=as_of)
    inv_status = [x["status"] for x in con.execute(
        """SELECT DISTINCT iv.status FROM invoices iv JOIN invoice_lines il
           ON il.invoice_id=iv.invoice_id WHERE il.invoice_line_id IN ('IL-000001','IL-000003','IL-000005')""")]
    check("XC2c 批准 dispute → 受影响发票 status=disputed（§2 状态机）",
          r["ok"] and inv_status == ["disputed"], str(inv_status))
    r = close_risk_event(con, cda_rid, "mitigated", "已进入争议流程，追款由系统边界外处理",
                         actor="daniel", role="ops", as_of=as_of)
    check("XC2d CD-A 风险关闭 mitigated（复用 A6）",
          r["ok"] and q1("SELECT status FROM risk_events WHERE risk_event_id=?", cda_rid)["status"] == "resolved")

    print("== XC2 后半 非设计 R6 accept_charge → 发票 approved ==")
    # SHP-2026-0010 R6 medium（CHS $120，无设计案例，CIF）——accept_charge 无 G4 约束
    ac_rid = q1("SELECT risk_event_id FROM risk_events WHERE shipment_id='SHP-2026-0010' AND rule_id='R6'")["risk_event_id"]
    r = assign_task(con, ac_rid, "ops", "P3", as_of, actor="daniel", role="ops", as_of=as_of)
    ac_tid = r["object_id"]
    r = propose_mitigation(con, ac_tid, "accept_charge", {"reason": "金额小，照付"},
                           actor="fin-anna", role="finance", as_of=as_of)
    r = approve_mitigation(con, ac_tid, "approved", "照付", actor="manager-li",
                           role="manager", as_of=as_of)
    ac_inv = [x["status"] for x in con.execute(
        """SELECT DISTINCT iv.status FROM invoices iv JOIN invoice_lines il
           ON il.invoice_id=iv.invoice_id WHERE il.invoice_line_id='IL-000127'""")]
    check("XC2e accept_charge 批准 → 发票 approved", r["ok"] and ac_inv == ["approved"], str(ac_inv))

    print("== XC3 CD-C rebill(FOB, DET∈可转嫁集) → G4 放行，审批通过 ==")
    cdc_rid = q1("SELECT risk_event_id FROM risk_events WHERE shipment_id='SHP-2026-0009' AND rule_id='R6'")["risk_event_id"]
    r = assign_task(con, cdc_rid, "ops", "P2", as_of, actor="daniel", role="ops", as_of=as_of)
    cdc_tid = r["object_id"]
    r = propose_mitigation(con, cdc_tid, "rebill_customer",
                           {"rebill_amount_usd": 900.0, "incoterm_basis": "FOB"},
                           actor="fin-anna", role="finance", as_of=as_of)
    check("XC3a CD-C rebill 提案成功", r["ok"])
    r = approve_mitigation(con, cdc_tid, "approved", "FOB 下滞箱费可转嫁买方", actor="manager-li",
                           role="manager", as_of=as_of)
    cdc_inv = [x["status"] for x in con.execute(
        """SELECT DISTINCT iv.status FROM invoices iv JOIN invoice_lines il
           ON il.invoice_id=iv.invoice_id WHERE il.invoice_line_id='IL-000108'""")]
    check("XC3b G4 放行 + 批准通过 + 发票 approved",
          r["ok"] and cdc_inv == ["approved"], f"ok={r['ok']} err={r.get('error')} inv={cdc_inv}")

    print("== XC4 CD-D rebill(DDP) → G4 拒绝且审计留痕 ==")
    cdd_rid = q1("SELECT risk_event_id FROM risk_events WHERE shipment_id='SHP-2026-0012' AND rule_id='R6'")["risk_event_id"]
    r = assign_task(con, cdd_rid, "ops", "P2", as_of, actor="daniel", role="ops", as_of=as_of)
    cdd_tid = r["object_id"]
    r = propose_mitigation(con, cdd_tid, "rebill_customer",
                           {"rebill_amount_usd": 900.0, "incoterm_basis": "DDP"},
                           actor="fin-anna", role="finance", as_of=as_of)
    r = approve_mitigation(con, cdd_tid, "approved", "试图 DDP 转嫁", actor="manager-li",
                           role="manager", as_of=as_of)
    g4_denied = q1("""SELECT count(*) c FROM action_log WHERE action='ApproveMitigation'
                      AND target_object_id=? AND result LIKE 'rejected%'""", cdd_tid)["c"]
    dd_inv = [x["status"] for x in con.execute(
        """SELECT DISTINCT iv.status FROM invoices iv JOIN invoice_lines il
           ON il.invoice_id=iv.invoice_id WHERE il.invoice_line_id='IL-000140'""")]
    check("XC4 DDP rebill 被 G4 拒（错误含 G4）+ 审计留痕 + 发票未变",
          not r["ok"] and "G4" in (r["error"] or "") and g4_denied >= 1 and dd_inv != ["approved"],
          f"err={r.get('error')} denied={g4_denied} inv={dd_inv}")

    print("== XC5 sales 提 dispute → denied 且审计 ==")
    # 另取一个费用风险的任务（CD-B R5）派单后由 sales 提案
    cdb_rid = q1("SELECT risk_event_id FROM risk_events WHERE shipment_id='SHP-2026-0002' AND rule_id='R5'")["risk_event_id"]
    r = assign_task(con, cdb_rid, "ops", "P2", as_of, actor="daniel", role="ops", as_of=as_of)
    cdb_tid = r["object_id"]
    r = propose_mitigation(con, cdb_tid, "dispute", {"reason": "越权试提", "disputed_amount_usd": 365.0},
                           actor="sales-sam", role="sales", as_of=as_of)
    sales_denied = q1("""SELECT count(*) c FROM action_log WHERE action='ProposeMitigation'
                         AND result LIKE 'denied%'""")["c"]
    check("XC5 sales 提案被拒且审计留痕（finance 提案成功已由 CD-A 覆盖）",
          not r["ok"] and sales_denied >= 1)

    print("== XC6 CD-A 全链审计单查询（Match 发票 + 风险/任务链）时序完整 ==")
    # Match 的 target 是发票 id（INV-2026-00001），其余动作 target 是风险/任务 id
    chain = [x["action"] for x in con.execute(
        """SELECT action FROM action_log WHERE target_object_id IN (?,?,?)
           AND result IN ('ok','created') ORDER BY log_id""",
        ("INV-2026-00001", cda_rid, cda_tid))]
    check("XC6 Match→CreateRisk→Assign→Propose→Approve→Close 时序完整",
          chain == ["MatchInvoice", "CreateRiskEvent", "AssignTask", "ProposeMitigation",
                    "ApproveMitigation", "CloseRiskEvent"], str(chain))

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试，data/ontology.sqlite 未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
