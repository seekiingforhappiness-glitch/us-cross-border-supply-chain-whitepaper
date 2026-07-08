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
from engine.graph import explain_path

AI_ACTOR = "ai-agent"
AI_ROLE = "ops"  # 默认角色（不传 role 时的向后兼容值）
# 审批/关闭/拒接类动作永不向任何 role 的 AI 会话开放（v0.2 E5 + v0.3 AD2 红线，原则2）
FORBIDDEN_TOOLS = {"approve_mitigation", "close_risk_event",
                   "approve_quote_decision", "reject_or_request_more_info"}
COST_FIELDS = {"quote_price_usd", "product_cost_usd", "first_mile_cost_usd",
               "international_freight_usd", "duty_tax_usd", "customs_brokerage_usd",
               "warehouse_cost_usd", "last_mile_cost_usd", "returns_allowance_usd",
               "risk_buffer_usd", "gross_margin_usd", "gross_margin_rate"}
MASK = "🔒无权查看"

# --- 角色 → 工具集 scoping（对象工作台切片：给同一框架注入 role，不复制平行 agent）---
# 读工具按域分组：风险/物流域对所有 role 开放（RiskEvent 对象工作台核心）；成本、准入域按
# role 相关性 scoping。ops 保留全域（历史基线，不回归 agent.evaluate）；cs 无成本域（"无 cost 相关"）；
# finance 可成本域；manager 全域只读。写工具白名单完全由 app.actions.ROLE_PERMS 决定（不复制权限）。
RISK_READ_TOOLS = {"list_open_risks", "get_risk", "get_shipment_context",
                   "get_impact_chain", "get_audit_trail", "explain_relationship_path"}
COST_READ_TOOLS = {"list_invoices", "get_invoice_context"}
ADMISSION_READ_TOOLS = {"list_admission_cases", "get_admission_context"}
COST_READ_ROLES = {"ops", "finance", "manager"}
ADMISSION_READ_ROLES = {"ops", "finance", "manager", "sales", "compliance"}
WRITE_TOOL_PERM = {"assign_task": "AssignTask", "propose_mitigation": "ProposeMitigation"}


def allowed_tools_for_role(role):
    """该 role 会话可用的工具集：读工具按域 scoping + 写工具经 ROLE_PERMS gate。
    approve/close 对任何 role 都不在此集合（FORBIDDEN_TOOLS，原则2：agent 只提案不审批）。"""
    tools = set(RISK_READ_TOOLS)
    if role in COST_READ_ROLES:
        tools |= COST_READ_TOOLS
    if role in ADMISSION_READ_ROLES:
        tools |= ADMISSION_READ_TOOLS
    for tool, perm in WRITE_TOOL_PERM.items():
        if role in ROLE_PERMS[perm]:
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
                    "+ 所属 shipment 摘要（incoterm/delay_days/status）。发票金额对 AI 可见"
                    "（对账数据非敏感，区别于 CostScenario 脱敏规则）",
     "input_schema": {"type": "object", "properties": {
         "invoice_id": {"type": "string"}}, "required": ["invoice_id"]}},
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
]


class AgentSession:
    """一次 AI 会话的工具执行环境。所有调用统一经 dispatch，越权/未知工具拒绝并审计。"""

    def __init__(self, db_path="data/ontology.sqlite", config_path="config/datagen.yaml",
                 role=AI_ROLE, focus_risk_event_id=None):
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
        self.as_of = cfg["window"]["as_of"]
        self.buf = cfg["buffers"]["customs_days"] + cfg["buffers"]["lastmile_days"]
        # permission-aware 注入：当前 role 决定工具集 + 脱敏；focus 决定对象 scoping
        self.role = role
        self.focus_risk_event_id = focus_risk_event_id
        self.allowed_tools = allowed_tools_for_role(role)

    def _rows(self, sql, *a):
        return [dict(r) for r in self.con.execute(sql, a)]

    def _focus_shipment_id(self):
        """focus 风险所属 shipment（对象 scoping 的邻居锚点）；无 focus/不存在返回 None。"""
        if not self.focus_risk_event_id:
            return None
        r = self._rows("SELECT shipment_id FROM risk_events WHERE risk_event_id=?",
                       self.focus_risk_event_id)
        return r[0]["shipment_id"] if r else None

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
        # 对象 scoping：focus 时检索默认聚焦该 risk 及其邻居（同一 shipment 上的风险），不是全库
        focus_ship = self._focus_shipment_id()
        if focus_ship:
            sql += " AND shipment_id=?"
            params.append(focus_ship)
        rows = self._rows(sql, *params)
        out = {"count": len(rows), "risks": rows}
        if focus_ship:
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

    def list_admission_cases(self, status=None):
        sql = """SELECT admission_case_id, case_title, customer_id, sku_id, incoterm_candidate,
                        risk_level, status, decision FROM admission_cases"""
        rows = self._rows(sql + " WHERE status=?" if status else sql,
                          *([status] if status else []))
        return {"count": len(rows), "cases": rows}

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
        rows = self._rows(sql + " WHERE status=? ORDER BY invoice_id"
                          if status else sql + " ORDER BY invoice_id",
                          *([status] if status else []))
        return {"count": len(rows), "invoices": rows}

    def get_invoice_context(self, invoice_id):
        inv = self._rows("SELECT * FROM invoices WHERE invoice_id=?", invoice_id)
        if not inv:
            return {"error": f"发票 {invoice_id} 不存在"}
        inv = inv[0]
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
        return {"invoice": inv, "lines": lines,
                "shipment": sp[0] if sp else None,
                "note": "invoice amounts visible to AI (对账数据非敏感)"}

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

    def _audit_denied(self, tool_name, args, result):
        """把一次被拒的调用写入 action_log（越权/越域一律留痕，AI 没有静默后门）。"""
        cur = self.con.cursor()
        target = str(args.get("task_id") or args.get("risk_event_id") or
                     args.get("admission_case_id") or args.get("invoice_id") or "?")
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
                    "explain_relationship_path": self.explain_relationship_path,
                    "assign_task": self._assign_task,
                    "propose_mitigation": self._propose_mitigation}
        if tool_name not in handlers:
            return {"refused": True, "reason": f"未注册的工具 {tool_name}"}
        # 按 role scoping：已知工具但不在本会话角色工具集 → 拒绝并审计（越权写 / 越域读）
        if tool_name not in self.allowed_tools:
            is_write = tool_name in WRITE_TOOL_PERM
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
