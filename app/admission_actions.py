"""v0.3 准入动作层 B1-B6（admission-manual-v0.3 §5）。

与 app/actions.py 同一套模式：权限→前置→成功→失败→审计，统一返回结构。
审计 target_object_id 统一为 admission_case_id（创建的子对象 id 放 params），
保证一个案件的全链审计一条查询可取（断言 AA7）。
"""
import json

try:
    from .action_context import transaction
    from .actions import _log, _res, can_approve_actor
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from action_context import transaction
    from actions import _log, _res, can_approve_actor

ADM_PERMS = {
    "CreateAdmissionCase": {"sales"},
    "RunCompliancePrecheck": {"compliance"},
    "BuildLogisticsPlan": {"ops"},
    "CalculateCostScenario": {"finance"},
    "ApproveQuoteDecision": {"manager"},
    "RejectOrRequestMoreInfo": {"compliance", "manager"},
}
CASE_TERMINAL = ("approved", "quote_with_conditions", "rejected")
SEV_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITIES = set(SEV_RANK)
FINDING_TYPES = {"hts", "pga", "labeling", "certification", "origin", "uflpa",
                 "ad_cvd", "section_301", "platform_rule"}
EVIDENCE = {"missing", "provided", "verified", "rejected"}
RECOMMEND = {"accept", "more_docs", "dap_only", "reject", "escalate"}
PLAN_KEYS = {"plan_name", "route_type", "incoterm", "origin_port_locode",
             "destination_port_locode", "us_warehouse_region", "last_mile_method",
             "estimated_transit_days", "sla_risk"}
COST_KEYS = ["product_cost_usd", "first_mile_cost_usd", "international_freight_usd",
             "duty_tax_usd", "customs_brokerage_usd", "warehouse_cost_usd",
             "last_mile_cost_usd", "returns_allowance_usd", "risk_buffer_usd"]


def _denied(con, action, target, actor, role, as_of):
    cur = con.cursor()
    _log(cur, actor, role, action, target, {}, as_of, "denied: role not permitted")
    con.commit()
    return _res(False, error=f"权限拒绝：角色 {role} 不允许执行 {action}（已记录审计）")


def _fail(con, action, target, actor, role, as_of, msg, params=None):
    cur = con.cursor()
    _log(cur, actor, role, action, target, params or {}, as_of, f"rejected: {msg}")
    con.commit()
    return _res(False, error=msg)


def _next_id(cur, table, prefix, width, id_col):
    n = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0] + 1
    return f"{prefix}{n:0{width}d}"


def _case(cur, aid):
    return cur.execute("SELECT * FROM admission_cases WHERE admission_case_id=?", (aid,)).fetchone()


def _quote_proposer_for_scenario(cur, admission_case_id, cost_scenario_id):
    rows = cur.execute("""SELECT actor, params_json FROM action_log
                         WHERE target_object_id=? AND action='CalculateCostScenario'
                         AND result='ok'
                         ORDER BY log_id DESC""", (admission_case_id,)).fetchall()
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if params.get("cost_scenario_id") == cost_scenario_id:
            return row["actor"]
    return None


def create_admission_case(con, customer_id, sku_id, request_type, incoterm_candidate,
                          target_launch_date, monthly_order_estimate, actor, role, as_of):
    """B1：建案（销售）。前置：SKU 必须是 candidate。"""
    if role not in ADM_PERMS["CreateAdmissionCase"]:
        return _denied(con, "CreateAdmissionCase", sku_id, actor, role, as_of)
    cur = con.cursor()
    cust = cur.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
    sku = cur.execute("SELECT * FROM skus WHERE sku_id=?", (sku_id,)).fetchone()
    if not cust or not sku:
        return _fail(con, "CreateAdmissionCase", sku_id, actor, role, as_of, "客户或 SKU 不存在")
    if sku["sku_status"] != "candidate":
        return _fail(con, "CreateAdmissionCase", sku_id, actor, role, as_of,
                     f"SKU {sku_id} 状态为 {sku['sku_status']}，仅 candidate 可建准入案（在售复核属 v0.4）")
    aid = _next_id(cur, "admission_cases", "AC-2026-", 4, "admission_case_id")
    cur.execute("INSERT INTO admission_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, f"{sku['sku_name']} 准入报价（{cust['customer_name']}）", customer_id, sku_id,
                 request_type, incoterm_candidate, target_launch_date, monthly_order_estimate,
                 "", "draft", "", "", ""))
    _log(cur, actor, role, "CreateAdmissionCase", aid,
         {"customer_id": customer_id, "sku_id": sku_id, "incoterm_candidate": incoterm_candidate},
         as_of, "ok")
    con.commit()
    return _res(True, aid, [f"AdmissionCase {aid} created (draft)"])


