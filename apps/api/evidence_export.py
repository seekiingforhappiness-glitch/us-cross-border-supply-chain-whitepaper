#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V23④ 证据包导出——单风险证据包（GET /risk-events/{id}/evidence-package?format=json|html）。

缘起（决策日志 V23④，Daniel 批"做导出"）：L-UX 轮3 合规陌生人"审计轨迹导不出去没法交差；
海关要材料难道截图"。给一条风险事件生成一份**可带走**的证据包（对象快照+影响链+关联任务+
时间线+协调往来+同类先例+导出元信息），json 结构化 / html 自包含单文件（浏览器可打印为 PDF）。

一句话（白话）：把驾驶舱里一条风险"从头到尾发生了什么、牵连了谁、谁处置的、历史上同类怎么办的"
打成一份文件，脱敏跟随导出人的角色（导出的包里就是掩码值、不旁路），导出这个动作本身也留审计。

七块内容（全部现查现算、逐块可缺省诚实标注——算不出/无数据一律如实标 available:false 或空态，
绝不编造、绝不 0 冒充"无"）：
  ① risk_snapshot   风险事件对象快照（脱敏后字段 + 白话中文名）——**复用对象读端点同一掩码路径**
                    （SensitiveFieldMasker，object_type='RiskEvent'）。
  ② impact          影响链——**复用波E evidence.impact_block**（受影响订单行/金额/客户；R19/R21 付款锚归并）。
  ③ tasks           关联任务与提案（含审批状态/理由/审批人）——tasks + action_log(ApproveMitigation) 现查。
  ④ timeline        时间线——action_log 该风险及其任务相关行（含 trace_id 血缘），按时间正序。
  ⑤ coordination    协调线程及往来——coordination_threads（该风险名下），对外催办/回应台账。
  ⑥ precedents      同类先例摘要——**复用波E evidence.precedents_block**（resolution_memory 同规则+同航线）。
  ⑦ export_meta     导出元信息（导出人 X-Actor / 角色 / 时间 as_of / 世界 verify|sim）——sim 显著标"模拟数据"。

红线（本模块的存在理由）：
  · **脱敏跟随请求角色、按声明执行（V25 裁决3 与对象读端点对齐）**：对象快照走 SensitiveFieldMasker(object_type)（对象读
    端点同款）；聚合金额（affected_value_usd / impact.amount_usd / 先例 impact_usd / 提案 est_cost_usd 等
    _usd 键）走 cockpit._mask_money（成本门 _can_see_cost，与波E build_evidence 同规、V21① 边界内全量掩、
    不套自队例外）；自由文本 "$金额" 走 cockpit._mask_text_amounts（轮3-A 收紧同款）。三层皆复用、无第二份规则。
  · **纯读聚合 + 一条审计写**：证据七块只 SELECT；唯一的写是导出行为落 action_log（action=
    ExportEvidencePackage）——**复用 app.actions.connect + 直写 action_log 的控制面审计模式**（照抄
    runtime._audit：独立写连接、独立事务、与业务写物理分开；审计失败不炸主流程）。ExportEvidencePackage
    是**控制面审计动作**非业务动作，与 runtime 的 StartAgentRun/KillAgentRun 同类——不入本体 actions 声明
    （ontology_lint 只比对 actions[]，不扫 action_log 值；无声明即无 lint 断言，与 runtime 控制面审计同保证）。
  · **不重写已存在的工具函数**（AGENTS.md §5）：影响链/先例块复用 evidence.py；金额/文本掩码复用 cockpit；
    航线派生复用 engine.resolution_memory.lane_for_shipment；规则/状态/质量白话复用 app.ux_copy。

为什么这样建（≤5 行，AGENTS.md §4）：
  · 包的装配逻辑是**纯函数**（build_evidence_package + render_package_html），不依赖 FastAPI 请求上下文——
    便于测试直接调、便于 html/json 两格式共用同一份 dict（json 直返，html 是同一 dict 的自包含渲染）。
  · html 自包含=内联 CSS、零外链（无 <script src>/<link>/远程 <img>）——克制样式、可浏览器打印为 PDF；
    world=sim 时加"模拟数据"水印（不变量11：sim 数据必须显著标注，防真伪混淆）。
  · 路由工厂由 main.py 尾部注入挂载（同 cockpit/evidence/runtime：本模块不反向 import main → 零循环导入）；
    读走注入的 get_ro_connection（GET mode=ro 物理只读），审计写另开 app.actions.connect(db_path)（写连接）。
