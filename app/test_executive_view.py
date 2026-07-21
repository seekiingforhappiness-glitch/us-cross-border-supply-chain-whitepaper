"""全局 Executive 一页视图数据层测试：python3 -m app.test_executive_view

验证 app/executive_view.build_executive_summary（纯只读跨 5 场景聚合）：
① 结构完整——5 场景（延误/费用/采购/仓储/准入）+ 跨场景横条 cross 全部存在且非空
② 每个计数与 risk_events/tasks/invoices/… 表直查一致（不臆造、不偏离底表）
③ 纯读——返回结构里不出现任何 action/write 类键（呈现层无动作）

不改任何对象/规则/KPI/schema/权限；纯读断言。
"""
import sqlite3
import sys

from .executive_view import (build_executive_summary, DELAY_RULES, COST_RULES,
                             PROCUREMENT_RULES, WAREHOUSE_RULES, OPEN_RISK)

DB = "data/ontology.sqlite"
FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _open_in(con, rules):
    ph = ",".join("?" * len(rules))
    return con.execute(f"SELECT count(*) FROM risk_events WHERE {OPEN_RISK} "
                       f"AND rule_id IN ({ph})", rules).fetchone()[0]


def _scalar(con, sql, params=()):
    return con.execute(sql, params).fetchone()[0]


def _walk_keys(obj):
    """递归收集 dict 里所有键名（用于「无动作键」断言）。"""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            out |= _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _walk_keys(v)
    return out


