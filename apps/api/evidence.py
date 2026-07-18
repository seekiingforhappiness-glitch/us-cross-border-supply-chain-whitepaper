#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""波E·证据链智能——提案证据包端点（spec `docs/superpowers/specs/2026-07-17-waveE-evidence-chain.md` §①/②）。

一句话（白话）：给驾驶舱每条 AI 处置提案配一份「证据包」——这次影响多大、历史上同类怎么处置的、
AI 在这类事上可信到什么程度、别的选项代价如何。**全部现查现算、纯只读聚合、零新写路**——证据是
增强不是门槛，算不出的字段一律如实 null+reason，先例统计绝不编数、样本不足如实标。

四块证据（GET /proposals/{task_id}/evidence，X-Role 脱敏同 objects、X-World 双世界）：
  1. impact      受影响订单行数/金额合计/波及客户数（复用 cockpit `_customer_risk_map` 影响 SQL 口径，
                 缩到单风险 affected_so_line_ids；无关联订单行的风险如实标注、不硬算）。
  2. precedents  resolution_memory 同 rule_id（+同 lane）检索——各决定例数分布 + 事后有效率
                 （quality_label='effective'）+ 近例 3 条白话结局。样本不足如实标、0 例=诚实空态（首例）。
  3. trust       data/gating_report.json 该 rule 域的 tier/rate/CI/n（复用 governance 的只读 JSON 路径，
                 **绝不 import agent.gating**——V15/16 静态隔离红线同 governance.py；文件缺失/域缺失
                 → available:false 诚实空态）。
  4. alternatives 本风险下 expedite vs accept_delay 的代价对比（延误天数、受影响货值、两选择历史有效率）
                 ——能算则算，算不出的字段如实 null+reason，绝不编。

红线（本模块的存在理由）：
  · **纯读**：只 SELECT，零 INSERT/UPDATE/DDL（不建表、不回填）；检索 resolution_memory 镜像
    engine.find_similar 的 G1 安全过滤（status='active'，voided 记忆绝不可见）。
  · **不 import agent.gating**：trust 只读 data/gating_report.json（离线放权门禁引擎的 display-only 产物），
    display_only 声明原样透传，避免"档位=已授权"误读（照抄 governance.py 模式）。
  · **不重写已存在的工具函数**（AGENTS.md §5）：JSON id 解析/金额掩码复用 cockpit `_json_ids`/`_mask_money`；
    航线派生/白话标签/列序复用 engine.resolution_memory（lane_for_shipment / *_LABELS / COLUMNS）。

为什么这样建（≤5 行，AGENTS.md §4）：
  · 证据逻辑是**纯函数**（impact/precedents/trust/alternatives_block + precedent_summary_line），
    不依赖 FastAPI 请求上下文——runtime 的 propose 步直接调 precedent_summary_line 拿一句摘要（spec §②）。
  · 先例检索的**层级**（设计决策，spec 措辞有歧义见文件尾 _retrieve_precedents 注释）：最相似=同规则+
    同航线；该子集 <5 例时放宽到同规则（任意航线），如实标 match_scope/widened，绝不用"加航线约束"
    去缩小（那只会更少）——广度换统计意义，诚实标注基准。
  · 金额脱敏在**装配末端**统一过 _mask_money（键名 _usd 后缀）：ops 等无成本可见角色看掩码，计数/比率/
    天数不掩（与 cockpit 脱敏层 b 同规、同 _can_see_cost 门）。
  · 路由工厂由 main.py 尾部注入挂载（同 cockpit/governance/runtime：本模块不反向 import main → 零循环导入）。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException

from agent.tools import _can_see_cost
# 复用 cockpit 既有纯helper（AGENTS.md §5 不重写）：_json_ids=affected_*_ids JSON 解析、
# _mask_money=键名 _usd 后缀就地掩码（内含 MASK 常量）、_tables=表存在性探测。cockpit 已被 main
# 常驻加载，无新增成本。
from apps.api.cockpit import _json_ids, _mask_money, _tables
# 复用 engine 既有只读 helper/常量（不改一行 engine）：航线派生 + 列序 + 白话标签。
from engine.resolution_memory import (COLUMNS, DECISION_LABELS, QUALITY_LABELS_ZH,
                                       ACTION_LABELS, lane_for_shipment)

