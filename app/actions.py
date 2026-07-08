"""W5 动作层：A3-A6（manual §5 五要素）。

- 纯函数写 sqlite，不依赖 Streamlit——W6 直接注册为 AI tools（D7）
- 统一返回 {ok, object_id, side_effects, error}，从不抛异常给调用方
- 一切调用（成功/失败/越权）都写 action_log（断言 B4/C4）
- 审计时间戳基于 as_of，不用系统时钟（D8）
"""
import json
import sqlite3

try:
    from .action_context import Actor, ApprovalPolicy, next_stable_id, transaction
    from .work_queue import Owner, assign_owner, sla_state
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from action_context import Actor, ApprovalPolicy, next_stable_id, transaction
    from work_queue import Owner, assign_owner, sla_state

from pipeline.outbox import enqueue_writeback

ROLE_PERMS = {  # manual §6 权限矩阵（cost-manual §5：ProposeMitigation +finance，P3）
    "AssignTask": {"ops", "system"},
    "ProposeMitigation": {"ops", "cs", "finance"},
    "ApproveMitigation": {"manager"},
    "CloseRiskEvent": {"ops"},
}
PARAM_SCHEMAS = {
    "expedite": {"new_mode", "est_cost_usd", "expected_new_eta"},
    "reschedule": {"new_promise_date", "notify_customer"},
    "accept_delay": {"reason"},
    # cost-manual §4 A4 提案类型扩展（费用异常处置）
    "dispute": {"reason", "disputed_amount_usd"},
    "accept_charge": {"reason"},
    "rebill_customer": {"rebill_amount_usd", "incoterm_basis"},
    # P1 采购 A4 提案类型扩展（采购 RiskEvent R7-R10 处置；决策日志 P1，Build 3）：
    # 走既有 assign→propose→approve 闭环，权限沿用 ProposeMitigation:{ops,cs,finance}、审批仍仅 manager。
    "expedite_po": {"reason"},                       # 催单：标记受影响 PoLine 加急
    "raise_supplier_claim": {"claim_amount_usd", "reason"},   # 供应商索赔：标记 PoLine 已发起索赔
    "dispute_supplier_invoice": {"reason", "disputed_amount_usd"},  # 争议供应商发票（复用 dispute 语义）
    "accept_receipt_variance": {"reason"},           # 接受短装/差异：标记 PoLine 差异已接受
    # P2 采购富化 A4 提案类型扩展（R12 预付款敞口 / R13 供应商资质；决策日志 P2，Build B）：
    # 同走既有 assign→propose→approve 闭环，权限沿用 ProposeMitigation:{ops,cs,finance}、审批仍仅 manager。
    "escalate_prepayment": {"reason"},               # R12 升级预付款敞口：标记 deposit 付款 at_risk
    "hold_balance_payment": {"reason"},              # R12 暂缓尾款：标记 balance 付款 at_risk
    "request_supplier_docs": {"reason"},             # R13 要求补交资质：标记过期资质 evidence_status=provided（cert_type 可选，缺省全过期证）
    "suspend_supplier": {"reason"},                  # R13 冻结供应商：标记过期资质 status=revoked（Supplier 无 status 字段，回写资质对象）
    # W1 仓储处置提案类型扩展（R16-R18 + 延误连接；决策日志 W1，Build 2/3）：
    # 同走既有 assign→propose→approve 闭环，权限沿用 ProposeMitigation:{ops,cs,finance}、审批仍仅 manager。
    "suggest_substitution": {"reason"},              # 延误(R1-R3)/R17：目的仓现货拆单先发 + 余量 backorder（杀手锏）
    "adjust_inventory": {"reason"},                  # R18：按 CycleCount.counted 调整 InventoryPosition + reconciled
    "escalate_replenishment": {"reason"},            # R16：升级补货（在途补足安全库存缺口）
    # P3 采购富化2 处置提案类型扩展（R14 单一来源 / R15 maverick；决策日志 P3，Build B）：
    # 同走既有 assign→propose→approve 闭环，权限沿用 ProposeMitigation:{ops,cs,finance}、审批仍仅 manager。
    "initiate_second_source": {"reason"},            # R14：对断供 SKU 建/标一条 RFQ(status=sent) 启动第二来源
    "block_non_po_payment": {"reason"},              # R15：把 maverick supplier_invoice 标 on_hold 拦截付款
    "backfill_po": {"reason"},                       # R15：为 maverick 发票补一张追溯 PurchaseOrder 并关联（合规补单）
}
# 采购处置动作 → PoLine.line_status 目标态（审批通过后回写受影响 PoLine 的可见状态）。
# dispute_supplier_invoice 不改 PoLine（改 supplier_invoices.status=disputed），故不在此表。
PO_LINE_STATUS_ON_APPROVE = {
    "expedite_po": "expedited",
    "raise_supplier_claim": "claim_raised",
    "accept_receipt_variance": "variance_accepted",
}
# P2 采购富化处置（决策日志 P2，Build B）——审批后回写受影响对象「可见状态」，非判风险
# （R12/R13 检测由 engine 从事实重算，绝不信 exposure_status/status 字段，同"不信状态字段"铁律）：
#   R12 预付款敞口（po_id 锚）→ PurchasePayment.exposure_status='at_risk'；
#   R13 供应商资质（supplier_id 锚）→ SupplierQualification.evidence_status/status（Supplier 无 status 字段）。
PREPAYMENT_ACTIONS = {"escalate_prepayment", "hold_balance_payment"}   # R12
QUALIFICATION_ACTIONS = {"request_supplier_docs", "suspend_supplier"}  # R13
PROCUREMENT_ACTIONS = (set(PO_LINE_STATUS_ON_APPROVE) | {"dispute_supplier_invoice"}
                       | PREPAYMENT_ACTIONS | QUALIFICATION_ACTIONS)