"""
from __future__ import annotations

import html as _html
import json
import sqlite3
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

import app.actions as app_actions                    # 写连接管理（connect）+ 控制面审计落 action_log（同 runtime）
from agent.mcp_server import SensitiveFieldMasker     # 对象读端点同款字段脱敏（object_type 作用域）
from agent.tools import MASK, _can_see_cost           # 掩码值 + 成本可见门（与 cockpit/evidence 同一权威源）
# 复用 cockpit 既有纯 helper（AGENTS.md §5 不重写）：金额聚合掩码 / 自由文本金额掩码。
from apps.api.cockpit import _mask_money, _mask_text_amounts
# 复用波E evidence 既有块（影响链 + 先例，含 R19/R21 付款锚归并与 resolution_memory 检索的全部诚实空态）。
from apps.api.evidence import impact_block, precedents_block
# 复用 engine 只读 helper（航线派生，先例块的第二维度）+ app 层白话映射（规则/状态/质量中文名）。
from engine.resolution_memory import lane_for_shipment
from pipeline.ontology_runtime import load_ontology

try:
    from app import ux_copy                           # 复用既有白话映射（RULE_CN/STATUS_CN/QUALITY_LABEL_CN）
except ImportError:                                    # pragma: no cover —— 包上下文缺失兜底（与 app 层惯例一致）
    import ux_copy                                     # type: ignore


# ── 风险事件字段白话中文名（①的"白话中文名"）─────────────────────────────────
# 为什么在此新建：objectLabels.ts 是前端 TS（语言边界无法 import）、ux_copy.py 无字段名→中文表
# （standard_object_view 直接用 snake_case 列名，见 objectLabels.ts 顶注核实结论）——故 RiskEvent 的
# 19 字段中文名在 Python 侧属**新增**。仅覆盖 RiskEvent（本导出的主对象），值语义仍从既有权威源humanize
# （规则名/状态/质量走 ux_copy），只补"字段名→中文"这一层，未登记字段兜底原样 snake_case（不编造）。
RISK_FIELD_LABELS_CN: dict[str, str] = {
    "risk_event_id": "风险编号",
    "type": "风险类型",
    "rule_id": "触发规则",
    "severity": "严重度",
    "shipment_id": "关联货件",
    "po_id": "关联采购单",
    "supplier_id": "关联供应商",
    "warehouse_id": "关联仓库",
    "affected_po_line_ids": "受影响采购行",
    "affected_so_line_ids": "受影响订单行",
    "affected_invoice_line_ids": "受影响发票行",
    "affected_sku_ids": "受影响 SKU",
    "affected_value_usd": "影响金额（美元）",
    "detected_at": "检出时间",
    "root_cause": "根因",
    "status": "处置状态",
    "resolved_at": "关闭时间",
    "outcome": "处置结果",
    "resolution_summary": "处置小结",
}

# 导出格式白名单（fail-fast 白话，同 X-World 惯例）。
_FORMATS = ("json", "html")


# ═══════════════════════════════════════════════════════════════════════════
# 小工具（表存在性 / JSON 解析 / 白话）
# ═══════════════════════════════════════════════════════════════════════════
def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def _loads(raw: Any) -> Any:
    """action_log.params_json / tasks.proposal_params 存 JSON 字符串 → dict；空→{}；坏值原样保留串
    （不放大为故障，掩码层对 str 也安全）。让内部 _usd 键能被 _mask_money 掩到（防时间线/提案参数漏金额）。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _rule_cn(rule_id: Any) -> str | None:
    return ux_copy.RULE_CN.get(rule_id) if rule_id else None


def _status_cn(status: Any) -> str | None:
    return ux_copy.STATUS_CN.get(status, status) if status else None


# ═══════════════════════════════════════════════════════════════════════════
# ① risk_snapshot —— 风险事件对象快照（对象读端点同款掩码路径 + 白话中文名）
# ═══════════════════════════════════════════════════════════════════════════
def _risk_snapshot(con: sqlite3.Connection, risk: dict, masker: SensitiveFieldMasker) -> dict:
    """风险对象快照：原始行 → SensitiveFieldMasker(object_type='RiskEvent')（对象读端点 get_object 同一
    掩码路径，V23③ 组式规则按类型作用域）。附字段白话中文名映射（①要求）+ 关键值 humanize（规则/状态
    白话，复用 ux_copy）。affected_value_usd 等 _usd 聚合金额此处不掩——装配末端统一过 _mask_money 成本门
    （与 cockpit/evidence 同规，见 build_evidence_package）。"""
    fields = dict(risk)
    masker.mask_value(fields, object_type="RiskEvent")   # 对象读端点同款：具名/嵌套/组式规则脱敏
    # 白话中文名用 [{field, label}] 对列表而非 {字段名: 中文} 字典——字段名本身以 _usd 结尾时，
    # 字典形态会被末端 _mask_money 误把"中文标签"当金额掩掉（浏览器实测抓到：affected_value_usd
    # 的标签被掩成锁串）；对列表的键是 field/label，不落金额键网，标签恒完整。
    labels = [{"field": k, "label": RISK_FIELD_LABELS_CN.get(k, k)} for k in fields]
    return {
        "object_type": "RiskEvent", "object_type_cn": "风险事件",
        "id": risk.get("risk_event_id"),
        "fields": fields,
        "field_labels_cn": labels,
        "rule_cn": _rule_cn(risk.get("rule_id")),
        "status_cn": _status_cn(risk.get("status")),
    }