# 先例检索的"最相似"门槛：同规则+同航线子集 < 此数即放宽到同规则（任意航线），换取统计意义。
_PRECEDENT_MIN = 5
# 备选对比的两个动作（spec §① 第4条点名 expedite vs accept_delay）。
_ALT_ACTIONS = ("expedite", "accept_delay")


# ═══════════════════════════════════════════════════════════════════════════
# ① impact —— 受影响订单行数 / 金额合计 / 波及客户数（复用 cockpit 影响 SQL 口径，缩到单风险）
# ═══════════════════════════════════════════════════════════════════════════
def impact_block(con: sqlite3.Connection, affected_so_line_ids: Any) -> dict:
    """单风险影响面（口径同 cockpit `_customer_risk_map`：affected_so_line_ids→sales_order_lines→
    sales_orders→customers，去重行 Σ(qty×unit_price_usd)、去重客户数）。JSON 解析复用 cockpit `_json_ids`。
    该风险无关联订单行（费用/单据类，affected_so_line_ids 空/'[]'）→ 三数为 0 + 白话 note（诚实非缺数）。
    id 悬空（JSON 有、库里查无）→ 如实略过并在 note 标出，不猜。金额键以 _usd 结尾 → 装配末端按角色掩码。"""
    line_ids = _json_ids(affected_so_line_ids)
    if not line_ids:
        return {"affected_order_lines": 0, "amount_usd": 0.0, "affected_customers": 0,
                "note": "该风险未关联具体订单行（如费用/单据类风险），无订单行级影响可算——"
                        "非缺数，是这类风险本就不落到订单行上。"}
    placeholders = ",".join("?" * len(line_ids))
    rows = con.execute(
        f"""SELECT sol.so_line_id, sol.qty, sol.unit_price_usd, so.customer_id
            FROM sales_order_lines sol
            JOIN sales_orders so ON so.so_id = sol.so_id
            WHERE sol.so_line_id IN ({placeholders})""", line_ids).fetchall()
    seen: set[str] = set()
    amount = 0.0
    customers: set[str] = set()
    for r in rows:
        lid = r["so_line_id"]
        if lid in seen:                      # 去重：同一行被多次列出只计一次金额（同 cockpit 去重口径）
            continue
        seen.add(lid)
        amount += (r["qty"] or 0) * (r["unit_price_usd"] or 0.0)
        if r["customer_id"]:
            customers.add(r["customer_id"])
    out: dict[str, Any] = {
        "affected_order_lines": len(seen), "amount_usd": round(amount, 2),
        "affected_customers": len(customers),
        "requested_line_ids": len(line_ids), "resolved_line_ids": len(seen)}
    if len(seen) < len(line_ids):            # 悬空 id 如实标注（脏数据不猜、不冒充完整）
        out["note"] = (f"{len(line_ids) - len(seen)} 个受影响订单行 id 在本世界库中查无对应行"
                       f"（已按现存 {len(seen)} 行如实计算，未猜测缺失行的金额/客户）。")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# ② precedents —— resolution_memory 同 rule_id（+同 lane）检索：例数分布 + 有效率 + 近例 3 条
# ═══════════════════════════════════════════════════════════════════════════
def _retrieve_precedents(con: sqlite3.Connection, rule_id: str, lane: str,
                         restrict_lane: bool, exclude_risk_id: str | None = None) -> list[dict]:
    """只读检索 resolution_memory（**镜像 engine.find_similar 的 G1 安全过滤 + 排除本案**：status='active' →
    voided/屏蔽记忆绝不返回；risk_event_id != 本案 → 提案绝不把自己的历史处置当成自己的先例，与
    find_similar 的 exclude_risk_id 同规）。restrict_lane=True→同规则+同航线精确；False→同规则、任意航线
    （放宽）。显式列出 COLUMNS（21 列，engine 单一来源）——库里 source 等后加列不在其中，避免位置错位。
    ORDER BY decided_at DESC=近例在前（"近例 3 条"取头 3）。"""
    if "resolution_memory" not in _tables(con):
        return []
    where = "status='active' AND rule_id=? AND risk_event_id != ?"
    params: list[Any] = [rule_id, exclude_risk_id or ""]
    if restrict_lane:
        where += " AND lane=?"
        params.append(lane)
    sql = (f"SELECT {','.join(COLUMNS)} FROM resolution_memory WHERE {where} "
           f"ORDER BY decided_at DESC, memory_id DESC")
    return [dict(zip(COLUMNS, r)) for r in con.execute(sql, params)]


