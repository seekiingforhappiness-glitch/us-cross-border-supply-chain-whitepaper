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
    from . import ux_copy
except ImportError:  # streamlit run app/streamlit_app.py：脚本目录在 sys.path
    from actions import ROLE_PERMS
    from admission_actions import ADM_PERMS, CASE_TERMINAL
    import ux_copy

from agent.explain import (build_risk_briefing, render_briefing_text,
                           build_invoice_briefing, render_invoice_briefing_text)
from agent.admission_explain import build_admission_briefing, render_admission_briefing
from agent.tools import (AgentSession, _can_see_tier, _can_see_cost,
                        COST_FIELDS, INVOICE_COST_FIELDS, MASK)

RISK_TERMINAL = ("resolved", "escalated")
TASK_TERMINAL = ("done", "cancelled")


_DEFAULT_DB = "data/ontology.sqlite"  # AI 自检 / llm_calls 落库位置（与对象库同库）


def _ai_availability(db_path=_DEFAULT_DB):
    """AI 助手可用性（available, reason）——便宜自检并缓存进 session_state，避免每次 rerun 真调 CLI。
    自检本身（agent.llm_agent.probe_cli_availability）绝不出境，只看 which/降级态/上次 llm_calls 状态。
    自检异常按「可用」放行（自检不成为故障点），真失败时提问路径再人话降级。缓存在一次提问后失效重算。"""
    import streamlit as st
    if "ai_cli_available" not in st.session_state:
        try:
            from agent.llm_agent import probe_cli_availability
            st.session_state["ai_cli_available"] = probe_cli_availability(db_path)
        except Exception:  # noqa: BLE001 —— 自检层兜底，绝不让自检故障放大为界面故障
            st.session_state["ai_cli_available"] = (True, "")
    return st.session_state["ai_cli_available"]


def _render_object_ai_qa(briefing_text, role, key, db_path=_DEFAULT_DB, anchor=None):
    """对象级 AI 提问区（6 个富工作台共用）：顶部状态徽标（可用/暂不可用）+ 提问框 + 询问按钮
    （不可用时禁用并说明，而非可点后报错）+ 作答（异常→人话降级 + 自动展开简报，见 _render_object_llm_answer）。
    徽标状态来自便宜自检 + session 缓存；一次提问后清缓存，让下次 rerun 按最新 llm_calls 状态翻牌。
    anchor=(对象类型, 对象ID)：新通道（真实查库）用它告诉模型当前聚焦对象（见 _ask_llm）。"""
    import streamlit as st
    available, reason = _ai_availability(db_path)
    if available:
        st.caption("AI 助手：🟢 **可用**（Opus 4.8 · 本账号订阅）")
    else:
        st.caption(f"AI 助手：🟠 **暂不可用**（{reason}）——下方确定性数据简报即为回答依据，可稍后重试。")
    q = st.text_input("向对象级 AI 提问（focus 已锁定本对象）", key=f"{key}_q",
                      disabled=not available)
    asked = st.button("询问（Opus 4.8 作答 · 本账号订阅）", key=f"{key}_ask",
                      disabled=not available,
                      help=None if available else "AI 助手暂不可用，已自动切换为下方确定性数据简报")
    if asked:
        _render_object_llm_answer(q, briefing_text, role, db_path, anchor=anchor)
        st.session_state.pop("ai_cli_available", None)  # 提问后失效缓存：下次 rerun 按最新状态重算徽标


def _ask_llm(question, briefing_text, role, db_path=_DEFAULT_DB, anchor=None):
    """询问路径的 provider 感知单点分派（纯函数、零 Streamlit 依赖，可单测）。返回 (answer, channel)：
      channel="mcp"      新通道（M4 主通道）：模型经本体只读 MCP server 多轮真调用自查库，
                         不再预取简报作 grounding；问题带工作台锚点（对象类型+ID，模型才知道问的是谁），
                         role 透传（server 侧按角色过滤工具 + 脱敏，与本面板同一权限模型）。
      channel="briefing" 旧单发合成档：answer_over_context 据已脱敏简报作答（调用逐字不变）。
    provider 解析复用 agent.llm_agent._resolve_provider（config agent.provider；env AGENT_PROVIDER 最高）
    ——UI 层不重复解析逻辑，config 一处控制全部入口（裁3 一键回退语义）。
    降级链第一跳在此：MCP 主通道失败 → 回落旧档（技术原因已由 answer_via_mcp 记入 llm_calls）；
    旧档再异常 → 向上抛，由 _render_object_llm_answer 既有 except 兜确定性简报（第二跳，文案不变）。"""
    from agent.llm_agent import _resolve_provider
    if _resolve_provider() in ("claude_cli_mcp", "mcp"):
        try:
            from agent.llm_agent import answer_via_mcp
            q = ((f"【工作台上下文】用户当前聚焦对象：{anchor[0]} {anchor[1]}。"
                  f"请用可用的只读工具查询本体后回答（可用 traverse 沿关系查邻居），"
                  f"关键事实附对象 ID，查不到就直说、禁止编造。\n用户问题：{question}")
                 if anchor else question)
            return answer_via_mcp(q, role=role, db=db_path), "mcp"
        except Exception:  # noqa: BLE001 —— MCP 主通道失败：回落旧档（错误已由 answer_via_mcp 落 llm_calls）
            pass
    from agent.llm_agent import answer_over_context
    return answer_over_context(question, briefing_text, role=role, db=db_path), "briefing"


def _render_object_llm_answer(question, briefing_text, role, db_path=_DEFAULT_DB, anchor=None):
    """对象级 AI 助手作答（provider 感知，分派见 _ask_llm）：
    新通道（claude_cli_mcp）= Opus 4.8 经只读 MCP 工具多轮真实查库作答（模型自己开口要数据，逐次调用
    留痕审计）；旧档 = 【仅据确定性简报】单发合成。两档模型都无任何行动能力（不派单/不审批），写动作
    仍只走下方表单 + maker-checker。故障（未登录/超时/降级等）一律人话降级并自动展开确定性简报——
    技术细节只进 llm_calls 日志，绝不进界面（不出现「退出码/CLI」等黑话）。"""
    import streamlit as st
    if not question:
        st.caption("请先在上方输入问题，Opus 4.8 将作答——不编造、继承本角色权限与脱敏、只解释不审批。")
        return
    from agent.llm_agent import _resolve_provider
    want_mcp = _resolve_provider() in ("claude_cli_mcp", "mcp")
    spin = ("AI 正在真实查询数据库（多轮工具调用，约 40–70 秒）…" if want_mcp
            else "Opus 4.8 作答中（本账号订阅渠道，约 30–50 秒）…")
    with st.spinner(spin):
        try:
            ans, channel = _ask_llm(question, briefing_text, role, db_path, anchor=anchor)
        except Exception:  # noqa: BLE001 —— 两档全失败人话降级；技术原因已由 llm_agent 记入 llm_calls
            st.warning("AI 助手暂时不可用，已切换为数据简报（技术原因已记录，可稍后重试）。")
            with st.expander("确定性数据简报（回答依据，每条带对象 ID）", expanded=True):
                st.text(briefing_text)
            return
    if channel == "mcp":
        st.markdown("**🤖 Opus 4.8（真实查库作答 · 多轮只读工具调用 · 继承本角色权限）**")
        with st.expander("回答依据说明（本次为 AI 真实查库）", expanded=False):
            st.caption("本次回答由 AI 通过只读工具当场查询数据库获得（非预生成简报），"
                       "每次工具调用已留痕审计（llm_calls 表 call_type='mcp_tool'）。"
                       "以下确定性简报仅供人工核对，本次回答未以其为依据：")
            st.text(briefing_text)
    else:
        st.markdown("**🤖 Opus 4.8（仅据确定性简报作答，继承本角色脱敏）**")
        with st.expander("确定性数据简报（回答的 grounding 数据，每条带对象 ID）", expanded=False):
            st.text(briefing_text)
    st.markdown(ans)


