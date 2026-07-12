"""W6 AI 工具层（D7 兑现）：AI 只能通过本文件注册的工具接触 ontology。

护栏（plan §11-W6 / v0.1 手册 AI 原则）：
1. proposal-only：写动作只暴露 assign_task 与 propose_mitigation；
   approve_mitigation / close_risk_event **永不注册**——诱导越权时 dispatcher 拒绝并写审计
2. 一个 permission-aware 框架：Session 接受当前 role（默认 ops，向后兼容）+ 可选 focus_risk_event_id；
   工具集按 ROLE_PERMS/域 scoping，字段脱敏随 role 变，focus 时检索/工具聚焦该对象及其邻居
3. 一切工具调用走 app.actions 的同一套校验与审计，AI 没有后门
"""
import json
import sqlite3

import yaml

from app.actions import assign_task, propose_mitigation, _log, ROLE_PERMS
from app.admission_actions import (ADM_PERMS, create_admission_case, run_compliance_precheck,
                                   build_logistics_plan, calculate_cost_scenario)
from engine.graph import explain_path
# C1 只读检索处置记忆（写入函数不 import——AI 永远拿不到写工具）
from engine.resolution_memory import find_similar, lane_for_shipment, render_precedent_block

AI_ACTOR = "ai-agent"
AI_ROLE = "ops"  # 默认角色（不传 role 时的向后兼容值）
# 审批/关闭/拒接类动作永不向任何 role 的 AI 会话开放（v0.2 E5 + v0.3 AD2 红线，原则2）
FORBIDDEN_TOOLS = {"approve_mitigation", "close_risk_event",
                   "approve_quote_decision", "reject_or_request_more_info"}
COST_FIELDS = {"quote_price_usd", "product_cost_usd", "first_mile_cost_usd",
               "international_freight_usd", "duty_tax_usd", "customs_brokerage_usd",
               "warehouse_cost_usd", "last_mile_cost_usd", "returns_allowance_usd",
               "risk_buffer_usd", "gross_margin_usd", "gross_margin_rate"}
# 发票对账金额字段脱敏集（单一事实源，app.object_workbench / standard_object_view 复用本常量）：
# 发票 total_usd + 逐行 amount/unit_price/baseline/diff。与 UI mask_cost 同规（finance/manager 可见）。
INVOICE_COST_FIELDS = ("amount_usd", "unit_price_usd", "baseline_usd", "diff_usd")
MASK = "🔒无权查看"

# --- 角色 → 工具集 scoping（对象工作台切片：给同一框架注入 role，不复制平行 agent）---
# 读工具按域分组：风险/物流域对所有 role 开放（RiskEvent 对象工作台核心）；成本、准入域按
# role 相关性 scoping。ops 保留全域（历史基线，不回归 agent.evaluate）；cs 无成本域（"无 cost 相关"）；
# finance 可成本域；manager 全域只读。写工具白名单完全由 app.actions.ROLE_PERMS 决定（不复制权限）。
RISK_READ_TOOLS = {"list_open_risks", "get_risk", "get_shipment_context",
                   "get_impact_chain", "get_audit_trail", "explain_relationship_path",
                   "get_similar_resolutions"}  # C1 先例检索：只读，随风险域对全角色开放
COST_READ_TOOLS = {"list_invoices", "get_invoice_context"}
ADMISSION_READ_TOOLS = {"list_admission_cases", "get_admission_context"}
COST_READ_ROLES = {"ops", "finance", "manager"}
ADMISSION_READ_ROLES = {"ops", "finance", "manager", "sales", "compliance"}
WRITE_TOOL_PERM = {"assign_task": "AssignTask", "propose_mitigation": "ProposeMitigation"}
# 准入准备动作 B1-B4（AdmissionCase 切片，同 RiskEvent 写工具模式：白名单由 ADM_PERMS gate，不复制权限）。
# B5/B6（approve_quote_decision/reject_or_request_more_info）**永不**出现在此——它们在 FORBIDDEN_TOOLS，
# agent 只做准备动作、不夺审批/拒接决策（maker-checker，原则2）。
ADMISSION_WRITE_PERM = {"create_admission_case": "CreateAdmissionCase",
                        "run_compliance_precheck": "RunCompliancePrecheck",
                        "build_logistics_plan": "BuildLogisticsPlan",
                        "calculate_cost_scenario": "CalculateCostScenario"}
ALL_WRITE_PERM = {**WRITE_TOOL_PERM, **ADMISSION_WRITE_PERM}


def allowed_tools_for_role(role):
    """该 role 会话可用的工具集：读工具按域 scoping + 写工具经 ROLE_PERMS/ADM_PERMS gate。
    approve/close/审批/拒接对任何 role 都不在此集合（FORBIDDEN_TOOLS，原则2：agent 只准备不决策）。"""
    tools = set(RISK_READ_TOOLS)
    if role in COST_READ_ROLES:
        tools |= COST_READ_TOOLS
    if role in ADMISSION_READ_ROLES:
        tools |= ADMISSION_READ_TOOLS
    for tool, perm in WRITE_TOOL_PERM.items():
        if role in ROLE_PERMS[perm]:
            tools.add(tool)
    for tool, perm in ADMISSION_WRITE_PERM.items():  # B1-B4 准备动作按 ADM_PERMS gate
        if role in ADM_PERMS[perm]:
            tools.add(tool)
    return tools