def _quality_stats(rows: list[dict]) -> dict:
    """事后有效率（spec §① 第2条『effective 字段』= quality_label='effective'）。分母=已回填质量标签的
    例数（effective+partial+ineffective），未回填（None/''）不进分母——'尚未结案'不该稀释有效率。
    labeled=0 → effective_rate=None（诚实空态，绝不 0/0 冒充）。"""
    counts = {"effective": 0, "partial": 0, "ineffective": 0, "unlabeled": 0}
    for r in rows:
        ql = r.get("quality_label")
        if ql in ("effective", "partial", "ineffective"):
            counts[ql] += 1
        else:
            counts["unlabeled"] += 1
    labeled = counts["effective"] + counts["partial"] + counts["ineffective"]
    return {"labeled": labeled, "effective": counts["effective"], "partial": counts["partial"],
            "ineffective": counts["ineffective"], "unlabeled": counts["unlabeled"],
            "effective_rate": round(counts["effective"] / labeled, 4) if labeled else None,
            "note": ("有效率分母=已结案回填质量标签的例数；未回填的先例不计入分母（不拿"
                     "'尚未结案'稀释有效率）。" if labeled else
                     "同类先例均尚未回填事后质量标签，暂无有效率可算（诚实空态，非 0%）。")}


def _example_plain(r: dict) -> str:
    """近例白话结局：'采纳「加急」，5 天关闭，专员标记有效' / '尚未结案（未回填结果）'。
    动作/决定/质量白话复用 engine.resolution_memory 的 *_LABELS（单一来源，不另立译名）。"""
    try:
        action = json.loads(r.get("proposal_summary") or "{}").get("action", "?")
    except (ValueError, TypeError):
        action = "?"
    decision_zh = DECISION_LABELS.get(r.get("decision"), r.get("decision") or "?")
    action_zh = ACTION_LABELS.get(action, action)
    head = f"{decision_zh}「{action_zh}」"
    if not r.get("closed_at"):
        return f"{head}，尚未结案（未回填事后结果）。"
    tail = f"，{r.get('outcome_resolved') or '-'}、{r.get('outcome_days')} 天关闭"
    if r.get("quality_label"):
        tail += f"，专员标记「{QUALITY_LABELS_ZH.get(r['quality_label'], r['quality_label'])}」"
    return head + tail + "。"


def _example_view(r: dict) -> dict:
    """近例条目（白话 + 可回查的原始字段；impact_usd 键 _usd 后缀 → 末端按角色掩码）。"""
    try:
        action = json.loads(r.get("proposal_summary") or "{}").get("action", "?")
    except (ValueError, TypeError):
        action = "?"
    return {"memory_id": r.get("memory_id"), "risk_event_id": r.get("risk_event_id"),
            "decision": r.get("decision"), "proposed_action": action,
            "quality_label": r.get("quality_label"), "outcome_resolved": r.get("outcome_resolved"),
            "outcome_days": r.get("outcome_days"), "decided_at": r.get("decided_at"),
            "impact_usd": r.get("impact_usd"), "plain": _example_plain(r)}