# ═══════════════════════════════════════════════════════════════════════════
# ③ tasks —— 关联任务与提案（审批状态/理由/审批人）
# ═══════════════════════════════════════════════════════════════════════════
_TASK_WISHLIST = ("task_id", "risk_event_id", "title", "status", "approval_status",
                  "approved_by_role", "proposed_action", "proposal_params", "priority",
                  "assignee_role", "proposal_actor_id", "proposal_actor_role", "action_taken")


def _approval_trail(con: sqlite3.Connection, tables: set[str], task_id: str) -> dict | None:
    """审批理由/审批人：从 action_log 的 ApproveMitigation 行现查（该动作把 comment 落 params_json、
    审批人落 actor/role——单一来源，不另建审批表）。取该任务最近一条 ApproveMitigation；无 → None
    （诚实：还没人审批过，非"隐藏"）。理由=params.comment、审批人=actor、审批角色=role、决定=params.decision。"""
    if "action_log" not in tables:
        return None
    row = con.execute(
        "SELECT actor, role, params_json, timestamp, result FROM action_log "
        "WHERE target_object_id=? AND action='ApproveMitigation' "
        "ORDER BY timestamp DESC, log_id DESC LIMIT 1", (task_id,)).fetchone()
    if row is None:
        return None
    params = _loads(row["params_json"])
    decision = params.get("decision") if isinstance(params, dict) else None
    comment = params.get("comment") if isinstance(params, dict) else None
    return {"approver_actor": row["actor"], "approver_role": row["role"],
            "decision": decision, "reason": comment,
            "decided_at": row["timestamp"], "result": row["result"]}


def _tasks_block(con: sqlite3.Connection, tables: set[str], risk_event_id: str,
                 masker: SensitiveFieldMasker) -> dict:
    """关联任务与提案。tasks（该风险名下）逐个：提案动作/参数 + 审批状态 + 审批理由/审批人（_approval_trail）。
    proposal_params 走 SensitiveFieldMasker(object_type='Task')（est_cost_usd 嵌套规则对无权角色掩码，
    与协作流富化同款）。缺 tasks 表/0 条 → 诚实空态（available/count），不 500。"""
    if "tasks" not in tables:
        return {"available": False, "count": 0, "items": [],
                "reason": "该世界无 tasks 表——无关联处置任务。"}
    cols = _columns(con, "tasks")
    sel = [c for c in _TASK_WISHLIST if c in cols]
    rows = con.execute(
        f"SELECT {','.join(sel)} FROM tasks WHERE risk_event_id=? ORDER BY task_id",
        (risk_event_id,)).fetchall()
    items: list[dict] = []
    for r in rows:
        d = dict(r)
        if "proposal_params" in d:
            d["proposal_params"] = _loads(d["proposal_params"])
        masker.mask_value(d, object_type="Task")     # 提案参数 est_cost_usd 嵌套脱敏（对象读端点同规）
        d["approval"] = _approval_trail(con, tables, d.get("task_id"))
        d["proposed_action_cn"] = ux_copy.RULE_CN.get(d.get("proposed_action"))  # 兜底 None（动作码非规则码，多数无译）
        d["status_cn"] = _status_cn(d.get("status"))
        d["approval_status_cn"] = _status_cn(d.get("approval_status"))
        items.append(d)
    return {"available": True, "count": len(items), "items": items}


# ═══════════════════════════════════════════════════════════════════════════
# ④ timeline —— action_log 该风险及其任务相关行（trace_id 血缘）
# ═══════════════════════════════════════════════════════════════════════════
def _timeline_block(con: sqlite3.Connection, tables: set[str], risk_event_id: str,
                    task_ids: list[str]) -> dict:
    """时间线：action_log 中 target_object_id ∈ {风险号} ∪ {该风险名下任务号} 的行，按时间正序。
    附 trace_id（血缘：AI 触发的写动作可拼回 llm_calls）。params_json 解析成 dict → 末端 _mask_money
    能掩其内 _usd 键（防提案参数金额从时间线漏）。缺 action_log 表 → 诚实空态。"""
    if "action_log" not in tables:
        return {"available": False, "count": 0, "items": [],
                "reason": "该世界无 action_log 表——无审计时间线。"}
    ids = [risk_event_id] + [t for t in task_ids if t]
    ph = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT log_id, actor, role, action, target_object_id, params_json, "
        f"as_of_date, timestamp, result, trace_id FROM action_log "
        f"WHERE target_object_id IN ({ph}) ORDER BY timestamp, log_id", ids).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["params"] = _loads(d.pop("params_json"))    # 解析成结构 → 末端 _mask_money 覆盖其内金额键
        d["action_cn"] = _ACTION_CN.get(d.get("action"), d.get("action"))
        items.append(d)
    return {"available": True, "count": len(items), "items": items}