def _can_see_tier(role):
    """Customer.tier 脱敏规则（与 UI mask_tier 同规）：cs/manager 可见，其余脱敏。"""
    return role in ("cs", "manager")


def _can_see_cost(role):
    """成本字段脱敏规则（与 UI mask_cost 同规）：finance/manager 可见，其余脱敏。"""
    return role in ("finance", "manager")

# Anthropic tool-use 格式的工具定义（任何支持 tool-use 的 LLM 均可转换使用）
TOOL_DEFS = [
    {"name": "list_open_risks",
     "description": "列出未关闭的风险事件，可按 severity 过滤（critical/high/medium）",
     "input_schema": {"type": "object", "properties": {
         "severity": {"type": "string", "enum": ["critical", "high", "medium"]}}}},
    {"name": "get_risk",
     "description": "查单个风险事件详情（类型/规则/级别/根因/受影响行/金额/状态）",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"}}, "required": ["risk_event_id"]}},
    {"name": "get_shipment_context",
     "description": "查货运完整上下文：基础信息、判重后事件流、所载订单行与客户（按角色脱敏）",
     "input_schema": {"type": "object", "properties": {
         "shipment_id": {"type": "string"}}, "required": ["shipment_id"]}},
    {"name": "get_impact_chain",
     "description": "查风险的影响链：受影响订单行→销售订单→客户，含分配数量与金额",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"}}, "required": ["risk_event_id"]}},
    {"name": "get_audit_trail",
     "description": "查对象（风险/任务）的审计历史，含被拒绝的调用",
     "input_schema": {"type": "object", "properties": {
         "object_id": {"type": "string"}}, "required": ["object_id"]}},
    {"name": "list_admission_cases",
     "description": "列出准入案件（v0.3），可按 status 过滤（draft/in_precheck/plan_ready/priced/approved/quote_with_conditions/rejected/needs_more_info）",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "get_admission_context",
     "description": "查准入案件完整上下文：案件、合规发现、物流方案、成本情景（成本字段按角色脱敏）、客户能力",
     "input_schema": {"type": "object", "properties": {
         "admission_case_id": {"type": "string"}}, "required": ["admission_case_id"]}},
    {"name": "list_invoices",
     "description": "列出发票（v0.4），可按 status 过滤（received/under_review/approved/disputed）。"
                    "返回 invoice_id/vendor/type/shipment/total/status/issue_date",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "get_invoice_context",
     "description": "查发票完整对账上下文：发票 + 行明细（join expected_costs 给出基准与差异列）"
                    "+ 所属 shipment 摘要（incoterm/delay_days/status）。发票金额字段按角色脱敏"
                    "（与 UI 费用工作台 mask_cost 同规：finance/manager 可见，其余掩码）",
     "input_schema": {"type": "object", "properties": {
         "invoice_id": {"type": "string"}}, "required": ["invoice_id"]}},
    {"name": "get_similar_resolutions",
     "description": "C1 只读检索处置记忆：按规则类型精确匹配+同航线（origin→destination LOCODE）"
                    "查同类风险的历史处置——同类 N 次、按方案/决定的统计、最相似 1 案详情"
                    "（当时提案/人的决定/实际结果/质量标签）。所有数字运行时从 resolution_memory "
                    "现算可回查；无先例如实返回首例；被屏蔽（voided）的记忆不返回",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"}}, "required": ["risk_event_id"]}},
    {"name": "explain_relationship_path",
     "description": "只读查询 object_relationships：解释两个对象之间的有向关系路径",
         "input_schema": {"type": "object", "properties": {
             "source_type": {"type": "string"},
             "source_id": {"type": "string"},
             "target_type": {"type": "string"},
             "target_id": {"type": "string"},
             "max_depth": {"type": "integer", "minimum": 1, "maximum": 6}},
             "required": ["source_type", "source_id", "target_type", "target_id", "max_depth"]}},
    {"name": "assign_task",
     "description": "为 open 状态的风险派发处置任务（A3）。这是允许 AI 执行的写动作之一",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"}, "assignee_role": {"type": "string", "enum": ["ops", "cs"]},
         "priority": {"type": "string", "enum": ["P1", "P2", "P3"]}, "due_at": {"type": "string"}},
         "required": ["risk_event_id", "assignee_role", "priority", "due_at"]}},
    {"name": "propose_mitigation",
     "description": "对 assigned 状态的任务提交处置提案（A4），最终须人工审批。proposal-only 的体现",
     "input_schema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "proposed_action": {"type": "string", "enum": ["reschedule", "expedite", "accept_delay"]},
         "proposal_params": {"type": "object"}},
         "required": ["task_id", "proposed_action", "proposal_params"]}},
    # ---- 准入准备动作 B1-B4（AdmissionCase 切片；按会话 role 经 ADM_PERMS gate，B5/B6 永不注册）----
    {"name": "create_admission_case",
     "description": "B1 建案（仅销售）：为 candidate SKU 建准入案。AI 准备动作之一，不做审批",
     "input_schema": {"type": "object", "properties": {
         "customer_id": {"type": "string"}, "sku_id": {"type": "string"},
         "request_type": {"type": "string"}, "incoterm_candidate": {"type": "string"},
         "target_launch_date": {"type": "string"}, "monthly_order_estimate": {"type": "integer"}},
         "required": ["customer_id", "sku_id", "request_type", "incoterm_candidate",
                      "target_launch_date", "monthly_order_estimate"]}},
    {"name": "run_compliance_precheck",
     "description": "B2 合规预审（仅合规）：提交 findings 列表，风险等级重算。AI 准备动作，不做审批",
     "input_schema": {"type": "object", "properties": {
         "admission_case_id": {"type": "string"},
         "findings": {"type": "array", "items": {"type": "object"}}},
         "required": ["admission_case_id", "findings"]}},
    {"name": "build_logistics_plan",
     "description": "B3 物流方案（仅运营）：建方案，DDP 门禁自动校验。AI 准备动作，不做审批",
     "input_schema": {"type": "object", "properties": {
         "admission_case_id": {"type": "string"}, "plan": {"type": "object"}},
         "required": ["admission_case_id", "plan"]}},
    {"name": "calculate_cost_scenario",
     "description": "B4 成本情景（仅财务）：算成本与毛利。AI 准备动作，不做审批/拒接决策",
     "input_schema": {"type": "object", "properties": {
         "logistics_plan_id": {"type": "string"}, "scenario": {"type": "object"}},
         "required": ["logistics_plan_id", "scenario"]}},
]