# W1 仓储处置动作（决策日志 W1，Build 2/3）——审批后回写受影响仓储对象「可见状态」，非判风险
# （R16-R18 检测由 engine 从事实重算，绝不信状态字段，同「不信状态字段」铁律）。回写逻辑在
# app.warehouse_actions.apply_warehouse_disposition（approve_mitigation 事务内调用），锚点：
#   suggest_substitution 延误(R1-R3, shipment 目的仓)/R17(warehouse_id) → Reservation + SalesOrderLine；
#   adjust_inventory R18(cycle_count 锚) → InventoryPosition + CycleCount.status=reconciled；
#   escalate_replenishment R16(inventory_position 锚) → InventoryPosition.in_transit_qty。
WAREHOUSE_ACTIONS = {"suggest_substitution", "adjust_inventory", "escalate_replenishment"}
# P3 采购富化2 处置动作（决策日志 P3，Build B）——审批后回写受影响采购/询价对象「可见状态」，非判风险
# （R14/R15 检测由 engine.detect_sourcing_risks 从事实重算，绝不信 rfq/invoice/po 状态字段，同「不信状态字段」铁律）。
# 回写逻辑在 app.sourcing_actions.apply_sourcing_disposition（approve_mitigation 事务内调用），锚点：
#   initiate_second_source R14(supplier_id 锚, sku 载体在 affected_po_line_ids[0]) → RFQ.status=sent（建/标询价）；
#   block_non_po_payment  R15(po_id 锚, maverick 行在 affected_invoice_line_ids) → supplier_invoices.status=on_hold；
#   backfill_po           R15 → 新建追溯 PurchaseOrder(supplier=biller) + supplier_invoices 重指向关联（合规补单）。
SOURCING_ACTIONS = {"initiate_second_source", "block_non_po_payment", "backfill_po"}
# cost-manual §4 A5 G4 incoterm 责任门禁矩阵（与 ontology JSON incotermRebillMatrix 同步）：
# rebill_customer 仅当受影响行费种 ⊆ 该票 incoterm 的可转嫁集合。
REBILL_MATRIX = {
    "DDP": set(),  # 门到门全我方，rebill 一律拒绝
    "CIF": {"DTY", "CUS", "WHS", "STO", "LMD", "DET", "DEM", "CHS", "ACC"},
    "FOB": {"OFT", "FSC", "THC", "DOC", "DTY", "CUS", "WHS", "STO", "LMD",
            "DET", "DEM", "CHS", "ACC"},
}
RISK_TERMINAL = ("resolved", "escalated")
TASK_TERMINAL = ("done", "cancelled")
TASK_GOVERNANCE_COLUMNS = {
    "assigned_by_actor_id": "TEXT",
    "proposal_actor_id": "TEXT",
    "proposal_actor_role": "TEXT",
}
TASK_WORK_QUEUE_COLUMNS = {
    "assignee_user_id": "TEXT",
    "assignee_team_id": "TEXT",
    "sla_state": "TEXT",
    "escalation_level": "INTEGER DEFAULT 0",
    "policy_version": "TEXT",
}
WORK_QUEUE_POLICY_VERSION = "M2-demo-work-queue-v1"
DEFAULT_DEMO_REGION = "US"
DEMO_ROSTER = [
    Owner(actor_id="u-ops-us", role="ops", region="US", active=True),
    Owner(actor_id="u-cs-us", role="cs", region="US", active=True),
    Owner(actor_id="u-manager-us", role="manager", region="US", active=True),
    Owner(actor_id="u-fin-us", role="finance", region="US", active=True),
    Owner(actor_id="u-ops-cn", role="ops", region="CN", active=True),
    # 实名 owner（enrich demo）：仅 append，第一个 ops/US 仍是 u-ops-us，
    # 故 resolve_actor / assign_owner 既有行为不变，只为运营快照提供更多分派对象。
    Owner(actor_id="u-ops-us-amelia", role="ops", region="US", active=True),
    Owner(actor_id="u-ops-us-diego", role="ops", region="US", active=True),
    Owner(actor_id="u-cs-us-priya", role="cs", region="US", active=True),
    Owner(actor_id="u-fin-us-marcus", role="finance", region="US", active=True),
    Owner(actor_id="u-ops-cn-lin", role="ops", region="CN", active=True),
]