def _actions_or_hint(acts, role, empty_default):
    """可发起动作的呈现串：① 有动作 → 顿号连接 ② 无动作但角色有提案权（ROLE_PERMS.ProposeMitigation）→
    人话引导去任务台提交处置方案，消除「有权提案却显示（无）」的断头路（陌生人测试 P0-1 王姐/财务）
    ③ 否则 → 领域专属 empty_default 说明。纯呈现，权限真源仍是 ROLE_PERMS（一字未改）。"""
    if acts:
        return "、".join(acts)
    if role in ROLE_PERMS["ProposeMitigation"]:
        return ("（本页暂无可直接发起的动作）——你可以在「任务处理台」对关联任务提交处置方案"
                "（如发起争议 dispute）")
    return empty_default
# Invoice 富工作台呈现层脱敏字段：发票金额随 role 掩码（与 UI mask_cost 同规：finance/manager 可见）。
# 收敛后 agent 对账工具 get_invoice_context 与本工作台**同一口径**（都掩码 INVOICE_COST_FIELDS+total_usd），
# 常量从 agent.tools 单一事实源导入，保证「agent 看得到的 == UI 看得到的」逐字一致。
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


# ========== Task 富工作台（切片三，复用 RiskEvent 已验证模式）==========
def task_available_actions(role, task):
    """按 ROLE_PERMS + 任务状态推导该 role 在本任务上可发起的动作。

    复用权限矩阵（不复制）：ProposeMitigation:{ops,cs,finance}（task=assigned）；
    ApproveMitigation:{manager}（task pending，人类审批动作——列在此仅表"该角色可点"，
    agent 永远拿不到 approve 工具，原则2/FORBIDDEN_TOOLS）。这是「该角色能不能点」的呈现层判断，
    真正执行仍由动作层 ROLE_PERMS + maker-checker 硬 gate。"""
    actions = []
    if role in ROLE_PERMS["ProposeMitigation"] and task["status"] == "assigned":
        actions.append("ProposeMitigation")
    if role in ROLE_PERMS["ApproveMitigation"] and task.get("approval_status") == "pending":
        actions.append("ApproveMitigation")
    return actions


def build_task_workbench(con, task_id, role):
    """组装任务中心视图：任务属性（status/approval/assignee/sla/escalation/priority/due）+
    关联对象（父 RiskEvent、受影响 SO 行、当前提案 proposal）+ 角色可用 action。

    tier 按 role 脱敏；提案里的成本参数 est_cost_usd 对 cs 掩码（与任务台 UI 同规）。
    返回纯 dict，可单测、可被 render_* 复用。approve/close 永不是 agent 工具（原则2）。"""
    trow = _rows(con, "SELECT * FROM tasks WHERE task_id=?", task_id)
    if not trow:
        return {"error": f"任务 {task_id} 不存在"}
    task = trow[0]
    risk = _rows(con, "SELECT * FROM risk_events WHERE risk_event_id=?", task["risk_event_id"])
    risk = risk[0] if risk else None

    lines = []
    if risk:
        lids = json.loads(risk["affected_so_line_ids"] or "[]")
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

    proposal = None
    if task["proposed_action"]:
        params = json.loads(task["proposal_params"] or "{}")
        # 成本参数脱敏（与任务台 UI 同规：cs 无成本权限 → est_cost_usd 掩码）
        if role == "cs" and isinstance(params, dict) and "est_cost_usd" in params:
            params["est_cost_usd"] = MASK
        proposal = {"proposed_action": task["proposed_action"], "proposal_params": params,
                    "approval_status": task["approval_status"],
                    "proposal_actor_role": task["proposal_actor_role"]}

    task_view = {k: task[k] for k in ("task_id", "risk_event_id", "title", "status",
                 "approval_status", "assignee_role", "assignee_user_id", "assignee_team_id",
                 "sla_state", "escalation_level", "priority", "due_at")}
    return {
        "task": task_view,
        "risk": {k: risk[k] for k in ("risk_event_id", "type", "rule_id", "severity", "status",
                                      "root_cause", "affected_value_usd", "shipment_id")}
                if risk else None,
        "affected_lines": lines,
        "proposal": proposal,
        "available_actions": task_available_actions(role, task),
        "role": role,
    }


def make_task_agent_session(role, task_id, db_path="data/ontology.sqlite"):
    """构造预 scope 到本 task + 父风险 + 当前 role 的对象级 agent 会话——同一 permission-aware
    框架，只注入 role 与 focus，不新造 agent。approve/close 不在其工具集内（原则2，FORBIDDEN_TOOLS）。"""
    return AgentSession(db_path=db_path, role=role, focus_task_id=task_id)


def focus_task_briefing_text(session):
    """无 API key 确定性 fallback：对 focus 的 task 生成简报（任务治理头 + 复用父风险确定性简报，
    每条事实带对象 ID 出处）。审批/关闭须人工，AI 只提案（原则2）。不依赖任何 LLM。"""
    tid = session.focus_task_id
    if not tid:
        return "本会话未 focus 到任何 task"
    bundle = session.focus_task_bundle()
    if "error" in bundle:
        return bundle["error"]
    task = bundle["task"]
    header = (f"[{tid}] 任务简报（AI 建议，审批/关闭须人工——原则2）\n"
              f"状态 {task['status']} / 审批 {task.get('approval_status') or '-'} / "
              f"负责人 {task.get('assignee_user_id') or '-'} / SLA {task.get('sla_state') or '-'} / "
              f"优先级 {task['priority']} / 处理截止 {task['due_at']}\n"
              f"父风险 {task['risk_event_id']} ——")
    return header + "\n" + render_briefing_text(build_risk_briefing(session, task["risk_event_id"]))


# ========== Invoice 富工作台（切片四，复用 RiskEvent/AdmissionCase 已验证模式）==========
def invoice_available_actions(role, tasks):
    """按 ROLE_PERMS + 费用处置任务状态推导该 role 在本发票上可发起的动作。

    复用权限矩阵（不复制）：费用提案 dispute/accept_charge/rebill_customer 经 propose_mitigation
    （ProposeMitigation:{ops,cs,finance}，须存在 assigned 的费用处置任务）；审批（ApproveMitigation:{manager}，
    人类动作，pending 时列出）——rebill 的 G4 incoterm 门禁在审批时校验（本切片不改）。approve 永不是
    agent 工具（原则2）。tasks 为 flag 本发票的费用风险上的处置任务。"""
    has_assigned = any(t["status"] == "assigned" for t in tasks)
    has_pending = any(t.get("approval_status") == "pending" for t in tasks)
    actions = []
    if role in ROLE_PERMS["ProposeMitigation"] and has_assigned:
        actions.append("ProposeMitigation")
    if role in ROLE_PERMS["ApproveMitigation"] and has_pending:
        actions.append("ApproveMitigation")
    return actions