# 时间线动作白话（覆盖本闭环高频动作 + 本模块的导出审计动作；未登记兜底原样，不编造）。
_ACTION_CN = {
    "AssignTask": "派单", "ProposeMitigation": "提交处置提案", "ApproveMitigation": "审批处置提案",
    "CloseRiskEvent": "关闭风险", "ProposeCollection": "催收提案", "RecordPayment": "记录收付",
    "CreateRiskEvent": "创建风险", "IngestMilestone": "摄入里程碑",
    "OpenCoordination": "发起协调", "RecordOutreach": "记录催办", "RecordResponse": "记录回应",
    "EscalateCoordination": "升级协调", "ResolveCoordination": "协调达成", "MarkDeadEnded": "协调谈崩",
    "StartAgentRun": "启动 AI 处置", "ResumeAgentRun": "续跑 AI 处置", "KillAgentRun": "急停 AI 处置",
    "ExportEvidencePackage": "导出证据包",
}


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ coordination —— 协调线程及往来（该风险名下）
# ═══════════════════════════════════════════════════════════════════════════
_COORD_WISHLIST = ("coordination_id", "task_id", "risk_event_id", "counterparty_type",
                   "counterparty_ref", "ask", "state", "followup_count", "escalation_level",
                   "owner", "next_action_due", "last_response", "outcome", "opened_at", "last_update")
_COUNTERPARTY_CN = {"supplier": "供应商", "forwarder": "货代", "customs_broker": "报关行",
                    "bank": "银行", "customer": "客户"}