def _log(cur, actor, role, action, target, params, as_of, result):
    cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id, params_json,
                   as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                (actor, role, action, target, json.dumps(params, ensure_ascii=False),
                 as_of, f"{as_of}T00:00:00Z", result))


def _res(ok, object_id=None, side_effects=None, error=None):
    return {"ok": ok, "object_id": object_id, "side_effects": side_effects or [], "error": error}


def _table_columns(cur, table):
    return {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_task_governance_columns(con, cur):
    cols = _table_columns(cur, "tasks")
    changed = False
    for name, ddl in {**TASK_GOVERNANCE_COLUMNS, **TASK_WORK_QUEUE_COLUMNS}.items():
        if name not in cols:
            cur.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")
            changed = True
    if changed:
        con.commit()


def _demo_region_for_risk(cur, risk):
    try:
        row = cur.execute("""SELECT destination_port_locode FROM shipments
                             WHERE shipment_id=?""", (risk["shipment_id"],)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table: shipments" not in str(exc):
            raise
        return DEFAULT_DEMO_REGION
    if row and row["destination_port_locode"].startswith("US"):
        return "US"
    return DEFAULT_DEMO_REGION


def _team_id_for(owner):
    return f"team-{owner.role}-{owner.region.lower()}"


def _escalation_level(due_at, as_of):
    return 1 if sla_state(due_at, as_of) == "overdue" else 0


def _enqueue_reschedule_writeback(con, task_id, risk, affected, params, actor, as_of):
    payload = {
        "task_id": task_id,
        "risk_event_id": risk["risk_event_id"],
        "shipment_id": risk["shipment_id"],
        "so_line_ids": sorted(affected),
        "new_promise_date": params["new_promise_date"],
        "notify_customer": bool(params.get("notify_customer")),
        "approved_by_actor_id": actor,
        "as_of_date": as_of,
    }
    return enqueue_writeback(
        con,
        target_system="oms_simulated",
        action_name="update_promise_date",
        target_object=f"Task:{task_id}",
        payload=payload,
        as_of=as_of,
    )


def _work_queue_assignment(cur, risk, assignee_role, due_at, as_of):
    region = _demo_region_for_risk(cur, risk)
    owner = assign_owner(assignee_role, region, DEMO_ROSTER)
    state = sla_state(due_at, as_of)
    return {
        "assignee_user_id": owner.actor_id,
        "assignee_team_id": _team_id_for(owner),
        "sla_state": state,
        "escalation_level": _escalation_level(due_at, as_of),
        "policy_version": WORK_QUEUE_POLICY_VERSION,
    }


def ensure_task_work_queue_columns(con, as_of_date):
    """M2 runtime compatibility for old ontology.sqlite copies; uses explicit as_of_date."""
    cur = con.cursor()
    _ensure_task_governance_columns(con, cur)
    rows = cur.execute("""SELECT * FROM tasks
                          WHERE assignee_user_id IS NULL OR assignee_team_id IS NULL
                             OR sla_state IS NULL OR policy_version IS NULL""").fetchall()
    for task in rows:
        risk = cur.execute("SELECT * FROM risk_events WHERE risk_event_id=?",
                           (task["risk_event_id"],)).fetchone()
        if not risk:
            continue
        assignment = _work_queue_assignment(cur, risk, task["assignee_role"],
                                            task["due_at"], as_of_date)
        cur.execute("""UPDATE tasks SET assignee_user_id=?, assignee_team_id=?,
                       sla_state=?, escalation_level=?, policy_version=?
                       WHERE task_id=?""",
                    (assignment["assignee_user_id"], assignment["assignee_team_id"],
                     assignment["sla_state"], assignment["escalation_level"],
                     assignment["policy_version"], task["task_id"]))
    if rows:
        con.commit()


def _next_task_id(cur, risk_event_id, as_of):
    base = next_stable_id("TSK", f"{risk_event_id}|assign|{as_of}")
    candidate = base
    suffix = 2
    while cur.execute("SELECT 1 FROM tasks WHERE task_id=?", (candidate,)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _proposal_actor_from_log(cur, task_id):
    row = cur.execute("""SELECT actor, role FROM action_log
                         WHERE action='ProposeMitigation' AND target_object_id=?
                         AND result='ok'
                         ORDER BY log_id DESC LIMIT 1""", (task_id,)).fetchone()
    if row:
        return row["actor"], row["role"]
    return None, None


def can_approve_actor(proposer_actor_id, approver_actor_id, approver_role):
    """M1 maker-checker helper; legacy unknown proposer keeps role-only behavior."""
    if approver_role not in ROLE_PERMS["ApproveMitigation"]:
        return False, "manager_role_required"
    if not proposer_actor_id:
        return True, "legacy_role_only"
    return ApprovalPolicy(policy_version="M1").can_approve(
        proposer=Actor(actor_id=proposer_actor_id, role="proposer"),
        approver=Actor(actor_id=approver_actor_id, role=approver_role),
    )


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


def assign_task(con, risk_event_id, assignee_role, priority, due_at, actor, role, as_of):
    """A3：派单。前置：风险 open 且无非终态任务。成功：Task=assigned，Risk→acknowledged。"""
    if role not in ROLE_PERMS["AssignTask"]:
        return _denied(con, "AssignTask", risk_event_id, actor, role, as_of)
    cur = con.cursor()
    risk = cur.execute("SELECT * FROM risk_events WHERE risk_event_id=?", (risk_event_id,)).fetchone()
    if not risk:
        return _fail(con, "AssignTask", risk_event_id, actor, role, as_of, "风险事件不存在")
    # 先查既有任务（manual A3：已有非终态 Task → 拒绝并返回其 id），再查状态
    ex = cur.execute("""SELECT task_id FROM tasks WHERE risk_event_id=? AND status NOT IN (?,?)""",
                     (risk_event_id, *TASK_TERMINAL)).fetchone()
    if ex:
        return _fail(con, "AssignTask", risk_event_id, actor, role, as_of,
                     f"已存在非终态任务 {ex['task_id']}（单风险单任务，D9/C4）")
    if risk["status"] != "open":
        return _fail(con, "AssignTask", risk_event_id, actor, role, as_of,
                     f"风险状态为 {risk['status']}，仅 open 可派单")
    _ensure_task_governance_columns(con, cur)
    try:
        assignment = _work_queue_assignment(cur, risk, assignee_role, due_at, as_of)
    except ValueError as exc:
        return _fail(con, "AssignTask", risk_event_id, actor, role, as_of, str(exc))
    tid = _next_task_id(cur, risk_event_id, as_of)
    with transaction(con):
        cur.execute("""INSERT INTO tasks
                       (task_id, risk_event_id, title, assignee_role, priority, due_at,
                        proposed_action, proposal_params, approval_status, approved_by_role,
                        action_taken, status, assigned_by_actor_id, assignee_user_id,
                        assignee_team_id, sla_state, escalation_level, policy_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tid, risk_event_id, f"处置 {risk['type']} @ {risk['shipment_id']}",
                     assignee_role, priority, due_at, None, None, None, None, None, "assigned",
                     actor, assignment["assignee_user_id"], assignment["assignee_team_id"],
                     assignment["sla_state"], assignment["escalation_level"],
                     assignment["policy_version"]))
        cur.execute("UPDATE risk_events SET status='acknowledged' WHERE risk_event_id=?",
                    (risk_event_id,))
        _log(cur, actor, role, "AssignTask", tid,
             {"risk_event_id": risk_event_id, "assignee_role": assignee_role,
              "priority": priority, "assignee_user_id": assignment["assignee_user_id"],
              "assignee_team_id": assignment["assignee_team_id"],
              "sla_state": assignment["sla_state"],
              "escalation_level": assignment["escalation_level"],
              "policy_version": assignment["policy_version"]},
             as_of, "ok")
    return _res(True, tid, [f"RiskEvent {risk_event_id}: open→acknowledged",
                            f"Task {tid} assigned to {assignment['assignee_user_id']}"])


def propose_mitigation(con, task_id, proposed_action, proposal_params, actor, role, as_of):
    """A4：提交处置方案。前置：Task=assigned、参数过 schema、改期日晚于受影响行当前承诺。"""
    if role not in ROLE_PERMS["ProposeMitigation"]:
        return _denied(con, "ProposeMitigation", task_id, actor, role, as_of)
    cur = con.cursor()
    task = cur.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not task:
        return _fail(con, "ProposeMitigation", task_id, actor, role, as_of, "任务不存在")
    if task["status"] != "assigned":
        return _fail(con, "ProposeMitigation", task_id, actor, role, as_of,
                     f"任务状态为 {task['status']}，仅 assigned 可提案")
    schema = PARAM_SCHEMAS.get(proposed_action)
    if schema is None or not schema <= set(proposal_params):
        return _fail(con, "ProposeMitigation", task_id, actor, role, as_of,
                     f"参数不符合 {proposed_action} schema（需 {sorted(schema or [])}）",
                     proposal_params)
    risk = cur.execute("SELECT * FROM risk_events WHERE risk_event_id=?",
                       (task["risk_event_id"],)).fetchone()
    if proposed_action == "reschedule":
        rows = cur.execute(
            f"""SELECT max(promised_delivery_date) m FROM sales_order_lines WHERE so_line_id IN
            ({','.join('?' * len(json.loads(risk['affected_so_line_ids'])))})""",
            json.loads(risk["affected_so_line_ids"])).fetchone()
        if rows["m"] and proposal_params["new_promise_date"] <= rows["m"]:
            return _fail(con, "ProposeMitigation", task_id, actor, role, as_of,
                         f"新承诺日必须晚于受影响行当前承诺日（最晚 {rows['m']}）", proposal_params)
    _ensure_task_governance_columns(con, cur)
    with transaction(con):
        cur.execute("""UPDATE tasks SET status='in_progress', proposed_action=?, proposal_params=?,
                       approval_status='pending', proposal_actor_id=?, proposal_actor_role=?
                       WHERE task_id=?""",
                    (proposed_action, json.dumps(proposal_params, ensure_ascii=False),
                     actor, role, task_id))
        cur.execute("UPDATE risk_events SET status='mitigating' WHERE risk_event_id=?",
                    (task["risk_event_id"],))
        _log(cur, actor, role, "ProposeMitigation", task_id,
             {"proposed_action": proposed_action, **proposal_params}, as_of, "ok")
    return _res(True, task_id, [f"Task {task_id}: assigned→in_progress (pending approval)",
                                f"RiskEvent {task['risk_event_id']}: →mitigating"])


def approve_mitigation(con, task_id, decision, comment, actor, role, as_of):
    """A5：审批（仅经理）。approved 按方案回写；rejected 退回 assigned，提案留痕于 action_log。"""
    if role not in ROLE_PERMS["ApproveMitigation"]:
        return _denied(con, "ApproveMitigation", task_id, actor, role, as_of)
    cur = con.cursor()
    task = cur.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not task or task["approval_status"] != "pending":
        return _fail(con, "ApproveMitigation", task_id, actor, role, as_of,
                     "任务不存在或无待审批提案")
    _ensure_task_governance_columns(con, cur)
    task = cur.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    proposer_actor_id = task["proposal_actor_id"]
    if not proposer_actor_id:
        proposer_actor_id, _ = _proposal_actor_from_log(cur, task_id)
    allowed, reason = can_approve_actor(proposer_actor_id, actor, role)
    if not allowed:
        return _fail(con, "ApproveMitigation", task_id, actor, role, as_of,
                     f"M1 审批边界拒绝：{reason}",
                     {"proposer_actor_id": proposer_actor_id, "approver_actor_id": actor})
    risk = cur.execute("SELECT * FROM risk_events WHERE risk_event_id=?",
                       (task["risk_event_id"],)).fetchone()
    affected = json.loads(risk["affected_so_line_ids"])
    params = json.loads(task["proposal_params"] or "{}")
    act = task["proposed_action"]
    # G4 incoterm 责任门禁（仅 rebill_customer）：审批前置校验，越界即拒绝并审计（A5/XC4）。
    if decision == "approved" and act == "rebill_customer":
        inv_lids = json.loads(risk["affected_invoice_line_ids"] or "[]")
        codes = set()
        if inv_lids:
            ph = ",".join("?" * len(inv_lids))
            codes = {r["charge_code"] for r in cur.execute(
                f"SELECT DISTINCT charge_code FROM invoice_lines WHERE invoice_line_id IN ({ph})",
                inv_lids)}
        ship = cur.execute("SELECT incoterm FROM shipments WHERE shipment_id=?",
                           (risk["shipment_id"],)).fetchone()
        incoterm = ship["incoterm"] if ship else ""
        allowed = REBILL_MATRIX.get(incoterm, set())
        overflow = codes - allowed
        if overflow:
            return _fail(con, "ApproveMitigation", task_id, actor, role, as_of,
                         f"G4 门禁未过：incoterm={incoterm} 下费种 {sorted(overflow)} 不可转嫁客户",
                         {"decision": decision, "proposal": params})
    effects = []
    if decision not in ("approved", "rejected"):
        return _fail(con, "ApproveMitigation", task_id, actor, role, as_of,
                     "decision 必须是 approved/rejected")
    try:
        with transaction(con):
            if decision == "rejected":
                cur.execute("""UPDATE tasks SET status='assigned', approval_status='rejected',
                               proposed_action=NULL, proposal_params=NULL WHERE task_id=?""", (task_id,))
                effects.append(f"Task {task_id}: →assigned（提案已驳回，参数留痕于审计）")
            elif decision == "approved":
                if act == "reschedule":
                    for lid in affected:
                        cur.execute("""UPDATE sales_order_lines SET promised_delivery_date=?,
                                       reschedule_count=reschedule_count+1, line_status='allocated'
                                       WHERE so_line_id=? AND line_status='at_risk'""",
                                    (params["new_promise_date"], lid))
                    outbox_key = _enqueue_reschedule_writeback(con, task_id, risk, affected,
                                                               params, actor, as_of)
                    effects.append(f"受影响行承诺日→{params['new_promise_date']}，at_risk→allocated")
                    effects.append(f"模拟写回 outbox 已入队：{outbox_key}")
                elif act == "expedite":
                    cur.execute("UPDATE shipments SET expedite_flag=1 WHERE shipment_id=?",
                                (risk["shipment_id"],))
                    for lid in affected:
                        cur.execute("""UPDATE sales_order_lines SET line_status='allocated'
                                       WHERE so_line_id=? AND line_status='at_risk'""", (lid,))
                    effects.append(f"Shipment {risk['shipment_id']} expedite_flag=1，行风险解除（D9/C2 简化）")
                elif act == "accept_delay":
                    effects.append("接受延误：行保持 at_risk 至交付")
                elif act in ("dispute", "accept_charge", "rebill_customer"):
                    # 费用提案：无行级副作用（affected_so_line_ids 空）；按 §2 状态机回写发票。
                    # dispute → 受影响行所属发票 disputed；accept_charge / rebill_customer → approved。
                    inv_lids = json.loads(risk["affected_invoice_line_ids"] or "[]")
                    new_inv_status = "disputed" if act == "dispute" else "approved"
                    inv_ids = []
                    if inv_lids:
                        ph = ",".join("?" * len(inv_lids))
                        inv_ids = [r["invoice_id"] for r in cur.execute(
                            f"SELECT DISTINCT invoice_id FROM invoice_lines WHERE invoice_line_id IN ({ph})",
                            inv_lids)]
                        for iid in inv_ids:
                            cur.execute("UPDATE invoices SET status=? WHERE invoice_id=?",
                                        (new_inv_status, iid))
                    effects.append(f"{act} 批准：受影响发票 {sorted(inv_ids)} → {new_inv_status}")
                elif act in PREPAYMENT_ACTIONS:
                    # R12 预付款敞口处置（po_id 锚）：审批后把相关付款 exposure_status 回写 at_risk
                    # （可见状态回写，非判风险——R12 由收货存在性+付款账龄重算，不信该字段）。
                    # escalate_prepayment 锚 deposit（预付敞口本体）；hold_balance_payment 锚 balance（暂缓尾款）。
                    pay_type = "deposit" if act == "escalate_prepayment" else "balance"
                    pay_ids = [r["payment_id"] for r in cur.execute(
                        "SELECT payment_id FROM purchase_payments WHERE po_id=? AND payment_type=?",
                        (risk["po_id"], pay_type))]
                    for pid in pay_ids:
                        cur.execute("UPDATE purchase_payments SET exposure_status='at_risk' "
                                    "WHERE payment_id=?", (pid,))
                    effects.append(f"{act} 批准：{pay_type} 付款 {sorted(pay_ids)} → exposure_status=at_risk")
                elif act in QUALIFICATION_ACTIONS:
                    # R13 供应商资质处置（supplier_id 锚）：审批后回写该供应商「过期证」的可见状态
                    # （无 Supplier.status 字段，回写资质对象；R13 由 valid_to vs as_of 重算，不信 status/evidence_status）。
                    # 过期证域 = valid_to < as_of（R13 检测口径）；request 可选 cert_type 缩到某类证。
                    cert_filter = params.get("cert_type")
                    quals = cur.execute(
                        """SELECT qualification_id, cert_type, valid_to FROM supplier_qualifications
                           WHERE supplier_id=?""", (risk["supplier_id"],)).fetchall()
                    qids = sorted(r["qualification_id"] for r in quals
                                  if r["valid_to"] < as_of
                                  and (cert_filter is None or r["cert_type"] == cert_filter))
                    if act == "request_supplier_docs":
                        for qid in qids:
                            cur.execute("UPDATE supplier_qualifications SET evidence_status='provided' "
                                        "WHERE qualification_id=?", (qid,))
                        effects.append(f"request_supplier_docs 批准：过期资质 {qids} "
                                       f"→ evidence_status=provided（补证请求已发）")
                    else:  # suspend_supplier
                        for qid in qids:
                            cur.execute("UPDATE supplier_qualifications SET status='revoked' "
                                        "WHERE qualification_id=?", (qid,))
                        effects.append(f"suspend_supplier 批准：供应商 {risk['supplier_id']} 过期资质 "
                                       f"{qids} → status=revoked（供应商已冻结）")
                elif act in PROCUREMENT_ACTIONS:
                    # P1 采购处置（Build 3）：采购 RiskEvent 用 po_id/affected_po_line_ids 锚点，
                    # 无 SO 行副作用；批准后回写受影响 PoLine 状态或供票状态（事实回写，非判风险）。
                    po_line_ids = json.loads(risk["affected_po_line_ids"] or "[]")
                    if act == "dispute_supplier_invoice":
                        sinv_ids = []
                        if po_line_ids:
                            ph = ",".join("?" * len(po_line_ids))
                            sinv_ids = [r["supplier_invoice_id"] for r in cur.execute(
                                f"""SELECT DISTINCT si.supplier_invoice_id FROM supplier_invoices si
                                    JOIN supplier_invoice_lines sil
                                      ON sil.supplier_invoice_id=si.supplier_invoice_id
                                    WHERE sil.po_line_id IN ({ph})""", po_line_ids)]
                        if not sinv_ids and risk["po_id"]:  # 回退：按 PO 取供票
                            sinv_ids = [r["supplier_invoice_id"] for r in cur.execute(
                                "SELECT supplier_invoice_id FROM supplier_invoices WHERE po_id=?",
                                (risk["po_id"],))]
                        for sid in sinv_ids:
                            cur.execute("UPDATE supplier_invoices SET status='disputed' "
                                        "WHERE supplier_invoice_id=?", (sid,))
                        effects.append(f"dispute_supplier_invoice 批准：供票 {sorted(sinv_ids)} → disputed")
                    else:
                        new_pol_status = PO_LINE_STATUS_ON_APPROVE[act]
                        for lid in po_line_ids:
                            cur.execute("UPDATE po_lines SET line_status=? WHERE po_line_id=?",
                                        (new_pol_status, lid))
                        effects.append(f"{act} 批准：受影响采购行 {sorted(po_line_ids)} "
                                       f"→ line_status={new_pol_status}")
                elif act in WAREHOUSE_ACTIONS:
                    # W1 仓储处置（Build 2/3）：仓储风险(R16-R18)锚 warehouse_id+affected_so_line_ids
                    # （承载受影响仓储对象 id）；延误连接(R1-R3)锚 shipment_id。审批后按已批准方案回写受影响
                    # 仓储对象「可见状态」（事实回写，非判风险——检测仍由 engine 从事实重算）。lazy import
                    # 避免 actions↔warehouse_actions 循环 import；helper 在本事务内只用 cur。
                    try:
                        from .warehouse_actions import apply_warehouse_disposition
                    except ImportError:
                        from warehouse_actions import apply_warehouse_disposition
                    effects.extend(apply_warehouse_disposition(cur, act, risk, affected, params, as_of))
                elif act in SOURCING_ACTIONS:
                    # P3 采购富化2 处置（Build B）：R14 锚 supplier_id + sku 载体(affected_po_line_ids[0])，
                    # R15 锚 po_id + maverick 行(affected_invoice_line_ids)；无 SO 行副作用。审批后按已批准方案
                    # 回写受影响 RFQ/supplier_invoice/purchase_order 的「可见状态」（事实回写，非判风险——检测仍由
                    # engine 从事实重算）。lazy import 避免 actions↔sourcing_actions 循环 import；helper 只用 cur。
                    try:
                        from .sourcing_actions import apply_sourcing_disposition
                    except ImportError:
                        from sourcing_actions import apply_sourcing_disposition
                    effects.extend(apply_sourcing_disposition(cur, act, risk, params, as_of))
                cur.execute("""UPDATE tasks SET status='done', approval_status='approved',
                               approved_by_role=?, action_taken=? WHERE task_id=?""",
                            (role, f"{act} approved: {json.dumps(params, ensure_ascii=False)}", task_id))
                effects.append(f"Task {task_id}: →done")
            _log(cur, actor, role, "ApproveMitigation", task_id,
                 {"decision": decision, "comment": comment, "proposal": params}, as_of, "ok")
    except (ValueError, sqlite3.Error) as exc:
        # M7 加固：outbox 入队等异常先经 transaction 回滚（业务状态与写回一起撤销），
        # 再转结构化失败返回，守住 actions.py 顶部“从不抛异常给调用方”契约。
        return _fail(con, "ApproveMitigation", task_id, actor, role, as_of,
                     f"审批执行失败（已回滚）：{exc}", {"decision": decision})
    return _res(True, task_id, effects)


def close_risk_event(con, risk_event_id, outcome, resolution_summary, actor, role, as_of):
    """A6：关闭。前置：任务全终态（false_alarm 例外：联动取消）；mitigated 须有已批准提案。"""
    if role not in ROLE_PERMS["CloseRiskEvent"]:
        return _denied(con, "CloseRiskEvent", risk_event_id, actor, role, as_of)
    cur = con.cursor()
    risk = cur.execute("SELECT * FROM risk_events WHERE risk_event_id=?", (risk_event_id,)).fetchone()
    if not risk or risk["status"] in RISK_TERMINAL:
        return _fail(con, "CloseRiskEvent", risk_event_id, actor, role, as_of, "风险不存在或已终态")
    if not resolution_summary:
        return _fail(con, "CloseRiskEvent", risk_event_id, actor, role, as_of, "关闭必须填写处理小结")
    open_tasks = cur.execute("""SELECT task_id FROM tasks WHERE risk_event_id=?
                                AND status NOT IN (?,?)""",
                             (risk_event_id, *TASK_TERMINAL)).fetchall()
    effects = []
    if outcome == "false_alarm":
        for tr in open_tasks:
            cur.execute("UPDATE tasks SET status='cancelled' WHERE task_id=?", (tr["task_id"],))
            effects.append(f"Task {tr['task_id']} cancelled（误报联动）")
        for lid in json.loads(risk["affected_so_line_ids"]):  # 误报回滚行状态
            cur.execute("""UPDATE sales_order_lines SET line_status='allocated'
                           WHERE so_line_id=? AND line_status='at_risk'""", (lid,))
    elif open_tasks:
        return _fail(con, "CloseRiskEvent", risk_event_id, actor, role, as_of,
                     f"存在非终态任务 {[t['task_id'] for t in open_tasks]}，仅 false_alarm 可强制关闭")
    if outcome == "mitigated":
        ok = cur.execute("""SELECT 1 FROM tasks WHERE risk_event_id=? AND approval_status='approved'
                            AND status='done'""", (risk_event_id,)).fetchone()
        if not ok:
            return _fail(con, "CloseRiskEvent", risk_event_id, actor, role, as_of,
                         "outcome=mitigated 需要存在已批准并执行的提案")
    status = "escalated" if outcome == "escalated" else "resolved"
    cur.execute("""UPDATE risk_events SET status=?, outcome=?, resolution_summary=?, resolved_at=?
                   WHERE risk_event_id=?""",
                (status, outcome, resolution_summary, as_of, risk_event_id))
    _log(cur, actor, role, "CloseRiskEvent", risk_event_id,
         {"outcome": outcome, "resolution_summary": resolution_summary}, as_of, "ok")
    con.commit()
    effects.append(f"RiskEvent {risk_event_id}: →{status} ({outcome})")
    return _res(True, risk_event_id, effects)


def connect(db_path="data/ontology.sqlite"):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con