def precedents_block(con: sqlite3.Connection, rule_id: str | None, lane: str,
                     exclude_risk_id: str | None = None) -> dict:
    """先例证据（spec §① 第2条）。层级检索（设计决策见 _retrieve_precedents / 文件头 docstring）：
      · 最相似 = 同规则+同航线；该子集 < _PRECEDENT_MIN(5) 例 → 放宽到同规则（任意航线），标 widened。
      · 统计基准 = 最终展示的池（rule_and_lane 或 rule_only），match_scope 如实标出。
      · exclude_risk_id = 本案风险号（提案不把自己的历史当自己的先例，同 find_similar）。
    返回 各决定例数分布 by_decision + 事后有效率 effectiveness + 近例 3 条 recent_examples。
    0 例（含本世界无 resolution_memory 或该规则无历史）→ 诚实空态（empty:true，'首例'）。
    rule_id 缺失（风险行查无）→ available:false+reason。"""
    if not rule_id:
        return {"available": False, "n": 0,
                "reason": "该提案关联的风险事件在本世界库中查无 rule_id，无法检索同类先例。"}
    tight = _retrieve_precedents(con, rule_id, lane, restrict_lane=True, exclude_risk_id=exclude_risk_id)
    widened = False
    widen_reason = None
    if len(tight) < _PRECEDENT_MIN:
        broad = _retrieve_precedents(con, rule_id, lane, restrict_lane=False,
                                     exclude_risk_id=exclude_risk_id)
        if len(broad) > len(tight):          # 放宽确实带来更多样本才切换（否则保持同航线口径）
            widened = True
            widen_reason = (f"同规则+同航线（{rule_id} · {lane or '无航线'}）仅 {len(tight)} 例（<{_PRECEDENT_MIN}），"
                            f"已放宽到同规则（任意航线）以取得更多样本；下列统计基准=同规则全部 {len(broad)} 例。")
            rows = broad
        else:
            rows = tight
    else:
        rows = tight
    match_scope = "rule_only" if widened else "rule_and_lane"

    if not rows:
        return {"available": True, "empty": True, "n": 0, "rule_id": rule_id, "lane": lane,
                "match_scope": "rule_and_lane", "by_decision": {},
                "effectiveness": _quality_stats([]), "recent_examples": [],
                "note": (f"无同类先例（首例）——本世界下规则 {rule_id}"
                         f"{'（航线 ' + lane + '）' if lane else ''} 暂无历史处置记忆可参照。")}

    by_decision: dict[str, int] = {}
    for r in rows:
        d = r.get("decision") or "?"
        by_decision[d] = by_decision.get(d, 0) + 1
    out: dict[str, Any] = {
        "available": True, "n": len(rows), "rule_id": rule_id, "lane": lane,
        "match_scope": match_scope, "widened": widened,
        "by_decision": by_decision,
        "effectiveness": _quality_stats(rows),
        "recent_examples": [_example_view(r) for r in rows[:3]]}
    if widen_reason:
        out["widen_reason"] = widen_reason
    if len(rows) < _PRECEDENT_MIN:           # 样本不足如实标（生产级硬门：诚实空态/样本标注）
        out["sample_note"] = (f"样本不足（仅 {len(rows)} 例 < {_PRECEDENT_MIN}）——先例统计仅供参考，"
                              f"不足以作为放权/自动化依据。")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# ③ trust —— data/gating_report.json 该 rule 域的 tier/rate/CI/n（只读 JSON，不 import gating）
