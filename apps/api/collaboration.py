#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协作流真身——coordination_threads 只读端点（波U·U6，spec docs/superpowers/specs/2026-07-16-waveU-user-facing.md）。

一句话：把驾驶舱右栏"协作流"标签从占位变真数据——暴露 coordination_threads（对外协调的
跟进线程：改配船期/工厂确认交期/客户接受拆单…），按风险聚合，留飞书/企微接入的 UI 形状。

设计口径：
  · 世界无关 + X-World 双世界：经注入的 get_db_path/get_ro_connection，X-World: sim 读模拟世界
    （28 条）、verify 读验证世界（seed 演示条）。表不存在的世界 → available:false 诚实空态；
    表在但 0 行 → available:true + threads:[]（0 条=诚实空数组，绝不 500/绝不编造）。
  · 关联富化（任务书 U6："关联对象 id/风险 id/参与角色/时间线"）：线程行本身已带 task_id(关联
    对象)、risk_event_id(风险)、owner+counterparty(参与角色)、opened_at/last_update/
    next_action_due(时间线)；另按 id 富化 risk 摘要(rule_id/type/severity/status，供"按风险聚合"
    上下文)与 task 摘要(含被协调的提案 proposal_params——协作线程协调的正是某处置提案)。
  · X-Role 脱敏语义同 /objects 端点：整个载荷过同一 agent.mcp_server.SensitiveFieldMasker
    （本体 sensitiveFieldRules 声明驱动）。线程列本身无具名敏感字段，但富化进来的
    task.proposal_params.est_cost_usd 是本体嵌套敏感规则(visibleTo ops/manager)——对无权角色
    (如 cs)自动掩码，与 /objects 一套权限、不是两套。

路由工厂由 main.py 尾部注入挂载（同 cockpit/decisions：本模块不反向 import main → 零循环导入；
复用 main 的 get_db_path/get_ro_connection ⇒ 测试对 get_db_path 的 dependency_overrides 自动生效）。
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, Query

from agent.mcp_server import SensitiveFieldMasker
from pipeline.ontology_runtime import load_ontology

# 富化 task 的字段愿望单——与实际列取交集（两世界 tasks schema 有差异：验证世界有 sla_state/
# assignee_user_id 等、模拟世界有 economics_json/decision_day 等，共有子集才安全选）。
# proposal_params 是关键：被协调的提案详情，且承载本体嵌套敏感规则 est_cost_usd。
_TASK_WISHLIST = ("task_id", "title", "status", "approval_status", "proposed_action",
                  "priority", "assignee_role", "proposal_params")
