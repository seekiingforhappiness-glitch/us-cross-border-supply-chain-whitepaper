#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""① runtime 治理 API——持久 Agent runtime 的 HTTP 门面（spec `docs/superpowers/specs/2026-07-17-wave2-final-smart-face.md` §①）。

一句话（白话）：给驾驶舱一条**看/管** AI 处置任务的通道——列出/查看每趟"AI 差事"（run）跑到哪、
每步干了什么（白话时间线）、预算还剩多少；能替 ops **启动**一趟新差事（同步推进到"等人拍板"或终态）、
批完后**续跑**、以及让 manager **急停**（kill）。本模块是 `agent.runtime` 的**门面不是重写**：
list/detail 只读回黑匣子两表，start/resume/kill 直接调 `Runtime` / `kill_run` 既有原语。

为什么这样建（≤5 行，AGENTS.md §4）：
  · **冻结区仍不可达**（红线）：本 API 只暴露 list/detail/start/resume/kill 五个动作，没有一个能执行
    审批/关闭/报价裁决——runtime 的写面恒为 Toolbox 既有 7 写工具、经 dispatch 单门拦截，API 不新开任何
    通往冻结区的路（新端点绝不可达审批执行）。
  · **GET 物理只读**（红线）：list/detail 用 mode=ro 连接读 agent_runs/agent_run_steps；两表未建（本世界
    还没跑过 run）→ 诚实空态（空列表 / 404），绝不在 GET 里建表写库。
  · **API 语境不烧订阅通道**：start/resume 起 Runtime 时 llm 默认 `off`（确定性剧本、零出境），可经
    env `RUNTIME_API_LLM=auto` 显式开启；实际 mode 如实标给前端（run 步时间线里 think 步的 mode 字段）。
  · **权限双层 + 审计留痕**（生产级硬门）：kill 是 manager 专属（后端独立鉴权 X-Role，不靠前端灰态）+
    X-Actor 必填；start/resume/kill 三个控制面动作都落 action_log 语义级留痕（谁对哪个 run 做了什么）。
  · 路由工厂由 main.py 尾部注入挂载（同 governance/decisions/collaboration：本模块不反向 import main →
    零循环导入）；X-World 经注入的 get_db_path 解析（runtime 两表落在对应世界库），与 /objects 同门。

术语对照（白话）：
  · run（一趟差事）：一条"处置 RSK-xxxx"的完整任务，有目标、预算、状态、总结。
  · step（一步）：差事里的一个动作。五种白话见 STEP_KIND_LABELS。
  · 稳态：run 同步推进后停下的地方——要么"等人拍板"(waiting_approval)，要么已到终态。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Callable

from fastapi import APIRouter, Body, Depends, Header, HTTPException

import app.actions as app_actions                       # 写连接管理（connect）+ 控制面审计落 action_log
from agent.runtime import Runtime, kill_run             # 门面复用既有原语（不重写状态机/杀令逻辑）


# 步骤类型白话标签（spec §① "steps 时间线返回白话 kind 标签"）——与 agent.runtime.STEP_KINDS 一一对应。
STEP_KIND_LABELS = {
    "think": "思考（想清楚怎么处置，不写库）",
    "tool": "查询（只读取详情，不改数据）",
    "command": "写入（经命令总线，幂等恰一次）",
    "wait": "等待人工审批（停下等人拍板，不烧预算）",
    "verify": "写后复读核实（读回数据库确认副作用真的发生）",
}
# run 状态白话标签（给前端状态徽章）——与 agent.runtime.RUN_STATUSES 一一对应。
RUN_STATUS_LABELS = {
    "created": "已登记（还没开跑）",
    "running": "运行中",
    "waiting_approval": "等待人工审批（去待拍板处批它）",
    "done": "已完成（写后复读核实通过）",
    "failed": "已停止（走不下去／提案被驳回／原地打转）",
    "timeout": "超时停止（时间预算耗尽）",
    "budget_exhausted": "预算耗尽（步数／工具次数用尽）",
    "killed": "已被人工急停",
}