# ═══════════════════════════════════════════════════════════════════════════
def trust_block(rule_id: str | None) -> dict:
    """AI 可信度档位（spec §① 第3条）：只读 data/gating_report.json（放权门禁引擎的 display-only 产物），
    按 rule 域匹配（报告里 group='resolution' 的 domain 字段 == rule_id，如 R1/R2/R4/R5/R6/R16）。
    **绝不 import agent.gating / 不实时算档**——照抄 governance.py 的只读搬运模式，复用其 GATING_REPORT_PATH
    单一路径来源（经 governance 模块引用 → 测试 monkeypatch governance.GATING_REPORT_PATH 对本块同样生效）。
    文件缺失/损坏/顶层非对象/该域缺失 → available:false + 白话 reason（诚实空态，绝不 500、绝不用旧值冒充）。
    display_only 原样透传（'档位=历史一致率的展示，非工具授权'），避免误读。"""
    from apps.api import governance          # 惰性引模块（非绑定名）→ 单一路径来源 + 可 monkeypatch
    if not rule_id:
        return {"available": False,
                "reason": "该提案关联的风险事件查无 rule_id，无法定位放权档位域。"}
    path = governance.GATING_REPORT_PATH
    if not path.exists():
        return {"available": False,
                "reason": f"治理报告尚未生成（{path.name} 不存在）——放权门禁引擎需先离线跑一遍落盘，"
                          f"本块只读不算。"}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {"available": False,
                "reason": f"治理报告存在但无法解析（{type(exc).__name__}）——如实报缺，"
                          f"绝不用旧值/猜测冒充当前档位。"}
    if not isinstance(report, dict):
        return {"available": False, "reason": "治理报告格式异常（顶层非对象），如实报缺。"}
    domains = report.get("domains", []) if isinstance(report.get("domains"), list) else []
    match = next((d for d in domains if isinstance(d, dict)
                  and d.get("group") == "resolution" and d.get("domain") == rule_id), None)
    if match is None:                        # 兜底：任意 group 下 domain==rule_id（域命名以报告真实字段为准）
        match = next((d for d in domains if isinstance(d, dict) and d.get("domain") == rule_id), None)
    if match is None:
        return {"available": False,
                "reason": f"治理报告中找不到规则 {rule_id} 的放权域——该规则可能未纳入放权算档，"
                          f"或报告版本不含此域（共 {len(domains)} 个域）。"}
    return {"available": True,
            "display_only": report.get("display_only"),   # 原样透传：档位≠已授权
            "rule_id": rule_id, "domain": match.get("domain"), "name": match.get("plain"),
            "group": match.get("group"), "tier": match.get("tier"), "next_tier": match.get("next_tier"),
            "n": match.get("n"), "hits": match.get("hits"), "rate": match.get("rate"),
            "ci": match.get("ci"), "low_sample": match.get("low_sample"),
            "escalation": match.get("escalation"), "gaps": match.get("gaps", []),
            "generated_at": report.get("generated_at"),
            "config_version": (report.get("config") or {}).get("version"),
            "note": "display-only：档位=模型在该域历史一致率的展示，非工具授权（不代表 AI 可自动执行）——"
                    "请谨慎复核。"}


# ═══════════════════════════════════════════════════════════════════════════
# ④ alternatives —— expedite vs accept_delay 代价对比（延误天数/受影响货值/两选择历史有效率）
# ═══════════════════════════════════════════════════════════════════════════
def _action_effectiveness(rows: list[dict], action: str) -> dict:
    """某动作在同类先例里的历史有效率（从 precedents 同源池按 proposal action 过滤）。
    0 例 → null+reason（诚实空态，绝不编）；有例但均未回填 → effective_rate=None+note。"""
    subset = []
    for r in rows:
        try:
            act = json.loads(r.get("proposal_summary") or "{}").get("action", "?")
        except (ValueError, TypeError):
            act = "?"
        if act == action:
            subset.append(r)
    if not subset:
        return {"n": 0, "effective_rate": None,
                "reason": f"同类先例中无『{ACTION_LABELS.get(action, action)}』历史案例，无法给出该选择的历史有效率。"}
    q = _quality_stats(subset)
    return {"n": len(subset), "labeled": q["labeled"], "effective": q["effective"],
            "effective_rate": q["effective_rate"], "note": q["note"]}


