"""对象工作台（RiskEvent 富视图）+ permission-aware 对象级 agent 面板。

设计动机（≤5 行「为什么这样建」）：
- Palantir Foundry 对象工作台切片：以 RiskEvent 为中心，聚合关联对象（Shipment/受影响 SO 行/
  关联 Task/相关 Invoice），按当前 role 用 ROLE_PERMS gate「可用 action」，并挂一个预 scope 到
  本对象 + 当前 role 的 permission-aware agent（复用 agent.tools.AgentSession，不新造平行 agent）。
- 不复制权限：可用 action 由 app.actions.ROLE_PERMS 推导；approve/close 永不做成 agent 工具（原则2）。
- 纯数据组装（build_workbench / available_actions）零 Streamlit 依赖、可单测；render_* 才碰 st。
"""
import json

try:  # 包上下文（python3 -m app.*）
    from .actions import ROLE_PERMS
    from .admission_actions import ADM_PERMS, CASE_TERMINAL
except ImportError:  # streamlit run app/streamlit_app.py：脚本目录在 sys.path
    from actions import ROLE_PERMS
    from admission_actions import ADM_PERMS, CASE_TERMINAL

from agent.explain import build_risk_briefing, render_briefing_text
from agent.admission_explain import build_admission_briefing, render_admission_briefing
from agent.tools import AgentSession, _can_see_tier, _can_see_cost, COST_FIELDS, MASK

RISK_TERMINAL = ("resolved", "escalated")
TASK_TERMINAL = ("done", "cancelled")
# 准入动作 → 案件状态前置（与 app.admission_actions B2-B6 前置一致；「该角色能不能点」的呈现层判断，
# 真正执行仍由动作层 ADM_PERMS + 门禁 G1/G2/G3 + maker-checker 硬 gate）。
ADM_ACTION_STATUS = {
    "RunCompliancePrecheck": ("draft", "in_precheck", "needs_more_info"),
    "BuildLogisticsPlan": ("in_precheck", "plan_ready"),
    "CalculateCostScenario": ("plan_ready", "priced"),
    "ApproveQuoteDecision": ("priced",),
}


def _rows(con, sql, *a):
    return [dict(r) for r in con.execute(sql, a)]


def available_actions(role, risk_status, tasks):
    """按 app.actions.ROLE_PERMS + 对象状态推导该 role 在本 risk 上可发起的动作。

    复用权限矩阵（不复制）：这是「该角色能不能点」的呈现层判断；真正的执行仍由动作层
    ROLE_PERMS + maker-checker 硬 gate。approve/close 是人类动作——列在此仅表示"该角色可点"，
    agent 永远拿不到 approve/close 工具（原则2）。
    """
    active_tasks = [t for t in tasks if t["status"] not in TASK_TERMINAL]
    has_assigned = any(t["status"] == "assigned" for t in tasks)
    has_pending = any(t.get("approval_status") == "pending" for t in tasks)
    actions = []
    # A3 派单：风险 open 且无非终态任务
    if role in ROLE_PERMS["AssignTask"] and risk_status == "open" and not active_tasks:
        actions.append("AssignTask")
    # A4 提案：存在 assigned 任务
    if role in ROLE_PERMS["ProposeMitigation"] and has_assigned:
        actions.append("ProposeMitigation")
    # A5 审批：存在 pending 提案（仅经理，人类动作）
    if role in ROLE_PERMS["ApproveMitigation"] and has_pending:
        actions.append("ApproveMitigation")
    # A6 关闭：风险未终态
    if role in ROLE_PERMS["CloseRiskEvent"] and risk_status not in RISK_TERMINAL:
        actions.append("CloseRiskEvent")
    return actions


