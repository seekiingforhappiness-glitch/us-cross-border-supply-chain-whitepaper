"""CL1 协调回路动作层（Coordination Loop，决策日志 CL1）。

与 app/actions.py / app/admission_actions.py 同一套模式：权限→前置→状态机→成功/失败→审计，
统一返回 {ok, coordination_id, side_effects, error}，从不抛异常给调用方；审计时间戳基于 as_of（D8）。

为什么这样建（≤5 行）：
- 延误 Task 说「expedite」，但真正的活是对外协调——追工厂改期后交期、追客户接受拆单先发。
  这段 ask→跟进→回复→升级 的往返是控制塔必须持有的运营状态，不是 fire-and-forget。
- CoordinationThread 锚在触发它的 Task（task_id）+ 冗余锚 RiskEvent（risk_event_id），
  overdue 为派生（state∈{awaiting,escalated} 且 next_action_due < as_of），绝不落字段。
- 权限走**独立新权限组** COORD_PERMS.ManageCoordination——不碰 maker-checker ROLE_PERMS 四键
  （ApproveMitigation 仍仅 manager、CloseRiskEvent 仍仅 ops）。协调写动作**不注册为 agent 工具**（agent 只读+提案）。
"""
import json

try:
    from .actions import _log, _res
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from actions import _log, _res

# 独立新权限组（不碰既有 ROLE_PERMS 四键）：六个协调动作同一 gate。
COORD_PERMS = {"ManageCoordination": {"ops", "cs", "procurement", "finance"}}
COUNTERPARTY_TYPES = {"supplier", "forwarder", "customs_broker", "bank", "customer"}
COORD_ACTIVE = ("awaiting", "responded", "escalated")     # 活跃（非终态）
COORD_TERMINAL = ("resolved", "dead_ended")               # 终态
OVERDUE_STATES = {"awaiting", "escalated"}                # overdue 派生仅这两态
# 运行期（动作层/UI）创建的线程 policy_version 标记；与 seed 的标记不同，故 seed 幂等清理不误伤。
RUNTIME_POLICY_VERSION = "CL1-coordination-v1"

# 状态机转移表：action → (合法起始态集合, 目标态)。open 单独处理（创建为 awaiting）。
COORD_TRANSITIONS = {
    "record_outreach": ({"awaiting"}, "awaiting"),
    "record_response": ({"awaiting", "escalated"}, "responded"),
    "escalate_coordination": ({"awaiting", "responded"}, "escalated"),
    "resolve_coordination": ({"awaiting", "responded", "escalated"}, "resolved"),
    "mark_dead_ended": ({"awaiting", "responded", "escalated"}, "dead_ended"),
}


def is_overdue(state, next_action_due, as_of):
    """派生 overdue（不落字段）：state∈{awaiting,escalated} 且 next_action_due < as_of。
    日期以 ISO 字符串按字典序比较（YYYY-MM-DD 可比）。终态/responded 一律非 overdue。"""
    if state not in OVERDUE_STATES:
        return False
    return bool(next_action_due) and next_action_due < as_of


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


def _thread(cur, coordination_id):
    return cur.execute("SELECT * FROM coordination_threads WHERE coordination_id=?",
                       (coordination_id,)).fetchone()


def _next_coordination_id(cur):
    """确定性 count-based id COORD-000N；带碰撞回避（种子用 COORD-DEMO- 前缀，不撞）。"""
    base_n = cur.execute("SELECT count(*) FROM coordination_threads").fetchone()[0] + 1
    n = base_n
    while cur.execute("SELECT 1 FROM coordination_threads WHERE coordination_id=?",
                      (f"COORD-{n:04d}",)).fetchone():
        n += 1
    return f"COORD-{n:04d}"


def _check_perm(con, action, target, actor, role, as_of):
    """ManageCoordination gate（独立权限组）；越权 → 记 denied 审计并返回拒绝结果，否则 None。"""
    if role not in COORD_PERMS["ManageCoordination"]:
        return _denied(con, action, target, actor, role, as_of)
    return None