def alternatives_block(con: sqlite3.Connection, risk: dict | None, precedent_rows: list[dict]) -> dict:
    """备选对比（spec §① 第4条）：expedite vs accept_delay 的代价对比——
      · 延误天数：风险货件 shipments.delay_days（无货件/无该列 → null+reason，不猜）。
      · 受影响货值：risk_events.affected_value_usd（金额键 _usd 后缀 → 末端按角色掩码）。
      · 两选择历史有效率：从同类先例池按动作过滤（precedent_rows 与 precedents_block 同源），算不出 null+reason。
    风险行缺失 → available:false+reason。全程能算则算、算不出如实标，绝不编。"""
    if risk is None:
        return {"available": False, "reason": "该提案关联的风险事件在本世界库中查无，无法比较备选方案代价。"}
    # 延误天数：风险 → 货件 → delay_days（现查真列；无 shipment 或列缺 → null+reason）
    delay_days: Any = None
    delay_reason = None
    shipment_id = risk.get("shipment_id")
    if not shipment_id:
        delay_reason = "该风险未锚定货件（如费用/采购/仓储类风险），无货件延误天数可算。"
    else:
        try:
            row = con.execute("SELECT delay_days FROM shipments WHERE shipment_id=?",
                              (shipment_id,)).fetchone()
            if row is None:
                delay_reason = f"风险锚定货件 {shipment_id} 在本世界库中查无，无延误天数可算。"
            else:
                delay_days = row["delay_days"]
        except sqlite3.OperationalError:
            delay_reason = "本世界 shipments 表无 delay_days 列，无法给出延误天数。"

    options = {}
    for act in _ALT_ACTIONS:
        options[act] = {"label": ACTION_LABELS.get(act, act),
                        "historical": _action_effectiveness(precedent_rows, act)}
    out: dict[str, Any] = {
        "available": True,
        "affected_value_usd": risk.get("affected_value_usd"),
        "delay_days": delay_days,
        "options": options,
        "note": "白话：『加急』可能缩短延误但要付加急费；『接受延误』省成本但货会晚到。两选择的历史有效率"
                "见各自 historical（同类先例现算，样本不足/无例时如实标 null，不编）。"}
    if delay_reason:
        out["delay_reason"] = delay_reason
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 装配 + 脱敏 + runtime 摘要
# ═══════════════════════════════════════════════════════════════════════════
def _load_task_risk(con: sqlite3.Connection, task_id: str):
    """task_id → (task_row, risk_row)。task 不存在 → (None, None)（路由据此 404）；
    task 存在但风险缺失（脏数据）→ (task_row, None)，各块如实降级（不把整包 500）。"""
    t = con.execute(
        "SELECT task_id, risk_event_id, proposed_action, proposal_params, approval_status "
        "FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if t is None:
        return None, None
    risk = None
    if t["risk_event_id"]:
        risk = con.execute(
            "SELECT risk_event_id, rule_id, severity, shipment_id, affected_value_usd, "
            "affected_so_line_ids FROM risk_events WHERE risk_event_id=?",
            (t["risk_event_id"],)).fetchone()
    return t, (dict(risk) if risk else None)


def build_evidence(con: sqlite3.Connection, task: sqlite3.Row, risk: dict | None, role: str) -> dict:
    """装配四块证据 + 末端金额脱敏。risk 缺失时各块如实降级（available:false/null+reason），不 500。
    脱敏（spec §① 'X-Role 脱敏同 objects'）：无成本可见角色（ops/cs）→ 全载荷键名 _usd 后缀就地掩码
    （复用 cockpit `_mask_money`，与 cockpit 脱敏层 b 同规、同 _can_see_cost 门）；计数/比率/天数/档位不掩。"""
    rule_id = risk.get("rule_id") if risk else None
    lane = lane_for_shipment(con, risk.get("shipment_id")) if risk else ""
    exclude_rid = risk.get("risk_event_id") if risk else None
    prec = precedents_block(con, rule_id, lane, exclude_risk_id=exclude_rid)
    # 备选块的历史有效率与先例块同源（同一次检索的池，避免两次不一致口径）——重取一次同池行。
    if rule_id:
        prec_rows = _retrieve_precedents(con, rule_id, lane, exclude_risk_id=exclude_rid,
                                         restrict_lane=(prec.get("match_scope") == "rule_and_lane"))
    else:
        prec_rows = []
    payload: dict[str, Any] = {
        "task_id": task["task_id"], "risk_event_id": task["risk_event_id"],
        "proposed_action": task["proposed_action"], "approval_status": task["approval_status"],
        "role": role,
        "impact": impact_block(con, risk.get("affected_so_line_ids")) if risk else
                  {"available": False, "reason": "该提案关联的风险事件在本世界库中查无，无法计算影响面。"},
        "precedents": prec,
        "trust": trust_block(rule_id),
        "alternatives": alternatives_block(con, risk, prec_rows)}
    if not _can_see_cost(role):              # ops/cs 等无成本可见角色：金额掩码（计数/比率/天数不掩）
        # V21① 边界（决策日志 V21①"不放宽订单行/客户敞口等其他金额"）：证据包金额为受影响订单行货值
        # (impact.amount_usd)、风险敞口(affected_value_usd)、先例影响(impact_usd)——**非提案金额**，故
        # **不**传 role、不套自队例外，一律按成本门掩码（与改动前 byte-identical）。
        _mask_money(payload)
    return payload


def precedent_summary_line(con: sqlite3.Connection, risk_event_id: str) -> str:
    """runtime 的 propose 步证据增强（spec §②）：给定风险事件号，回一句白话先例摘要
    （'同类 N 例…X% 事后有效'）。纯只读、role 无关（不含金额，无需脱敏）。风险查无/无先例都回诚实串
    （不 raise——调用方 runtime 只做增强不阻断；本函数把'无'表达成话，不把'无'表达成异常）。"""
    r = con.execute("SELECT rule_id, shipment_id FROM risk_events WHERE risk_event_id=?",
                    (risk_event_id,)).fetchone()
    if r is None or not r["rule_id"]:
        return f"（{risk_event_id} 查无 rule_id，无同类先例可引。）"
    lane = lane_for_shipment(con, r["shipment_id"])
    prec = precedents_block(con, r["rule_id"], lane, exclude_risk_id=risk_event_id)
    scope = f"{r['rule_id']}" + (f" · {lane}" if lane else "")
    if prec.get("n", 0) == 0:
        return f"同类先例：无（首例，{scope}）。"
    by = prec["by_decision"]
    eff = prec["effectiveness"]
    decided = "、".join(f"{DECISION_LABELS.get(d, d)} {n}" for d, n in sorted(by.items()))
    if prec["match_scope"] == "rule_only":
        scope = f"{r['rule_id']}，航线样本不足已放宽全航线"    # 不再套内层括号（外层已有 （…））
    if eff["labeled"]:
        eff_str = f"已结案 {eff['labeled']} 例中 {round(eff['effective_rate'] * 100)}% 事后标记有效"
    else:
        eff_str = "同类先例均尚未回填事后结果"
    tail = "（样本<5，仅供参考）" if prec["n"] < _PRECEDENT_MIN else ""
    return f"同类 {prec['n']} 例（{scope}）：{decided}；{eff_str}。{tail}"


# ═══════════════════════════════════════════════════════════════════════════
# 路由工厂：main.py 尾部注入挂载（同 cockpit/governance/runtime，不反向 import main → 零循环导入）
# ═══════════════════════════════════════════════════════════════════════════
def build_evidence_router(get_db_path: Callable, get_ro_connection: Callable,
                          infer_world: Callable[[str], str]) -> APIRouter:
    router = APIRouter(prefix="/proposals", tags=["evidence"])

    @router.get("/{task_id}/evidence")
    def proposal_evidence(task_id: str,
                          x_role: str = Header(default="ops", alias="X-Role"),
                          con: sqlite3.Connection = Depends(get_ro_connection),
                          db_path: str = Depends(get_db_path)) -> dict:
        """提案证据包：impact / precedents / trust / alternatives 四块现算（纯读聚合，零写路）。
        X-Role 脱敏同 objects（无成本可见角色看金额掩码）；X-World 双世界（经注入 get_db_path 解析）。
        task 不存在 → 404 诚实空态；风险/先例/治理任一缺 → 该块 available:false 或 null+reason（不 500）。"""
        task, risk = _load_task_risk(con, task_id)
        if task is None:
            raise HTTPException(
                404, detail=f"提案（任务）'{task_id}' 不存在——证据包针对具体处置提案生成，"
                            f"请确认任务号（形如 TSK-xxxx）。")
        return {"world": infer_world(db_path), **build_evidence(con, task, risk, x_role)}

    return router