_RISK_FIELDS = ("rule_id", "type", "severity", "status")


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def _parse_proposal(raw: Any) -> Any:
    """proposal_params（DB 存 JSON 字符串）→ dict，供 API 直用且让本体嵌套脱敏规则可作用。
    空 → {}；非法 JSON → 原样保留字符串（不放大为故障，SensitiveFieldMasker 对 str 也能处理）。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    return val


def build_collaboration_router(get_db_path: Callable, get_ro_connection: Callable,
                               infer_world: Callable[[str], str]) -> APIRouter:
    router = APIRouter(prefix="/collaboration", tags=["collaboration"])

    @router.get("/threads")
    def collaboration_threads(
            risk_event_id: str | None = Query(
                default=None, description="按风险过滤：只看该风险 id 的协作线程；缺省=全部"),
            limit: int = Query(default=200, ge=1, le=1000),
            x_role: str = Header(default="ops", alias="X-Role"),
            con: sqlite3.Connection = Depends(get_ro_connection),
            db_path: str = Depends(get_db_path)) -> dict:
        """协作流线程（coordination_threads）+ 关联风险/任务富化 + 按风险聚合。
        X-World 双世界、X-Role 脱敏（同 /objects）、0 条=诚实空数组、缺表=available:false。
        可选 risk_event_id 过滤（点开某风险看其协作流）；limit 默认 200（协作线程量级小）。"""
        world = infer_world(db_path)
        tables = _tables(con)
        if "coordination_threads" not in tables:
            return {"world": world, "role": x_role, "available": False,
                    "reason": "该世界无 coordination_threads 表（协作流域未灌）",
                    "count": 0, "threads": [], "by_risk": [],
                    "summary": {"by_state": {}, "by_counterparty_type": {}, "escalated": 0}}

        thread_cols = _columns(con, "coordination_threads")
        where, params = "", []
        if risk_event_id is not None and "risk_event_id" in thread_cols:
            where = " WHERE risk_event_id = ?"
            params.append(risk_event_id)
        rows = [dict(r) for r in con.execute(
            f"SELECT * FROM coordination_threads{where} ORDER BY coordination_id LIMIT ?",
            (*params, limit))]

        # —— 按 id 批量富化关联 risk / task（不 JOIN，避免两世界 tasks schema 差异下的列歧义）——
        risk_ids = sorted({r["risk_event_id"] for r in rows
                           if r.get("risk_event_id")}) if "risk_event_id" in thread_cols else []
        task_ids = sorted({r["task_id"] for r in rows
                           if r.get("task_id")}) if "task_id" in thread_cols else []

        risk_map: dict[str, dict] = {}
        if risk_ids and "risk_events" in tables:
            rcols = _columns(con, "risk_events")
            rsel = ["risk_event_id"] + [c for c in _RISK_FIELDS if c in rcols]
            ph = ",".join("?" * len(risk_ids))
            for rr in con.execute(
                    f"SELECT {','.join(rsel)} FROM risk_events "
                    f"WHERE risk_event_id IN ({ph})", risk_ids):
                d = dict(rr)
                risk_map[d["risk_event_id"]] = {k: d.get(k) for k in rsel}

        task_map: dict[str, dict] = {}
        if task_ids and "tasks" in tables:
            tcols = _columns(con, "tasks")
            tsel = [c for c in _TASK_WISHLIST if c in tcols]
            if "task_id" in tsel:
                ph = ",".join("?" * len(task_ids))
                for tr in con.execute(
                        f"SELECT {','.join(tsel)} FROM tasks "
                        f"WHERE task_id IN ({ph})", task_ids):
                    d = dict(tr)
                    if "proposal_params" in d:
                        d["proposal_params"] = _parse_proposal(d["proposal_params"])
                    task_map[d["task_id"]] = d

        threads: list[dict] = []
        for r in rows:
            rid = r.get("risk_event_id")
            tid = r.get("task_id")
            r["risk"] = risk_map.get(rid) if rid else None
            r["task"] = task_map.get(tid) if tid else None
            threads.append(r)

        # —— 按风险聚合（U6"按风险聚合展示"）——
        by_risk_acc: dict[str, dict] = {}
        for r in threads:
            rid = r.get("risk_event_id") or "(未关联风险)"
            grp = by_risk_acc.setdefault(rid, {
                "risk_event_id": r.get("risk_event_id"),
                "rule_id": (r.get("risk") or {}).get("rule_id"),
                "severity": (r.get("risk") or {}).get("severity"),
                "thread_count": 0, "states": defaultdict(int), "coordination_ids": []})
            grp["thread_count"] += 1
            grp["states"][r.get("state") or "(空)"] += 1
            grp["coordination_ids"].append(r.get("coordination_id"))
        by_risk = []
        for grp in by_risk_acc.values():
            grp["states"] = dict(grp["states"])
            by_risk.append(grp)
        by_risk.sort(key=lambda g: (-g["thread_count"], g["risk_event_id"] or ""))

        # —— 总览聚合（状态/对手方分布 + 升级件数）——
        by_state: dict[str, int] = defaultdict(int)
        by_cp: dict[str, int] = defaultdict(int)
        escalated = 0
        for r in threads:
            by_state[r.get("state") or "(空)"] += 1
            by_cp[r.get("counterparty_type") or "(空)"] += 1
            if (r.get("escalation_level") or 0) > 0:
                escalated += 1

        payload: dict[str, Any] = {
            "world": world, "role": x_role, "available": True,
            "count": len(threads), "threads": threads, "by_risk": by_risk,
            "summary": {"by_state": dict(by_state), "by_counterparty_type": dict(by_cp),
                        "escalated": escalated}}
        # X-Role 脱敏：整载荷过同一 SensitiveFieldMasker（与 /objects 一套权限）。线程列本身无具名
        # 敏感字段；富化的 task.proposal_params.est_cost_usd 走本体嵌套规则对无权角色掩码（递归生效）。
        SensitiveFieldMasker(load_ontology(), x_role).mask_value(payload)
        return payload

    return router