def build_invoice_workbench(con, invoice_id, role):
    """组装发票中心视图：发票属性（status/金额/currency）+ 关联对象（invoice_lines join
    ExpectedCost 基准与差异、关联 Shipment、flag 本票账单行的费用类 RiskEvent(R4/R5/R6)、
    其上费用处置任务）+ 角色可用 action。

    成本字段随 role 脱敏（与 UI mask_cost 同规：finance/manager 见金额，其余掩码——见
    INVOICE_COST_FIELDS；收敛后与 agent 对账工具 get_invoice_context 同口径）。返回纯 dict，可单测。"""
    irow = _rows(con, "SELECT * FROM invoices WHERE invoice_id=?", invoice_id)
    if not irow:
        return {"error": f"发票 {invoice_id} 不存在"}
    inv = irow[0]
    lines = _rows(con, """SELECT il.invoice_line_id, il.charge_code, il.container_no, il.qty,
                          il.unit_price_usd, il.amount_usd, ec.baseline_usd
                          FROM invoice_lines il
                          LEFT JOIN expected_costs ec
                            ON ec.shipment_id=? AND ec.charge_code=il.charge_code
                            AND ec.container_no=COALESCE(il.container_no,'')
                          WHERE il.invoice_id=? ORDER BY il.invoice_line_id""",
                 inv["shipment_id"], invoice_id)
    for ln in lines:
        ln["diff_usd"] = (round(ln["amount_usd"] - ln["baseline_usd"], 2)
                          if ln["baseline_usd"] is not None else None)
    ship = _rows(con, """SELECT shipment_id, incoterm, delay_days, status,
                         origin_port_locode, destination_port_locode
                         FROM shipments WHERE shipment_id=?""", inv["shipment_id"])
    ship = ship[0] if ship else None

    # flag 本票账单行的费用类风险（同 shipment、affected_invoice_line_ids 与本票行交集非空）
    line_ids = {ln["invoice_line_id"] for ln in lines}
    flagging_risks, anom = [], set()
    for rr in _rows(con, """SELECT risk_event_id, type, rule_id, severity, status,
                            affected_invoice_line_ids FROM risk_events WHERE shipment_id=?
                            ORDER BY risk_event_id""", inv["shipment_id"]):
        ils = set(json.loads(rr["affected_invoice_line_ids"] or "[]"))
        hit = line_ids & ils
        if hit:
            flagging_risks.append({k: rr[k] for k in ("risk_event_id", "type", "rule_id",
                                                      "severity", "status")}
                                  | {"affected_lines_on_this_invoice": sorted(hit)})
            anom |= hit
    for ln in lines:
        ln["is_anomaly"] = ln["invoice_line_id"] in anom

    # flag 本票的费用风险上的费用处置任务（供可用 action 判定）
    flag_rids = [r["risk_event_id"] for r in flagging_risks]
    tasks = []
    if flag_rids:
        ph = ",".join("?" * len(flag_rids))
        tasks = _rows(con, f"""SELECT task_id, risk_event_id, status, approval_status,
                       proposed_action, assignee_role FROM tasks
                       WHERE risk_event_id IN ({ph}) ORDER BY task_id""", *flag_rids)

    # 成本字段呈现层脱敏（finance/manager 见金额，其余掩码——INVOICE_COST_FIELDS）
    cost_visible = _can_see_cost(role)
    inv_view = {k: inv[k] for k in ("invoice_id", "vendor_name", "vendor_type",
                "vendor_invoice_no", "shipment_id", "issue_date", "currency", "status", "total_usd")}
    if not cost_visible:
        inv_view["total_usd"] = MASK
        for ln in lines:
            for f in INVOICE_COST_FIELDS:
                if ln.get(f) is not None:
                    ln[f] = MASK
    return {
        "invoice": inv_view,
        "lines": lines,
        "shipment": ship,
        "flagging_risks": flagging_risks,
        "cost_tasks": tasks,
        "available_actions": invoice_available_actions(role, tasks),
        "cost_visible": cost_visible,
        "role": role,
        "note": "按 role 脱敏发票金额（UI mask_cost 同规）；agent 对账工具 get_invoice_context 同口径（已收敛）",
    }


def make_invoice_agent_session(role, invoice_id, db_path="data/ontology.sqlite"):
    """构造预 scope 到本发票 + 关联费用风险 + 当前 role 的对象级 agent 会话——同一 permission-aware
    框架，只注入 role 与 focus，不新造 agent。帮分析费用差异、起草 dispute 提案；approve/close 不在其
    工具集内（原则2，FORBIDDEN_TOOLS：agent 只提案不审批）。"""
    return AgentSession(db_path=db_path, role=role, focus_invoice_id=invoice_id)


def focus_invoice_briefing_text(session):
    """无 API key 确定性 fallback：对 focus 的发票生成对账简报（逐行差异 + 异常 + dispute 提案草案，
    每条带对象 ID 出处，needs_human_approval 恒 true）。不依赖任何 LLM。"""
    if not session.focus_invoice_id:
        return "本会话未 focus 到任何 invoice"
    return render_invoice_briefing_text(
        build_invoice_briefing(session, session.focus_invoice_id))


# ========== PurchaseOrder 富工作台（采购切片，Build 3，复用已验证四次的模式）==========
def po_available_actions(role, risks_with_tasks):
    """对本 PO 上每个锚定采购风险，按 ROLE_PERMS + 风险/任务状态推导可用 action（复用 available_actions）。

    采购 RiskEvent(R7-R10) 走既有 assign→propose→approve 闭环，与控制塔风险同一权限矩阵——
    故直接复用 available_actions（不复制权限）。approve/close 永远人来点，agent 拿不到（原则2）。
    返回 {risk_event_id: [actions]}。"""
    return {r["risk_event_id"]: available_actions(role, r["status"], r.get("tasks", []))
            for r in risks_with_tasks}


