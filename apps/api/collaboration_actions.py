#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协作流人类协调通道（V22②，决策日志 docs/control-tower-plan-v0.2.md V22 条）。

一句话：为 CL1 协调回路的「对既有线程做状态机转移」五动作（催办 / 记回应 / 升级 / 达成 / 谈崩）
开一条**人类专用** POST 通道，让驾驶舱前端能直接点一下催一下，而 AI 任何形态仍不可触达这些写动作。

缘起：docs/research/2026-07-19-ux-stranger-round2.md 陌生人「李珊」任务3 完全失败——协作流只读断头路，
「系统里没有任何地方能让我实际点一下催一下」。此通道把 coordination_actions 六动作里对既有线程操作的
五个经 Command 总线暴露给人类 HTTP，权限 / 审计 / 幂等 / 状态机不变量照旧（coordination_actions 一行不改）。

为什么这样建、它与 /decisions 的关系（≤5 行，AGENTS.md §4）：
  · 完全复用 apps/api/decisions.py 的人类通道模式——白名单声明驱动 + X-Actor 必填 + 经
    app.command_bus.execute_command（写总线，落 commands 台账 + Idempotency-Key 幂等）+ 白话错误信封。
  · 白名单**恰为** coordination_actions.COORD_TRANSITIONS 的五个键（对既有线程的转移动作），
    与本体 permission_key=ManageCoordination 取交集双确认；OpenCoordination（开新线程）不在
    COORD_TRANSITIONS → 天然排除（本批不做，范围控制，报告挂账）。白名单外一律 404。
  · AI 面零暴露：这五个动作本体 exposed_as_tool=false / ai_executable=never，从不进 /actions 白名单、
    从不进 MCP build_tool_defs——本通道是它们唯一的 HTTP 暴露面，只对人开、与 AI 面物理隔离。
  · 权限 = COORD_PERMS["ManageCoordination"]（{ops,cs,procurement,finance}），越权走 coordination_actions
    自己的 _denied 语义（写 action_log denied 审计），本层只据同一份权限数据选 HTTP 状态码、不重建鉴权。

不建第二写路径：写入原样走 app.coordination_actions 既有函数（状态机 / 权限 / 审计逻辑一字不改，
与 /decisions 复用同一 execute_command 写总线）。路由工厂由 main.py 尾部注入挂载（同 cockpit /
decisions / collaboration：本模块不反向 import main → 零循环导入；复用 main 的 get_db_path ⇒ X-World
双世界与测试 dependency_overrides 自动生效）。
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Body, Depends, Header, HTTPException

import app.actions as app_actions                       # 写连接管理（connect，与 /decisions 同源）
import app.coordination_actions as coordination_actions  # CL1 动作层（只调用，语义/状态机/权限一行不改）
from app.command_bus import execute_command             # 波2 写总线（spec §一.4；与 /actions、/decisions 同门）
from pipeline.ontology_runtime import load_ontology, snake_case


def _coordination_write_actions(ontology: dict) -> list[dict]:
    """白名单动作 = 本体 permission_key==ManageCoordination 且 snake_case(name) 在
    coordination_actions.COORD_TRANSITIONS（即对既有线程做状态机转移的五动作）。声明驱动、不手写清单
    （同 decisions.py 从 ai_executable=frozen 集导出）；两个声明源（本体权限键 + 动作层转移表）取交集
    双确认，OpenCoordination（开新线程，不在 COORD_TRANSITIONS）自然落选，白名单外一律 404。"""
    return [a for a in ontology["actions"]
            if a.get("permission_key") == "ManageCoordination"
            and snake_case(a["name"]) in coordination_actions.COORD_TRANSITIONS]


def _alias_map(ontology: dict) -> dict[str, dict]:
    """{动作名(PascalCase): action 字典, snake_case(动作名): action 字典}——仅五个协调写动作在此出现。
    白名单外动作（含 OpenCoordination 与其余非协调动作）不在此字典 ⇒ 端点天然 404（"路由生成层就不
    存在"，与 decisions.py / main.py ACTION_BY_ALIAS 同一保证方式）。两种拼法都收录：本体 name 是
    PascalCase，前端/脚本用 snake_case（record_outreach…）调用更顺手，两者都应能路由。"""
    out: dict[str, dict] = {}
    for a in _coordination_write_actions(ontology):
        out[a["name"]] = a
        out[snake_case(a["name"])] = a
    return out


