"""波2 Command 写总线核心（spec `docs/superpowers/specs/2026-07-16-wave2-command-bus.md` §一）。

一句话：给三门（agent/tools.py dispatch、apps/api /actions、/decisions，+ Streamlit 任务处理台）
一个**单一写入口** execute_command——落 commands 台账（幂等键 + 参数指纹 + 审批绑提案指纹），
再原样调既有 app.actions / app.admission_actions 函数（**函数本体一行不改**，总线是包装不是重写）。

为什么这样建（≤5 行，AGENTS.md §4）：
- 写路径归一（V14 接缝②「所有写操作收敛到单一 Command 通道，不再新增第三条写路」）：三门都经此，
  命令有唯一台账、幂等可复放、审批可绑提案指纹；但 maker-checker / 权限判定仍留在 app 层原函数
  （**总线不代判**，spec §一.5）——本模块零权限判断、零状态机判断。
- commands 表首用自建（CREATE TABLE IF NOT EXISTS，同 agent.egress_gate 的 llm_calls 自建兜底模式），
  不改 35 对象表结构、不碰冻结区 / 真值 / EXPECTED_* / 本体 JSON。
- 事务边界沿用 action_context：动作函数各自 BEGIN IMMEDIATE…COMMIT 并直接 commit 自己的审计
  （_fail/_denied/_log）；总线**绝不跨动作持锁**，只在动作前后各自独立 commit 台账行（认领先落坑、
  结果回填），否则会与动作内部的直接 commit 冲突。
- created_at 用真实 UTC（运行态遥测，非业务 as_of；同 egress_gate.log_llm_call 的 D8 遥测例外并留痕），
  「最近一条 propose」排序一律靠 rowid（不依赖 created_at），故时钟不影响幂等 / 绑定的确定性。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

# 幂等「在飞」冲突复用同型异常（spec §二：_StateConflict→HTTP 409）；current_action_trace_id 让
# AI 经 dispatch 触发的写命令把 LLM trace 一并落进台账（G-Trace 血缘，与 action_log 同号）。
# 仅取这两个模块级名字——不 import 动作函数本体（动作函数由各门作为 action_func 传入）。
from app.actions import _StateConflict, current_action_trace_id


APPROVE_ACTION = "ApproveMitigation"
# 指纹不一致（提案在审批间隙被改）白话错误——spec §一.3 原话，逐字使用。
FINGERPRINT_MISMATCH_MESSAGE = "你看到的方案和现在库里的不是同一版"

# commands 台账 DDL（单一来源）。规格列（spec §一.1）：command_id / idempotency_key / action / actor /
# role / params_fingerprint（sha256 of 规范化 JSON）/ trace_id / result_status / object_id / created_at。
# **实现必需增列（如实标注，同 egress_gate 在规格列外加 error 列的先例）**：
#   · fingerprint_check —— 审批绑定结果留痕：'no_baseline'（历史/种子任务查不到 propose 命令，放行
#     不误杀既有演示数据，spec §一.3 明确要求留痕）/ 'match' / 'mismatch'；非审批命令留 NULL。
#   · result_json —— 首次结果全文 {ok,object_id,side_effects,error}，供幂等复放「原样返回首次结果」
#     （含 ok=False 的首次结果也原样回）；规格列 result_status / object_id 是它的可查询投影。
_COMMANDS_DDL = """CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    action TEXT NOT NULL,
    actor TEXT,
    role TEXT,
    params_fingerprint TEXT NOT NULL,
    trace_id TEXT,
    result_status TEXT,
    object_id TEXT,
    fingerprint_check TEXT,
    result_json TEXT,
    proposal_fingerprint TEXT,
    created_at TEXT NOT NULL
)"""
# proposal_fingerprint（对抗复核 F1 修正列）：提案落库成功后，总线从**任务行现状**现算的规范提案
# 指纹 sha256({task_id, proposed_action, proposal_params})。与 params_fingerprint（原始入参指纹）
# 分开存的原因：ProposeCollection 等提案动作的入参形状（{payment_id,note}）与任务行不同构，拿
# 入参指纹做审批绑定基线会把"漏放行"变成"必误杀"；规范提案指纹按产物（任务行）统一形状，
# 审批绑定据此做到**动作名不可知**——未来任何产出待批任务的新提案动作自动被覆盖。


def ensure_commands_table(con: sqlite3.Connection) -> None:
    """建表兜底（幂等）：首次使用时自建 + 旧副本自愈补列（同 egress_gate 的 llm_calls 模式）。"""
    con.execute(_COMMANDS_DDL)
    cols = {r[1] for r in con.execute("PRAGMA table_info(commands)")}
    if "proposal_fingerprint" not in cols:      # 早于 F1 修正建的副本：只增列，不动既有行
        con.execute("ALTER TABLE commands ADD COLUMN proposal_fingerprint TEXT")
    con.commit()


def fingerprint_params(params) -> str:
    """指纹 = 对 params 做「键排序 + 紧凑分隔」的 JSON 再 sha256（spec §一.1）。sort_keys 递归生效，
    故嵌套 proposal_params 内层键序不影响指纹；default=str 兜底非常规类型（正常业务参数为
    str/num/bool/list/dict，不触发）。同一函数供 落库 与 审批现算 两处调用，保证可比。"""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _result_status(result) -> str:
    return "ok" if isinstance(result, dict) and result.get("ok") else "rejected"


def _lookup_by_key(con: sqlite3.Connection, key: str):
    return con.execute(
        "SELECT result_status, result_json FROM commands WHERE idempotency_key=?",
        (key,)).fetchone()


def _replay_or_conflict(row, key: str) -> dict:
    """幂等命中：已有终态 → 原样返回首次结果（含 ok=False）；仍 pending（并发在飞）→ _StateConflict(409)。"""
    status = row[0]
    if status == "pending":
        raise _StateConflict(
            f"幂等键 {key} 正在处理中：另一个并发请求已认领这次写入且尚未完成。"
            "请勿重复提交，稍后凭同一 key 取回它的结果。")
    return json.loads(row[1])


def check_approval_binding(con: sqlite3.Connection, params: dict):
    """审批绑提案指纹（spec §一.3）：approve 执行**前**，反查该 task 最近一条成功 propose 命令的指纹，
    与「任务当前 proposal 现算指纹」比对。返回 (allowed, fingerprint_check, error)：
      · 查不到 propose 命令记录（历史/种子任务）→ (True, 'no_baseline', None)：放行但留痕，不因新
        机制误杀既有演示数据（spec §一.3 原文要求）。
      · 一致 → (True, 'match', None)；不一致（提案在审批间隙被改）→ (False, 'mismatch', 白话错误)。
    现算指纹重建 propose 命令的原始 params 形状 {task_id, proposed_action, proposal_params}——三者
    都取任务当前状态（task_id 不变；proposed_action / proposal_params 落在 tasks 表），故未被篡改时
    逐字节等于 propose 命令落库时的指纹，且同时覆盖 proposed_action 与 proposal_params 两处改动。
    按 rowid DESC 取「最近」，不依赖 created_at（同一 as_of 天内多命令仍可稳定定序）。"""
    task_id = params.get("task_id")
    if not task_id:
        return True, None, None   # task_id 缺失交给 app 层报它自己的错（总线不代判）
    # 基线=该任务最近一条带规范提案指纹的命令（**动作名不可知**，对抗复核 F1 修正：按动作名枚举会
    # 漏掉 ProposeCollection 一族——它们的入参形状与任务行不同构，故基线一律用落库后现算的
    # proposal_fingerprint，任何产出待批任务的提案动作自动被覆盖）。
    row = con.execute(
        "SELECT proposal_fingerprint FROM commands WHERE object_id=? "
        "AND proposal_fingerprint IS NOT NULL ORDER BY rowid DESC LIMIT 1",
        (task_id,)).fetchone()
    if row is None:
        return True, "no_baseline", None
    baseline_fp = row[0]
    task = con.execute(
        "SELECT proposed_action, proposal_params FROM tasks WHERE task_id=?",
        (task_id,)).fetchone()
    if task is None:
        return True, None, None   # 任务不存在交给 app 层（approve_mitigation 会报「任务不存在」）
    current = {"task_id": task_id, "proposed_action": task[0],
               "proposal_params": json.loads(task[1] or "{}")}
    if fingerprint_params(current) == baseline_fp:
        return True, "match", None
    return False, "mismatch", FINGERPRINT_MISMATCH_MESSAGE


def _insert_row(con, command_id, key, action, actor, role, fp, trace_id,
                result_status, object_id, fingerprint_check, result_json, created_at,
                proposal_fingerprint=None) -> None:
    con.execute(
        """INSERT INTO commands (command_id, idempotency_key, action, actor, role,
           params_fingerprint, trace_id, result_status, object_id, fingerprint_check,
           result_json, created_at, proposal_fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (command_id, key, action, actor, role, fp, trace_id, result_status, object_id,
         fingerprint_check, result_json, created_at, proposal_fingerprint))
    con.commit()