def build_po_workbench(con, po_id, role):
    """组装采购单中心视图：PO 属性 + PoLine 行 + 关联 GoodsReceipt/SupplierInvoice +
    三方对账（订购×收货×开票逐行）+ 锚定本 PO 的采购 RiskEvent(R7-R10) + 每风险角色可用 action。

    金额字段随 role 脱敏（unit_price/amount/total，与 UI mask_cost / 标准视图 MONEY_FIELDS 同规：
    finance/manager 可见，其余掩码——R7 延误/R8 短装/R9 QC 是数量/日期维度，ops 无需金额即可处置；
    R10 价量不符是财务维度）。返回纯 dict，可单测、可被 render_* 复用。"""
    po_row = _rows(con, "SELECT * FROM purchase_orders WHERE po_id=?", po_id)
    if not po_row:
        return {"error": f"采购单 {po_id} 不存在"}
    po = po_row[0]
    sup = _rows(con, """SELECT supplier_id, supplier_name, city, factory_audit_status
                        FROM suppliers WHERE supplier_id=?""", po["supplier_id"])
    sup = sup[0] if sup else None
    cost_visible = _can_see_cost(role)

    def money(v):
        return MASK if (v is not None and not cost_visible) else v

    po_lines = _rows(con, "SELECT * FROM po_lines WHERE po_id=? ORDER BY po_line_id", po_id)
    grns = _rows(con, "SELECT * FROM goods_receipts WHERE po_id=? ORDER BY grn_id", po_id)
    sinvs = _rows(con, """SELECT * FROM supplier_invoices WHERE po_id=?
                          ORDER BY supplier_invoice_id""", po_id)

    # 收货聚合（每 po_line：累计收货/验收/拒收 + 最早到货日 + QC）
    recv = {}
    for r in _rows(con, """SELECT gl.po_line_id, gl.received_qty, gl.accepted_qty, gl.rejected_qty,
                           gl.qc_status, gl.defect_ppm, gl.received_date
                           FROM goods_receipt_lines gl JOIN goods_receipts g ON g.grn_id=gl.grn_id
                           WHERE g.po_id=? ORDER BY gl.po_line_id, gl.grn_line_id""", po_id):
        a = recv.setdefault(r["po_line_id"], {"received": 0, "accepted": 0, "rejected": 0,
                                              "first_recv": None, "qc_failed": False, "max_ppm": 0})
        a["received"] += r["received_qty"]
        a["accepted"] += r["accepted_qty"]
        a["rejected"] += r["rejected_qty"]
        a["qc_failed"] = a["qc_failed"] or r["qc_status"] == "failed"
        a["max_ppm"] = max(a["max_ppm"], r["defect_ppm"])
        if a["first_recv"] is None or r["received_date"] < a["first_recv"]:
            a["first_recv"] = r["received_date"]
    # 开票聚合（每 po_line：累计开票量/金额 + 单价）
    invg = {}
    for r in _rows(con, """SELECT sil.po_line_id, sil.qty, sil.unit_price_usd, sil.amount_usd
                           FROM supplier_invoice_lines sil JOIN supplier_invoices si
                             ON si.supplier_invoice_id=sil.supplier_invoice_id
                           WHERE si.po_id=? ORDER BY sil.po_line_id""", po_id):
        a = invg.setdefault(r["po_line_id"], {"qty": 0, "amount": 0.0, "unit_price": None})
        a["qty"] += r["qty"]
        a["amount"] = round(a["amount"] + r["amount_usd"], 2)
        if a["unit_price"] is None:
            a["unit_price"] = r["unit_price_usd"]
    # 三方对账逐行（订购 × 收货 × 开票）
    reconciliation = []
    for pl in po_lines:
        rc = recv.get(pl["po_line_id"], {})
        iv = invg.get(pl["po_line_id"], {})
        reconciliation.append({
            "po_line_id": pl["po_line_id"], "sku_id": pl["sku_id"],
            "ordered_qty": pl["qty"], "unit_price_usd": money(pl["unit_price_usd"]),
            "expected_ready_date": pl["expected_ready_date"], "line_status": pl["line_status"],
            "received_qty": rc.get("received", 0), "accepted_qty": rc.get("accepted", 0),
            "rejected_qty": rc.get("rejected", 0), "earliest_receipt": rc.get("first_recv"),
            "qc_failed": rc.get("qc_failed", False), "max_defect_ppm": rc.get("max_ppm", 0),
            "invoiced_qty": iv.get("qty", 0), "invoiced_amount_usd": money(iv.get("amount")),
            "invoice_unit_price_usd": money(iv.get("unit_price")),
            "qty_short": pl["qty"] - rc.get("received", 0)})

    # 锚定本 PO 的采购风险 + 处置任务 + 每风险角色可用 action
    risks_view, all_actions = [], set()
    for r in _rows(con, "SELECT * FROM risk_events WHERE po_id=? ORDER BY risk_event_id", po_id):
        tasks = _rows(con, """SELECT task_id, status, approval_status, assignee_role,
                              assignee_user_id, proposed_action FROM tasks WHERE risk_event_id=?
                              ORDER BY task_id""", r["risk_event_id"])
        acts = available_actions(role, r["status"], tasks)
        all_actions |= set(acts)
        risks_view.append({
            "risk_event_id": r["risk_event_id"], "rule_id": r["rule_id"], "type": r["type"],
            "severity": r["severity"], "status": r["status"],
            "affected_value_usd": r["affected_value_usd"], "root_cause": r["root_cause"],
            "affected_po_line_ids": json.loads(r["affected_po_line_ids"] or "[]"),
            "tasks": tasks, "available_actions": acts})

    return {
        "purchase_order": {k: po[k] for k in ("po_id", "supplier_id", "sku_id", "qty",
                           "po_date", "expected_ready_date", "status")},
        "supplier": sup,
        "po_lines": [{**pl, "unit_price_usd": money(pl["unit_price_usd"])} for pl in po_lines],
        "goods_receipts": grns,
        "supplier_invoices": [{**si, "total_usd": money(si["total_usd"])} for si in sinvs],
        "reconciliation": reconciliation,
        "anchored_risks": risks_view,
        "available_actions": sorted(all_actions),
        "cost_visible": cost_visible,
        "role": role,
    }


def make_po_agent_session(role, po_id, db_path="data/ontology.sqlite"):
    """构造预 scope 到本 PO + 当前 role 的对象级 agent 会话——同一 permission-aware 框架，
    只注入 role 与 focus_po_id，不新造 agent。帮分析三方对账差异、起草供应商索赔/发票争议提案；
    approve/close 不在其工具集内（原则2，FORBIDDEN_TOOLS：agent 只提案不审批）。"""
    return AgentSession(db_path=db_path, role=role, focus_po_id=po_id)


def focus_po_briefing_text(session):
    """无 API key 确定性 fallback：对 focus 的采购单生成三方对账简报（逐行订购/收货/开票差异 +
    锚定采购风险 + 处置提案建议，金额随 role 脱敏，每条带对象 ID 出处，审批须人工——原则2）。
    不依赖任何 LLM。"""
    pid = session.focus_po_id
    if not pid:
        return "本会话未 focus 到任何 purchase order"
    b = session.focus_po_bundle()
    if "error" in b:
        return b["error"]
    po = b["purchase_order"]
    lines = [f"[{pid}] 采购三方对账简报（AI 建议，审批/关闭须人工——原则2）",
             f"供应商 {po['supplier_id']} / 采购单状态 {po['status']} / "
             f"下单 {po['po_date']} / 数据出处对象 purchase_orders.{pid}",
             "三方对账（订购×收货×开票，逐行；数据出处对象 po_lines/goods_receipt_lines/"
             "supplier_invoice_lines）："]
    for rc in b["reconciliation"]:
        lines.append(
            f"  · {rc['po_line_id']}（{rc['sku_id']}）订 {rc['ordered_qty']} @ "
            f"{rc['unit_price_usd']} | 收 {rc['received_qty']}（验 {rc['accepted_qty']}/"
            f"拒 {rc['rejected_qty']}，最早到货 {rc['earliest_receipt'] or '未收'}，"
            f"QC {'failed' if rc['qc_failed'] else 'ok'}，ppm≤{rc['max_defect_ppm']}）| "
            f"开票 {rc['invoiced_qty']} @ {rc['invoice_unit_price_usd']} = "
            f"{rc['invoiced_amount_usd']} | 缺口 {rc['qty_short']}")
    if b["anchored_risks"]:
        lines.append("锚定采购风险（R7-R10，数据出处对象 risk_events，锚点 po_id）：")
        for r in b["anchored_risks"]:
            lines.append(f"  · {r['risk_event_id']} {r['rule_id']} {r['type']}（{r['severity']}，"
                         f"{r['status']}，影响 ${r['affected_value_usd']}）：{r['root_cause']}")
        lines.append("建议：延误→expedite_po / 短装→accept_receipt_variance / "
                     "质量→raise_supplier_claim / 价量→dispute_supplier_invoice；"
                     "均须走 assign→propose→approve，needs_human_approval: true（AI 不审批）。")
    else:
        lines.append("锚定采购风险：无（本 PO 三方对账未检出 R7-R10 异常）。")
    return "\n".join(lines)


# ========== Warehouse 富工作台（仓储切片，复用已验证五次的模式）==========
def warehouse_available_actions(role, risks_with_tasks):
    """对本仓每个锚定仓储风险(R16-R18)，按 ROLE_PERMS + 风险/任务状态推导可用 action（复用 available_actions）。

    仓储 RiskEvent 走既有 assign→propose→approve 闭环（处置类型 suggest_substitution/adjust_inventory/
    escalate_replenishment 是 ProposeMitigation 的提案），与控制塔风险同一权限矩阵——故直接复用
    available_actions（不复制权限）。approve/close 永远人来点，agent 拿不到（原则2）。返回 {risk_event_id: [actions]}。"""
    return {r["risk_event_id"]: available_actions(role, r["status"], r.get("tasks", []))
            for r in risks_with_tasks}