def main():
    con = sqlite3.connect(DB)
    summary = build_executive_summary(con)

    print("== ① 结构完整：5 场景 + 跨场景横条 ==")
    for k in ("delay", "cost", "procurement", "warehouse", "admission", "cross"):
        check(f"含场景 {k}", k in summary and summary[k])

    print("== ② 延误运营（R1-R3）与底表一致 ==")
    exp = _open_in(con, DELAY_RULES)
    check("延误 open_risk == 直查 R1-R3", summary["delay"]["open_risk"] == exp,
          f"{summary['delay']['open_risk']} != {exp}")
    sev = summary["delay"]["by_severity"]
    check("延误 severity 分布加总 == open_risk", sum(sev.values()) == exp,
          f"{sum(sev.values())} != {exp}")
    for s in ("critical", "high", "medium"):
        e = _scalar(con, f"SELECT count(*) FROM risk_events WHERE {OPEN_RISK} "
                         f"AND rule_id IN ({','.join('?'*len(DELAY_RULES))}) AND severity=?",
                    (*DELAY_RULES, s))
        check(f"延误 severity[{s}] 与直查一致", sev[s] == e, f"{sev[s]} != {e}")
    ear = _scalar(con, "SELECT count(*) FROM sales_order_lines WHERE line_status='at_risk'")
    check("延误 at_risk 订单行与直查一致", summary["delay"]["at_risk_so_lines"] == ear,
          f"{summary['delay']['at_risk_so_lines']} != {ear}")

    print("== ③ 费用稽核（R4-R6）与底表一致 ==")
    check("费用 open_risk == 直查 R4-R6",
          summary["cost"]["open_risk"] == _open_in(con, COST_RULES))
    check("under_review 发票与直查一致",
          summary["cost"]["under_review_invoices"] ==
          _scalar(con, "SELECT count(*) FROM invoices WHERE status='under_review'"))
    check("disputed 发票与直查一致",
          summary["cost"]["disputed_invoices"] ==
          _scalar(con, "SELECT count(*) FROM invoices WHERE status='disputed'"))

    print("== ④ 采购（R7-R15 + V23① R22/R23）与底表一致 ==")
    p = summary["procurement"]
    check("采购 open_risk == 直查 R7-R15+R22/R23", p["open_risk"] == _open_in(con, PROCUREMENT_RULES))
    check("三方对账异常 == 直查 R10+R11", p["three_way_recon"] == _open_in(con, ("R10", "R11")))
    check("预付款敞口 == 直查 R12", p["prepayment_exposure"] == _open_in(con, ("R12",)))
    check("资质过期 == 直查 R13", p["qualification_expired"] == _open_in(con, ("R13",)))
    check("单一来源 == 直查 R14", p["single_source"] == _open_in(con, ("R14",)))
    check("maverick == 直查 R15", p["maverick_spend"] == _open_in(con, ("R15",)))
    check("绩效劣化 == 直查 R22", p["perf_degradation"] == _open_in(con, ("R22",)))
    check("资质预警 == 直查 R23", p["qual_expiry_warning"] == _open_in(con, ("R23",)))
    # 采购分项计数不应超过场景总数（内部自洽）
    check("采购分项 ≤ 采购 open_risk 总数",
          p["three_way_recon"] + p["prepayment_exposure"] + p["qualification_expired"]
          + p["single_source"] + p["maverick_spend"] + p["perf_degradation"]
          + p["qual_expiry_warning"] <= p["open_risk"])

    print("== ⑤ 仓储库存（R16-R18）与底表一致 ==")
    w = summary["warehouse"]
    check("仓储 open_risk == 直查 R16-R18", w["open_risk"] == _open_in(con, WAREHOUSE_RULES))
    check("断货 == 直查 R16", w["stockout"] == _open_in(con, ("R16",)))
    check("不可履约 == 直查 R17", w["unfulfillable"] == _open_in(con, ("R17",)))
    check("盘点差异 == 直查 R18", w["shrinkage"] == _open_in(con, ("R18",)))
    check("仓储三分项加总 == open_risk",
          w["stockout"] + w["unfulfillable"] + w["shrinkage"] == w["open_risk"])

    print("== ⑥ 准入合规与底表一致 ==")
    a = summary["admission"]
    exp_status = {r[0]: r[1] for r in con.execute(
        "SELECT status, count(*) FROM admission_cases GROUP BY status")}
    check("准入 by_status 与直查一致", a["by_status"] == exp_status,
          f"{a['by_status']} != {exp_status}")
    check("准入 by_status 加总 == 全部案件数",
          sum(a["by_status"].values()) ==
          _scalar(con, "SELECT count(*) FROM admission_cases"))
    check("门禁触发 == 直查（critical & !verified）",
          a["gate_triggered"] ==
          _scalar(con, "SELECT count(*) FROM compliance_findings "
                       "WHERE severity='critical' AND evidence_status!='verified'"))

    print("== ⑦ 跨场景横条与 risk_events/tasks 直查一致 ==")
    cr = summary["cross"]
    total = _scalar(con, f"SELECT count(*) FROM risk_events WHERE {OPEN_RISK}")
    check("总未结风险 == 直查", cr["total_open_risk"] == total, f"{cr['total_open_risk']} != {total}")
    # 全域自洽：6 场景 open 之和（R1-R23 全覆盖）== 总未结风险
    # （2026-07-19 修锈蚀+补真缺口：F1 资金流上线后本断言"五场景 R1-R18"必失败——
    #   经 executive_view 补 finance 场景块后恢复全覆盖恒等式，断言升级为六场景。
    #   V23① R22/R23 入 procurement 场景元组，恒等式覆盖同步升至 R1-R23。）
    fin = summary["finance"]
    check("资金流 open_risk == 直查", fin["open_risk"] == _scalar(
        con, f"SELECT count(*) FROM risk_events WHERE {OPEN_RISK} AND rule_id IN ('R19','R20','R21')"),
        str(fin))
    check("资金流细分之和 == 场景 open", fin["overdue_receivable"] + fin["cash_breach"]
          + fin["payment_anomaly"] == fin["open_risk"], str(fin))
    scenario_sum = (summary["delay"]["open_risk"] + summary["cost"]["open_risk"]
                    + p["open_risk"] + w["open_risk"] + fin["open_risk"])
    check("六场景 open 之和 == 总未结风险（R1-R23 全覆盖）",
          scenario_sum == total, f"{scenario_sum} != {total}")
    for s in ("open", "due_today", "overdue"):
        e = _scalar(con, "SELECT count(*) FROM tasks WHERE sla_state=?", (s,))
        check(f"任务 SLA[{s}] 与直查一致", cr["tasks_by_sla"][s] == e, f"{cr['tasks_by_sla'][s]} != {e}")
    check("升级候选与直查一致",
          cr["escalation_candidates"] ==
          _scalar(con, "SELECT count(*) FROM tasks WHERE sla_state='overdue' OR escalation_level>0"))
    check("DQ 待处置与直查一致",
          cr["dq_open"] == _scalar(con, "SELECT count(*) FROM dq_issues WHERE status!='closed'"))

    print("== ⑧ 纯读：返回结构无任何动作/写入键 ==")
    keys = _walk_keys(summary)
    banned = {"action", "actions", "write", "mutation", "submit", "approve", "reject",
              "delete", "update", "insert"}
    hit = keys & banned
    check("无 action/write 类键（呈现层只读）", not hit, f"出现禁用键 {hit}")

    con.close()
    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
