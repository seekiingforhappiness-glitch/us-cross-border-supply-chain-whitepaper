#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人类决策 HTTP 通道（A-1，Daniel 裁决 V13①，docs/control-tower-plan-v0.2.md §4）。

一句话：为冻结区四个「人类决策」动作开一条**人类专用**的 POST 通道，让驾驶舱前端能直接
按批准/驳回，而 AI 任何形态仍不可触达这四个动作。

为什么单开这条通道、它与 /actions 的关系（≤5 行）：
  · 冻结区四动作——审批 ApproveMitigation / 关闭 CloseRiskEvent / 准入批 ApproveQuoteDecision /
    拒接 RejectOrRequestMoreInfo——本体声明 ai_executable="frozen"：AI 不可执行（全局红线3）。
  · POST /actions/{name}（AI 面的平行验证通道）对这四个动作**维持 404**——它们从不进 /actions
    白名单（exposed_as_tool=false），也从不进 MCP 工具面 build_tool_defs（暴露集 ∩ frozen == ∅）。
  · 本通道 POST /decisions/{name} 只存在于 HTTP API 供人用的前端调用，与 MCP/AI 工具面物理隔离；
    白名单**恰为** frozen 集（声明驱动，不手写四项），白名单外一律 404。

与 /actions 的关键差异——X-Actor 必填（真实决策人 id）：
  · /actions 用常量 API_ACTOR（那是 AI 面的平行验证通道，无人类身份）；
  · /decisions 是人类决策通道，审计留痕与 maker-checker（提案人≠审批人的双人复核）都靠真实
    actor id，故 X-Actor 缺失即 422，端点只透传、绝不代填。

不建第二写路径：写入原样走 app.actions / app.admission_actions 既有函数（maker-checker、门禁、
审计逻辑一字不改，与 /actions 复用同一 _resolve_action_func 解析器）。路由工厂由 main.py 尾部
注入依赖挂载（同 cockpit：本模块不反向 import main → 零循环导入）。
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from fastapi import APIRouter, Body, Depends, Header, HTTPException

import app.actions as app_actions                                       # 写连接管理（connect，与 /actions 同源）
from app.command_bus import execute_command                            # 波2 写总线（spec §一.4 /decisions 门）
from pipeline.ontology_runtime import build_role_perms, load_ontology, snake_case


def _frozen_actions(ontology: dict) -> list[dict]:
    """冻结区动作 = ai_executable=="frozen"（当前恰四个：A5/A6/B5/B6）。声明驱动，不手写清单。"""
    return [a for a in ontology["actions"] if a.get("ai_executable") == "frozen"]


def _decision_alias_map(ontology: dict) -> dict[str, dict]:
    """{动作名(PascalCase): action 字典, snake_case(动作名): action 字典}——仅冻结区四动作在此出现。
    白名单外动作（含 6 个 exposed 动作与其余 never 动作）不在此字典 ⇒ POST /decisions/{name} 对它们
    天然 404（"路由生成层就不存在"，与 main.py ACTION_BY_ALIAS 同一保证方式）。两种别名都收录：
    GET /ontology 里 actions[].name 是 PascalCase，前端/脚本用任一拼法都应能调用。"""
    out: dict[str, dict] = {}
    for a in _frozen_actions(ontology):
        out[a["name"]] = a
        out[snake_case(a["name"])] = a
    return out