def build_workbench(con, risk_event_id, role):
    """组装对象中心视图：风险属性 + 关联对象（shipment / 受影响 SO 行 / 任务 / 发票）+ 角色可用 action。

    tier 按 role 脱敏（与 agent 工具 / UI 同规）。返回纯 dict，可单测、可被 render_* 复用。
    """
    risk = _rows(con, "SELECT * FROM risk_events WHERE risk_event_id=?", risk_event_id)
    if not risk:
        return {"error": f"风险事件 {risk_event_id} 不存在"}
    risk = risk[0]
    ship = _rows(con, "SELECT * FROM shipments WHERE shipment_id=?", risk["shipment_id"])
    ship = ship[0] if ship else None

    lids = json.loads(risk["affected_so_line_ids"] or "[]")
    lines = []
    if lids:
        ph = ",".join("?" * len(lids))
        lines = _rows(con, f"""SELECT l.so_line_id, l.qty, l.promised_delivery_date, l.line_status,
                       so.so_id, c.customer_id, c.customer_name, c.tier
                       FROM sales_order_lines l JOIN sales_orders so ON so.so_id=l.so_id
                       JOIN customers c ON c.customer_id=so.customer_id
                       WHERE l.so_line_id IN ({ph}) ORDER BY l.so_line_id""", *lids)
        if not _can_see_tier(role):
            for ln in lines:
                ln["tier"] = MASK

    tasks = _rows(con, "SELECT * FROM tasks WHERE risk_event_id=? ORDER BY task_id", risk_event_id)

    # 相关发票：本票所属发票（按 shipment）+ 受影响账单行所属发票（费用类风险）
    invoices = _rows(con, """SELECT invoice_id, vendor_name, vendor_type, total_usd, status, issue_date
                    FROM invoices WHERE shipment_id=? ORDER BY invoice_id""", risk["shipment_id"])
    ilids = json.loads(risk["affected_invoice_line_ids"] or "[]") if \
        risk["affected_invoice_line_ids"] else []
    affected_invoice_lines = []
    if ilids:
        ph = ",".join("?" * len(ilids))
        affected_invoice_lines = _rows(con, f"""SELECT il.invoice_line_id, il.charge_code,
                       il.container_no, il.amount_usd, il.invoice_id
                       FROM invoice_lines il WHERE il.invoice_line_id IN ({ph})
                       ORDER BY il.invoice_line_id""", *ilids)

    return {
        "risk": {k: risk[k] for k in ("risk_event_id", "type", "rule_id", "severity", "status",
                                      "root_cause", "affected_value_usd", "shipment_id")},
        "shipment": ship,
        "affected_lines": lines,
        "tasks": tasks,
        "invoices": invoices,
        "affected_invoice_lines": affected_invoice_lines,
        "available_actions": available_actions(role, risk["status"], tasks),
        "role": role,
    }


def make_agent_session(role, risk_event_id, db_path="data/ontology.sqlite"):
    """构造预 scope 到本 risk + 当前 role 的对象级 agent 会话——同一 permission-aware 框架，
    只是注入了 role 与 focus，不新造 agent。approve/close 不在其工具集内（原则2）。"""
    return AgentSession(db_path=db_path, role=role, focus_risk_event_id=risk_event_id)


def focus_briefing_text(session):
    """无 API key 确定性 fallback：对 focus 的风险生成有据可查（每个数字带对象 ID 出处）的
    简报文本，不依赖任何 LLM。对象级 agent 面板在无 key 时走这条路径。"""
    if not session.focus_risk_event_id:
        return "本会话未 focus 到任何 risk"
    return render_briefing_text(build_risk_briefing(session, session.focus_risk_event_id))


# ========== AdmissionCase 富工作台（切片二，复用 RiskEvent 已验证模式）==========
def admission_available_actions(role, case_status, has_plan):
    """按 ADM_PERMS + 案件状态推导该 role 在本准入案上可发起的动作（B2-B6）。

    复用权限矩阵（不复制）：这是「该角色能不能点」的呈现层判断；真正执行仍由动作层 ADM_PERMS +
    门禁 G1/G2/G3 + maker-checker 硬 gate。ApproveQuoteDecision/RejectOrRequestMoreInfo 是人类决策——
    列在此仅表示"该角色可点"，agent 永远拿不到这两个工具（原则2，FORBIDDEN_TOOLS）。
    B1 建案是「造新对象」不是「对本案的动作」，故不在案件工作台的可用动作内。
    """
    actions = []
    # B2 合规预审
    if role in ADM_PERMS["RunCompliancePrecheck"] and \
            case_status in ADM_ACTION_STATUS["RunCompliancePrecheck"]:
        actions.append("RunCompliancePrecheck")
    # B3 物流方案
    if role in ADM_PERMS["BuildLogisticsPlan"] and \
            case_status in ADM_ACTION_STATUS["BuildLogisticsPlan"]:
        actions.append("BuildLogisticsPlan")
    # B4 成本情景（须已有方案可挂）
    if role in ADM_PERMS["CalculateCostScenario"] and \
            case_status in ADM_ACTION_STATUS["CalculateCostScenario"] and has_plan:
        actions.append("CalculateCostScenario")
    # B5 审批（仅经理，人类动作，案件须 priced）
    if role in ADM_PERMS["ApproveQuoteDecision"] and \
            case_status in ADM_ACTION_STATUS["ApproveQuoteDecision"]:
        actions.append("ApproveQuoteDecision")
    # B6 拒接 / 补资料（合规或经理，案件未终态）
    if role in ADM_PERMS["RejectOrRequestMoreInfo"] and case_status not in CASE_TERMINAL:
        actions.append("RejectOrRequestMoreInfo")
    return actions