def build_warehouse_workbench(con, warehouse_id, role):
    """组装仓库中心视图：Warehouse 属性 + 该仓 InventoryPosition 列表（available/reserved/in_transit +
    ATP + 低于 safety_stock 标红）+ InventoryReservation + CycleCount(variance) + 锚定本仓的
    仓储 RiskEvent(R16-R18) + 每风险角色可用 action。

    脱敏随 role（与既有口径一致）：库存桶字段（数量）是运营数据不脱敏（ops 需据此处置断货/履约）；
    本域各仓储表无 _usd 字段，仅派生的「在手库存金额 inventory_value_usd=available×SKU 单价」按
    _can_see_cost 脱敏（与 UI mask_cost / 标准视图 MONEY_FIELDS 同规：finance/manager 可见，其余掩码），
    风险 affected_value_usd 与其它工作台同规对所有角色可见。返回纯 dict，可单测、可被 render_* 复用。"""
    wh_row = _rows(con, "SELECT * FROM warehouses WHERE warehouse_id=?", warehouse_id)
    if not wh_row:
        return {"error": f"仓库 {warehouse_id} 不存在"}
    wh = wh_row[0]
    cost_visible = _can_see_cost(role)

    def money(v):
        return MASK if (v is not None and not cost_visible) else v

    sku_price = {r["sku_id"]: r["unit_price_usd"] for r in
                 _rows(con, "SELECT sku_id, unit_price_usd FROM skus")}
    positions = []
    for p in _rows(con, """SELECT inventory_position_id, sku_id, available_qty, reserved_qty,
                           in_transit_qty, safety_stock FROM inventory_positions WHERE warehouse_id=?
                           ORDER BY inventory_position_id""", warehouse_id):
        val = round(p["available_qty"] * float(sku_price.get(p["sku_id"]) or 0.0), 2)
        positions.append({
            "inventory_position_id": p["inventory_position_id"], "sku_id": p["sku_id"],
            "available_qty": p["available_qty"], "reserved_qty": p["reserved_qty"],
            "in_transit_qty": p["in_transit_qty"], "safety_stock": p["safety_stock"],
            "atp": p["available_qty"] + p["in_transit_qty"] - p["reserved_qty"],
            "below_safety": p["available_qty"] <= p["safety_stock"],
            "inventory_value_usd": money(val)})
    reservations = _rows(con, """SELECT res.reservation_id, res.so_line_id, res.inventory_position_id,
                          res.qty, res.status FROM inventory_reservations res
                          JOIN inventory_positions p ON p.inventory_position_id=res.inventory_position_id
                          WHERE p.warehouse_id=? ORDER BY res.reservation_id""", warehouse_id)
    cycle_counts = _rows(con, """SELECT cycle_count_id, inventory_position_id, system_qty,
                          counted_qty, variance, status FROM cycle_counts WHERE warehouse_id=?
                          ORDER BY cycle_count_id""", warehouse_id)

    # 锚定本仓的仓储风险(R16-R18) + 处置任务 + 每风险角色可用 action（锚点 warehouse_id；
    # 受影响对象 id 承载在 affected_so_line_ids：R16=inventory_position_id / R17=so_line_id / R18=cycle_count_id）
    risks_view, all_actions = [], set()
    for r in _rows(con, """SELECT * FROM risk_events WHERE warehouse_id=?
                           ORDER BY risk_event_id""", warehouse_id):
        tasks = _rows(con, """SELECT task_id, status, approval_status, assignee_role,
                              assignee_user_id, proposed_action FROM tasks WHERE risk_event_id=?
                              ORDER BY task_id""", r["risk_event_id"])
        acts = available_actions(role, r["status"], tasks)
        all_actions |= set(acts)
        risks_view.append({
            "risk_event_id": r["risk_event_id"], "rule_id": r["rule_id"], "type": r["type"],
            "severity": r["severity"], "status": r["status"],
            "affected_value_usd": r["affected_value_usd"], "root_cause": r["root_cause"],
            "affected_object_ids": json.loads(r["affected_so_line_ids"] or "[]"),
            "tasks": tasks, "available_actions": acts})

    return {
        "warehouse": {k: wh[k] for k in ("warehouse_id", "type", "operator", "region",
                                         "capacity_units")},
        "positions": positions,
        "reservations": reservations,
        "cycle_counts": cycle_counts,
        "anchored_risks": risks_view,
        "available_actions": sorted(all_actions),
        "cost_visible": cost_visible,
        "role": role,
    }


def make_warehouse_agent_session(role, warehouse_id, db_path="data/ontology.sqlite"):
    """构造预 scope 到本仓 + 当前 role 的对象级 agent 会话——同一 permission-aware 框架，
    只注入 role 与 focus_warehouse_id，不新造 agent。帮分析库存/断货/现货可用性，起草断货补货/
    现货拆单/盘点调整提案；approve/close 不在其工具集内（原则2，FORBIDDEN_TOOLS：agent 只分析/提案不审批）。"""
    return AgentSession(db_path=db_path, role=role, focus_warehouse_id=warehouse_id)