def open_coordination(con, task_id, counterparty_type, counterparty_ref, ask, owner,
                      next_action_due, actor, role, as_of):
    """A20：开线程（协调角色）。前置：Task 存在（锚点完整）+ counterparty_type 合法。
    创建为 awaiting、followup_count=1、escalation_level=0；risk_event_id 从 Task 派生（冗余锚）。"""
    denied = _check_perm(con, "OpenCoordination", task_id, actor, role, as_of)
    if denied:
        return denied
    cur = con.cursor()
    if counterparty_type not in COUNTERPARTY_TYPES:
        return _fail(con, "OpenCoordination", task_id, actor, role, as_of,
                     f"counterparty_type 非法：{counterparty_type}（需 {sorted(COUNTERPARTY_TYPES)}）")
    task = cur.execute("SELECT task_id, risk_event_id FROM tasks WHERE task_id=?",
                       (task_id,)).fetchone()
    if not task:
        return _fail(con, "OpenCoordination", task_id, actor, role, as_of,
                     f"锚点 Task {task_id} 不存在（协调线程必须锚到真实 Task）")
    if not ask or not counterparty_ref or not owner or not next_action_due:
        return _fail(con, "OpenCoordination", task_id, actor, role, as_of,
                     "ask/counterparty_ref/owner/next_action_due 均不可为空")
    risk_event_id = task["risk_event_id"]   # 冗余锚：从 Task 派生，保证与风险图一致
    cid = _next_coordination_id(cur)
    cur.execute("""INSERT INTO coordination_threads
                   (coordination_id, task_id, risk_event_id, counterparty_type, counterparty_ref,
                    ask, state, followup_count, escalation_level, owner, next_action_due,
                    last_response, outcome, opened_at, last_update, policy_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, task_id, risk_event_id, counterparty_type, counterparty_ref, ask,
                 "awaiting", 1, 0, owner, next_action_due, None, None, as_of, as_of,
                 RUNTIME_POLICY_VERSION))
    _log(cur, actor, role, "OpenCoordination", cid,
         {"task_id": task_id, "risk_event_id": risk_event_id,
          "counterparty_type": counterparty_type, "counterparty_ref": counterparty_ref,
          "ask": ask, "owner": owner, "next_action_due": next_action_due}, as_of, "ok")
    con.commit()
    return _res(True, cid, [f"CoordinationThread {cid} opened (awaiting) → {counterparty_type}",
                            f"anchored on Task {task_id} / RiskEvent {risk_event_id}"])


def _transition(con, action, coordination_id, actor, role, as_of, apply_fn, effect):
    """六动作共用骨架：权限→线程存在→状态机合法起始态→apply_fn 回写→审计→返回。
    apply_fn(cur, thread) 执行 UPDATE 并返回写入 action_log 的 params。非法转移/终态一律 ok=False。"""
    denied = _check_perm(con, _CAMEL[action], coordination_id, actor, role, as_of)
    if denied:
        return denied
    cur = con.cursor()
    thread = _thread(cur, coordination_id)
    if not thread:
        return _fail(con, _CAMEL[action], coordination_id, actor, role, as_of,
                     f"协调线程 {coordination_id} 不存在")
    valid_from, _target = COORD_TRANSITIONS[action]
    if thread["state"] not in valid_from:
        return _fail(con, _CAMEL[action], coordination_id, actor, role, as_of,
                     f"非法转移：{action} 需状态 ∈ {sorted(valid_from)}，当前 {thread['state']}"
                     + ("（已终态）" if thread["state"] in COORD_TERMINAL else ""))
    params = apply_fn(cur, thread)
    _log(cur, actor, role, _CAMEL[action], coordination_id, params, as_of, "ok")
    con.commit()
    return _res(True, coordination_id, [effect(thread)])


_CAMEL = {  # 动作名 → 审计 action_type（CamelCase，对齐 ontology action 名）
    "record_outreach": "RecordOutreach",
    "record_response": "RecordResponse",
    "escalate_coordination": "EscalateCoordination",
    "resolve_coordination": "ResolveCoordination",
    "mark_dead_ended": "MarkDeadEnded",
}


def record_outreach(con, coordination_id, next_action_due, note, actor, role, as_of):
    """A21：再发一次外联（awaiting→awaiting）。followup_count++、重设 next_action_due。"""
    if not next_action_due:
        # 与其它前置一致：缺 due 直接失败（但仍要先过权限/存在校验，故在 apply 前用轻量校验）
        denied = _check_perm(con, "RecordOutreach", coordination_id, actor, role, as_of)
        if denied:
            return denied
        return _fail(con, "RecordOutreach", coordination_id, actor, role, as_of,
                     "record_outreach 必须提供新的 next_action_due")

    def apply_fn(cur, thread):
        new_count = thread["followup_count"] + 1
        cur.execute("""UPDATE coordination_threads SET followup_count=?, next_action_due=?,
                       last_update=? WHERE coordination_id=?""",
                    (new_count, next_action_due, as_of, coordination_id))
        return {"followup_count": new_count, "next_action_due": next_action_due,
                "note": note or ""}

    return _transition(con, "record_outreach", coordination_id, actor, role, as_of,
                       apply_fn, lambda t: f"CoordinationThread {coordination_id}: 第 "
                       f"{t['followup_count'] + 1} 次外联，next_action_due→{next_action_due}")


def record_response(con, coordination_id, last_response, actor, role, as_of):
    """A22：记录对方回复（awaiting/escalated→responded）。存 last_response。"""
    if not last_response:
        denied = _check_perm(con, "RecordResponse", coordination_id, actor, role, as_of)
        if denied:
            return denied
        return _fail(con, "RecordResponse", coordination_id, actor, role, as_of,
                     "record_response 必须提供 last_response 内容")

    def apply_fn(cur, thread):
        cur.execute("""UPDATE coordination_threads SET state='responded', last_response=?,
                       last_update=? WHERE coordination_id=?""",
                    (last_response, as_of, coordination_id))
        return {"last_response": last_response}

    return _transition(con, "record_response", coordination_id, actor, role, as_of,
                       apply_fn, lambda t: f"CoordinationThread {coordination_id}: "
                       f"{t['state']}→responded（已记录回复）")


def escalate_coordination(con, coordination_id, actor, role, as_of, next_action_due=None):
    """A23：升级（awaiting/responded→escalated）。escalation_level++；可选重设 next_action_due。"""
    def apply_fn(cur, thread):
        new_level = thread["escalation_level"] + 1
        due = next_action_due or thread["next_action_due"]
        cur.execute("""UPDATE coordination_threads SET state='escalated', escalation_level=?,
                       next_action_due=?, last_update=? WHERE coordination_id=?""",
                    (new_level, due, as_of, coordination_id))
        return {"escalation_level": new_level, "next_action_due": due}

    return _transition(con, "escalate_coordination", coordination_id, actor, role, as_of,
                       apply_fn, lambda t: f"CoordinationThread {coordination_id}: "
                       f"{t['state']}→escalated（escalation_level={t['escalation_level'] + 1}）")


def resolve_coordination(con, coordination_id, outcome, actor, role, as_of):
    """A24：解决（活跃→resolved 终态）。存 outcome。"""
    if not outcome:
        denied = _check_perm(con, "ResolveCoordination", coordination_id, actor, role, as_of)
        if denied:
            return denied
        return _fail(con, "ResolveCoordination", coordination_id, actor, role, as_of,
                     "resolve_coordination 必须填写 outcome")

    def apply_fn(cur, thread):
        cur.execute("""UPDATE coordination_threads SET state='resolved', outcome=?,
                       last_update=? WHERE coordination_id=?""",
                    (outcome, as_of, coordination_id))
        return {"outcome": outcome}

    return _transition(con, "resolve_coordination", coordination_id, actor, role, as_of,
                       apply_fn, lambda t: f"CoordinationThread {coordination_id}: "
                       f"{t['state']}→resolved（{outcome}）")


def mark_dead_ended(con, coordination_id, outcome, actor, role, as_of):
    """A25：判为无解/放弃（活跃→dead_ended 终态）。存 outcome（原因）。"""
    if not outcome:
        denied = _check_perm(con, "MarkDeadEnded", coordination_id, actor, role, as_of)
        if denied:
            return denied
        return _fail(con, "MarkDeadEnded", coordination_id, actor, role, as_of,
                     "mark_dead_ended 必须填写 outcome（放弃/无解原因）")

    def apply_fn(cur, thread):
        cur.execute("""UPDATE coordination_threads SET state='dead_ended', outcome=?,
                       last_update=? WHERE coordination_id=?""",
                    (outcome, as_of, coordination_id))
        return {"outcome": outcome}

    return _transition(con, "mark_dead_ended", coordination_id, actor, role, as_of,
                       apply_fn, lambda t: f"CoordinationThread {coordination_id}: "
                       f"{t['state']}→dead_ended（{outcome}）")