def run_compliance_precheck(con, admission_case_id, findings, actor, role, as_of):
    """B2：合规预审（合规）。整体校验通过才落库（不落半截）；risk_level 重算。"""
    if role not in ADM_PERMS["RunCompliancePrecheck"]:
        return _denied(con, "RunCompliancePrecheck", admission_case_id, actor, role, as_of)
    cur = con.cursor()
    case = _case(cur, admission_case_id)
    if not case:
        return _fail(con, "RunCompliancePrecheck", admission_case_id, actor, role, as_of, "案件不存在")
    if case["status"] not in ("draft", "in_precheck", "needs_more_info"):
        return _fail(con, "RunCompliancePrecheck", admission_case_id, actor, role, as_of,
                     f"案件状态 {case['status']} 不可预审")
    if not findings:
        return _fail(con, "RunCompliancePrecheck", admission_case_id, actor, role, as_of,
                     "预审必须至少提交一条 finding")
    for f in findings:  # 先整体校验
        if f.get("finding_type") not in FINDING_TYPES or f.get("severity") not in SEVERITIES \
                or f.get("evidence_status") not in EVIDENCE or f.get("recommendation") not in RECOMMEND:
            return _fail(con, "RunCompliancePrecheck", admission_case_id, actor, role, as_of,
                         f"finding 枚举校验失败: {f}", {"bad": str(f)[:200]})
        if f["finding_type"] == "hts" and not f.get("hts_candidate"):
            return _fail(con, "RunCompliancePrecheck", admission_case_id, actor, role, as_of,
                         "finding_type=hts 必须提供 hts_candidate")
    ids = []
    for f in findings:
        cfid = _next_id(cur, "compliance_findings", "CF-", 5, "compliance_finding_id")
        cur.execute("INSERT INTO compliance_findings VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cfid, admission_case_id, f.get("finding_title", f["finding_type"]),
                     f["finding_type"], f["severity"], f.get("hts_candidate", ""),
                     f.get("pga_agency", "none"), f.get("required_document", ""),
                     f["evidence_status"], f["recommendation"]))
        ids.append(cfid)
    risk = max((r["severity"] for r in cur.execute(
        "SELECT severity FROM compliance_findings WHERE admission_case_id=?",
        (admission_case_id,))), key=lambda s: SEV_RANK[s])
    cur.execute("UPDATE admission_cases SET status='in_precheck', risk_level=? WHERE admission_case_id=?",
                (risk, admission_case_id))
    _log(cur, actor, role, "RunCompliancePrecheck", admission_case_id,
         {"finding_ids": ids, "risk_level": risk}, as_of, "ok")
    con.commit()
    return _res(True, admission_case_id,
                [f"{len(ids)} findings recorded", f"case→in_precheck, risk_level={risk}"])


def build_logistics_plan(con, admission_case_id, plan, actor, role, as_of):
    """B3：物流方案（运营）。DDP 门禁：客户须 has_ior 且已有 HTS finding。"""
    if role not in ADM_PERMS["BuildLogisticsPlan"]:
        return _denied(con, "BuildLogisticsPlan", admission_case_id, actor, role, as_of)
    cur = con.cursor()
    case = _case(cur, admission_case_id)
    if not case or case["status"] not in ("in_precheck", "plan_ready"):
        return _fail(con, "BuildLogisticsPlan", admission_case_id, actor, role, as_of,
                     "案件不存在或状态不可建方案（需 in_precheck/plan_ready）")
    if not PLAN_KEYS <= set(plan):
        return _fail(con, "BuildLogisticsPlan", admission_case_id, actor, role, as_of,
                     f"方案缺字段: {sorted(PLAN_KEYS - set(plan))}")
    if plan["incoterm"] == "DDP":
        cust = cur.execute("""SELECT c.ior_capability FROM customers c
                              JOIN admission_cases a ON a.customer_id=c.customer_id
                              WHERE a.admission_case_id=?""", (admission_case_id,)).fetchone()
        has_hts = cur.execute("""SELECT 1 FROM compliance_findings
                                 WHERE admission_case_id=? AND hts_candidate != ''""",
                              (admission_case_id,)).fetchone()
        missing = []
        if cust["ior_capability"] != "has_ior":
            missing.append(f"客户 IOR 能力={cust['ior_capability']}（需 has_ior）")
        if not has_hts:
            missing.append("缺 HTS finding")
        if missing:
            return _fail(con, "BuildLogisticsPlan", admission_case_id, actor, role, as_of,
                         "DDP 门禁未过：" + "；".join(missing))
    pid = _next_id(cur, "logistics_plans", "LP-", 5, "logistics_plan_id")
    cur.execute("INSERT INTO logistics_plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, admission_case_id, plan["plan_name"], plan["route_type"], plan["incoterm"],
                 plan["origin_port_locode"], plan["destination_port_locode"],
                 plan["us_warehouse_region"], plan["last_mile_method"],
                 plan["estimated_transit_days"], plan["sla_risk"], plan.get("operational_notes", "")))
    cur.execute("UPDATE admission_cases SET status='plan_ready' WHERE admission_case_id=?",
                (admission_case_id,))
    _log(cur, actor, role, "BuildLogisticsPlan", admission_case_id,
         {"logistics_plan_id": pid, "incoterm": plan["incoterm"], "route": plan["route_type"]},
         as_of, "ok")
    con.commit()
    return _res(True, pid, [f"LogisticsPlan {pid} created", "case→plan_ready"])