def build_admission_workbench(con, admission_case_id, role):
    """组装准入案件中心视图：案件属性 + 关联对象（客户/SKU/合规发现/物流方案/成本情景）+ 角色可用 action。

    成本字段按 role 脱敏（与 agent 工具 / UI mask_cost 同规：finance/manager 可见，其余掩码）。
    返回纯 dict，可单测、可被 render_* 复用。
    """
    case = _rows(con, "SELECT * FROM admission_cases WHERE admission_case_id=?", admission_case_id)
    if not case:
        return {"error": f"准入案件 {admission_case_id} 不存在"}
    case = case[0]
    cust = _rows(con, """SELECT customer_id, customer_name, business_model, ior_capability,
                         broker_status FROM customers WHERE customer_id=?""", case["customer_id"])
    cust = cust[0] if cust else None
    sku = _rows(con, "SELECT sku_id, sku_name, category, sku_status FROM skus WHERE sku_id=?",
                case["sku_id"])
    sku = sku[0] if sku else None
    finds = _rows(con, """SELECT * FROM compliance_findings WHERE admission_case_id=?
                          ORDER BY compliance_finding_id""", admission_case_id)
    plans = _rows(con, """SELECT * FROM logistics_plans WHERE admission_case_id=?
                          ORDER BY logistics_plan_id""", admission_case_id)
    cost_visible = _can_see_cost(role)
    scens = []
    for p in plans:
        for s in _rows(con, """SELECT * FROM cost_scenarios WHERE logistics_plan_id=?
                               ORDER BY cost_scenario_id""", p["logistics_plan_id"]):
            # 成本字段按角色脱敏（与 UI mask_cost / get_admission_context 同规，AD4）
            scens.append(s if cost_visible else
                         {k: (MASK if k in COST_FIELDS else v) for k, v in s.items()})
    return {
        "case": {k: case[k] for k in ("admission_case_id", "case_title", "request_type",
                                      "incoterm_candidate", "risk_level", "status", "decision",
                                      "customer_id", "sku_id")},
        "customer": cust,
        "sku": sku,
        "findings": finds,
        "plans": plans,
        "cost_scenarios": scens,
        "available_actions": admission_available_actions(role, case["status"], bool(plans)),
        "role": role,
    }


def make_admission_agent_session(role, admission_case_id, db_path="data/ontology.sqlite"):
    """构造预 scope 到本准入案 + 当前 role 的对象级 agent 会话——同一 permission-aware 框架，
    只注入 role 与 focus，不新造 agent。B5/B6（审批/拒接）不在其工具集内（原则2，FORBIDDEN_TOOLS）。"""
    return AgentSession(db_path=db_path, role=role, focus_admission_case_id=admission_case_id)


def focus_admission_briefing_text(session):
    """无 API key 确定性 fallback：对 focus 的准入案生成有据可查（每条事实带对象 ID 出处、
    hts 不编造、成本不可见如实声明）的简报文本，不依赖任何 LLM。"""
    if not session.focus_admission_case_id:
        return "本会话未 focus 到任何 admission case"
    return render_admission_briefing(
        build_admission_briefing(session, session.focus_admission_case_id))