# ─── 只读原语（GET 物理只读红线：绝不在 GET 里建表写库） ───
def _ro_connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _runtime_tables_exist(con: sqlite3.Connection) -> bool:
    """两张黑匣子表是否已建（sqlite_master 恒在，未建表也不抛错——诚实空态的判据）。"""
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'").fetchone() is not None


def _run_exists(db_path: str, run_id: str) -> bool:
    """只读探测 run 是否存在——供 resume/kill 在写操作前做无副作用的 404 判定（不建表、不动库）。"""
    con = _ro_connect(db_path)
    try:
        return _runtime_tables_exist(con) and con.execute(
            "SELECT 1 FROM agent_runs WHERE run_id=?", (run_id,)).fetchone() is not None
    finally:
        con.close()


def _budget_summary(budget_json: str | None, steps_used: int, tool_calls_used: int | None) -> dict:
    """预算余量摘要（列表给概览、详情给全量）：耗时/步数/工具次数三条余量都现算。
    tool_calls_used=None（列表不逐 run 数工具步以省一次子查询）时该项余量留 None，前端按"未统计"呈现。"""
    b = json.loads(budget_json or "{}")
    max_steps, max_tool = b.get("max_steps"), b.get("max_tool_calls")
    max_sec, spent = b.get("max_seconds"), b.get("spent_seconds", 0.0)
    return {
        "max_steps": max_steps, "steps_used": steps_used,
        "steps_remaining": (max_steps - steps_used) if isinstance(max_steps, int) else None,
        "max_tool_calls": max_tool, "tool_calls_used": tool_calls_used,
        "tool_calls_remaining": (max_tool - tool_calls_used)
                                if isinstance(max_tool, int) and tool_calls_used is not None else None,
        "max_seconds": max_sec, "spent_seconds": spent,
        "seconds_remaining": round(max_sec - spent, 3)
                             if isinstance(max_sec, (int, float)) else None,
    }


def _api_llm_mode() -> str:
    """API 语境的 think 步 LLM 档（env 默认）：默认 `off`（不烧订阅通道、零出境、确定性剧本），env
    `RUNTIME_API_LLM=auto` 才显式开启（探测可用才出境，不可用优雅降级并如实标 mode）。"""
    return "auto" if os.environ.get("RUNTIME_API_LLM", "off").strip().lower() == "auto" else "off"


def _resolve_llm_mode(body: dict | None) -> str:
    """think 步 LLM 档解析（波E②真模型开关）：请求体 {"llm": true} → `auto`（该趟走真模型，**覆盖 env
    默认**）；{"llm": false} → `off`（确定性剧本）；未带 llm 键（或非布尔）→ 回落 env 默认 _api_llm_mode()。
    `auto` 仍 probe 先行（探测 claude CLI 可用才出境，不可用优雅降级并如实标 mode）——开真模型是**显式
    opt-in**、非静默烧订阅通道（生产级硬门：真模型开关不静默烧钱；响应 llm_mode 字段如实回传实际档）。"""
    llm = (body or {}).get("llm")
    if isinstance(llm, bool):
        return "auto" if llm else "off"
    return _api_llm_mode()


def _require_actor(x_actor: str | None) -> str:
    """X-Actor 必填（start/resume/kill 都要留痕『是谁操作的』，系统不代填）——缺失即 422 白话。"""
    if x_actor is None or not x_actor.strip():
        raise HTTPException(
            422, detail="缺少 X-Actor 请求头：启动／续跑／急停 AI 任务都要记录『是谁操作的』，"
                        "这个身份 id 必须由你带上，系统不替你代填。")
    return x_actor.strip()


def _audit(db_path: str, as_of: str, actor: str, role: str, action: str,
           target: str | None, params: dict) -> None:
    """控制面审计留痕（谁对哪个 run 做了 start/resume/kill）：独立写连接、独立事务落 action_log，
    trace_id 为 NULL（人类控制面动作，非 AI 追踪链）。与 run 内业务动作的审计（命令总线经
    AgentSession.dispatch 落 action_log）物理分开，绝不与 runtime 事务交叉持锁。target 缺失（如 start
    异常未拿到 run_id）则跳过——审计不该反过来把主流程炸掉。"""
    if not target:
        return
    con = app_actions.connect(db_path)
    try:
        con.execute(
            """INSERT INTO action_log (actor, role, action, target_object_id, params_json,
               as_of_date, timestamp, result, trace_id) VALUES (?,?,?,?,?,?,?,?,NULL)""",
            (actor, role, action, target, json.dumps(params, ensure_ascii=False),
             as_of, f"{as_of}T00:00:00Z", "ok"))
        con.commit()
    finally:
        con.close()