def _proposal_fingerprint_for(con: sqlite3.Connection, result) -> str | None:
    """若本命令产出/更新了一个**待批提案任务**，从任务行现状算规范提案指纹（对抗复核 F1）：
    sha256({task_id, proposed_action, proposal_params})。动作名不可知——判据是产物而非名字：
    result.object_id 指向 approval_status='pending' 的任务即算（ProposeMitigation/ProposeCollection
    及未来一切提案动作自动覆盖）；assign（无 pending）/approve（已 approved）自然跳过。"""
    if not (isinstance(result, dict) and result.get("ok") and result.get("object_id")):
        return None
    row = con.execute(
        "SELECT proposed_action, proposal_params FROM tasks "
        "WHERE task_id=? AND approval_status='pending'", (result["object_id"],)).fetchone()
    if row is None:
        return None
    canonical = {"task_id": result["object_id"], "proposed_action": row[0],
                 "proposal_params": json.loads(row[1] or "{}")}
    return fingerprint_params(canonical)


def _finalize(con, command_id, key, action, actor, role, fp, trace_id,
              result, fingerprint_check, created_at) -> None:
    """命令落终态：已认领（pending 行）→ UPDATE 回填结果；无幂等键直调 → INSERT 一条终态行。
    提案类产物同步落 proposal_fingerprint（审批绑定的基线，见 _proposal_fingerprint_for）。"""
    status = _result_status(result)
    object_id = result.get("object_id") if isinstance(result, dict) else None
    result_json = json.dumps(result, ensure_ascii=False, default=str)
    proposal_fp = _proposal_fingerprint_for(con, result)
    if command_id is not None:
        con.execute(
            """UPDATE commands SET result_status=?, object_id=?, fingerprint_check=?,
               result_json=?, proposal_fingerprint=? WHERE command_id=?""",
            (status, object_id, fingerprint_check, result_json, proposal_fp, command_id))
        con.commit()
    else:
        _insert_row(con, uuid.uuid4().hex, key, action, actor, role, fp, trace_id,
                    status, object_id, fingerprint_check, result_json, created_at,
                    proposal_fingerprint=proposal_fp)