def focus_warehouse_briefing_text(session):
    """无 API key 确定性 fallback：对 focus 的仓库生成库存简报（低于安全库存头寸 + 盘点差异 +
    锚定仓储风险 + 处置建议，每条带对象 ID 出处，审批须人工——原则2）。不依赖任何 LLM。"""
    wid = session.focus_warehouse_id
    if not wid:
        return "本会话未 focus 到任何 warehouse"
    b = session.focus_warehouse_bundle()
    if "error" in b:
        return b["error"]
    wh = b["warehouse"]
    positions, ccs, risks = b["positions"], b["cycle_counts"], b["anchored_risks"]
    low = [p for p in positions if p["below_safety"]]
    variance_ccs = [c for c in ccs if c["variance"] != 0]
    active_res = [r for r in b["reservations"] if r["status"] not in ("released", "fulfilled")]
    lines = [f"[{wid}] 仓储库存简报（AI 建议，审批/关闭须人工——原则2）",
             f"仓库类型 {wh['type']} / 运营方 {wh['operator']} / 区域 {wh['region']} / "
             f"容量 {wh['capacity_units']} / 数据出处对象 warehouses.{wid}",
             f"库存概览（数据出处对象 inventory_positions）：共 {len(positions)} 个头寸，"
             f"其中 {len(low)} 个低于安全库存；活跃预留 {len(active_res)} 条；"
             f"盘点差异 {len(variance_ccs)} 处。"]
    if low:
        lines.append("低于安全库存头寸（断货，建议 escalate_replenishment 升级补货）：")
        for p in low:
            lines.append(f"  · {p['inventory_position_id']}（{p['sku_id']}）可用 {p['available_qty']} "
                         f"≤ 安全 {p['safety_stock']}（在途 {p['in_transit_qty']}，ATP {p['atp']}）")
    if variance_ccs:
        lines.append("盘点差异（数据出处对象 cycle_counts，建议 adjust_inventory 按实盘调整）：")
        for c in variance_ccs:
            lines.append(f"  · {c['cycle_count_id']}（{c['inventory_position_id']}）账面 "
                         f"{c['system_qty']} 实盘 {c['counted_qty']}（差 {c['variance']}，{c['status']}）")
    if risks:
        lines.append("锚定仓储风险（R16-R18，数据出处对象 risk_events，锚点 warehouse_id）：")
        for r in risks:
            anchors = json.loads(r["affected_so_line_ids"] or "[]")
            lines.append(f"  · {r['risk_event_id']} {r['rule_id']} {r['type']}（{r['severity']}，"
                         f"{r['status']}，影响 ${r['affected_value_usd']}，受影响 {anchors}）："
                         f"{r['root_cause']}")
        lines.append("建议：断货(R16)→escalate_replenishment / 不可履约(R17)→suggest_substitution"
                     "（现货拆单先发+余量 backorder）/ 盘点差异(R18)→adjust_inventory；均须走"
                     " assign→propose→approve，needs_human_approval: true（AI 不审批）。")
    else:
        lines.append("锚定仓储风险：无（本仓 R16-R18 未检出异常）。")
    return "\n".join(lines)


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

    # P1-2：确定性简报移到对象详情区顶部并默认展开（陌生人测试两位测试者都点名这是全场最有用的
    # 东西，原先在页面底部默认折叠会被忽略）。生成逻辑不变（同一个 sess/briefing_text，只是提前算、
    # 挪到最上面渲染 + expanded 改 True），下方 ④ 对象级 AI 面板复用同一份 briefing_text 不重算。
    sess = make_agent_session(role, risk_event_id)
    _briefing = focus_briefing_text(sess)
    with st.expander("查看确定性风险简报（无需 API key，每条事实带对象 ID 出处）", expanded=True):
        st.text(_briefing)

    # ① 风险属性
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("级别", r["severity"])
    c2.metric("状态", r["status"])
    c3.metric("规则", r["rule_id"])
    c4.metric("影响金额$", r["affected_value_usd"])
    st.markdown(f"**类型** `{r['type']}`　**货运** `{r['shipment_id']}`　"
                f"**根因**：{ux_copy.humanize_root_cause(r['rule_id'], r['root_cause'])}")

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

    # ④ 对象级 agent 面板（预 scope 到本 risk + 当前 role；简报已在页首展开，此处只放提问区）
    st.markdown("**对象级 AI 助手**")
    st.caption(f"本会话工具集（role={role}，focus={risk_event_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— approve/close 永不在内（agent 只提案不审批）。")
    _render_object_ai_qa(_briefing, role, key=f"wb_{risk_event_id}",
                         anchor=("RiskEvent", risk_event_id))


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

    # P1-2：确定性简报移到顶部默认展开（同一份 sess/briefing_text，下方④面板复用不重算）
    sess = make_admission_agent_session(role, admission_case_id)
    _briefing = focus_admission_briefing_text(sess)
    with st.expander("查看确定性准入简报（无需 API key，每条事实带对象 ID 出处）", expanded=True):
        st.text(_briefing)

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

    # ④ 对象级 agent 面板（预 scope 到本案 + 当前 role；简报已在页首展开，此处只放提问区）
    st.markdown("**对象级 AI 助手**")
    st.caption(f"本会话工具集（role={role}，focus={admission_case_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— 审批/拒接（B5/B6）永不在内（agent 只准备不决策）。")
    _render_object_ai_qa(_briefing, role, key=f"awb_{admission_case_id}",
                         anchor=("AdmissionCase", admission_case_id))


def render_task_object_workbench(task_id, role, actor, as_of, db_factory, render_table):
    """在任务处理台内渲染任务对象工作台：① 任务属性 ② 关联对象（父 RiskEvent / 受影响 SO 行 /
    当前提案）③ 角色可用 action ④ 对象级 agent 面板（预 scope 到本 task + 父风险，permission-aware）。

    与 render_object_workbench 同结构（复用已验证模式）。db_factory / render_table 同规。"""
    import streamlit as st

    con = db_factory()
    wb = build_task_workbench(con, task_id, role)
    if "error" in wb:
        st.warning(wb["error"])
        return
    t, r = wb["task"], wb["risk"]
    st.markdown(f"### 🔬 对象工作台 · {t['task_id']}")
    st.caption("以 Task 为中心的富视图：属性 + 关联对象（父风险/受影响行/提案）+ 该角色可用动作 + "
               "对象级 AI（预 scope 到本 task + 父风险，permission-aware）")

    # P1-2：确定性简报移到顶部默认展开（同一份 sess/briefing_text，下方④面板复用不重算）
    sess = make_task_agent_session(role, task_id)
    _briefing = focus_task_briefing_text(sess)
    with st.expander("查看确定性任务简报（无需 API key，每条事实带对象 ID 出处）", expanded=True):
        st.text(_briefing)

    # ① 任务属性
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("任务状态", t["status"])
    c2.metric("审批", t["approval_status"] or "-")
    c3.metric("SLA", t["sla_state"] or "-")
    c4.metric("升级级别", t["escalation_level"])
    st.markdown(f"**{t['title']}**　负责人 `{t['assignee_user_id'] or '-'}`　"
                f"团队 `{t['assignee_team_id'] or '-'}`　负责角色 `{t['assignee_role']}`　"
                f"优先级 `{t['priority']}`　处理截止 `{t['due_at']}`")

    # ② 关联对象
    if r:
        st.markdown(f"**关联 · 父风险** `{r['risk_event_id']}`（{r['severity']} 级 {r['type']}，"
                    f"规则 {r['rule_id']}，货运 {r['shipment_id']}，影响 ${r['affected_value_usd']}）"
                    f"　根因：{ux_copy.humanize_root_cause(r['rule_id'], r['root_cause'])}")
    st.markdown("**关联 · 受影响订单行**")
    render_table([{"订单行": x["so_line_id"], "订单": x["so_id"], "数量": x["qty"],
                   "承诺日": x["promised_delivery_date"], "行状态": x["line_status"],
                   "客户": x["customer_name"], "客户等级": x["tier"]}
                  for x in wb["affected_lines"]])
    if wb["proposal"]:
        p = wb["proposal"]
        st.markdown(f"**关联 · 当前提案**：`{p['proposed_action']}` {p['proposal_params']}"
                    f"　审批 `{p['approval_status'] or '-'}`　提案角色 `{p['proposal_actor_role'] or '-'}`")

    # ③ 该角色可用 action（按 ROLE_PERMS gate）
    acts = wb["available_actions"]
    st.markdown(f"**该角色（{role}）在本任务上可发起的动作**："
                + ("、".join(acts) if acts else "（无——该角色对本任务状态无可发起动作）"))
    st.caption("动作权限由 app.actions.ROLE_PERMS + maker-checker 硬 gate；审批（A5）永远人来点，"
               "AI 只提案不审批（原则2）。执行入口在任务台「提案/审批」表单。")

    # ④ 对象级 agent 面板（预 scope 到本 task + 父风险；简报已在页首展开，此处只放提问区）
    st.markdown("**对象级 AI 助手**")
    st.caption(f"本会话工具集（role={role}，focus={task_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— approve/close 永不在内（agent 只提案不审批）。")
    _render_object_ai_qa(_briefing, role, key=f"twb_{task_id}",
                         anchor=("Task", task_id))