def build_collaboration_actions_router(get_db_path: Callable, as_of: str) -> APIRouter:
    """路由工厂（main.py 尾部挂载，注入式依赖，不反向 import main → 零循环导入，同 decisions）。

    get_db_path = main.get_db_path（X-World 双世界解析：sim→模拟库、verify→验证库；与 /actions、
    /decisions 同一机制，测试 dependency_overrides 自动生效）。as_of = main.AS_OF（仿真时钟单一来源）。

    启动期 fail-fast（照抄 decisions.py `_unresolved` 自检模式）：白名单动作在 coordination_actions
    解析不到实现函数则拒起服务——防「本体声明为 ManageCoordination 转移动作」与「动作层其实没有该
    函数」这种声明-实现悄悄漂移。"""
    ontology = load_ontology()
    alias_map = _alias_map(ontology)

    _unresolved = [a["name"] for a in _coordination_write_actions(ontology)
                   if not callable(getattr(coordination_actions, snake_case(a["name"]), None))]
    if _unresolved:
        raise RuntimeError(
            f"本体声明 permission_key=ManageCoordination 且在 COORD_TRANSITIONS，但未在 "
            f"app.coordination_actions 找到实现函数：{_unresolved}（协调通道声明-实现漂移，需先修复再起服务）")

    router = APIRouter(prefix="/collaboration", tags=["collaboration"])

    @router.post("/threads/{coordination_id}/actions/{action_name}")
    def post_coordination_action(
            coordination_id: str, action_name: str, body: dict = Body(default={}),
            x_role: str = Header(default="ops", alias="X-Role"),
            x_actor: str | None = Header(default=None, alias="X-Actor"),
            idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
            db_path: str = Depends(get_db_path)) -> dict:
        """人类协调通道：仅五个协调写动作（催办 / 记回应 / 升级 / 达成 / 谈崩）。coordination_id 走路径
        （操作既有线程），动作参数走 body。X-Role 鉴权 + X-Actor 必填（真实操作人，审计留痕『谁催的』）；
        写入原样走 coordination_actions 既有函数（状态机 / 权限 / 审计不变）。白名单外动作 → 404；无权 →
        403（app 层已 _denied 留痕）；非法状态转移 / 缺必填参数 → 422 携 app 层 error 原文；成功原样返回。"""
        action = alias_map.get(action_name)
        if action is None:                              # 非协调写动作 = 本通道白名单外，路由生成层就不认
            allowed = sorted({a["name"] for a in _coordination_write_actions(load_ontology())})
            raise HTTPException(
                404, detail=f"未知或非协调线程动作 '{action_name}'——本通道仅对既有线程操作的五动作 "
                            f"{allowed} 可 POST（开新线程 OpenCoordination 本批未接，仍走 Streamlit 操作台）。")

        # X-Actor 必填（与 /decisions 同款差异）：这是人工协调通道，审计要留痕『谁催的 / 谁记的回应』，
        # 系统不替你代填。缺失即 422 并用白话中文说清为什么必须带。
        if x_actor is None or not x_actor.strip():
            raise HTTPException(
                422, detail="缺少 X-Actor 请求头：这是人工协调通道，必须带上做这次操作的人的身份 id。"
                            "催办 / 记回应 / 升级都要留痕『谁做的』，所以这个身份必须由你带上，系统不替你代填。")
        actor = x_actor.strip()

        fn = getattr(coordination_actions, snake_case(action["name"]))

        # X-Role 鉴权（与 main.post_action / post_decision 同款）：直接取 coordination_actions.COORD_PERMS
        # 这份唯一权限数据（不 build_role_perms 另算一份平行的）。无论 permitted 与否都照常调用 fn——它会
        # 用同一份 COORD_PERMS 再判一次并在拒绝时走自己的 _denied()（写 action_log denied 审计）。这里独立
        # 算 permitted 只为选 HTTP 状态码，不重建平行写路径、不绕过 app 层鉴权。
        permitted = x_role in coordination_actions.COORD_PERMS["ManageCoordination"]

        # coordination_id 来自路径（操作既有线程），合并进 params 交给动作函数；body 里若私带
        # coordination_id 会被路径值覆盖（路径是权威）。若 body 私带 actor/role/as_of 或多余键，
        # execute_command 以 fn(con,**params,actor=,role=,as_of=) 调用会撞重复/未知关键字 → TypeError → 422。
        params = {**body, "coordination_id": coordination_id}

        # 波2：写入经 execute_command（写总线，spec §一.4）——落 commands 台账 + Idempotency-Key 透传实现
        # 幂等（同键重放原样返回首次结果，不重复执行）。协调五动作不在 BINDING_CHECKED_ACTIONS，故无审批
        # 指纹绑定；maker-checker/状态机/审计仍在 coordination_actions 原函数内（总线不代判）。在飞幂等冲突
        # → 总线抛 _StateConflict → main.py 注册的处理器映射 409。签名不匹配 TypeError 就地映射 422。
        con = app_actions.connect(db_path)
        try:
            try:
                result = execute_command(con, action=action["name"], params=params,
                                         actor=actor, role=x_role, as_of=as_of,
                                         action_func=fn, idempotency_key=idempotency_key)
            except TypeError as exc:
                raise HTTPException(
                    422, detail=f"请求体参数与动作 '{action['name']}' 签名不匹配：{exc}")
        finally:
            con.close()

        if not permitted:                              # 无权：app 层已 _denied 留痕，此处映射 403
            raise HTTPException(
                403, detail=result.get("error")
                or f"角色 '{x_role}' 无权执行 '{action['name']}'（协调权限组 ManageCoordination，已记录审计）")
        if not result.get("ok", False):                # 有权但被拒（非法状态转移 / 缺必填参数 / 线程不存在）
            raise HTTPException(422, detail=result.get("error") or f"动作 '{action['name']}' 执行未通过")
        return result                                  # 成功：原样返回 {ok, object_id, side_effects, error}

    return router