def execute_command(con: sqlite3.Connection, *, action: str, params: dict, actor: str, role: str,
                    as_of: str, action_func, idempotency_key: str | None = None,
                    trace_id: str | None = None) -> dict:
    """单一写入口。落 commands 台账 →（approve 绑提案指纹）→ 原样调 app 层动作函数 → 回填结果。

    幂等键（可空）命中：终态 → 原样返回首次结果（含 ok=False）；仍 pending（并发在飞）→ _StateConflict(409)。
    无幂等键：每次都执行并记一条终态命令行（AI dispatch / 无 Idempotency-Key 头的 API 走此，语义与
    直调一字不差——总线对成功路径透明）。返回动作函数的原结果字典 {ok,object_id,side_effects,error}。

    action：本体 PascalCase 动作名（如 ProposeMitigation / ApproveMitigation），三门统一口径，供
      审批绑定按 action 反查 propose 命令。action_func：由各门传入的既有实现函数，本模块以
      action_func(con, **params, actor=, role=, as_of=) 调用（**函数本体一行不改**）。
    """
    con.execute("PRAGMA busy_timeout=5000")   # 并发下把「锁竞争」变成清晰的等待/UNIQUE 冲突（非持久，不改库 md5）
    ensure_commands_table(con)                # 先设 busy_timeout 再建表：并发首建表也走等待而非 BUSY 报错
    fp = fingerprint_params(params)
    key = idempotency_key.strip() if isinstance(idempotency_key, str) and idempotency_key.strip() else None
    if trace_id is None:                      # 未显式传 → 取 AI dispatch 期间设的 G-Trace（API/人工无 → None）
        trace_id = current_action_trace_id()
    created_at = _now_utc()

    command_id = None
    if key is not None:
        prior = _lookup_by_key(con, key)
        if prior is not None:
            return _replay_or_conflict(prior, key)
        # 认领（claim-first）：先落 pending 行占坑，并发同 key 的另一方 INSERT 撞 UNIQUE 被挡在门外，
        # 保证「双发只执行一次」——占坑在调动作函数之前，故任何时刻至多一个执行者。
        command_id = uuid.uuid4().hex
        try:
            _insert_row(con, command_id, key, action, actor, role, fp, trace_id,
                        "pending", None, None, None, created_at)
        except (sqlite3.IntegrityError, sqlite3.OperationalError):
            # IntegrityError=UNIQUE 撞坑（正常并发路径）；OperationalError=busy_timeout 超时的锁竞争
            # 极窄窗（对抗复核提示：赢家持锁超 5s 时输家 INSERT 可能报 BUSY——同样走复查而非冒 500）。
            con.rollback()
            prior = _lookup_by_key(con, key)
            if prior is None:
                # 认领竞争后对方又撤坑（极端：并发同 key 且对方是畸形请求 TypeError 撤销了坑）——
                # 安全起见抛 409 让重试，绝不冒双执行风险。
                raise _StateConflict(f"幂等键 {key} 认领竞争，请稍后用同一 key 重试。")
            return _replay_or_conflict(prior, key)

    # 审批绑提案指纹（approve 执行前；spec §一.3）：不一致直接拒，动作函数不被调用（无 approve 副作用）。
    fingerprint_check = None
    if action == APPROVE_ACTION:
        allowed, fingerprint_check, err = check_approval_binding(con, params)
        if not allowed:
            result = {"ok": False, "object_id": None, "side_effects": [], "error": err}
            _finalize(con, command_id, key, action, actor, role, fp, trace_id,
                      result, fingerprint_check, created_at)
            return result

    # 原样调既有动作函数（函数本体一行不改；它自管 BEGIN IMMEDIATE 事务与 action_log 审计）。
    try:
        result = action_func(con, **params, actor=actor, role=role, as_of=as_of)
    except Exception:
        # 撤销已占的 pending 坑再原样抛出（对抗复核 F2 泛化）：不止 TypeError（键不匹配→API 门映射
        # 422，动作未执行零副作用），**任何**未预期异常若留下 pending 行，同一幂等键将永久 409
        # "处理中"——键被毒化且无法自愈。动作内部事务由 action_context 自行回滚，撤坑不影响其审计。
        if command_id is not None:
            con.execute("DELETE FROM commands WHERE command_id=?", (command_id,))
            con.commit()
        raise

    _finalize(con, command_id, key, action, actor, role, fp, trace_id,
              result, fingerprint_check, created_at)
    return result
