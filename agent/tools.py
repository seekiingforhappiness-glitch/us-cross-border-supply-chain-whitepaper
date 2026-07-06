"""W6 AI 工具层（D7 兑现）：AI 只能通过本文件注册的工具接触 ontology。

护栏（plan §11-W6 / v0.1 手册 AI 原则）：
1. proposal-only：写动作只暴露 assign_task 与 propose_mitigation；
   approve_mitigation / close_risk_event **永不注册**——诱导越权时 dispatcher 拒绝并写审计
2. AI 以 ops 角色读数据（Customer.tier 对其同样脱敏——权限对人对 AI 一视同仁）
3. 一切工具调用走 app.actions 的同一套校验与审计，AI 没有后门
"""
import json
import sqlite3

import yaml

from app.actions import assign_task, propose_mitigation, _log

AI_ACTOR = "ai-agent"
AI_ROLE = "ops"
FORBIDDEN_TOOLS = {"approve_mitigation", "close_risk_event"}

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

    def __init__(self, db_path="data/ontology.sqlite", config_path="config/datagen.yaml"):
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
        self.as_of = cfg["window"]["as_of"]
        self.buf = cfg["buffers"]["customs_days"] + cfg["buffers"]["lastmile_days"]

    def _rows(self, sql, *a):
        return [dict(r) for r in self.con.execute(sql, a)]

    # ---------- 查询工具（只读） ----------
    def list_open_risks(self, severity=None):
        sql = """SELECT risk_event_id, type, rule_id, severity, shipment_id,
                        affected_value_usd, status FROM risk_events
                 WHERE status NOT IN ('resolved','escalated')"""
        rows = self._rows(sql + " AND severity=?" if severity else sql,
                          *( [severity] if severity else [] ))
        return {"count": len(rows), "risks": rows}

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
                              l.line_status, so.so_id, c.customer_id
                              FROM shipment_allocations a
                              JOIN sales_order_lines l ON l.so_line_id=a.so_line_id
                              JOIN sales_orders so ON so.so_id=l.so_id
                              JOIN customers c ON c.customer_id=so.customer_id
                              WHERE a.shipment_id=?""", shipment_id)
        # 权限：AI 以 ops 角色工作，tier 不可见（与 UI 同一规则）
        return {"shipment": sp[0], "milestones": ms, "onboard_lines": chain,
                "note": "customer tier masked for role=ops"}

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
                              so.so_id, c.customer_id, c.customer_name
                              FROM sales_order_lines l
                              JOIN sales_orders so ON so.so_id=l.so_id
                              JOIN customers c ON c.customer_id=so.customer_id
                              WHERE l.so_line_id IN ({ph})""", *lids)
        return {"risk_event_id": risk_event_id, "shipment_id": r["shipment_id"],
                "affected_value_usd": r["affected_value_usd"], "affected": rows}

    def get_audit_trail(self, object_id):
        rows = self._rows("""SELECT actor, role, action, params_json, as_of_date, result
                             FROM action_log WHERE target_object_id=? ORDER BY log_id""", object_id)
        return {"object_id": object_id, "entries": rows} if rows else \
            {"error": f"未找到 {object_id} 的审计记录"}

    # ---------- 写动作（仅 proposal-only 白名单） ----------
    def _assign_task(self, risk_event_id, assignee_role, priority, due_at):
        return assign_task(self.con, risk_event_id, assignee_role, priority, due_at,
                           actor=AI_ACTOR, role=AI_ROLE, as_of=self.as_of)

    def _propose_mitigation(self, task_id, proposed_action, proposal_params):
        return propose_mitigation(self.con, task_id, proposed_action, proposal_params,
                                  actor=AI_ACTOR, role=AI_ROLE, as_of=self.as_of)

    # ---------- 统一调度 ----------
    def dispatch(self, tool_name, args):
        if tool_name in FORBIDDEN_TOOLS:
            cur = self.con.cursor()
            _log(cur, AI_ACTOR, AI_ROLE, tool_name, str(args.get("task_id") or
                 args.get("risk_event_id") or "?"), args, self.as_of,
                 "denied: tool not exposed to AI (proposal-only guardrail)")
            self.con.commit()
            return {"refused": True,
                    "reason": "该动作未向 AI 开放：审批与关闭必须由人执行（proposal-only 护栏），"
                              "本次尝试已记录审计"}
        handlers = {"list_open_risks": self.list_open_risks, "get_risk": self.get_risk,
                    "get_shipment_context": self.get_shipment_context,
                    "get_impact_chain": self.get_impact_chain,
                    "get_audit_trail": self.get_audit_trail,
                    "assign_task": self._assign_task,
                    "propose_mitigation": self._propose_mitigation}
        if tool_name not in handlers:
            return {"refused": True, "reason": f"未注册的工具 {tool_name}"}
        try:
            return handlers[tool_name](**args)
        except TypeError as e:
            return {"error": f"参数错误: {e}"}