# ---------- Streamlit 渲染（延迟 import st；仅 UI 用，纯逻辑测试不触及）----------
def render_object_workbench(risk_event_id, role, actor, as_of, db_factory, render_table):
    """在风险队列内渲染对象工作台：① 风险属性 ② 关联对象 ③ 角色可用 action ④ 对象级 agent 面板。

    db_factory: 无参可调用，返回带 row_factory 的 sqlite 连接（复用 streamlit_app.db）。
    render_table: streamlit_app 的暗色表格渲染器（避免 object_workbench ↔ streamlit_app 循环 import）。
    """
    import streamlit as st

    con = db_factory()
    wb = build_workbench(con, risk_event_id, role)
    if "error" in wb:
        st.warning(wb["error"])
        return
    r = wb["risk"]
    st.markdown(f"### 🔬 对象工作台 · {r['risk_event_id']}")
    st.caption("以 RiskEvent 为中心的富视图：属性 + 关联对象 + 该角色可用动作 + 对象级 AI"
               "（预 scope 到本对象 + 当前角色，permission-aware）")

    # ① 风险属性
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("级别", r["severity"])
    c2.metric("状态", r["status"])
    c3.metric("规则", r["rule_id"])
    c4.metric("影响金额$", r["affected_value_usd"])
    st.markdown(f"**类型** `{r['type']}`　**货运** `{r['shipment_id']}`　**根因**：{r['root_cause']}")

    # ② 关联对象
    st.markdown("**关联 · 受影响订单行**")
    render_table([{"订单行": x["so_line_id"], "订单": x["so_id"], "数量": x["qty"],
                   "承诺日": x["promised_delivery_date"], "行状态": x["line_status"],
                   "客户": x["customer_name"], "客户等级": x["tier"]}
                  for x in wb["affected_lines"]])
    st.markdown("**关联 · 处置任务**")
    render_table([{"任务": t["task_id"], "负责人": t["assignee_user_id"] or "-",
                   "负责角色": t["assignee_role"], "优先级": t["priority"],
                   "提案": t["proposed_action"] or "-", "审批": t["approval_status"] or "-",
                   "状态": t["status"]} for t in wb["tasks"]])
    st.markdown("**关联 · 相关发票（同货运）**")
    render_table([{"发票": iv["invoice_id"], "vendor": iv["vendor_name"], "类型": iv["vendor_type"],
                   "金额$": iv["total_usd"], "状态": iv["status"]} for iv in wb["invoices"]])
    if wb["affected_invoice_lines"]:
        st.markdown("**关联 · 受影响账单行**")
        render_table([{"账单行": il["invoice_line_id"], "费种": il["charge_code"],
                       "柜": il["container_no"] or "-", "金额$": il["amount_usd"],
                       "所属发票": il["invoice_id"]} for il in wb["affected_invoice_lines"]])

    # ③ 该角色可用 action（按 ROLE_PERMS gate；执行走既有风险台/任务台表单，此处呈现可用性）
    acts = wb["available_actions"]
    st.markdown(f"**该角色（{role}）在本对象上可发起的动作**："
                + ("、".join(acts) if acts else "（无——该角色对本对象状态无可发起动作）"))
    st.caption("动作权限由 app.actions.ROLE_PERMS + maker-checker 硬 gate；审批 / 关闭永远人来点，"
               "AI 只提案不审批（原则2）。执行入口在风险台「派发/关闭」与任务台「提案/审批」表单。")

    # ④ 对象级 agent 面板（预 scope 到本 risk + 当前 role）
    st.markdown("**对象级 AI 助手**")
    sess = make_agent_session(role, risk_event_id)
    st.caption(f"本会话工具集（role={role}，focus={risk_event_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— approve/close 永不在内（agent 只提案不审批）。")
    with st.expander("查看确定性风险简报（无需 API key，每条事实带对象 ID 出处）", expanded=False):
        st.text(focus_briefing_text(sess))
    q = st.text_input("向对象级 AI 提问（focus 已锁定本对象）", key=f"wb_q_{risk_event_id}")
    if st.button("询问（确定性简报作答，无 key 可跑）", key=f"wb_ask_{risk_event_id}"):
        st.text(focus_briefing_text(sess))
        if q:
            st.caption(f"（本切片以确定性简报作答；接入 LLM 后同一 focus/role 会话可就"
                       f"「{q}」自由问答，工具集与脱敏不变。）")