def render_invoice_object_workbench(invoice_id, role, actor, as_of, db_factory, render_table):
    """在费用工作台内渲染发票对象工作台：① 发票属性 ② 关联对象（账单行+基准差异 / 关联货运 /
    flag 它的费用风险 / 费用处置任务）③ 角色可用 action ④ 对象级 agent 面板（预 scope 到本发票 +
    关联风险，permission-aware，帮分析费用差异/起草 dispute，不审批）。

    与 render_admission_object_workbench 同结构（复用已验证模式）。成本字段随 role 脱敏。"""
    import streamlit as st

    con = db_factory()
    wb = build_invoice_workbench(con, invoice_id, role)
    if "error" in wb:
        st.warning(wb["error"])
        return
    iv, sp = wb["invoice"], wb["shipment"]
    st.markdown(f"### 🔬 对象工作台 · {iv['invoice_id']}")
    st.caption("以 Invoice 为中心的富视图：属性 + 关联对象（账单行/货运/费用风险）+ 该角色可用动作 + "
               "对象级 AI（预 scope 到本发票 + 关联风险，permission-aware）")

    # P1-2：确定性简报移到顶部默认展开（同一份 sess/briefing_text，下方④面板复用不重算）
    sess = make_invoice_agent_session(role, invoice_id)
    _briefing = focus_invoice_briefing_text(sess)
    with st.expander("查看确定性发票对账简报（无需 API key，逐行差异带对象 ID 出处）", expanded=True):
        st.text(_briefing)

    # ① 发票属性（金额随 role 脱敏）
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("状态", iv["status"])
    c2.metric("金额", f"{iv['total_usd']} {iv['currency']}" if wb["cost_visible"] else MASK)
    c3.metric("类型", iv["vendor_type"])
    c4.metric("开票日", iv["issue_date"])
    st.markdown(f"**vendor** `{iv['vendor_name']}`　单号 `{iv['vendor_invoice_no'] or '-'}`　"
                f"货运 `{iv['shipment_id']}`"
                + (f"（incoterm {sp['incoterm']}，{sp['origin_port_locode']}→"
                   f"{sp['destination_port_locode']}，延误 {sp['delay_days']} 天）" if sp else ""))
    if not wb["cost_visible"]:
        st.caption(f"成本字段对角色 {role} 脱敏（🔒）——finance/manager 可见金额，其余掩码"
                   "（与费用工作台 mask_cost 同规）。")

    # ② 关联对象
    st.markdown("**关联 · 账单行（join 基准与差异，异常来自费用风险标记）**")
    render_table([{"账单行": x["invoice_line_id"], "费种": ux_copy.charge_code_label(x["charge_code"]),
                   "柜": x["container_no"] or "-", "金额$": x["amount_usd"],
                   "基准$": x["baseline_usd"] if x["baseline_usd"] is not None else "无基准",
                   "差异$": x["diff_usd"], "异常": "!" if x["is_anomaly"] else ""}
                  for x in wb["lines"]])
    with st.expander("费种代码对照（缩写 = 中文）", expanded=False):
        st.caption(ux_copy.CHARGE_CODE_LEGEND)
    if wb["flagging_risks"]:
        st.markdown("**关联 · flag 本发票的费用风险（R4/R5/R6）**")
        render_table([{"风险": r["risk_event_id"], "规则": r["rule_id"], "类型": r["type"],
                       "级别": r["severity"], "状态": r["status"],
                       "命中本票账单行": "、".join(r["affected_lines_on_this_invoice"])}
                      for r in wb["flagging_risks"]])
        with st.expander("规则代码对照（R几 = 什么风险）", expanded=False):
            st.caption(ux_copy.RULE_LEGEND)
    if wb["cost_tasks"]:
        st.markdown("**关联 · 费用处置任务**")
        render_table([{"任务": t["task_id"], "风险": t["risk_event_id"],
                       "负责角色": t["assignee_role"], "提案": t["proposed_action"] or "-",
                       "审批": t["approval_status"] or "-", "状态": t["status"]}
                      for t in wb["cost_tasks"]])

    # ③ 该角色可用 action（按 ROLE_PERMS gate）
    acts = wb["available_actions"]
    st.markdown(f"**该角色（{role}）在本发票上可发起的动作**："
                + _actions_or_hint(acts, role, "（无——该角色对本发票关联任务状态无可发起动作）"))
    st.caption("费用提案 dispute/accept_charge/rebill_customer 经 propose_mitigation（ProposeMitigation "
               "权限）；rebill 的 G4 incoterm 门禁在审批时判定；审批（A5）永远人来点，AI 只提案不审批"
               "（原则2）。执行入口在任务台费用处置表单。")

    # ④ 对象级 agent 面板（预 scope 到本发票 + 关联风险；简报已在页首展开，此处只放提问区）
    st.markdown("**对象级 AI 助手**")
    st.caption(f"本会话工具集（role={role}，focus={invoice_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— approve/close 永不在内（agent 只分析/起草提案，不审批）。")
    _render_object_ai_qa(_briefing, role, key=f"iwb_{invoice_id}",
                         anchor=("Invoice", invoice_id))


def render_po_object_workbench(po_id, role, actor, as_of, db_factory, render_table):
    """在采购工作台内渲染采购单对象工作台：① PO 属性 ② PoLine 行 + 三方对账（订购×收货×开票）+
    关联 GoodsReceipt/SupplierInvoice ③ 锚定本 PO 的采购风险(R7-R10) + 角色可用 action ④ 对象级
    agent 面板（预 scope 到本 PO，帮分析三方差异/起草索赔，permission-aware，不审批）。

    与 render_invoice_object_workbench 同结构（复用已验证模式）。金额字段随 role 脱敏。"""
    import streamlit as st

    con = db_factory()
    wb = build_po_workbench(con, po_id, role)
    if "error" in wb:
        st.warning(wb["error"])
        return
    po, sup = wb["purchase_order"], wb["supplier"]
    st.markdown(f"### 🔬 对象工作台 · {po['po_id']}")
    st.caption("以 PurchaseOrder 为中心的富视图：属性 + 采购行 + 三方对账 + 锚定采购风险 + "
               "该角色可用动作 + 对象级 AI（预 scope 到本 PO + 当前角色，permission-aware）")

    # P1-2：确定性简报移到顶部默认展开（同一份 sess/briefing_text，下方④面板复用不重算）
    sess = make_po_agent_session(role, po_id)
    _briefing = focus_po_briefing_text(sess)
    with st.expander("查看确定性三方对账简报（无需 API key，逐行差异带对象 ID 出处）", expanded=True):
        st.text(_briefing)

    # ① PO 属性
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("状态", po["status"])
    c2.metric("采购行数", len(wb["po_lines"]))
    c3.metric("收货单", len(wb["goods_receipts"]))
    c4.metric("供应商发票", len(wb["supplier_invoices"]))
    st.markdown(f"**供应商** `{po['supplier_id']}"
                + (f" {sup['supplier_name']}（{sup['city']}，验厂 {sup['factory_audit_status']}）"
                   if sup else "") + f"`　下单日 `{po['po_date']}`　预计齐备 `{po['expected_ready_date']}`")
    if not wb["cost_visible"]:
        st.caption(f"金额字段对角色 {role} 脱敏（🔒 单价/金额/发票总额）——finance/manager 可见"
                   "（与费用工作台 mask_cost / 标准视图同规；R7/R8/R9 数量维度不受影响）。")

    # ② 三方对账（订购 × 收货 × 开票，逐行）
    st.markdown("**三方对账 · 订购 × 收货 × 开票（逐行）**")
    render_table([{"采购行": rc["po_line_id"], "SKU": rc["sku_id"], "订购": rc["ordered_qty"],
                   "单价$": rc["unit_price_usd"], "预计齐备": rc["expected_ready_date"],
                   "收货": rc["received_qty"], "验收": rc["accepted_qty"], "拒收": rc["rejected_qty"],
                   "最早到货": rc["earliest_receipt"] or "未收", "QC": "✗" if rc["qc_failed"] else "✓",
                   "缺口": rc["qty_short"], "开票量": rc["invoiced_qty"],
                   "开票单价$": rc["invoice_unit_price_usd"], "开票额$": rc["invoiced_amount_usd"],
                   "行状态": rc["line_status"]} for rc in wb["reconciliation"]])
    st.markdown("**关联 · 收货单**")
    render_table([{"收货单": g["grn_id"], "收货日": g["received_date"], "状态": g["status"]}
                  for g in wb["goods_receipts"]])
    st.markdown("**关联 · 供应商发票**")
    render_table([{"供票": si["supplier_invoice_id"], "单号": si["vendor_invoice_no"] or "-",
                   "开票日": si["issue_date"], "总额$": si["total_usd"], "状态": si["status"]}
                  for si in wb["supplier_invoices"]])

    # ③ 锚定采购风险 + 角色可用 action
    st.markdown("**锚定本 PO 的采购风险（R7-R10）**")
    if wb["anchored_risks"]:
        render_table([{"风险": r["risk_event_id"], "规则": r["rule_id"], "类型": r["type"],
                       "级别": r["severity"], "状态": r["status"], "影响$": r["affected_value_usd"],
                       "采购行": "、".join(r["affected_po_line_ids"]),
                       "可用动作": "、".join(r["available_actions"]) or "—"}
                      for r in wb["anchored_risks"]])
    else:
        st.caption("（本 PO 三方对账未检出 R7-R10 异常）")
    with st.expander("规则代码对照（R几 = 什么风险）", expanded=False):
        st.caption(ux_copy.RULE_LEGEND)
    acts = wb["available_actions"]
    st.markdown(f"**该角色（{role}）在本 PO 锚定风险上可发起的动作**："
                + _actions_or_hint(acts, role, "（无——该角色对本 PO 风险状态无可发起动作）"))
    st.caption("采购风险走既有闭环：派发（A3，运营）→提案 expedite_po/raise_supplier_claim/"
               "dispute_supplier_invoice/accept_receipt_variance（A4，ProposeMitigation 权限）→审批"
               "（A5，仅经理，maker-checker）；审批/关闭永远人来点，AI 只提案不审批（原则2）。"
               "执行入口在任务台「提案/审批」表单。")

    # ④ 对象级 agent 面板（预 scope 到本 PO；简报已在页首展开，此处只放提问区）
    st.markdown("**对象级 AI 助手**")
    st.caption(f"本会话工具集（role={role}，focus={po_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— approve/close 永不在内（agent 只分析三方差异/起草提案，不审批）。")
    _render_object_ai_qa(_briefing, role, key=f"pwb_{po_id}",
                         anchor=("PurchaseOrder", po_id))