def _coordination_block(con: sqlite3.Connection, tables: set[str], risk_event_id: str) -> dict:
    """协调线程及往来：coordination_threads 该风险名下的对外协调台账（催办次数/升级级别/最近回应/诉求）。
    缺表/0 条 → 诚实空态（available/count；0 条是常态，多数风险无对外协调，绝不 500/绝不编）。
    线程列本身无具名敏感字段（金额不落协调），末端 _mask_money 仍会兜底扫过（无 _usd 键=无操作）。"""
    if "coordination_threads" not in tables:
        return {"available": False, "count": 0, "items": [],
                "reason": "该世界无 coordination_threads 表——协调回路域未接入。"}
    cols = _columns(con, "coordination_threads")
    sel = [c for c in _COORD_WISHLIST if c in cols]
    rows = con.execute(
        f"SELECT {','.join(sel)} FROM coordination_threads WHERE risk_event_id=? "
        f"ORDER BY coordination_id", (risk_event_id,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["counterparty_type_cn"] = _COUNTERPARTY_CN.get(d.get("counterparty_type"))
        d["state_cn"] = _status_cn(d.get("state"))
        items.append(d)
    return {"available": True, "count": len(items), "items": items}


# ═══════════════════════════════════════════════════════════════════════════
# 装配 + 脱敏
# ═══════════════════════════════════════════════════════════════════════════
def _load_risk(con: sqlite3.Connection, tables: set[str], risk_event_id: str) -> dict | None:
    if "risk_events" not in tables:
        return None
    row = con.execute("SELECT * FROM risk_events WHERE risk_event_id=?",
                      (risk_event_id,)).fetchone()
    return dict(row) if row else None


def build_evidence_package(con: sqlite3.Connection, risk_event_id: str, role: str,
                           world: str, exporter: str | None, as_of: str,
                           fmt: str = "json") -> dict | None:
    """装配七块证据 + 末端统一脱敏。风险查无 → None（路由据此 404）。
    脱敏三层（复用既有掩码层、不开新洞）：
      1. 对象快照 SensitiveFieldMasker(object_type)（RiskEvent/Task；对象读端点同规、组式按类型作用域）；
      2. 聚合金额 cockpit._mask_money（成本门 not _can_see_cost 全量掩 _usd 键——affected_value_usd /
         impact.amount_usd / 先例 impact_usd / est_cost_usd；V21① 边界内不套自队例外，同 evidence.build_evidence）；
      3. 自由文本 cockpit._mask_text_amounts（root_cause/resolution_summary 里的 "$金额" → $•••；轮3-A 同款）。
    world=sim 显著标"模拟数据"（不变量11）。逐块诚实空态由各 *_block 自理。"""
    tables = _tables(con)
    risk = _load_risk(con, tables, risk_event_id)
    if risk is None:
        return None
    masker = SensitiveFieldMasker(load_ontology(), role)

    tasks = _tasks_block(con, tables, risk_event_id, masker)
    task_ids = [t.get("task_id") for t in tasks.get("items", [])]
    lane = lane_for_shipment(con, risk.get("shipment_id"))

    world_is_sim = world in ("simulation", "sim")
    package: dict[str, Any] = {
        "kind": "risk_evidence_package",
        "risk_event_id": risk_event_id,
        "world": world,
        "role": role,
        "risk_snapshot": _risk_snapshot(con, risk, masker),
        # ② 影响链：复用波E impact_block（含 R19/R21 付款锚归并、悬空 id 诚实标注、无订单行费用类风险 note）。
        "impact": impact_block(con, risk.get("affected_so_line_ids"), risk=risk),
        "tasks": tasks,
        "timeline": _timeline_block(con, tables, risk_event_id, task_ids),
        "coordination": _coordination_block(con, tables, risk_event_id),
        # ⑥ 先例：复用波E precedents_block（resolution_memory 同规则+同航线；样本不足/首例诚实空态）。
        "precedents": precedents_block(con, risk.get("rule_id"), lane, exclude_risk_id=risk_event_id),
        # ⑦ 导出元信息（导出人/角色/时间/世界；sim 显著标注）。
        "export_meta": {
            "exported_by": exporter,
            "role": role,
            "as_of": as_of,
            "world": world,
            "world_is_simulation": world_is_sim,
            "format": fmt,
            "simulation_notice": ("本证据包取自「模拟世界」（连续时间流的合成演示数据），"
                                  "不是真实业务数据——请勿据此对外交付/申报。" if world_is_sim else None),
            "disclaimer": ("原型级证据包：脱敏跟随导出人角色（导出的即掩码值）；数据取自本控制塔"
                           "当时快照，不构成对外法律/报关文书。"),
        },
    }

    # ── 脱敏口径（V25 裁决3，2026-07-20 Daniel"对齐：证据包按声明放开"）：
    # 原末端全量成本门（_mask_money+_mask_text_amounts）掩过了头——affected_value_usd 等**未声明**
    # 敏感的运营影响金额在对象卡明文、在证据包却被掩，同一事实两出口口径打架（轮4 合规陌生人实证）。
    # 现与对象读端点对齐：**只按本体 sensitiveFieldRules 声明掩**（各块内 masker 已执行，含组式规则），
    # 未声明字段与对象卡一致明文。已声明字段（Invoice.total_usd 等）若入包仍掩（masker 层兜住）。
    return package


# ═══════════════════════════════════════════════════════════════════════════
# 审计：导出行为落 action_log（复用 app.actions.connect 控制面审计模式，照抄 runtime._audit）
# ═══════════════════════════════════════════════════════════════════════════
def _audit_export(db_path: str, as_of: str, actor: str, role: str,
                  risk_event_id: str, fmt: str) -> None:
    """导出留痕：独立写连接、独立事务落 action_log（action=ExportEvidencePackage，target=风险号，
    params 含 format/风险号，actor/role 如实）。与业务写物理分开、绝不交叉持锁；**审计失败不炸主流程**
    （导出是读级动作，留痕缺列/缺表只丢一条痕，不该反过来让导出 500——同 runtime._audit 哲学）。
    ExportEvidencePackage 是控制面审计动作（非业务动作、非工具、不入 AI 面）——与 runtime 的
    StartAgentRun/KillAgentRun 同类，不进本体 actions 声明（ontology_lint 不扫 action_log 值）。"""
    con = app_actions.connect(db_path)
    try:
        if "action_log" not in {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
            return                          # 无审计表的世界：跳过留痕（不新建表、不阻断导出）
        params = json.dumps({"format": fmt, "risk_event_id": risk_event_id}, ensure_ascii=False)
        try:
            con.execute(
                "INSERT INTO action_log (actor, role, action, target_object_id, params_json, "
                "as_of_date, timestamp, result, trace_id) VALUES (?,?,?,?,?,?,?,?,NULL)",
                (actor, role, "ExportEvidencePackage", risk_event_id, params,
                 as_of, f"{as_of}T00:00:00Z", "ok"))
        except sqlite3.OperationalError:    # 旧库副本无 trace_id 列：回落 8 列（同 app.actions._log 兜底）
            con.execute(
                "INSERT INTO action_log (actor, role, action, target_object_id, params_json, "
                "as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)",
                (actor, role, "ExportEvidencePackage", risk_event_id, params,
                 as_of, f"{as_of}T00:00:00Z", "ok"))
        con.commit()
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════════════════
# html 自渲染（自包含单文件：内联 CSS、零外链；world=sim 加"模拟数据"水印；可浏览器打印为 PDF）
# ═══════════════════════════════════════════════════════════════════════════
def _esc(v: Any) -> str:
    """HTML 转义（防注入 + 忠实呈现）。None → —；掩码串/普通值一律转义后直呈（掩码值原样带锁 emoji）。"""
    if v is None:
        return "—"
    return _html.escape(str(v), quote=True)


def _kv_rows(fields: dict, labels: list | dict) -> str:
    """字段字典 → <tr> 行（左中文名、右值）。labels 为 [{field, label}] 对列表（见 _risk_snapshot 注），
    兼容 dict 形态；缺失兜底原字段名（不编造）。label 与字段名相同（未登记字段）时不重复渲染 code 角标。"""
    if isinstance(labels, list):
        label_map = {p.get("field"): p.get("label") for p in labels if isinstance(p, dict)}
    else:
        label_map = labels or {}
    out = []
    for k, v in fields.items():
        if k.startswith("_"):               # _validation_warnings 等内部键不进主表（避免噪声）
            continue
        label = label_map.get(k, k)
        code = f"<span class='code'>{_esc(k)}</span>" if label != k else ""
        out.append(f"<tr><th>{_esc(label)}{code}</th><td>{_esc(v)}</td></tr>")
    return "\n".join(out)


def _section(title: str, body_html: str, note: str | None = None) -> str:
    note_html = f"<p class='note'>{_esc(note)}</p>" if note else ""
    return (f"<section><h2>{_esc(title)}</h2>{note_html}{body_html}</section>")


def render_package_html(package: dict) -> str:
    """把证据包 dict 渲染成自包含单文件 HTML（内联 CSS、零外链）。克制样式、打印友好；
    world=sim 加固定"模拟数据"水印 + 顶部横幅（不变量11：显著标注模拟数据）。
    所有值经 _esc 转义（掩码值/None 忠实呈现，不二次加工事实）。"""
    meta = package.get("export_meta", {})
    risk_id = package.get("risk_event_id")
    world_is_sim = bool(meta.get("world_is_simulation"))
    snap = package.get("risk_snapshot", {})

    # ── ① 风险快照 ──
    snap_body = (f"<table class='kv'>{_kv_rows(snap.get('fields', {}), snap.get('field_labels_cn', {}))}</table>"
                 if snap.get("fields") else "<p class='empty'>风险对象快照缺失。</p>")
    rule_cn = snap.get("rule_cn")
    snap_head = (f"<p class='muted'>规则：{_esc(snap.get('fields', {}).get('rule_id'))}"
                 f"{f'（{_esc(rule_cn)}）' if rule_cn else ''}"
                 f" · 状态：{_esc(snap.get('status_cn') or snap.get('fields', {}).get('status'))}</p>")

    # ── ② 影响链 ──
    imp = package.get("impact", {})
    if imp.get("available") is False:
        impact_body = f"<p class='empty'>{_esc(imp.get('reason'))}</p>"
    else:
        rowsi = [f"<tr><th>受影响订单行</th><td>{_esc(imp.get('affected_order_lines'))}</td></tr>",
                 f"<tr><th>影响金额（美元）</th><td>{_esc(imp.get('amount_usd'))}</td></tr>",
                 f"<tr><th>波及客户数</th><td>{_esc(imp.get('affected_customers'))}</td></tr>"]
        impact_body = f"<table class='kv'>{''.join(rowsi)}</table>"
        pay_rows = imp.get("payment_rows") or []
        if pay_rows:
            prs = "".join(
                f"<tr><td>{_esc(p.get('payment_id'))}</td><td>{_esc(p.get('counterparty_id'))}</td>"
                f"<td>{_esc(p.get('ref_id'))}</td><td>{_esc(p.get('amount_usd'))}</td>"
                f"<td>{_esc(p.get('overdue_days'))}</td></tr>" for p in pay_rows)
            impact_body += ("<table class='grid'><thead><tr><th>付款</th><th>对手方</th><th>单据</th>"
                            f"<th>金额</th><th>逾期天数</th></tr></thead><tbody>{prs}</tbody></table>")
    impact_body_note = imp.get("note") or imp.get("payment_note")
    if impact_body_note:
        impact_body += f"<p class='note'>{_esc(impact_body_note)}</p>"

    # ── ③ 关联任务与提案 ──
    tk = package.get("tasks", {})
    if not tk.get("items"):
        tasks_body = f"<p class='empty'>{_esc(tk.get('reason') or '该风险名下暂无处置任务。')}</p>"
    else:
        cards = []
        for t in tk["items"]:
            ap = t.get("approval") or {}
            appr = (f"审批：{_esc(ap.get('decision') or t.get('approval_status'))}"
                    f" · 审批人 {_esc(ap.get('approver_actor'))}（{_esc(ap.get('approver_role'))}）"
                    f" · 理由：{_esc(ap.get('reason'))}" if ap else
                    f"审批状态：{_esc(t.get('approval_status_cn') or t.get('approval_status') or '—')}（暂无审批记录）")
            pp = t.get("proposal_params")
            pp_txt = _esc(json.dumps(pp, ensure_ascii=False)) if pp else "—"
            cards.append(
                f"<div class='card'><div class='card-h'><b>{_esc(t.get('task_id'))}</b>"
                f"<span class='muted'>{_esc(t.get('status_cn') or t.get('status'))}</span></div>"
                f"<p>提案动作：{_esc(t.get('proposed_action'))} · 指派 {_esc(t.get('assignee_role'))}"
                f" · 提案人 {_esc(t.get('proposal_actor_id'))}</p>"
                f"<p class='muted'>提案参数：{pp_txt}</p>"
                f"<p>{appr}</p></div>")
        tasks_body = "\n".join(cards)

    # ── ④ 时间线 ──
    tl = package.get("timeline", {})
    if not tl.get("items"):
        tl_body = f"<p class='empty'>{_esc(tl.get('reason') or '无审计时间线记录。')}</p>"
    else:
        trs = "".join(
            f"<tr><td>{_esc(e.get('timestamp'))}</td><td>{_esc(e.get('action_cn') or e.get('action'))}</td>"
            f"<td>{_esc(e.get('actor'))}（{_esc(e.get('role'))}）</td><td>{_esc(e.get('target_object_id'))}</td>"
            f"<td>{_esc(e.get('result'))}</td><td class='code'>{_esc(e.get('trace_id'))}</td></tr>"
            for e in tl["items"])
        tl_body = ("<table class='grid'><thead><tr><th>时间</th><th>动作</th><th>操作者</th>"
                   f"<th>对象</th><th>结果</th><th>血缘 trace</th></tr></thead><tbody>{trs}</tbody></table>")

    # ── ⑤ 协调线程 ──
    co = package.get("coordination", {})
    if not co.get("items"):
        co_body = f"<p class='empty'>{_esc(co.get('reason') or '该风险无对外协调线程。')}</p>"
    else:
        crs = "".join(
            f"<tr><td>{_esc(c.get('coordination_id'))}</td>"
            f"<td>{_esc(c.get('counterparty_type_cn') or c.get('counterparty_type'))} {_esc(c.get('counterparty_ref'))}</td>"
            f"<td>{_esc(c.get('ask'))}</td><td>{_esc(c.get('state_cn') or c.get('state'))}</td>"
            f"<td>{_esc(c.get('followup_count'))}</td><td>{_esc(c.get('last_response'))}</td></tr>"
            for c in co["items"])
        co_body = ("<table class='grid'><thead><tr><th>线程</th><th>对手方</th><th>诉求</th>"
                   f"<th>状态</th><th>催办次数</th><th>最近回应</th></tr></thead><tbody>{crs}</tbody></table>")

    # ── ⑥ 先例 ──
    pc = package.get("precedents", {})
    if pc.get("available") is False:
        pc_body = f"<p class='empty'>{_esc(pc.get('reason'))}</p>"
    elif pc.get("empty") or pc.get("n", 0) == 0:
        pc_body = f"<p class='empty'>{_esc(pc.get('note') or '无同类先例（首例）。')}</p>"
    else:
        eff = pc.get("effectiveness", {})
        by = pc.get("by_decision", {})
        pc_head = (f"<p>同类先例 {_esc(pc.get('n'))} 例（{_esc(pc.get('match_scope'))}）"
                   f" · 决定分布 {_esc(json.dumps(by, ensure_ascii=False))}"
                   f" · 事后有效率 {_esc(eff.get('effective_rate'))}（已结案 {_esc(eff.get('labeled'))} 例）</p>")
        exs = "".join(f"<li>{_esc(ex.get('plain'))}</li>" for ex in (pc.get("recent_examples") or []))
        pc_body = pc_head + (f"<ul>{exs}</ul>" if exs else "")
        if pc.get("sample_note"):
            pc_body += f"<p class='note'>{_esc(pc.get('sample_note'))}</p>"

    # ── ⑦ 导出元信息 ──
    meta_rows = "".join([
        f"<tr><th>导出人</th><td>{_esc(meta.get('exported_by'))}</td></tr>",
        f"<tr><th>角色</th><td>{_esc(meta.get('role'))}</td></tr>",
        f"<tr><th>数据时点 as_of</th><td>{_esc(meta.get('as_of'))}</td></tr>",
        f"<tr><th>世界</th><td>{_esc(meta.get('world'))}"
        f"{'（模拟数据）' if world_is_sim else ''}</td></tr>",
    ])
    meta_body = (f"<table class='kv'>{meta_rows}</table>"
                 f"<p class='note'>{_esc(meta.get('disclaimer'))}</p>")

    watermark = "<div class='watermark'>模拟数据 · SIMULATION</div>" if world_is_sim else ""
    sim_banner = (f"<div class='sim-banner'>⚠ {_esc(meta.get('simulation_notice'))}</div>"
                  if world_is_sim else "")

    body = "".join([
        _section("① 风险事件快照", snap_head + snap_body),
        _section("② 影响链", impact_body),
        _section("③ 关联任务与提案", tasks_body),
        _section("④ 时间线（审计轨迹）", tl_body),
        _section("⑤ 协调线程及往来", co_body),
        _section("⑥ 同类先例", pc_body),
        _section("⑦ 导出元信息", meta_body),
    ])

    # 自包含：全部 CSS 内联，零外链（无 <script src>/<link>/远程 <img>）；A4 打印友好。
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>证据包 · {_esc(risk_id)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    color: #1c2430; background: #f4f6f9; margin: 0; padding: 24px; line-height: 1.55; position: relative; }}
  .sheet {{ max-width: 920px; margin: 0 auto; background: #fff; border: 1px solid #d9dee6;
    border-radius: 8px; padding: 28px 32px; position: relative; z-index: 1; }}
  header h1 {{ font-size: 20px; margin: 0 0 2px; }}
  header .sub {{ color: #5b6675; font-size: 13px; margin: 0 0 16px; }}
  .sim-banner {{ background: #fff4e5; border: 1px solid #f0b664; color: #8a4b00;
    padding: 10px 14px; border-radius: 6px; font-size: 13px; font-weight: 600; margin: 0 0 18px; }}
  section {{ border-top: 1px solid #e6e9ee; padding: 16px 0 6px; }}
  section:first-of-type {{ border-top: none; }}
  h2 {{ font-size: 15px; margin: 0 0 10px; color: #24303f; }}
  table {{ width: 100%; border-collapse: collapse; margin: 4px 0 8px; font-size: 13px; }}
  table.kv th {{ text-align: left; width: 34%; color: #48525f; font-weight: 600; vertical-align: top;
    padding: 5px 8px; border-bottom: 1px solid #eef1f4; }}
  table.kv td {{ padding: 5px 8px; border-bottom: 1px solid #eef1f4; word-break: break-word; }}
  table.grid th {{ background: #f0f3f7; text-align: left; padding: 6px 8px; border: 1px solid #e2e7ee;
    font-weight: 600; color: #48525f; }}
  table.grid td {{ padding: 5px 8px; border: 1px solid #eef1f4; word-break: break-word; }}
  .code {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; color: #8a94a3;
    margin-left: 6px; }}
  .muted {{ color: #6a7482; font-size: 12px; margin: 2px 0 8px; }}
  .note {{ color: #8a5a00; font-size: 12px; background: #fffaf0; border-left: 3px solid #f0c674;
    padding: 6px 10px; margin: 6px 0; }}
  .empty {{ color: #7a8494; font-style: italic; font-size: 13px; }}
  .card {{ border: 1px solid #e4e8ee; border-radius: 6px; padding: 10px 12px; margin: 8px 0; }}
  .card-h {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .card p {{ margin: 4px 0; font-size: 13px; }}
  ul {{ margin: 4px 0; padding-left: 20px; font-size: 13px; }}
  .watermark {{ position: fixed; top: 45%; left: 50%; transform: translate(-50%, -50%) rotate(-28deg);
    font-size: 78px; font-weight: 800; color: rgba(214, 92, 40, 0.10); letter-spacing: 8px;
    white-space: nowrap; pointer-events: none; z-index: 0; user-select: none; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .sheet {{ border: none; border-radius: 0; padding: 12px 8px; max-width: none; }}
    .watermark {{ position: fixed; color: rgba(214, 92, 40, 0.12); }}
  }}
</style>
</head>
<body>
{watermark}
<div class="sheet">
<header>
  <h1>风险证据包 · {_esc(risk_id)}</h1>
  <p class="sub">世界：{_esc(meta.get('world'))} · 导出人：{_esc(meta.get('exported_by'))}（{_esc(meta.get('role'))}） · 数据时点：{_esc(meta.get('as_of'))}</p>
</header>
{sim_banner}
{body}
</div>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# 路由工厂：main.py 尾部注入挂载（同 cockpit/evidence/runtime，不反向 import main → 零循环导入）
# ═══════════════════════════════════════════════════════════════════════════
def build_evidence_export_router(get_db_path: Callable, get_ro_connection: Callable,
                                 infer_world: Callable[[str], str], as_of: str) -> APIRouter:
    """路由工厂。get_ro_connection = GET mode=ro 物理只读（读七块）；get_db_path 供审计写另开写连接；
    infer_world = 库路径 → 世界标识（verify/sim，元信息与 sim 水印用）；as_of = 仿真时钟（审计 as_of_date）。"""
    router = APIRouter(prefix="/risk-events", tags=["evidence-export"])

    @router.get("/{risk_event_id}/evidence-package")
    def export_evidence_package(
            risk_event_id: str,
            format: str = Query(default="json", description="json（结构化包）| html（自包含单文件，可打印为 PDF）"),
            x_role: str = Header(default="ops", alias="X-Role"),
            x_actor: str | None = Header(default=None, alias="X-Actor"),
            con: sqlite3.Connection = Depends(get_ro_connection),
            db_path: str = Depends(get_db_path)):
        """单风险证据包导出：七块现算（对象快照/影响链/关联任务/时间线/协调/先例/元信息）。
        X-Role 脱敏（导出的包里就是掩码值、不旁路）；X-World 双世界（经 get_db_path 解析）；format=json|html。
        风险查无 → 404 诚实空态；导出行为落 action_log（ExportEvidencePackage，actor/role 如实）。
        X-Actor 可选（读级端点，缺省记 role 派生的匿名标识；前端恒带真实经手身份）。"""
        fmt = (format or "json").strip().lower()
        if fmt not in _FORMATS:
            raise HTTPException(
                422, detail=f"未知 format '{format}'——仅支持 json（结构化包）/ html（自包含单文件，"
                            f"可浏览器打印为 PDF）。")
        world = infer_world(db_path)
        exporter = x_actor.strip() if (x_actor and x_actor.strip()) else None
        package = build_evidence_package(con, risk_event_id, x_role, world, exporter, as_of, fmt=fmt)
        if package is None:
            raise HTTPException(
                404, detail=f"风险事件 '{risk_event_id}' 在当前世界不存在——确认编号（形如 RSK-xxxx）"
                            f"或先切到正确的世界（验证 / 模拟）。")

        # 导出留痕（读级动作的访问审计）：actor 如实——缺 X-Actor 记 role 派生匿名标识（诚实非编造真名）。
        audit_actor = exporter or f"anon-{x_role}"
        _audit_export(db_path, as_of, audit_actor, x_role, risk_event_id, fmt)

        if fmt == "html":
            return HTMLResponse(content=render_package_html(package))
        return JSONResponse(content=package)

    return router