def build_runtime_router(get_db_path: Callable, infer_world: Callable, as_of: str) -> APIRouter:
    """路由工厂（main.py 尾部注入式挂载，同 governance/decisions/collaboration，不反向 import main）。

    get_db_path = main.get_db_path（X-World → 世界库解析；测试 dependency_overrides 对本路由同样生效）。
    infer_world = main._infer_world（库路径 → 世界标识，用于响应 world 信封字段，同 /objects/collaboration）。
    as_of = main.AS_OF（仿真时钟单一来源，用于控制面审计的 as_of_date，不新增平行日期常量）。
    """
    router = APIRouter(prefix="/runtime", tags=["runtime"])

    # ── GET /runtime/runs：列表（status/goal/budget 摘要/updated_at，游标同 /objects 惯例） ──
    @router.get("/runs")
    def list_runs_endpoint(
            cursor: str | None = None,
            limit: int = 100,
            db_path: str = Depends(get_db_path)) -> dict:
        """列出本世界全部 AI 任务运行。诚实空态：两表未建（还没跑过任何 run）→ 返回空列表（非 404/500）。
        游标同 /objects 惯例：`cursor` 在场→按 run_id keyset 翻页（响应带 next_cursor）；缺省→自然序 + LIMIT
        （响应无 next_cursor，与 /objects 无游标分支同构）。首页传空游标（?cursor=）即从头翻。"""
        if limit <= 0:
            raise HTTPException(422, detail="limit 必须 > 0")
        con = _ro_connect(db_path)
        try:
            world = infer_world(db_path)
            if not _runtime_tables_exist(con):        # 诚实空态：本世界还没有任何 AI 任务运行记录
                empty = {"world": world, "count": 0, "limit": limit, "items": [],
                         "note": "本世界还没有任何 AI 任务运行记录（尚未有人启动过 run）。"}
                if cursor is not None:
                    empty["next_cursor"] = None
                return empty

            select = ("""SELECT r.run_id, r.status, r.killed, r.goal, r.agent_role, r.budget_json,
                                r.updated_at, r.summary,
                                (SELECT count(*) FROM agent_run_steps s WHERE s.run_id=r.run_id) AS steps
                         FROM agent_runs r""")
            if cursor is None:                        # 无游标：新→旧稳定排序 + LIMIT（对抗复核低危①：
                # 无 ORDER BY 的 LIMIT 是"随机子集"，运行数超限时静默丢最新——按 rowid DESC 保最新可见）
                rows = con.execute(select + " ORDER BY r.rowid DESC LIMIT ?", (limit,)).fetchall()
                items = [_list_item(r) for r in rows]
                return {"world": world, "count": len(items), "limit": limit, "items": items}
            # 游标在场：keyset by run_id（主键全序稳定 → 逐页并集=全量、无重漏，同 /objects cursor 分支）
            where = ' WHERE r.run_id > ?' if cursor else ''
            params = ([cursor] if cursor else []) + [limit]
            rows = con.execute(select + where + ' ORDER BY r.run_id LIMIT ?', params).fetchall()
            next_cursor = rows[-1]["run_id"] if len(rows) == limit else None
            items = [_list_item(r) for r in rows]
            return {"world": world, "count": len(items), "limit": limit, "items": items,
                    "next_cursor": next_cursor}
        finally:
            con.close()

    # ── GET /runtime/runs/{id}：详情（状态 + 预算余量 + steps 白话时间线全量） ──
    @router.get("/runs/{run_id}")
    def get_run_endpoint(run_id: str, db_path: str = Depends(get_db_path)) -> dict:
        """单个 run 详情：状态 + 预算余量 + steps 时间线全量（每步带白话 kind 标签）。
        run 不存在（含本世界两表未建）→ 404 诚实空态。"""
        con = _ro_connect(db_path)
        try:
            run = None
            if _runtime_tables_exist(con):
                run = con.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise HTTPException(
                    404, detail=f"AI 任务运行 '{run_id}' 不存在（可用 GET /runtime/runs 查看现有运行）。")
            step_rows = con.execute(
                "SELECT step_no, kind, payload_json, result_json, created_at "
                "FROM agent_run_steps WHERE run_id=? ORDER BY step_no", (run_id,)).fetchall()
            steps = [_step_view(s) for s in step_rows]
            tool_calls_used = sum(1 for s in step_rows if s["kind"] in ("tool", "command"))
            return {
                "world": infer_world(db_path),
                "run_id": run["run_id"], "status": run["status"],
                "status_label": RUN_STATUS_LABELS.get(run["status"], run["status"]),
                "goal": run["goal"], "agent_role": run["agent_role"],
                "killed": run["killed"], "summary": run["summary"],
                "created_at": run["created_at"], "updated_at": run["updated_at"],
                "budget": _budget_summary(run["budget_json"], len(step_rows), tool_calls_used),
                "steps": steps,
            }
        finally:
            con.close()

    # ── POST /runtime/runs：启动（X-Actor 必填；同步推进到首个稳态） ──
    @router.post("/runs")
    def start_run_endpoint(
            body: dict = Body(default={}),
            x_role: str = Header(default="ops", alias="X-Role"),
            x_actor: str | None = Header(default=None, alias="X-Actor"),
            db_path: str = Depends(get_db_path)) -> dict:
        """启动一趟 AI 处置差事：创建 run 并同步推进到首个稳态（waiting_approval / 终态）。
        body={goal_risk_id, llm?}（goal_risk_id=要处置的风险事件号 形如 RSK-0007；可选 llm 布尔）；
        X-Actor 必填（留痕）。llm 档（波E②）：body {"llm": true} → 该趟 think 走真模型（覆盖 env 默认、
        显式 opt-in、probe 先行不静默烧钱），{"llm": false} → off，不带 → env 默认（默认 off、不烧订阅通道）；
        实际 mode 见响应 llm_mode 字段与 steps 里 think 步的 mode，如实回传。
        提案-only 语义原样：run 只会推进到"提交提案、等人审批"，绝不自行批准（冻结区不可达）。"""
        actor = _require_actor(x_actor)
        goal_risk_id = (body or {}).get("goal_risk_id")
        if not goal_risk_id or not str(goal_risk_id).strip():
            raise HTTPException(
                422, detail="缺少 goal_risk_id：请指定要让 AI 处置的风险事件号（形如 RSK-0007）。")
        goal = f"处置 {str(goal_risk_id).strip()}"
        llm_mode = _resolve_llm_mode(body)
        rt = Runtime(db_path=db_path, role=x_role, llm=llm_mode)
        out = None
        try:
            out = rt.start(goal)
        finally:
            rt.con.close()
            # 对抗复核低危②：审计在 finally 内——即使 start 中途抛异常（run 行可能已建），
            # "谁启动的"也必须留痕；out 为 None 时 run_id 如实记 null。
            _audit(db_path, as_of, actor, x_role, "StartAgentRun",
                   (out or {}).get("run_id"), {"goal": goal, "llm_mode": llm_mode})
        return _run_envelope(out, db_path, infer_world, llm_mode)

    # ── POST /runtime/runs/{id}/resume：续跑（等审批三分支语义透传） ──
    @router.post("/runs/{run_id}/resume")
    def resume_run_endpoint(
            run_id: str,
            x_role: str = Header(default="ops", alias="X-Role"),
            x_actor: str | None = Header(default=None, alias="X-Actor"),
            db_path: str = Depends(get_db_path)) -> dict:
        """断点续跑：等审批三分支语义原样透传——pending（仍在等，不推进、不耗预算）／approved（回到
        running 做写后复读核实→done）／rejected（记 failed 带白话原因）。X-Actor 必填。
        run 不存在 → 404（无副作用探测，不建表）。"""
        actor = _require_actor(x_actor)
        if not _run_exists(db_path, run_id):
            raise HTTPException(
                404, detail=f"AI 任务运行 '{run_id}' 不存在，无法续跑（可用 GET /runtime/runs 查看）。")
        llm_mode = _api_llm_mode()
        rt = Runtime(db_path=db_path, role=x_role, llm=llm_mode)
        try:
            out = rt.resume(run_id)
        finally:
            rt.con.close()
        _audit(db_path, as_of, actor, x_role, "ResumeAgentRun", run_id,
               {"resulting_status": out.get("status"), "llm_mode": llm_mode})
        return _run_envelope(out, db_path, infer_world, llm_mode)

    # ── POST /runtime/runs/{id}/kill：急停（manager 专属 + X-Actor 必填；幂等） ──
    @router.post("/runs/{run_id}/kill")
    def kill_run_endpoint(
            run_id: str,
            x_role: str = Header(default="ops", alias="X-Role"),
            x_actor: str | None = Header(default=None, alias="X-Actor"),
            db_path: str = Depends(get_db_path)) -> dict:
        """急停（kill switch）：manager 专属人类安全控制。后端独立鉴权（X-Role 必须 manager，不靠前端
        灰态）+ X-Actor 必填双查。幂等：重复 kill 返回同结果（killed=1，状态保持原终态）。
        审计落 action_log 语义级记录（谁急停了哪个 run）。run 不存在 → 404。"""
        actor = _require_actor(x_actor)                            # ① X-Actor 必填 → 422
        if x_role != "manager":                                    # ② manager 专属 → 403（后端独立鉴权）
            raise HTTPException(
                403, detail="急停（kill）是 manager 专属的人类安全控制：只有经理角色能强制停止一个正在跑的"
                            "AI 任务。当前角色无此权限，请让经理来操作。")
        if not _run_exists(db_path, run_id):                       # ③ 不存在 → 404（无副作用探测）
            raise HTTPException(404, detail=f"AI 任务运行 '{run_id}' 不存在，无法急停。")
        out = kill_run(db_path, run_id)                            # ④ 调既有原语（幂等留痕）
        if not out.get("ok"):     # 探测通过后仍失败（极端竞态：run 被并发清理）——如实 404，不冒假成功
            raise HTTPException(404, detail=out.get("error") or f"AI 任务运行 '{run_id}' 急停失败。")
        _audit(db_path, as_of, actor, x_role, "KillAgentRun", run_id,
               {"result_status": out.get("status")})
        return {**out, "world": infer_world(db_path),
                "status_label": RUN_STATUS_LABELS.get(out.get("status"), out.get("status"))}

    return router