def calculate_cost_scenario(con, logistics_plan_id, scenario, actor, role, as_of):
    """B4：成本情景（财务）。DDP 成本门禁：税费/清关/风险金必须>0。毛利现算。"""
    if role not in ADM_PERMS["CalculateCostScenario"]:
        return _denied(con, "CalculateCostScenario", logistics_plan_id, actor, role, as_of)
    cur = con.cursor()
    plan = cur.execute("SELECT * FROM logistics_plans WHERE logistics_plan_id=?",
                       (logistics_plan_id,)).fetchone()
    if not plan:
        return _fail(con, "CalculateCostScenario", logistics_plan_id, actor, role, as_of, "方案不存在")
    aid = plan["admission_case_id"]
    case = _case(cur, aid)
    if case["status"] not in ("plan_ready", "priced"):
        return _fail(con, "CalculateCostScenario", aid, actor, role, as_of,
                     f"案件状态 {case['status']} 不可计价")
    need = set(COST_KEYS) | {"scenario_type", "quote_price_usd"}
    if not need <= set(scenario):
        return _fail(con, "CalculateCostScenario", aid, actor, role, as_of,
                     f"情景缺字段: {sorted(need - set(scenario))}")
    if plan["incoterm"] == "DDP":
        bad = [k for k in ("duty_tax_usd", "customs_brokerage_usd", "risk_buffer_usd")
               if float(scenario[k]) <= 0]
        if bad:
            return _fail(con, "CalculateCostScenario", aid, actor, role, as_of,
                         f"DDP 成本门禁未过：{bad} 必须 > 0")
    total = sum(float(scenario[k]) for k in COST_KEYS)
    quote = float(scenario["quote_price_usd"])
    margin = round(quote - total, 2)
    sid = _next_id(cur, "cost_scenarios", "CS-", 5, "cost_scenario_id")
    cur.execute("INSERT INTO cost_scenarios VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, logistics_plan_id, scenario["scenario_type"], quote,
                 *[float(scenario[k]) for k in COST_KEYS], margin,
                 round(margin / quote, 4) if quote else 0))
    cur.execute("UPDATE admission_cases SET status='priced' WHERE admission_case_id=?", (aid,))
    _log(cur, actor, role, "CalculateCostScenario", aid,
         {"cost_scenario_id": sid, "scenario_type": scenario["scenario_type"],
          "gross_margin_usd": margin}, as_of, "ok")
    con.commit()
    return _res(True, sid, [f"CostScenario {sid} margin={margin}", "case→priced"])