def render_warehouse_object_workbench(warehouse_id, role, actor, as_of, db_factory, render_table):
    """在对象浏览器内渲染仓库对象工作台：① Warehouse 属性 ② 该仓 InventoryPosition（含 ATP + 低于
    安全库存标红）+ InventoryReservation + CycleCount(variance) ③ 锚定本仓的仓储风险(R16-R18) +
    角色可用 action ④ 对象级 agent 面板（预 scope 到本仓，帮分析库存/断货/现货可用性，permission-aware，不审批）。

    与 render_po_object_workbench 同结构（复用已验证模式）。库存数量为运营字段不脱敏；派生的在手库存
    金额随 role 脱敏（与 UI mask_cost 同规）。"""
    import streamlit as st

    con = db_factory()
    wb = build_warehouse_workbench(con, warehouse_id, role)
    if "error" in wb:
        st.warning(wb["error"])
        return
    wh = wb["warehouse"]
    st.markdown(f"### 🔬 对象工作台 · {wh['warehouse_id']}")
    st.caption("以 Warehouse 为中心的富视图：属性 + 库存头寸/预留/盘点 + 锚定仓储风险(R16-R18) + "
               "该角色可用动作 + 对象级 AI（预 scope 到本仓 + 当前角色，permission-aware）")

    # P1-2：确定性简报移到顶部默认展开（同一份 sess/briefing_text，下方④面板复用不重算）
    sess = make_warehouse_agent_session(role, warehouse_id)
    _briefing = focus_warehouse_briefing_text(sess)
    with st.expander("查看确定性仓储库存简报（无需 API key，每条带对象 ID 出处）", expanded=True):
        st.text(_briefing)

    # ① 仓库属性
    low_n = sum(1 for p in wb["positions"] if p["below_safety"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("类型", wh["type"])
    c2.metric("库存头寸", len(wb["positions"]))
    c3.metric("低于安全库存", low_n)
    c4.metric("盘点差异", sum(1 for c in wb["cycle_counts"] if c["variance"] != 0))
    st.markdown(f"**运营方** `{wh['operator']}`　区域 `{wh['region']}`　容量 `{wh['capacity_units']}`")
    if not wb["cost_visible"]:
        st.caption(f"在手库存金额对角色 {role} 脱敏（🔒 inventory_value_usd）——finance/manager 可见"
                   "（与费用工作台 mask_cost / 标准视图同规；数量字段为运营数据不脱敏）。")

    # ② 库存头寸（低于 safety_stock 标红）+ 预留 + 盘点
    st.markdown("**库存头寸（available / reserved / in_transit vs safety_stock；⚠=低于安全库存）**")
    render_table([{"头寸": p["inventory_position_id"], "SKU": p["sku_id"],
                   "可用": p["available_qty"], "预留": p["reserved_qty"], "在途": p["in_transit_qty"],
                   "ATP": p["atp"], "安全库存": p["safety_stock"],
                   "在手金额$": p["inventory_value_usd"],
                   "断货": "⚠" if p["below_safety"] else ""} for p in wb["positions"]])
    st.markdown("**关联 · 库存预留（Reservation）**")
    render_table([{"预留": r["reservation_id"], "订单行": r["so_line_id"],
                   "头寸": r["inventory_position_id"], "数量": r["qty"], "状态": r["status"]}
                  for r in wb["reservations"]])
    st.markdown("**关联 · 循环盘点（CycleCount，variance≠0 为差异）**")
    render_table([{"盘点单": c["cycle_count_id"], "头寸": c["inventory_position_id"],
                   "账面": c["system_qty"], "实盘": c["counted_qty"], "差异": c["variance"],
                   "状态": c["status"]} for c in wb["cycle_counts"]])

    # ③ 锚定仓储风险 + 角色可用 action
    st.markdown("**锚定本仓的仓储风险（R16 断货 / R17 不可履约 / R18 盘点差异）**")
    if wb["anchored_risks"]:
        render_table([{"风险": r["risk_event_id"], "规则": r["rule_id"], "类型": r["type"],
                       "级别": r["severity"], "状态": r["status"], "影响$": r["affected_value_usd"],
                       "受影响对象": "、".join(r["affected_object_ids"]),
                       "可用动作": "、".join(r["available_actions"]) or "—"}
                      for r in wb["anchored_risks"]])
    else:
        st.caption("（本仓未检出 R16-R18 异常）")
    with st.expander("规则代码对照（R几 = 什么风险）", expanded=False):
        st.caption(ux_copy.RULE_LEGEND)
    acts = wb["available_actions"]
    st.markdown(f"**该角色（{role}）在本仓锚定风险上可发起的动作**："
                + ("、".join(acts) if acts else "（无——该角色对本仓风险状态无可发起动作）"))
    st.caption("仓储风险走既有闭环：派发（A3，运营）→提案 escalate_replenishment（R16）/"
               "suggest_substitution（R17 现货拆单先发）/adjust_inventory（R18 按实盘调整）"
               "（A4，ProposeMitigation 权限）→审批（A5，仅经理，maker-checker）；审批/关闭永远人来点，"
               "AI 只提案不审批（原则2）。执行入口在风险台派发 + 任务台「提案/审批」表单。")

    # ④ 对象级 agent 面板（预 scope 到本仓；简报已在页首展开，此处只放提问区）
    st.markdown("**对象级 AI 助手**")
    st.caption(f"本会话工具集（role={role}，focus={warehouse_id}）："
               + "、".join(sorted(sess.allowed_tools))
               + "　— approve/close 永不在内（agent 只分析库存/断货/现货可用性、起草提案，不审批）。")
    _render_object_ai_qa(_briefing, role, key=f"wwb_{warehouse_id}",
                         anchor=("Warehouse", warehouse_id))