# ─── 视图组装（纯函数，无 DB 依赖：便于单测） ───
def _list_item(row: sqlite3.Row) -> dict:
    """列表项：status/goal/budget 摘要/updated_at（spec §①）+ 状态白话标签。列表不逐 run 数工具步
    （省一次子查询），故 budget 的 tool_calls_used 传 None（前端按"未统计"呈现）。"""
    return {
        "run_id": row["run_id"], "status": row["status"],
        "status_label": RUN_STATUS_LABELS.get(row["status"], row["status"]),
        "goal": row["goal"], "agent_role": row["agent_role"], "killed": row["killed"],
        "steps": row["steps"], "updated_at": row["updated_at"], "summary": row["summary"],
        "budget": _budget_summary(row["budget_json"], row["steps"], None),
    }


def _step_view(row: sqlite3.Row) -> dict:
    """单步视图：白话 kind 标签 + payload/result 原样（供时间线逐步点亮）。"""
    return {
        "step_no": row["step_no"], "kind": row["kind"],
        "kind_label": STEP_KIND_LABELS.get(row["kind"], row["kind"]),
        "payload": json.loads(row["payload_json"] or "{}"),
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "created_at": row["created_at"],
    }


def _run_envelope(out: dict, db_path: str, infer_world: Callable, llm_mode: str) -> dict:
    """start/resume 的驱动结果信封：原样透传 run 驱动结果（status/task_id/note/summary/approval_status）
    + world 信封字段 + 实际 llm_mode + 状态白话标签。绝不改写 runtime 的裁决结果（门面不代判）。"""
    return {**out, "world": infer_world(db_path), "llm_mode": llm_mode,
            "status_label": RUN_STATUS_LABELS.get(out.get("status"), out.get("status"))}