def render_admission_object_workbench(admission_case_id, role, actor, as_of, db_factory, render_table):
    """在准入工作台内渲染准入案对象工作台：① 案件属性 ② 关联对象（客户/SKU/合规发现/物流方案/成本情景）
    ③ 角色可用 action ④ 对象级 agent 面板（预 scope 到本案 + 当前角色，permission-aware）。

    与 render_object_workbench 同结构（复用 RiskEvent 已验证模式）。db_factory / render_table 同规。
    """
    import streamlit as st

    con = db_factory()
    wb = build_admission_workbench(con, admission_case_id, role)
    if "error" in wb:
        st.warning(wb["error"])
        return
    c = wb["case"]
    st.markdown(f"### 🔬 对象工作台 · {c['admission_case_id']}")
    st.caption("以 AdmissionCase 为中心的富视图：属性 + 关联对象 + 该角色可用动作 + 对象级 AI"
               "（预 scope 到本案 + 当前角色，permission-aware）")

    # ① 案件属性
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("状态", c["status"])
    c2.metric("风险等级", c["risk_level"] or "-")
    c3.metric("贸易术语", c["incoterm_candidate"])
    c4.metric("请求类型", c["request_type"])
    cust, sku = wb["customer"], wb["sku"]
    st.markdown(f"**{c['case_title']}**"
                + (f"　决定 `{c['decision']}`" if c["decision"] else "")
                + (f"　客户 `{cust['customer_id']} {cust['customer_name']}`"
                   f"（IOR {cust['ior_capability']}）" if cust else "")
                + (f"　SKU `{sku['sku_id']} {sku['sku_name']}`（{sku['sku_status']}）" if sku else ""))

    # ② 关联对象
    st.markdown("**关联 · 合规发现**")
    render_table([{"发现": f["compliance_finding_id"], "类型": f["finding_type"],
                   "级别": f["severity"], "HTS": f["hts_candidate"] or "-",
                   "机构": f["pga_agency"], "证据": f["evidence_status"],
                   "建议": f["recommendation"]} for f in wb["findings"]])
    st.markdown("**关联 · 物流方案**")
    render_table([{"方案": p["logistics_plan_id"], "路线": p["route_type"], "术语": p["incoterm"],
                   "港口": f"{p['origin_port_locode']}→{p['destination_port_locode']}",
                   "时效(天)": p["estimated_transit_days"], "SLA风险": p["sla_risk"]}
                  for p in wb["plans"]])
    if wb["cost_scenarios"]:
        st.markdown("**关联 · 成本情景（成本与毛利仅财务/经理可见）**")
        render_table([{"情景": s["cost_scenario_id"], "方案": s["logistics_plan_id"],
                       "类型": s["scenario_type"], "报价$": s["quote_price_usd"],
                       "毛利$": s["gross_margin_usd"], "毛利率": s["gross_margin_rate"]}
                      for s in wb["cost_scenarios"]])

    # ③ 该角色可用 action（按 ADM_PERMS gate；执行走准入工作台既有 B2-B6 表单，此处呈现可用性）
    acts = wb["available_actions"]
    st.markdown(f"**该角色（{role}）在本案上可发起的动作**："
                + ("、".join(acts) if acts else "（无——该角色对本案状态无可发起动作）"))
    st.caption("动作权限由 app.admission_actions.ADM_PERMS + 门禁 G1/G2/G3 + maker-checker 硬 gate；"
               "审批（B5）/ 拒接（B6）永远人来点，AI 只做准备动作不做决策（原则2）。"
               "执行入口在准入工作台上方各角色表单。")

    # ④ 对象级 agent 面板（预 scope 到本案 + 当前 role）
    st.markdown("**对象级 AI 助手**")
    sess = make_admission_agent_session(role, admission_case_id)
    st.caption(f"本会话工具集（role={role}，focus={admission_case_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— 审批/拒接（B5/B6）永不在内（agent 只准备不决策）。")
    with st.expander("查看确定性准入简报（无需 API key，每条事实带对象 ID 出处）", expanded=False):
        st.text(focus_admission_briefing_text(sess))
    q = st.text_input("向对象级 AI 提问（focus 已锁定本案）", key=f"awb_q_{admission_case_id}")
    if st.button("询问（确定性简报作答，无 key 可跑）", key=f"awb_ask_{admission_case_id}"):
        st.text(focus_admission_briefing_text(sess))
        if q:
            st.caption(f"（本切片以确定性简报作答；接入 LLM 后同一 focus/role 会话可就"
                       f"「{q}」自由问答，工具集与脱敏不变。）")