def approve_quote_decision(con, admission_case_id, approved_logistics_plan_id,
                           approved_cost_scenario_id, decision, decision_reason, conditions,
                           actor, role, as_of):
    """B5：审批（仅经理）。三重门禁 G1/G2/G3，任一触发即拒绝并审计。"""
    if role not in ADM_PERMS["ApproveQuoteDecision"]:
        return _denied(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of)
    cur = con.cursor()
    case = _case(cur, admission_case_id)
    if not case:
        return _fail(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of, "案件不存在")
    if decision not in ("approve", "quote_with_conditions"):
        return _fail(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of,
                     "decision 必须是 approve / quote_with_conditions（拒接走 B6）")
    if not decision_reason:
        return _fail(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of, "必须填写审批理由")
    # G3 状态与引用
    plan = cur.execute("""SELECT * FROM logistics_plans WHERE logistics_plan_id=?
                          AND admission_case_id=?""",
                       (approved_logistics_plan_id, admission_case_id)).fetchone()
    scen = cur.execute("""SELECT * FROM cost_scenarios WHERE cost_scenario_id=?
                          AND logistics_plan_id=?""",
                       (approved_cost_scenario_id, approved_logistics_plan_id)).fetchone()
    if case["status"] != "priced" or not plan or not scen:
        return _fail(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of,
                     f"G3 门禁未过：案件须 priced（当前 {case['status']}）且方案/情景引用有效")
    proposer_actor_id = _quote_proposer_for_scenario(cur, admission_case_id,
                                                     approved_cost_scenario_id)
    allowed, reason = can_approve_actor(proposer_actor_id, actor, role)
    if not allowed:
        return _fail(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of,
                     f"M1 审批边界拒绝：{reason}",
                     {"proposer_actor_id": proposer_actor_id, "approver_actor_id": actor})
    # G1 critical 合规
    crit = cur.execute("""SELECT compliance_finding_id FROM compliance_findings
                          WHERE admission_case_id=? AND severity='critical'
                          AND evidence_status != 'verified'""", (admission_case_id,)).fetchall()
    if crit:
        return _fail(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of,
                     f"G1 门禁未过：存在未核实的 critical 合规发现 {[r[0] for r in crit]}")
    # G2 DDP-IOR
    if plan["incoterm"] == "DDP":
        cust = cur.execute("SELECT ior_capability FROM customers WHERE customer_id=?",
                           (case["customer_id"],)).fetchone()
        if cust["ior_capability"] != "has_ior":
            return _fail(con, "ApproveQuoteDecision", admission_case_id, actor, role, as_of,
                         f"G2 门禁未过：DDP 方案但客户 IOR={cust['ior_capability']}")
    status = "approved" if decision == "approve" else "quote_with_conditions"
    effects = [f"case→{status}"]
    with transaction(con):
        cur.execute("""UPDATE admission_cases SET status=?, decision=?, decision_reason=?, conditions=?
                       WHERE admission_case_id=?""",
                    (status, decision, decision_reason, conditions or "", admission_case_id))
        if decision == "approve":  # E1/N5 生命周期咬合
            cur.execute("UPDATE skus SET sku_status='active' WHERE sku_id=? AND sku_status='candidate'",
                        (case["sku_id"],))
            effects.append(f"Sku {case['sku_id']}: candidate→active")
        _log(cur, actor, role, "ApproveQuoteDecision", admission_case_id,
             {"decision": decision, "plan": approved_logistics_plan_id,
              "scenario": approved_cost_scenario_id, "gates_checked": ["G1", "G2", "G3"]},
             as_of, "ok")
    return _res(True, admission_case_id, effects)


def reject_or_request_more_info(con, admission_case_id, decision, missing_documents,
                                rejection_reason, actor, role, as_of):
    """B6：拒接（仅经理）/ 要求补资料（合规或经理）。"""
    if role not in ADM_PERMS["RejectOrRequestMoreInfo"]:
        return _denied(con, "RejectOrRequestMoreInfo", admission_case_id, actor, role, as_of)
    if decision == "reject" and role != "manager":
        return _denied(con, "RejectOrRequestMoreInfo", admission_case_id, actor, role, as_of)
    cur = con.cursor()
    case = _case(cur, admission_case_id)
    if not case or case["status"] in CASE_TERMINAL:
        return _fail(con, "RejectOrRequestMoreInfo", admission_case_id, actor, role, as_of,
                     "案件不存在或已终态")
    if decision == "reject":
        if not rejection_reason:
            return _fail(con, "RejectOrRequestMoreInfo", admission_case_id, actor, role, as_of,
                         "拒接必须填写明确原因")
        cur.execute("""UPDATE admission_cases SET status='rejected', decision='reject',
                       decision_reason=? WHERE admission_case_id=?""",
                    (rejection_reason, admission_case_id))
        result = "case→rejected"
    elif decision == "more_info":
        if not missing_documents:
            return _fail(con, "RejectOrRequestMoreInfo", admission_case_id, actor, role, as_of,
                         "补资料必须列出缺失项")
        cur.execute("""UPDATE admission_cases SET status='needs_more_info', decision='more_info',
                       decision_reason=? WHERE admission_case_id=?""",
                    ("待补: " + "; ".join(missing_documents), admission_case_id))
        result = "case→needs_more_info"
    else:
        return _fail(con, "RejectOrRequestMoreInfo", admission_case_id, actor, role, as_of,
                     "decision 必须是 reject / more_info")
    _log(cur, actor, role, "RejectOrRequestMoreInfo", admission_case_id,
         {"decision": decision, "missing_documents": missing_documents or [],
          "rejection_reason": rejection_reason or ""}, as_of, "ok")
    con.commit()
    return _res(True, admission_case_id, [result])
