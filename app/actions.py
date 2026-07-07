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
}
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
                effects.append(f"受影响行承诺日→{params['new_promise_date']}，at_risk→allocated")
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
            cur.execute("""UPDATE tasks SET status='done', approval_status='approved',
                           approved_by_role=?, action_taken=? WHERE task_id=?""",
                        (role, f"{act} approved: {json.dumps(params, ensure_ascii=False)}", task_id))
            effects.append(f"Task {task_id}: →done")
        _log(cur, actor, role, "ApproveMitigation", task_id,
             {"decision": decision, "comment": comment, "proposal": params}, as_of, "ok")
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