def build_decisions_router(get_db_path: Callable, resolve_action_func: Callable, as_of: str) -> APIRouter:
    """路由工厂（main.py 尾部挂载，注入式依赖，不反向 import main → 零循环导入，同 cockpit）。

    resolve_action_func = main._resolve_action_func（snake_case(动作名) → app.actions /
    app.admission_actions 实现函数），与 /actions **复用同一解析器**，不建平行映射。
    as_of = main.AS_OF（仿真时钟，config/datagen.yaml window.as_of 单一来源，不新增平行日期常量）。

    启动期 fail-fast（照抄 main.py `_unresolved` 自检模式）：冻结区动作解析不到实现函数则拒绝起服务
    ——防「本体声明为 frozen」与「app 层其实没有该函数」这种声明-实现悄悄漂移。"""
    ontology = load_ontology()
    alias_map = _decision_alias_map(ontology)

    _unresolved = [a["name"] for a in _frozen_actions(ontology) if resolve_action_func(a["name"]) is None]
    if _unresolved:
        raise RuntimeError(
            f"本体声明 ai_executable=frozen 但未在 app.actions/app.admission_actions 找到实现函数："
            f"{_unresolved}（人类决策通道声明-实现漂移，需先修复再起服务）")

    router = APIRouter(prefix="/decisions", tags=["decisions"])

    @router.post("/{name}")
    def post_decision(name: str, body: dict = Body(default={}),
                      x_role: str = Header(default="ops", alias="X-Role"),
                      x_actor: str | None = Header(default=None, alias="X-Actor"),
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                      db_path: str = Depends(get_db_path)) -> dict:
        """人类决策通道：仅冻结区四动作。X-Role 鉴权 + X-Actor 必填（真实决策人）；写入原样走 app
        层既有函数（maker-checker/门禁/审计不变）。冻结区/白名单外动作 → 404；无权 → 403；参数或
        maker-checker/前置不满足 → 4xx 携 app 层 error 原文；成功原样返回函数结果。"""
        action = alias_map.get(name)
        if action is None:                              # 非冻结区动作 = 本通道白名单外，路由生成层就不认
            allowed = sorted({a["name"] for a in _frozen_actions(load_ontology())})
            raise HTTPException(
                404, detail=f"未知或非人类决策动作 '{name}'——本通道仅冻结区四动作 {allowed} 可 POST"
                            "（这四个是只能由人拍板的审批/关闭/拒接；AI 可执行的准备类动作请走 /actions）")

        # X-Actor 必填（与 /actions 的关键差异）：这是人类决策通道，审批留痕与「提案人≠审批人」的
        # 双人复核都要靠真实决策人身份 id，系统不替你代填。缺失即 422 并用白话中文说清为什么必须带。
        if x_actor is None or not x_actor.strip():
            raise HTTPException(
                422, detail="缺少 X-Actor 请求头：这是人类决策通道，必须带上做这次决定的人的身份 id。"
                            "审批要留痕『谁批的』，系统还要用它挡住『提案人自己批自己』（双人复核），"
                            "所以这个身份必须由你带上，系统不替你代填。")
        actor = x_actor.strip()

        # V22③ 审批理由必填（Daniel 批，缘起 L-UX 轮2 王总"万把刀的处置点一下就落地，连为什么都不用写"）：
        # ApproveMitigation 的审批理由（comment）在批准/驳回两条路径都必须非空（trim 后）——理由随 comment
        # 透传到 app 层原函数，落 action_log 审计（_log 记 comment）与处置记忆 decision_note（批注），
        # maker-checker/门禁/审批绑指纹一行不动。只补 ApproveMitigation 这一处：其余三个冻结动作的理由
        # 已由各自 app 层函数强制非空（ApproveQuoteDecision.decision_reason / RejectOrRequestMoreInfo.
        # rejection_reason / CloseRiskEvent.resolution_summary，缺失即 _fail→下方映射 422），在边界重复设卡
        # 只会与 app 层双拦造成口径分叉，故不做。放在 X-Actor 校验之后：无身份仍先 422 报身份（既有语义不变）。
        if action["name"] == "ApproveMitigation":
            reason = body.get("comment")
            if not isinstance(reason, str) or not reason.strip():
                raise HTTPException(
                    422, detail="请写一句为什么批准 / 驳回——这条理由会记入审计与处置记忆（谁批的、"
                                "为什么批），是这笔处置日后复盘与 AI 放权评估的依据，系统不替你留空放行。")

        fn = resolve_action_func(action["name"])
        if fn is None:  # pragma: no cover —— 启动期 fail-fast 已保证不会到这，此处仅防御性兜底
            raise HTTPException(
                500, detail=f"动作 '{action['name']}' 冻结区声明但未找到实现函数（内部配置错误）")

        # X-Role 经 build_role_perms 鉴权（与 main.post_action 同款）：无论 permitted 与否都照常调用
        # fn——它会用同一份权限数据再判一次并在拒绝时走自己的 _denied()（写 action_log denied 审计）。
        # 这里独立算 permitted 只为选 HTTP 状态码，不重建一条平行写路径、不绕过 app 层鉴权。
        permission_key = action.get("permission_key", action["name"])
        allowed_roles = build_role_perms(load_ontology()).get(permission_key, set())
        permitted = x_role in allowed_roles

        # 波2：写入经 execute_command（写总线，spec §一.4 /decisions 门）——落 commands 台账 +
        # 审批绑提案指纹（approve 执行前反查 propose 命令指纹 vs 任务当前提案现算指纹，不一致=提案
        # 在审批间隙被改 → ok=False 白话错误，下方按既有 ok=False 路径映射 422）+ Idempotency-Key 透传。
        # maker-checker / 门禁 / 审计仍在 fn 原函数内（总线不代判）。在飞幂等冲突 → 总线抛 _StateConflict
        # → main.py 注册的处理器映射 409。签名不匹配 TypeError 仍就地映射 422（向后兼容）。
        con = app_actions.connect(db_path)
        try:
            try:
                result = execute_command(con, action=action["name"], params=body,
                                         actor=actor, role=x_role, as_of=as_of,
                                         action_func=fn, idempotency_key=idempotency_key)
            except TypeError as exc:
                # 请求体键与动作签名不匹配（缺字段/多字段/在 body 里私带 actor/role/as_of 均落此）。
                raise HTTPException(
                    422, detail=f"请求体参数与动作 '{action['name']}' 签名不匹配：{exc}")
        finally:
            con.close()

        if not permitted:                              # 无权：app 层已 _denied 留痕，此处映射 403
            raise HTTPException(
                403, detail=result.get("error") or f"角色 '{x_role}' 无权执行 '{action['name']}'（已记录审计）")
        if not result.get("ok", False):                # 有权但被拒（maker-checker/门禁/前置不满足）
            raise HTTPException(422, detail=result.get("error") or f"动作 '{action['name']}' 执行未通过")
        return result                                  # 成功：原样返回 {ok, object_id, side_effects, error}

    return router