class AgentSession:
    """一次 AI 会话的工具执行环境。所有调用统一经 dispatch，越权/未知工具拒绝并审计。"""

    def __init__(self, db_path="data/ontology.sqlite", config_path="config/datagen.yaml",
                 role=AI_ROLE, focus_risk_event_id=None, focus_admission_case_id=None,
                 focus_task_id=None, focus_invoice_id=None, focus_po_id=None,
                 focus_warehouse_id=None):
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
        self.as_of = cfg["window"]["as_of"]
        self.buf = cfg["buffers"]["customs_days"] + cfg["buffers"]["lastmile_days"]
        # permission-aware 注入：当前 role 决定工具集 + 脱敏；focus 决定对象 scoping
        # （RiskEvent / AdmissionCase / Task / Invoice 四类 focus 同一机制，只是聚焦不同对象及其邻居）
        self.role = role
        self.focus_risk_event_id = focus_risk_event_id
        self.focus_admission_case_id = focus_admission_case_id
        self.focus_task_id = focus_task_id
        self.focus_invoice_id = focus_invoice_id
        # PurchaseOrder focus（采购对象工作台切片，Build 3）：采购 RiskEvent 无 shipment，
        # 用 po_id 锚点做对象 scoping（与 shipment 邻居锚点并列，检索聚焦本 PO 的采购风险）。
        self.focus_po_id = focus_po_id
        # Warehouse focus（仓储对象工作台切片）：仓储 RiskEvent(R16-R18) 无 shipment/po_id，
        # 用 warehouse_id 锚点做对象 scoping（检索聚焦本仓的库存/预留/盘点及锚定风险）。
        self.focus_warehouse_id = focus_warehouse_id
        self.allowed_tools = allowed_tools_for_role(role)

    def _rows(self, sql, *a):
        return [dict(r) for r in self.con.execute(sql, a)]

    def _focus_shipment_id(self):
        """focus 对象所属 shipment（对象 scoping 的邻居锚点）；无 focus/不存在返回 None。
        RiskEvent focus 直接取其 shipment；Task focus 经父 RiskEvent 取 shipment；
        Invoice focus 直接取其 shipment——三类对象工作台共用同一 shipment 邻居锚点。"""
        if self.focus_risk_event_id:
            r = self._rows("SELECT shipment_id FROM risk_events WHERE risk_event_id=?",
                           self.focus_risk_event_id)
            return r[0]["shipment_id"] if r else None
        if self.focus_task_id:
            r = self._rows("""SELECT re.shipment_id FROM tasks t
                              JOIN risk_events re ON re.risk_event_id=t.risk_event_id
                              WHERE t.task_id=?""", self.focus_task_id)
            return r[0]["shipment_id"] if r else None
        if self.focus_invoice_id:
            r = self._rows("SELECT shipment_id FROM invoices WHERE invoice_id=?",
                           self.focus_invoice_id)
            return r[0]["shipment_id"] if r else None
        return None

    def tool_defs(self):
        """按会话 role/focus 暴露的工具定义子集（供 LLM 注册；与 dispatch gating 对齐，
        approve/close 从不出现在其中）。"""
        return [t for t in TOOL_DEFS if t["name"] in self.allowed_tools]

    # ---------- 查询工具（只读） ----------
    def list_open_risks(self, severity=None):
        sql = """SELECT risk_event_id, type, rule_id, severity, shipment_id,
                        affected_value_usd, status FROM risk_events
                 WHERE status NOT IN ('resolved','escalated')"""
        params = []
        if severity:
            sql += " AND severity=?"
            params.append(severity)
        # 对象 scoping：focus 时检索默认聚焦该对象及其邻居，不是全库。
        # PO focus（采购）用 po_id 锚点、Warehouse focus（仓储）用 warehouse_id 锚点（这两类采购/
        # 仓储 RiskEvent 无 shipment）；其余用 shipment 邻居锚点。
        focus_ship = self._focus_shipment_id()
        if self.focus_po_id:
            sql += " AND po_id=?"
            params.append(self.focus_po_id)
        elif self.focus_warehouse_id:
            sql += " AND warehouse_id=?"
            params.append(self.focus_warehouse_id)
        elif focus_ship:
            sql += " AND shipment_id=?"
            params.append(focus_ship)
        rows = self._rows(sql, *params)
        out = {"count": len(rows), "risks": rows}
        if self.focus_po_id:
            out["focus_scope"] = {"po_id": self.focus_po_id}
        elif self.focus_warehouse_id:
            out["focus_scope"] = {"warehouse_id": self.focus_warehouse_id}
        elif focus_ship:
            out["focus_scope"] = {"risk_event_id": self.focus_risk_event_id,
                                  "shipment_id": focus_ship}
        return out

    def get_risk(self, risk_event_id):
        r = self._rows("SELECT * FROM risk_events WHERE risk_event_id=?", risk_event_id)
        return r[0] if r else {"error": f"风险事件 {risk_event_id} 不存在"}

    def get_shipment_context(self, shipment_id):
        sp = self._rows("SELECT * FROM shipments WHERE shipment_id=?", shipment_id)
        if not sp:
            return {"error": f"货运 {shipment_id} 不存在"}
        ms = self._rows("""SELECT event_time, event_type, event_classifier, event_locode, new_eta,
                           is_duplicate FROM shipment_milestones WHERE shipment_id=?
                           ORDER BY event_time""", shipment_id)
        chain = self._rows("""SELECT a.so_line_id, a.allocated_qty, l.promised_delivery_date,
                              l.line_status, so.so_id, c.customer_id, c.tier
                              FROM shipment_allocations a
                              JOIN sales_order_lines l ON l.so_line_id=a.so_line_id
                              JOIN sales_orders so ON so.so_id=l.so_id
                              JOIN customers c ON c.customer_id=so.customer_id
                              WHERE a.shipment_id=?""", shipment_id)
        # 字段脱敏随 role 变（与 UI mask_tier 同规）：cs/manager 可见 tier，其余脱敏
        tier_visible = _can_see_tier(self.role)
        for row in chain:
            if not tier_visible:
                row["tier"] = MASK
        return {"shipment": sp[0], "milestones": ms, "onboard_lines": chain,
                "note": f"customer tier {'visible' if tier_visible else 'masked'} for role={self.role}"}

    def get_impact_chain(self, risk_event_id):
        r = self.get_risk(risk_event_id)
        if "error" in r:
            return r
        lids = json.loads(r["affected_so_line_ids"])
        if not lids:
            return {"risk_event_id": risk_event_id, "affected": []}
        ph = ",".join("?" * len(lids))
        rows = self._rows(f"""SELECT l.so_line_id, l.qty, l.unit_price_usd,
                              l.promised_delivery_date, l.line_status, l.reschedule_count,
                              so.so_id, c.customer_id, c.customer_name, c.tier
                              FROM sales_order_lines l
                              JOIN sales_orders so ON so.so_id=l.so_id
                              JOIN customers c ON c.customer_id=so.customer_id
                              WHERE l.so_line_id IN ({ph})""", *lids)
        tier_visible = _can_see_tier(self.role)
        for row in rows:
            if not tier_visible:
                row["tier"] = MASK
        return {"risk_event_id": risk_event_id, "shipment_id": r["shipment_id"],
                "affected_value_usd": r["affected_value_usd"], "affected": rows}

    def focus_bundle(self):
        """对象 scoping 汇总：把检索聚焦到本会话 focus 的 risk 及其邻居（影响链上的
        shipment / SO 行 / 客户），而非全库。无 focus 返回 error。"""
        rid = self.focus_risk_event_id
        if not rid:
            return {"error": "本会话未 focus 到任何 risk"}
        risk = self.get_risk(rid)
        if "error" in risk:
            return risk
        impact = self.get_impact_chain(rid)
        ctx = self.get_shipment_context(risk["shipment_id"])
        neighbors = {
            "shipment_id": risk["shipment_id"],
            "affected_so_line_ids": [a["so_line_id"] for a in impact.get("affected", [])],
            "customer_ids": sorted({a["customer_id"] for a in impact.get("affected", [])}),
        }
        return {"focus_risk_event_id": rid, "risk": risk, "impact": impact,
                "shipment_context": ctx, "neighbors": neighbors,
                "note": f"object-scoped to {rid} and neighbors; role={self.role}"}

    def get_audit_trail(self, object_id):
        rows = self._rows("""SELECT actor, role, action, params_json, as_of_date, result
                             FROM action_log WHERE target_object_id=? ORDER BY log_id""", object_id)
        return {"object_id": object_id, "entries": rows} if rows else \
            {"error": f"未找到 {object_id} 的审计记录"}

    def get_similar_resolutions(self, risk_event_id):
        """C1 只读先例检索：以 risk 的 rule_id+lane 为键查 resolution_memory（排除本案），
        返回统计 + 最相似 1 案 + 渲染好的先例区块。纯查询——AI 无任何处置记忆写工具。"""
        r = self._rows("""SELECT risk_event_id, rule_id, shipment_id FROM risk_events
                          WHERE risk_event_id=?""", risk_event_id)
        if not r:
            return {"error": f"风险事件 {risk_event_id} 不存在"}
        lane = lane_for_shipment(self.con, r[0]["shipment_id"])
        similar = find_similar(self.con, r[0]["rule_id"], lane, exclude_risk_id=risk_event_id)
        return {**similar, "risk_event_id": risk_event_id,
                "precedent_block": render_precedent_block(similar),
                "note": "只读检索；数字运行时从 resolution_memory 现算，可回查核对（C1）"}

    def list_admission_cases(self, status=None):
        sql = """SELECT admission_case_id, case_title, customer_id, sku_id, incoterm_candidate,
                        risk_level, status, decision FROM admission_cases"""
        # 对象 scoping：focus 到某案时检索默认聚焦该案（不是全库），与 focus_risk 同规
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if self.focus_admission_case_id:
            clauses.append("admission_case_id=?")
            params.append(self.focus_admission_case_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._rows(sql, *params)
        out = {"count": len(rows), "cases": rows}
        if self.focus_admission_case_id:
            out["focus_scope"] = {"admission_case_id": self.focus_admission_case_id}
        return out

    def focus_admission_bundle(self):
        """对象 scoping 汇总：把检索聚焦到本会话 focus 的准入案及其邻居（Customer / Sku /
        ComplianceFinding / LogisticsPlan / CostScenario），而非全库。成本字段随 role 脱敏
        （复用 get_admission_context）。无 focus 返回 error。"""
        aid = self.focus_admission_case_id
        if not aid:
            return {"error": "本会话未 focus 到任何 admission case"}
        ctx = self.get_admission_context(aid)
        if "error" in ctx:
            return ctx
        neighbors = {
            "customer_id": ctx["customer"]["customer_id"],
            "sku_id": ctx["case"]["sku_id"],
            "compliance_finding_ids": [f["compliance_finding_id"] for f in ctx["findings"]],
            "logistics_plan_ids": [p["logistics_plan_id"] for p in ctx["plans"]],
            "cost_scenario_ids": [s.get("cost_scenario_id") for s in ctx["cost_scenarios"]],
        }
        return {"focus_admission_case_id": aid, "case": ctx["case"], "customer": ctx["customer"],
                "findings": ctx["findings"], "plans": ctx["plans"],
                "cost_scenarios": ctx["cost_scenarios"], "neighbors": neighbors,
                "note": f"object-scoped to {aid} and neighbors; role={self.role}"}

    def get_admission_context(self, admission_case_id):
        case = self._rows("SELECT * FROM admission_cases WHERE admission_case_id=?",
                          admission_case_id)
        if not case:
            return {"error": f"准入案件 {admission_case_id} 不存在"}
        case = case[0]
        # 字段脱敏随 role：credit_terms/risk_tier 仅 tier 可见角色（cs/manager）返回
        tier_visible = _can_see_tier(self.role)
        cust_cols = "customer_id, customer_name, business_model, ior_capability, broker_status"
        if tier_visible:
            cust_cols += ", credit_terms, risk_tier"
        cust = self._rows(f"SELECT {cust_cols} FROM customers WHERE customer_id=?",
                          case["customer_id"])[0]
        finds = self._rows("SELECT * FROM compliance_findings WHERE admission_case_id=?",
                           admission_case_id)
        plans = self._rows("SELECT * FROM logistics_plans WHERE admission_case_id=?",
                           admission_case_id)
        cost_visible = _can_see_cost(self.role)
        scens = []
        for p in plans:
            for s in self._rows("SELECT * FROM cost_scenarios WHERE logistics_plan_id=?",
                                p["logistics_plan_id"]):
                # 成本字段按角色脱敏（与 UI mask_cost 同规，AD4）：finance/manager 可见，其余脱敏
                scens.append(dict(s) if cost_visible else
                             {k: (MASK if k in COST_FIELDS else v) for k, v in s.items()})
        return {"case": case, "customer": cust, "findings": finds, "plans": plans,
                "cost_scenarios": scens,
                "note": f"cost fields {'visible' if cost_visible else 'masked'} for role={self.role}; "
                        f"credit_terms/risk_tier {'returned' if tier_visible else 'not returned'}"}

    def list_invoices(self, status=None):
        sql = """SELECT invoice_id, vendor_type, vendor_name, shipment_id, total_usd,
                        status, issue_date FROM invoices"""
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        # 对象 scoping：focus 到某发票时检索默认聚焦本票所属 shipment 的发票（同货运邻居，
        # 不是全库），与 focus_risk 的 shipment 邻居锚点同规。
        focus_ship = self._focus_shipment_id() if self.focus_invoice_id else None
        if focus_ship:
            clauses.append("shipment_id=?")
            params.append(focus_ship)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._rows(sql + " ORDER BY invoice_id", *params)
        out = {"count": len(rows), "invoices": rows}
        if focus_ship:
            out["focus_scope"] = {"invoice_id": self.focus_invoice_id, "shipment_id": focus_ship}
        return out

    def get_invoice_context(self, invoice_id):
        inv = self._rows("SELECT * FROM invoices WHERE invoice_id=?", invoice_id)
        if not inv:
            return {"error": f"发票 {invoice_id} 不存在"}
        inv = dict(inv[0])
        # 行明细 join expected_costs（基准与差异列）——与费用工作台 UI 同一 join 规则
        lines = self._rows(
            """SELECT il.invoice_line_id, il.charge_code, il.container_no, il.qty,
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
        # 行级异常：来自本票所属 shipment 的风险 affected_invoice_line_ids 并集
        anom = set()
        for rr in self._rows("SELECT affected_invoice_line_ids FROM risk_events WHERE shipment_id=?",
                             inv["shipment_id"]):
            if rr["affected_invoice_line_ids"]:
                anom |= set(json.loads(rr["affected_invoice_line_ids"]))
        for ln in lines:
            ln["is_anomaly"] = ln["invoice_line_id"] in anom
        sp = self._rows("""SELECT shipment_id, incoterm, delay_days, status
                           FROM shipments WHERE shipment_id=?""", inv["shipment_id"])
        # 成本字段脱敏随 role（收敛口径：与 UI build_invoice_workbench mask_cost 逐字一致）：
        # finance/manager 见金额，其余掩码 total_usd + 每行 amount/unit_price/baseline/diff。
        cost_visible = _can_see_cost(self.role)
        if not cost_visible:
            if inv.get("total_usd") is not None:
                inv["total_usd"] = MASK
            for ln in lines:
                for f in INVOICE_COST_FIELDS:
                    if ln.get(f) is not None:
                        ln[f] = MASK
        return {"invoice": inv, "lines": lines,
                "shipment": sp[0] if sp else None,
                "note": f"invoice cost fields {'visible' if cost_visible else 'masked'} "
                        f"for role={self.role}（与 UI 费用工作台 mask_cost 同规，收敛口径）"}

    def focus_task_bundle(self):
        """对象 scoping 汇总：把检索聚焦到本会话 focus 的 task 及其邻居（父 RiskEvent、
        受影响 SO 行、当前提案），而非全库。无 focus 返回 error。审批/关闭不在此（原则2）。"""
        tid = self.focus_task_id
        if not tid:
            return {"error": "本会话未 focus 到任何 task"}
        task = self._rows("SELECT * FROM tasks WHERE task_id=?", tid)
        if not task:
            return {"error": f"任务 {tid} 不存在"}
        task = task[0]
        rid = task["risk_event_id"]
        risk = self.get_risk(rid)
        impact = self.get_impact_chain(rid) if "error" not in risk else {"affected": []}
        neighbors = {
            "risk_event_id": rid,
            "shipment_id": risk.get("shipment_id") if "error" not in risk else None,
            "affected_so_line_ids": [a["so_line_id"] for a in impact.get("affected", [])],
            "assignee_user_id": task.get("assignee_user_id"),
        }
        return {"focus_task_id": tid, "task": task,
                "risk": risk if "error" not in risk else None, "impact": impact,
                "neighbors": neighbors,
                "note": f"object-scoped to {tid} and parent risk {rid}; role={self.role}"}

    def focus_invoice_bundle(self):
        """对象 scoping 汇总：把检索聚焦到本会话 focus 的 invoice 及其邻居（invoice_lines、
        所属 shipment、flag 本票账单行的费用类风险 R4/R5/R6），而非全库。成本金额随 role 脱敏
        （与 get_invoice_context / UI mask_cost 同口径）；审批不在此（原则2）。无 focus 返回 error。"""
        iid = self.focus_invoice_id
        if not iid:
            return {"error": "本会话未 focus 到任何 invoice"}
        ctx = self.get_invoice_context(iid)
        if "error" in ctx:
            return ctx
        inv, lines = ctx["invoice"], ctx["lines"]
        line_ids = {ln["invoice_line_id"] for ln in lines}
        related_risks = []
        for rr in self._rows("""SELECT risk_event_id, rule_id, type, severity, status,
                                affected_invoice_line_ids FROM risk_events WHERE shipment_id=?
                                ORDER BY risk_event_id""", inv["shipment_id"]):
            ils = json.loads(rr["affected_invoice_line_ids"]) if rr["affected_invoice_line_ids"] else []
            if line_ids & set(ils):
                related_risks.append(rr)
        neighbors = {
            "shipment_id": inv["shipment_id"],
            "invoice_line_ids": sorted(line_ids),
            "risk_event_ids": [r["risk_event_id"] for r in related_risks],
        }
        return {"focus_invoice_id": iid, "invoice": inv, "lines": lines,
                "shipment": ctx.get("shipment"), "related_risks": related_risks,
                "neighbors": neighbors,
                "note": f"object-scoped to {iid} and related risks; role={self.role}"}

    def focus_po_bundle(self):
        """对象 scoping 汇总（采购切片，Build 3）：把检索聚焦到本会话 focus 的 PurchaseOrder 及其邻居
        （PoLine、GoodsReceipt 行、SupplierInvoice 行、三方对账逐行、锚定本 PO 的采购 RiskEvent R7-R10），
        而非全库。金额字段随 role 脱敏（unit_price/amount/total，与 UI PO 工作台 mask_cost 同规：
        finance/manager 可见，其余掩码）；审批/关闭不在此（原则2）。无 focus 返回 error。"""
        pid = self.focus_po_id
        if not pid:
            return {"error": "本会话未 focus 到任何 purchase order"}
        po = self._rows("SELECT * FROM purchase_orders WHERE po_id=?", pid)
        if not po:
            return {"error": f"采购单 {pid} 不存在"}
        po = po[0]
        cost_visible = _can_see_cost(self.role)

        def money(v):
            return MASK if (v is not None and not cost_visible) else v

        po_lines = self._rows("SELECT * FROM po_lines WHERE po_id=? ORDER BY po_line_id", pid)
        # 收货聚合（每 po_line：累计收货/验收/拒收 + 最早到货日 + QC）
        recv = {}
        for r in self._rows("""SELECT gl.po_line_id, gl.received_qty, gl.accepted_qty,
                               gl.rejected_qty, gl.qc_status, gl.defect_ppm, gl.received_date
                               FROM goods_receipt_lines gl JOIN goods_receipts g ON g.grn_id=gl.grn_id
                               WHERE g.po_id=? ORDER BY gl.po_line_id, gl.grn_line_id""", pid):
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
        inv = {}
        for r in self._rows("""SELECT sil.po_line_id, sil.qty, sil.unit_price_usd, sil.amount_usd
                               FROM supplier_invoice_lines sil
                               JOIN supplier_invoices si ON si.supplier_invoice_id=sil.supplier_invoice_id
                               WHERE si.po_id=? ORDER BY sil.po_line_id""", pid):
            a = inv.setdefault(r["po_line_id"], {"qty": 0, "amount": 0.0, "unit_price": None})
            a["qty"] += r["qty"]
            a["amount"] = round(a["amount"] + r["amount_usd"], 2)
            if a["unit_price"] is None:
                a["unit_price"] = r["unit_price_usd"]
        # 三方对账逐行（订购 × 收货 × 开票）
        reconciliation = []
        for pl in po_lines:
            rc = recv.get(pl["po_line_id"], {})
            iv = inv.get(pl["po_line_id"], {})
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
        anchored_risks = self._rows(
            """SELECT risk_event_id, rule_id, type, severity, status, affected_value_usd,
                      root_cause, affected_po_line_ids FROM risk_events WHERE po_id=?
               ORDER BY risk_event_id""", pid)
        return {"focus_po_id": pid, "purchase_order": po, "po_lines": po_lines,
                "reconciliation": reconciliation, "anchored_risks": anchored_risks,
                "neighbors": {"po_id": pid, "supplier_id": po["supplier_id"],
                              "po_line_ids": [pl["po_line_id"] for pl in po_lines],
                              "risk_event_ids": [r["risk_event_id"] for r in anchored_risks]},
                "note": f"object-scoped to {pid} (三方对账); cost fields "
                        f"{'visible' if cost_visible else 'masked'} for role={self.role}"}

    def focus_warehouse_bundle(self):
        """对象 scoping 汇总（仓储切片）：把检索聚焦到本会话 focus 的 Warehouse 及其邻居
        （该仓 InventoryPosition、经头寸挂靠的 InventoryReservation、CycleCount、锚定本仓的
        R16-R18 RiskEvent），而非全库。库存桶字段（available/reserved/in_transit/safety）为运营
        数据、不脱敏（ops 需据此处置断货/履约）；本域无 _usd 字段，故无成本脱敏。审批/关闭不在此
        （原则2）。无 focus 返回 error。"""
        wid = self.focus_warehouse_id
        if not wid:
            return {"error": "本会话未 focus 到任何 warehouse"}
        wh = self._rows("SELECT * FROM warehouses WHERE warehouse_id=?", wid)
        if not wh:
            return {"error": f"仓库 {wid} 不存在"}
        wh = wh[0]
        positions = self._rows(
            """SELECT inventory_position_id, sku_id, available_qty, reserved_qty, in_transit_qty,
                      safety_stock FROM inventory_positions WHERE warehouse_id=?
               ORDER BY inventory_position_id""", wid)
        for p in positions:
            p["atp"] = p["available_qty"] + p["in_transit_qty"] - p["reserved_qty"]
            p["below_safety"] = p["available_qty"] <= p["safety_stock"]
        reservations = self._rows(
            """SELECT res.reservation_id, res.so_line_id, res.inventory_position_id, res.qty,
                      res.status FROM inventory_reservations res
               JOIN inventory_positions p ON p.inventory_position_id=res.inventory_position_id
               WHERE p.warehouse_id=? ORDER BY res.reservation_id""", wid)
        cycle_counts = self._rows(
            """SELECT cycle_count_id, inventory_position_id, system_qty, counted_qty, variance,
                      status FROM cycle_counts WHERE warehouse_id=? ORDER BY cycle_count_id""", wid)
        anchored_risks = self._rows(
            """SELECT risk_event_id, rule_id, type, severity, status, affected_value_usd,
                      root_cause, affected_so_line_ids FROM risk_events WHERE warehouse_id=?
               ORDER BY risk_event_id""", wid)
        return {"focus_warehouse_id": wid, "warehouse": wh, "positions": positions,
                "reservations": reservations, "cycle_counts": cycle_counts,
                "anchored_risks": anchored_risks,
                "neighbors": {"warehouse_id": wid,
                              "inventory_position_ids": [p["inventory_position_id"]
                                                         for p in positions],
                              "risk_event_ids": [r["risk_event_id"] for r in anchored_risks]},
                "note": f"object-scoped to {wid} (库存/预留/盘点/风险); role={self.role}"}

    def explain_relationship_path(self, source_type, source_id, target_type, target_id, max_depth):
        try:
            depth = int(max_depth)
        except (TypeError, ValueError):
            return {"error": "max_depth must be an integer between 1 and 6"}
        if depth < 1 or depth > 6:
            return {"error": "max_depth must be between 1 and 6"}
        path = explain_path(self.con, source_type, source_id, target_type, target_id, depth)
        return {
            "source": {"type": source_type, "id": source_id},
            "target": {"type": target_type, "id": target_id},
            "max_depth": depth,
            "edges": [edge.__dict__ for edge in path],
        }

    # ---------- 写动作（仅 proposal-only 白名单；role 随会话注入，动作层再校验一次） ----------
    def _assign_task(self, risk_event_id, assignee_role, priority, due_at):
        return assign_task(self.con, risk_event_id, assignee_role, priority, due_at,
                           actor=AI_ACTOR, role=self.role, as_of=self.as_of)

    def _propose_mitigation(self, task_id, proposed_action, proposal_params):
        return propose_mitigation(self.con, task_id, proposed_action, proposal_params,
                                  actor=AI_ACTOR, role=self.role, as_of=self.as_of)

    # 准入准备动作 B1-B4：role 随会话注入，动作层 ADM_PERMS + 门禁再校验一次（双闸）。
    def _create_admission_case(self, customer_id, sku_id, request_type, incoterm_candidate,
                               target_launch_date, monthly_order_estimate):
        return create_admission_case(self.con, customer_id, sku_id, request_type, incoterm_candidate,
                                     target_launch_date, monthly_order_estimate,
                                     actor=AI_ACTOR, role=self.role, as_of=self.as_of)

    def _run_compliance_precheck(self, admission_case_id, findings):
        return run_compliance_precheck(self.con, admission_case_id, findings,
                                       actor=AI_ACTOR, role=self.role, as_of=self.as_of)

    def _build_logistics_plan(self, admission_case_id, plan):
        return build_logistics_plan(self.con, admission_case_id, plan,
                                    actor=AI_ACTOR, role=self.role, as_of=self.as_of)

    def _calculate_cost_scenario(self, logistics_plan_id, scenario):
        return calculate_cost_scenario(self.con, logistics_plan_id, scenario,
                                       actor=AI_ACTOR, role=self.role, as_of=self.as_of)

    def _audit_denied(self, tool_name, args, result):
        """把一次被拒的调用写入 action_log（越权/越域一律留痕，AI 没有静默后门）。"""
        cur = self.con.cursor()
        target = str(args.get("task_id") or args.get("risk_event_id") or
                     args.get("admission_case_id") or args.get("logistics_plan_id") or
                     args.get("invoice_id") or args.get("sku_id") or "?")
        _log(cur, AI_ACTOR, self.role, tool_name, target, args, self.as_of, result)
        self.con.commit()

    # ---------- 统一调度 ----------
    def dispatch(self, tool_name, args):
        # 原则2：审批/关闭类对任何 role 永不开放——最先拦截并审计（诱导越权 → 拒绝 + 留痕）
        if tool_name in FORBIDDEN_TOOLS:
            self._audit_denied(tool_name, args,
                               "denied: tool not exposed to AI (proposal-only guardrail)")
            return {"refused": True,
                    "reason": "该动作未向 AI 开放：审批与关闭必须由人执行（proposal-only 护栏），"
                              "本次尝试已记录审计"}
        handlers = {"list_open_risks": self.list_open_risks, "get_risk": self.get_risk,
                    "get_shipment_context": self.get_shipment_context,
                    "get_impact_chain": self.get_impact_chain,
                    "get_audit_trail": self.get_audit_trail,
                    "list_admission_cases": self.list_admission_cases,
                    "get_admission_context": self.get_admission_context,
                    "list_invoices": self.list_invoices,
                    "get_invoice_context": self.get_invoice_context,
                    "get_similar_resolutions": self.get_similar_resolutions,
                    "explain_relationship_path": self.explain_relationship_path,
                    "assign_task": self._assign_task,
                    "propose_mitigation": self._propose_mitigation,
                    "create_admission_case": self._create_admission_case,
                    "run_compliance_precheck": self._run_compliance_precheck,
                    "build_logistics_plan": self._build_logistics_plan,
                    "calculate_cost_scenario": self._calculate_cost_scenario}
        if tool_name not in handlers:
            return {"refused": True, "reason": f"未注册的工具 {tool_name}"}
        # 按 role scoping：已知工具但不在本会话角色工具集 → 拒绝并审计（越权写 / 越域读）
        if tool_name not in self.allowed_tools:
            is_write = tool_name in ALL_WRITE_PERM
            self._audit_denied(tool_name, args,
                               f"denied: tool not available to role={self.role}"
                               + (" (over-role write)" if is_write else " (out-of-domain read)"))
            return {"refused": True,
                    "reason": f"工具 {tool_name} 未向角色 {self.role} 开放"
                              + ("（越权写：该动作权限不含此角色，须换有权角色，动作层同步拦截）"
                                 if is_write else "（越域读：不在本角色数据域）")
                              + "，本次尝试已记录审计"}
        try:
            return handlers[tool_name](**args)
        except TypeError as e:
            return {"error": f"参数错误: {e}"}
