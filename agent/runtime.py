"""波2-2c 持久 Agent runtime 状态机（spec `docs/superpowers/specs/2026-07-17-wave2c-agent-runtime.md`）。

一句话（白话）：给 AI 一个"能干长活"的驾驶座——它按剧本一步步处置风险（查详情→派单→提交提案），
走到"要人拍板"的地方就**停下等审批**（waiting_approval），人批完 `--resume` 接着走并**读回对象状态
核实副作用真的发生**（写后复读）；中途崩溃可断点续跑（写步骤凭幂等键恰一次，不会重复派单）；
有硬预算（步数/工具次数/秒数）和防打转护栏（连续 3 次同工具同参数自动停）；人随时 `--kill` 急停留痕。

为什么这样建（≤5 行，AGENTS.md §4）：
- 业务写**全走 Command 总线**（execute_command，幂等键=run:{run_id}:{step_no}）：崩溃后 resume 重放
  同键拿首次结果，恰一次由总线保证，runtime 自己不发明第二套去重。
- 写面=Toolbox 既有 7 写工具（经 AgentSession.dispatch，含越权拦截+审计）：冻结区四个审批/关闭类
  动作在 dispatch 门就不可达，runtime 源码零出现（test_runtime 静态断言锁死）。
- agent_runs / agent_run_steps 是 runtime 自己的黑匣子（运行遥测，真实 UTC 时间戳），不是业务对象表：
  已登记 pipeline.db_digest.TELEMETRY_TABLES 豁免（重建间允许不同），业务表确定性门不受影响。
- 审批等待不烧预算：pending 时 resume 只如实返回不记步——人慢不该吃掉 AI 的预算。

术语对照（白话）：
- run（一次任务）：一条"处置 RSK-xxxx"的完整差事，有目标、预算、状态、总结。
- step（一步）：差事里的一个动作。五种：think（想）、tool（查）、command（写，经总线）、
  wait（停下等人批）、verify（批完读回核实）。
- 幂等键（防重复执行的暗号）：同一把钥匙只开一次门——重放只取首次结果，不再执行。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import date, timedelta, datetime, timezone

# 只 import 不改：指纹函数复用总线的规范化 JSON sha256（loop guard 判"同参数"与总线同一把尺）。
from app.command_bus import fingerprint_params
# 写面与门禁复用 Toolbox（AGENTS.md §5：不重写已存在的工具函数）：
#   ALL_WRITE_PERM = 既有 7 写工具（本 runtime 的写面上限）；dispatch = 唯一调用门（越权拦截+审计）。
from agent.tools import ALL_WRITE_PERM, AgentSession

DEFAULT_DB = "data/ontology.sqlite"
DEFAULT_BUDGET = {"max_steps": 12, "max_tool_calls": 8, "max_seconds": 300}
RUNTIME_WRITE_TOOLS = frozenset(ALL_WRITE_PERM)   # 恰 7 个；新增写工具会在此处显式暴露

# ─── 状态机（单一权威源） ───
# created→running→waiting_approval→running→done|failed|timeout|budget_exhausted|killed
# 语义（白话）：created=登记完还没动；running=正在走剧本；waiting_approval=提案交了、停下等人批；
# done=活干完且复读核实；failed=走不下去（含提案被驳回/打转）；timeout=时间预算耗尽；
# budget_exhausted=步数/工具次数预算耗尽；killed=被人急停。后五者为终态，不可再迁移。
RUN_STATUSES = ("created", "running", "waiting_approval", "done", "failed",
                "timeout", "budget_exhausted", "killed")
TERMINAL_STATUSES = frozenset({"done", "failed", "timeout", "budget_exhausted", "killed"})
ALLOWED_TRANSITIONS = {
    "created": {"running", "killed"},
    "running": {"waiting_approval", "done", "failed", "timeout", "budget_exhausted", "killed"},
    "waiting_approval": {"running", "failed", "killed"},
    "done": set(), "failed": set(), "timeout": set(), "budget_exhausted": set(), "killed": set(),
}
STEP_KINDS = ("think", "tool", "command", "wait", "verify")

# 两张 runtime 表（自建 CREATE IF NOT EXISTS，同 commands / llm_calls 模式；列=任务书原样）。
# created_at/updated_at 用真实 UTC（运行遥测，非业务 as_of；D8 遥测例外同 egress_gate 先例）——
# 已登记 pipeline.db_digest.TELEMETRY_TABLES 豁免（重建间允许不同，理由见该文件头）。
_RUNS_DDL = """CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('created','running','waiting_approval','done',
                                           'failed','timeout','budget_exhausted','killed')),
    agent_role TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    killed INTEGER NOT NULL DEFAULT 0,
    summary TEXT
)"""
_STEPS_DDL = """CREATE TABLE IF NOT EXISTS agent_run_steps (
    run_id TEXT NOT NULL,
    step_no INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('think','tool','command','wait','verify')),
    payload_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, step_no)
)"""


class IllegalTransition(RuntimeError):
    """非法状态迁移（如 done→running）：状态机的边不存在，调用方逻辑有错，立刻炸出来不吞。"""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_runtime_tables(con: sqlite3.Connection) -> None:
    """建表兜底（幂等，同 command_bus.ensure_commands_table 模式）：首次使用时在业务库自建两表。"""
    con.execute(_RUNS_DDL)
    con.execute(_STEPS_DDL)
    con.commit()


def _parse_risk_id(goal: str):
    """从目标句里解析风险事件号（如"处置 RSK-0001"→ RSK-0001）；解析不到返回 None（run 如实 failed）。"""
    m = re.search(r"RSK-[0-9A-Za-z_-]+", goal or "")
    return m.group(0) if m else None


def kill_run(db_path: str, run_id: str) -> dict:
    """kill switch（急停留痕）：置 killed=1；若 run 未终态则迁移到 killed 并落白话 summary。
    已终态的 run 也置 killed=1 留痕（"有人下过杀令"这个事实本身要可查），状态保持原终态如实返回。"""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        ensure_runtime_tables(con)
        row = con.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": f"run {run_id} 不存在"}
        con.execute("UPDATE agent_runs SET killed=1, updated_at=? WHERE run_id=?",
                    (_now_utc(), run_id))
        con.commit()
        status = row["status"]
        if status not in TERMINAL_STATUSES:
            con.execute("UPDATE agent_runs SET status='killed', summary=?, updated_at=? "
                        "WHERE run_id=?",
                        (f"被人工 kill 急停（原状态 {status}）。已执行的步骤与命令台账全部留痕。",
                         _now_utc(), run_id))
            con.commit()
            status = "killed"
        return {"ok": True, "run_id": run_id, "status": status, "killed": 1}
    finally:
        con.close()


def list_runs(db_path: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        ensure_runtime_tables(con)
        return [dict(r) for r in con.execute(
            """SELECT r.run_id, r.status, r.killed, r.goal, r.agent_role, r.updated_at,
                      (SELECT count(*) FROM agent_run_steps s WHERE s.run_id=r.run_id) AS steps,
                      r.summary
               FROM agent_runs r ORDER BY r.created_at, r.run_id""")]
    finally:
        con.close()


class Runtime:
    """持久 Agent 运行时。一个实例绑一个库连接（AgentSession 同款），可 start 新 run 或 resume 旧 run。

    llm："auto"=think 步先探测 claude CLI 可用性（probe，不出境），可用才真调 agent.llm_agent 通道，
    不可用优雅降级为确定性脚本步并如实标注 mode；"off"=永远确定性（测试用，绝不出境）。
    _plan_override / _crash_hook：测试注入缝（分别用于 loop-guard 集成测试与崩溃恢复测试），
    生产调用不传即无行为影响——不是 stub，默认路径零变化。
    """

    def __init__(self, db_path: str = DEFAULT_DB, role: str = "ops", llm: str = "off",
                 budget: dict | None = None, _plan_override=None, _crash_hook=None):
        self.session = AgentSession(db_path=db_path, role=role)
        self.con = self.session.con
        self.role = role
        self.llm = llm
        self.budget_defaults = {**DEFAULT_BUDGET, **(budget or {})}
        self._plan_override = _plan_override
        self._crash_hook = _crash_hook
        ensure_runtime_tables(self.con)

    # ---------- 台账原语 ----------
    def _load(self, run_id: str) -> dict:
        row = self.con.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"run {run_id} 不存在（--list 可查现有 run）")
        return dict(row)

    def _history(self, run_id: str) -> list[dict]:
        out = []
        for r in self.con.execute(
                "SELECT step_no, kind, payload_json, result_json, created_at "
                "FROM agent_run_steps WHERE run_id=? ORDER BY step_no", (run_id,)):
            out.append({"step_no": r["step_no"], "kind": r["kind"],
                        "payload": json.loads(r["payload_json"] or "{}"),
                        "result": json.loads(r["result_json"]) if r["result_json"] else None})
        return out

    def _transition(self, run_id: str, new_status: str, summary: str | None = None) -> None:
        """唯一状态迁移口：按 ALLOWED_TRANSITIONS 校验，非法迁移抛 IllegalTransition（不吞不糊）。"""
        run = self._load(run_id)
        cur = run["status"]
        if new_status not in ALLOWED_TRANSITIONS.get(cur, set()):
            raise IllegalTransition(
                f"非法状态迁移：{cur} → {new_status}（run {run_id}）。"
                f"合法去向：{sorted(ALLOWED_TRANSITIONS.get(cur, set())) or '（终态，无去向）'}")
        self.con.execute(
            "UPDATE agent_runs SET status=?, summary=COALESCE(?, summary), updated_at=? "
            "WHERE run_id=?", (new_status, summary, _now_utc(), run_id))
        self.con.commit()

    def _record_step(self, run_id: str, step_no: int, kind: str, payload: dict,
                     result, duration_s: float) -> None:
        """步骤落账 + 预算耗时累加 + updated_at 一次 commit（崩溃窗口最小化：总线先自 commit，
        这里是 runtime 自己的黑匣子行；两者之间崩溃= resume 凭同幂等键重放拿首次结果）。"""
        run = self._load(run_id)
        budget = json.loads(run["budget_json"])
        budget["spent_seconds"] = round(budget.get("spent_seconds", 0.0) + duration_s, 3)
        self.con.execute(
            "INSERT INTO agent_run_steps (run_id, step_no, kind, payload_json, result_json, "
            "created_at) VALUES (?,?,?,?,?,?)",
            (run_id, step_no, kind, json.dumps(payload, ensure_ascii=False, default=str),
             json.dumps(result, ensure_ascii=False, default=str), _now_utc()))
        self.con.execute("UPDATE agent_runs SET budget_json=?, updated_at=? WHERE run_id=?",
                         (json.dumps(budget, ensure_ascii=False), _now_utc(), run_id))
        self.con.commit()

    def _approval_status(self, task_id: str):
        """只读回查任务审批态（等审批三分支的判据）；任务行不存在返回 None。"""
        row = self.con.execute("SELECT approval_status FROM tasks WHERE task_id=?",
                               (task_id,)).fetchone()
        return row["approval_status"] if row else None

    @staticmethod
    def _task_id_from_history(history: list[dict]):
        """从已落账的派单命令结果里取任务号（历史是断点续跑的唯一事实源，不猜库状态）。"""
        for s in reversed(history):
            if (s["kind"] == "command" and s["payload"].get("tool") == "assign_task"
                    and isinstance(s["result"], dict) and s["result"].get("ok")):
                return s["result"].get("object_id")
        return None

    # ---------- 公共入口 ----------
    def start(self, goal: str) -> dict:
        """登记并启动一个 run：created→running→（按剧本推进，直到等审批/终态/预算停）。"""
        run_id = f"RUN-{uuid.uuid4().hex[:12].upper()}"
        budget = {**self.budget_defaults, "spent_seconds": 0.0}
        now = _now_utc()
        self.con.execute(
            "INSERT INTO agent_runs (run_id, goal, status, agent_role, budget_json, created_at, "
            "updated_at, killed, summary) VALUES (?,?,?,?,?,?,?,0,NULL)",
            (run_id, goal, "created", self.role, json.dumps(budget, ensure_ascii=False), now, now))
        self.con.commit()
        self._transition(run_id, "running")
        return self._drive(run_id)

    def resume(self, run_id: str) -> dict:
        """断点恢复：kill 优先 → 终态如实返回 → 等审批三分支（approved 继续做写后复读 /
        rejected 记 failed 带白话原因 / pending 保持等待如实返回）→ 其余从历史断点继续驱动。"""
        run = self._load(run_id)
        if run["killed"]:
            if run["status"] not in TERMINAL_STATUSES:
                self._transition(run_id, "killed",
                                 f"被人工 kill 急停（原状态 {run['status']}），resume 前检查生效。")
            r = self._load(run_id)
            return {"run_id": run_id, "status": r["status"], "killed": 1,
                    "note": "该 run 已被人工 kill，不再推进（留痕可查）。", "summary": r["summary"]}
        if run["status"] in TERMINAL_STATUSES:
            return {"run_id": run_id, "status": run["status"],
                    "note": "该 run 已是终态，无事可做。", "summary": run["summary"]}
        if run["status"] == "waiting_approval":
            history = self._history(run_id)
            task_id = self._task_id_from_history(history)
            if task_id is None:
                self._transition(run_id, "failed",
                                 "等待审批中却在历史里找不到派单任务号（台账异常），如实停止。")
                return {"run_id": run_id, "status": "failed", "note": "台账异常，已停。"}
            st = self._approval_status(task_id)
            if st is None:
                self._transition(run_id, "failed",
                                 f"等待审批的任务 {task_id} 在库中不存在（可能被清理），如实停止。")
                return {"run_id": run_id, "status": "failed", "note": f"任务 {task_id} 不存在。"}
            if st == "pending":
                self.con.execute("UPDATE agent_runs SET updated_at=? WHERE run_id=?",
                                 (_now_utc(), run_id))
                self.con.commit()
                return {"run_id": run_id, "status": "waiting_approval",
                        "approval_status": "pending", "task_id": task_id,
                        "note": "提案仍在等人审批（pending）：未推进、未耗预算，批完再 resume。"}
            # approved / rejected 都回到 running，由 verify 步读回对象状态做统一裁决（写后复读）。
            self._transition(run_id, "running")
        elif run["status"] == "created":
            self._transition(run_id, "running")
        return self._drive(run_id)

    # ---------- 驱动循环 ----------
    def _drive(self, run_id: str) -> dict:
        while True:
            run = self._load(run_id)
            # ① kill switch：任何 step 前检查（跨进程下杀令 commit 后此处可见）。
            if run["killed"]:
                self._transition(run_id, "killed", "被人工 kill 急停（step 前检查命中）。")
                return {"run_id": run_id, "status": "killed", "note": "已按杀令停止，留痕可查。"}
            history = self._history(run_id)
            budget = json.loads(run["budget_json"])
            n_steps = len(history)
            n_tools = sum(1 for s in history if s["kind"] in ("tool", "command"))
            # ② 硬预算：时间→timeout；步数→budget_exhausted（均落白话 summary）。
            #    读法说明：任务书把三种超限都叫"预算耗尽"，状态机又单列 timeout——本实现把
            #    时间预算超限记 timeout、次数预算超限记 budget_exhausted（否则 timeout 不可达），
            #    两者语义都是"预算停"，报告已列明此裁量。
            if budget.get("spent_seconds", 0.0) >= budget["max_seconds"]:
                self._transition(run_id, "timeout",
                                 f"时间预算耗尽：累计执行 {budget.get('spent_seconds', 0.0)}s ≥ "
                                 f"上限 {budget['max_seconds']}s（等待人审批的时间不计入）。")
                return {"run_id": run_id, "status": "timeout"}
            if n_steps >= budget["max_steps"]:
                self._transition(run_id, "budget_exhausted",
                                 f"步数预算耗尽：已执行 {n_steps} 步 ≥ 上限 {budget['max_steps']} 步。")
                return {"run_id": run_id, "status": "budget_exhausted"}
            # ③ 计划下一步（确定性剧本：只依据 goal + 已落账历史，断点续跑天然可复现）。
            plan = (self._plan_override or self._plan_next)(run, history)
            kind = plan["kind"]
            if kind == "finish":
                self._transition(run_id, plan["status"], plan["summary"])
                return {"run_id": run_id, "status": plan["status"], "summary": plan["summary"]}
            if kind == "wait":
                return self._enter_wait(run_id, run, history, plan)
            if kind in ("tool", "command") and n_tools >= budget["max_tool_calls"]:
                self._transition(run_id, "budget_exhausted",
                                 f"工具调用预算耗尽：已调 {n_tools} 次 ≥ 上限 {budget['max_tool_calls']} 次。")
                return {"run_id": run_id, "status": "budget_exhausted"}
            # ④ 执行并落账（写步骤经总线幂等键，恰一次）。
            step_no = n_steps + 1
            t0 = time.monotonic()
            payload, result = self._execute(run, plan, step_no)
            duration = round(time.monotonic() - t0, 3)
            if self._crash_hook is not None:      # 测试缝：模拟"总线已写、黑匣子未记"的崩溃窗口
                self._crash_hook(kind, payload, step_no)
            self._record_step(run_id, step_no, kind, payload, result, duration)
            # ⑤ loop guard（防打转）：连续 N 次同 (tool, 参数指纹) → 自动停（failed+白话）。
            trip = self._loop_guard_tripped(self._history(run_id))
            if trip:
                self._transition(run_id, "failed", trip)
                return {"run_id": run_id, "status": "failed", "summary": trip}

    LOOP_GUARD_N = 3

    def _loop_guard_tripped(self, history: list[dict]):
        """取 tool/command 子序列尾部，末 N 步 (tool, 参数指纹) 全同 → 返回白话停机理由。"""
        calls = [(s["payload"].get("tool"), s["payload"].get("params_fp"))
                 for s in history if s["kind"] in ("tool", "command")]
        n = self.LOOP_GUARD_N
        if len(calls) >= n and len(set(calls[-n:])) == 1 and calls[-1][1] is not None:
            tool = calls[-1][0]
            return (f"防打转护栏触发：连续 {n} 次以完全相同参数调用同一工具 {tool}，"
                    f"判定为原地打转，自动停止（已执行的调用全部留痕）。")
        return None

    # ---------- 步骤执行 ----------
    def _execute(self, run: dict, plan: dict, step_no: int):
        """执行一个非 wait/finish 步，返回 (payload, result)。payload 里记 params_fp（loop guard 用，
        与总线同一把指纹尺）；command 步的幂等键在此现算并留痕。"""
        kind = plan["kind"]
        if kind == "think":
            payload = {"question": plan["question"]}
            return payload, self._think(plan["question"], plan.get("context", ""))
        if kind == "tool":
            payload = {"tool": plan["tool"], "args": plan["args"],
                       "params_fp": fingerprint_params({"tool": plan["tool"], "args": plan["args"]})}
            return payload, self.session.dispatch(plan["tool"], plan["args"])
        if kind == "command":
            tool = plan["tool"]
            if tool not in RUNTIME_WRITE_TOOLS:   # 双保险（dispatch 门本身也会拦）
                return ({"tool": tool, "args": plan["args"]},
                        {"ok": False, "error": f"runtime 写面只含 Toolbox 既有 {len(RUNTIME_WRITE_TOOLS)} "
                                               f"个写工具，{tool} 不在其中，拒绝执行。"})
            key = f"run:{run['run_id']}:{step_no}"
            payload = {"tool": tool, "args": plan["args"], "idempotency_key": key,
                       "params_fp": fingerprint_params({"tool": tool, "args": plan["args"]})}
            if plan.get("note"):    # 波E②：证据摘要一句留痕在步 payload——不入 dispatch 参数、不进
                payload["note"] = plan["note"]   # 指纹（fingerprint 只算 args）→ 提案本体/幂等键 byte-identical
            return payload, self.session.dispatch(tool, plan["args"], idempotency_key=key)
        if kind == "verify":
            return self._verify(plan["task_id"])
        raise ValueError(f"未知步骤类型 {kind}")

    def _think(self, question: str, context: str) -> dict:
        """think 步：llm='auto' 时走 agent.llm_agent 现有通道（probe 先行，不可用→优雅降级）；
        llm='off' 或降级时输出确定性剧本说明，mode 如实标注（绝不假装是模型说的）。"""
        if self.llm == "auto":
            try:
                from agent.llm_agent import answer_over_context, probe_cli_availability
                ok, reason = probe_cli_availability(self.con)
                if ok:
                    text = answer_over_context(question, context, db=self.con,
                                               call_type="briefing")
                    return {"mode": "llm", "text": text}
                note = reason
            except Exception as exc:  # noqa: BLE001 —— LLM 故障绝不让 run 崩溃，降级留痕
                note = f"LLM 通道异常：{str(exc)[:120]}"
        else:
            note = "llm=off（本 run 配置为确定性模式）"
        return {"mode": "deterministic", "note": note,
                "text": ("确定性剧本：查风险详情 → 派单（ops）→ 提交处置提案（proposal-only）→ "
                         "停下等人审批 → 批准后读回任务状态核实副作用（写后复读）。")}

    def _verify(self, task_id: str):
        """写后复读（审批后的后置校验步）：读回任务行，核实审批副作用真实落库。
        approved 且 status=done 且 action_taken 非空 → ok；被驳回/异常 → ok=False 带白话。"""
        row = self.con.execute(
            "SELECT approval_status, status, action_taken, proposed_action FROM tasks "
            "WHERE task_id=?", (task_id,)).fetchone()
        payload = {"task_id": task_id,
                   "expect": {"approval_status": "approved", "status": "done",
                              "action_taken": "非空"}}
        if row is None:
            return payload, {"ok": False, "observed": None,
                             "reason": f"任务 {task_id} 不存在，无法核实。"}
        observed = dict(row)
        if (observed["approval_status"] == "approved" and observed["status"] == "done"
                and observed["action_taken"]):
            return payload, {"ok": True, "observed": observed,
                             "reason": "写后复读通过：审批副作用已真实落库。"}
        if observed["approval_status"] == "rejected":
            return payload, {"ok": False, "observed": observed,
                             "reason": "提案被人工驳回（任务退回 assigned，提案参数留痕于审计）。"}
        return payload, {"ok": False, "observed": observed,
                         "reason": f"审批后任务状态与预期不符：{observed}"}

    # ---------- 确定性剧本 planner ----------
    def _plan_next(self, run: dict, history: list[dict]) -> dict:
        """只依据 goal + 已落账历史计划下一步（断点续跑同键可复现）；审批结果经 wait/verify 判读。
        剧本：think → tool:get_risk → command:assign_task → command:propose_mitigation →
        wait（等人批）→ verify（写后复读）→ finish。任何一步业务失败 → finish failed（白话原因）。"""
        goal = run["goal"]
        risk_id = _parse_risk_id(goal)
        if risk_id is None:
            return {"kind": "finish", "status": "failed",
                    "summary": f"目标「{goal}」里找不到风险事件号（形如 RSK-0001），无法开工。"}

        def latest(kind, tool=None):
            for s in reversed(history):
                if s["kind"] == kind and (tool is None or s["payload"].get("tool") == tool):
                    return s
            return None

        if latest("think") is None:
            return {"kind": "think",
                    "question": f"请用一段话给出对 {risk_id} 的处置思路（proposal-only，审批在人）。",
                    "context": f"目标：{goal}\n执行剧本：查详情→派单→提案→人审→复读核实。"}
        grisk = latest("tool", "get_risk")
        if grisk is None:
            return {"kind": "tool", "tool": "get_risk", "args": {"risk_event_id": risk_id}}
        if not isinstance(grisk["result"], dict) or grisk["result"].get("error"):
            return {"kind": "finish", "status": "failed",
                    "summary": f"查询 {risk_id} 失败：{(grisk['result'] or {}).get('error', '无返回')}"}
        assign = latest("command", "assign_task")
        if assign is None:
            sev = grisk["result"].get("severity")
            due = (date.fromisoformat(self.session.as_of) + timedelta(days=3)).isoformat()
            return {"kind": "command", "tool": "assign_task",
                    "args": {"risk_event_id": risk_id, "assignee_role": "ops",
                             "priority": "P1" if sev == "critical" else "P2", "due_at": due}}
        if not (isinstance(assign["result"], dict) and assign["result"].get("ok")):
            err = (assign["result"] or {}).get("error") or (assign["result"] or {}).get("reason")
            return {"kind": "finish", "status": "failed",
                    "summary": f"派单失败，run 停止：{err}"}
        task_id = assign["result"]["object_id"]
        prop = latest("command", "propose_mitigation")
        if prop is None:
            act, params = self._choose_disposal(risk_id, grisk["result"])
            return {"kind": "command", "tool": "propose_mitigation",
                    "args": {"task_id": task_id, "proposed_action": act,
                             "proposal_params": params},
                    "note": self._evidence_note(risk_id)}   # 波E②：证据摘要一句（增强，失败不阻断提案）
        if not (isinstance(prop["result"], dict) and prop["result"].get("ok")):
            err = (prop["result"] or {}).get("error") or (prop["result"] or {}).get("reason")
            return {"kind": "finish", "status": "failed",
                    "summary": f"提交处置提案失败，run 停止：{err}"}
        if latest("wait") is None or self._approval_status(task_id) == "pending":
            return {"kind": "wait", "task_id": task_id}
        verify = latest("verify")
        if verify is None:
            return {"kind": "verify", "task_id": task_id}
        if isinstance(verify["result"], dict) and verify["result"].get("ok"):
            obs = verify["result"]["observed"]
            return {"kind": "finish", "status": "done",
                    "summary": (f"处置完成：任务 {task_id} 的提案（{obs.get('proposed_action')}）"
                                f"已获人工批准并执行；写后复读确认 approval_status=approved、"
                                f"status=done、action_taken 已落。共 {len(history)} 步。")}
        reason = (verify["result"] or {}).get("reason", "复读未通过")
        return {"kind": "finish", "status": "failed",
                "summary": f"审批后置校验未通过：{reason}"}

    def _evidence_note(self, risk_id: str) -> str:
        """波E② propose 步证据增强：调 apps.api.evidence 纯函数拿一句白话先例摘要
        （"同类 N 例…X% 事后有效"），供 propose 步 payload 的 note 引用（_execute 只把它写进步 payload，
        **不进 dispatch 参数、不改提案本体**——提案 args/幂等键/指纹 byte-identical）。证据是增强不是门槛：
        检索/导入任何失败都不阻断提案，窄捕获（命名异常集，非裸 except——不吞未知 bug）降级为诚实留痕串。
        惰性 import：runtime 冷路径不预拉证据/证据依赖的证据栈（apps.api.evidence），仅 propose 步触发。"""
        try:
            from apps.api.evidence import precedent_summary_line
            return precedent_summary_line(self.con, risk_id)
        except (ImportError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as exc:
            return f"（证据摘要暂不可用：{type(exc).__name__}，检索降级，不影响提案。）"

    @staticmethod
    def _choose_disposal(risk_id: str, risk: dict):
        """确定性处置提案选择（最小可批集）：费用类风险→accept_charge；其余→accept_delay。
        两者审批路径均无危险副作用（发票状态回写/仅任务收口），适合 runtime 剧本演示。"""
        reason = f"runtime 确定性剧本提案（{risk_id}，type={risk.get('type')}），审批在人。"
        if risk.get("type") in ("rate_overbilling", "duplicate_charge", "unplanned_charge"):
            return "accept_charge", {"reason": reason}
        return "accept_delay", {"reason": reason}

    # ---------- wait 处理 ----------
    def _enter_wait(self, run_id: str, run: dict, history: list[dict], plan: dict) -> dict:
        """进入/保持等审批：pending → （首次）记 wait 步 + 迁到 waiting_approval，如实返回；
        已有 wait 步的崩溃窗口（状态还停在 running）→ 只补状态不重复记账。resume 时 pending
        在 resume() 就拦下（不进 drive），故此处不会空转烧预算。"""
        task_id = plan["task_id"]
        st = self._approval_status(task_id)
        if st == "pending":
            if not any(s["kind"] == "wait" for s in history):
                self._record_step(run_id, len(history) + 1, "wait",
                                  {"task_id": task_id},
                                  {"approval_status": "pending",
                                   "note": "提案已提交，等待人工审批（maker-checker）。"}, 0.0)
            if run["status"] != "waiting_approval":
                self._transition(run_id, "waiting_approval")
            return {"run_id": run_id, "status": "waiting_approval", "task_id": task_id,
                    "note": f"提案已提交（任务 {task_id}），等人审批；批完 --resume {run_id} 继续。"}
        return {"run_id": run_id, "status": run["status"], "task_id": task_id,
                "approval_status": st,
                "note": "审批已有结果，请 resume 继续做写后复读。"}


# ---------- CLI ----------
def _cmd_start(args) -> int:
    rt = Runtime(db_path=args.db, role=args.role, llm=("off" if args.no_llm else "auto"),
                 budget={k: v for k, v in (("max_steps", args.max_steps),
                                           ("max_tool_calls", args.max_tool_calls),
                                           ("max_seconds", args.max_seconds)) if v is not None})
    out = rt.start(args.start)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_resume(args) -> int:
    rt = Runtime(db_path=args.db, role=args.role, llm=("off" if args.no_llm else "auto"))
    out = rt.resume(args.resume)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_list(args) -> int:
    rows = list_runs(args.db)
    if not rows:
        print("（暂无 run）")
        return 0
    for r in rows:
        print(f"{r['run_id']}  {r['status']:<17} killed={r['killed']} steps={r['steps']:<3} "
              f"role={r['agent_role']:<5} goal={r['goal']}")
        if r["summary"]:
            print(f"    └─ {r['summary']}")
    return 0


def _cmd_kill(args) -> int:
    out = kill_run(args.db, args.kill)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m agent.runtime",
        description="持久 Agent runtime：可暂停等审批、可断点恢复、带硬预算、防打转。")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", metavar="GOAL", help='启动一个 run，如 --start "处置 RSK-0001"')
    g.add_argument("--resume", metavar="RUN_ID", help="断点恢复（含审批后继续）")
    g.add_argument("--list", action="store_true", help="列出全部 run")
    g.add_argument("--kill", metavar="RUN_ID", help="急停（留痕，resume/step 前检查）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--role", default="ops")
    ap.add_argument("--no-llm", action="store_true", help="think 步不出境（确定性模式）")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--max-tool-calls", type=int, default=None)
    ap.add_argument("--max-seconds", type=int, default=None)
    args = ap.parse_args(argv)
    try:
        if args.start:
            return _cmd_start(args)
        if args.resume:
            return _cmd_resume(args)
        if args.kill:
            return _cmd_kill(args)
        return _cmd_list(args)
    except (ValueError, IllegalTransition) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
