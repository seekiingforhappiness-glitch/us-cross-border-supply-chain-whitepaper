#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驾驶舱 API 聚合层（B1，V8 决议③授权）——三只读端点，服务首屏三区。

规格源：docs/superpowers/specs/2026-07-14-cockpit-screen-narrative.md「画面复述·二稿」
（七掌控区【直投】项 = 需求清单；【候批】资金流项 V8-② 已获批并于 G2 补齐——见钱区
receivables/payables/net_cash_14d 三指标，docs/finance-manual-v0.11.md）。

  · GET /cockpit/vitals    公司体征带——七区体征块（钱/履约/客户/供应商/库存/AI/待拍板）
  · GET /cockpit/panorama  小全景分层图数据（五层节点 + 本体关系投影边 + 异常标注 + 迷你指标）
  · GET /cockpit/ai-flow   AI 工作流时间线（llm_calls / ai-agent 审计行 / 提案流转 / sim 留痕合并）

三条通用红线：
  1) 全部 mode=ro（复用 apps.api.main.get_ro_connection，物理只读）；
  2) 世界无关性——ONTOLOGY_DB 指向 data/simworld.sqlite 也必须能跑：该世界缺采购/准入/
     审计等表（见下），对应指标返回 {"value": null, "reason": "该世界无此域数据（…）"}
     如实标注，端点不得 500。表/列存在性运行时探测（_tables/_columns），不猜；
  3) 绝不编造——trend 无历史支撑一律 null；缺数指标标 reason 不填 0 冒充。

「今日/世界时钟」口径（D8：不隐式调系统时间，一律从库内数据推导）：
  clock = max( date(risk_events.detected_at), date(action_log.timestamp)[若有表],
               sim_event_log.sim_date[若有表] )
  验证世界 = 2026-08-08（=config as_of，检测/审计账本一致）；模拟世界 = 世界推进到的最新事件日。
  刻意不采 shipments.last_event_time（含未来 ETA 投影事件，会把"今日"推到未来）。

trend 字段：验证世界（静态快照）全 null。模拟世界仅两处有真实历史支撑、按 7 天窗口对比：
  履约区 OTD（到达日期账本完整：ata 覆盖全部 delivered）、AI 区检测数（detected_at 流水完整）。
  其余区块（钱/客户/供应商/库存/待拍板的 headline 均为"存量"指标）无逐日快照表——从事件流
  反推存量属推算而非测量，按红线 3 一律 null（docstring 留此说明供后续 sim 快照表落地再点亮）。

「时间轴回放」口径（as_of，A-2/V13②；D8"日期滑条回放"落地。诚实边界=本节最重要的约束）：
  三端点均接受可选 as_of=YYYY-MM-DD。缺省/≥世界时钟/非法串 → effective_clock=世界时钟、
  replay=False，行为与现状 **byte-identical**（现有前端/测试零感知，无 as_of 信封键）。
  window_start≤as_of<世界时钟 → effective_clock=as_of、replay=True，进入回放态：把 effective_clock
  当"今天"喂给既有时窗口径，并对**真能按时点重算**的指标沿事件流回算，对**存量类**如实标注。
  X-Role 脱敏在 as_of 路径同样生效（各端点末仍过 _apply_role_masks，不因回放绕过）。

  ┌ 真可回放（有不可变事件流/时间戳支撑，回算是测量非推算）───────────────────────────
  │ · 风险活跃度：risk_events.detected_at≤clock 且 (resolved_at 空 或 >clock)——"截至当日仍未
  │   闭环"。**仅模拟世界**（detected_at 真跨 68 天）：钱区费用敞口/供应商单一依赖+对账差异/
  │   客户敞口客户集/库存可救行的风险集、AI 今日检测数，回放态按此重建。验证世界 detected_at
  │   是单日批量快照（全 = 世界时钟当天），回算=当日前恒 0 属误导→**验证世界不重建风险类**
  │   （与既有"验证世界 trend 恒 null"同因同栈：静态快照无逐日史）。
  │ · 在途票数（任务书点名口径）：有离港里程碑(event_time≤clock)且无到港/交付里程碑(≤clock)的
  │   票。里程碑两世界都真分布 → **两世界都可回放**（钱区 in_transit_as_of）。
  │ · 时窗类（喂 effective_clock 即回算，due_date/due_at 是真排期）：应收/应付逾期(due_date<clock)、
  │   14 天净流出窗口(clock,clock+N]、待拍板超期任务(due_at<clock)、AI/履约 7 天趋势窗（sim）。
  │ · AI 工作流事件流(ai-flow)：各来源按自身时间戳≤as_of 过滤——事件日志天然可回放。
  ├ 不可回放（对象表只存当前态，无历史版本）→ 回放态如实标注，**绝不给假历史数字**─────────
  │   履约 OTD 累计率/清关卡点/延误直方、钱区毛利率分布/在途货值(申报价值)、应收应付**存量水位**
  │   (status='scheduled' 是现值)、供应商交期达成率/缺陷率(GRN 累计)、库存击穿/盘点差异(现货现值)、
  │   客户准入漏斗/健康度/敞口金额(qty×现价)、待拍板存量件数/升级件、AI 累计提案、panorama 节点/边。
  └ 回放态信息传达：payload 加 as_of 信封（requested/effective/world_clock/window/is_replay/
    world_is_sim/replayable/current_state_only/note）；vitals 每区加 headline_as_of=replayed|current
    供卡面显"显示当前值"小灰标。window={start,end} 始终随三端点回传（滑条定义域，纯附加键）。

X-Role 脱敏（沿既有两层，见各端点）：
  a) 本体 sensitiveFieldRules 具名字段 → agent.mcp_server.SensitiveFieldMasker（与五路由同一实例
     逻辑）：如 Customer.tier（客户健康度交叉表对非 cs/manager 自动掩码）、
     Task.proposal_params.est_cost_usd 嵌套规则；
  b) 金额类聚合值 → 与 UI COST_FIELDS/MONEY_FIELDS 同规（finance/manager 可见，复用
     agent.tools._can_see_cost）：对无权角色把 vitals/panorama 载荷里**键名以 _usd 结尾**的
     聚合金额与 margin_distribution 整块替换为 MASK（保守读法：宁可多掩，歧义清单供评审——
     备选=仅掩 UI 明确掩码来源字段派生的聚合）。计数/比率/状态不掩。

══════════════════════════ 七区指标 SQL 口径总表 ══════════════════════════
（Daniel 看画面质疑数字时按此回查；各 zone builder docstring 有逐条完整版）

【钱 money】
  费用异常敞口   SELECT count(*),sum(affected_value_usd) FROM risk_events
                 WHERE rule_id IN('R4','R5','R6') AND status='open'
  在途货值       SUM(shipment_allocations.allocated_qty × skus.declared_value_usd)
                 （shipments.status='in_transit'；申报价值空 → 该行不计；全空 → null+reason）
  被拦截超收     SELECT count(*),sum(affected_value_usd) FROM risk_events
                 WHERE rule_id='R4' AND status='resolved'
  准入毛利率分布 cost_scenarios.gross_margin_rate 分桶（<0 / 0-10% / 10-20% / ≥20%）；
                 simworld 无 cost_scenarios 表 → null+reason
  应收水位       SELECT count(*),sum(amount_usd) FROM payments WHERE direction='in'
                 AND status='scheduled'；overdue 子集加 AND due_date<clock AND 未 paid
                 （V8-② payments 表；缺表/世界时钟不可推导 → null+reason）
  应付水位       同构，direction='out'；overdue 同口径（要付未付、其中已逾期）
  净流出预警     R20 同口径：窗口(clock,clock+cash_watch_window_days] 内
                 Σout.scheduled.amount_usd − Σin.scheduled.amount_usd；阈值=config.finance.
                 cash_watch_threshold_usd（不硬编码，同 engine/finance_rules.py 单一来源）
【履约 fulfillment】
  OTD           fulfilled 行中 实际到达≤promised_delivery_date 的比例；实际到达=该行所有
                 已到货件的最迟到达日；到达=shipments.ata，空则回退该货件最后一条
                 event_type='delivered' 里程碑 event_time；无任何到达信息的行不进分母
  延误分布       shipments.delay_days>0 直方图桶（1-3/4-7/8-14/15-29/≥30，含历史已到票）
  清关卡点       count(*) WHERE customs_status='not_filed' AND status='in_transit'
【客户 customers】
  风险敞口Top    open 风险 affected_so_line_ids →sales_order_lines→sales_orders→customers；
                 客户敞口=被波及订单行**去重后** Σ(qty×unit_price_usd)（非 affected_value_usd
                 均摊——歧义清单#3），按敞口降序 Top5
  准入漏斗       SELECT status,count(*) FROM admission_cases GROUP BY status（simworld 缺表→null）
  客户健康度     tier × (客户数, 被波及客户数, open 风险数) 交叉；tier 经 sensitiveFieldRules 掩码
【供应商 suppliers】
  交期达成率     PO 首张 GRN 的 min(goods_receipts.received_date) ≤ purchase_orders.
                 expected_ready_date 记达成；未收货 PO 不进分母；按供应商升序排名（最差前）
  缺陷率Top      avg(goods_receipt_lines.defect_ppm) 按供应商降序 Top5（grn_lines→grns→po→supplier）
  单一依赖       count(*) WHERE rule_id='R14' AND status='open'
  对账差异       count(*),sum(affected_value_usd) WHERE rule_id IN('R7'..'R13') AND status='open'
  绩效劣化       count(*),sum(affected_value_usd) WHERE rule_id='R22'（V23① 供应商级慢性交期）
  资质预警       count(*) WHERE rule_id='R23'（V23① 逐证过期/临期未续）
【库存 inventory】
  安全库存击穿   inventory_positions WHERE available_qty<safety_stock（计数+明细清单缺口降序）
  盘点差异       cycle_counts WHERE variance!=0 计数+Σ|variance|（simworld 缺 cycle_counts 表→
                 null+reason；其 sim_cycle_counts 并入与否列歧义清单#4，保守不并）
  在途补给可救性 open R1-R3 风险的受影响行（line_status∈open/at_risk）逐行只读复算
                 app/warehouse_actions.py::_spot_position（目的仓优先，回退全网最大现货）：
                 现货≥需求=可全救 / 0<现货<需求=可部分救；行独立判定不模拟头寸争抢
【AI ai】
  今日           clock 日的 检测数（date(risk_events.detected_at)=clock）+提案/批/驳
                 （验证=action_log 按 action 名；模拟=sim_ai_activity 按 activity）+今日通过率
                 （今日无决策→null，绝不拿历史冒充）
  累计           tasks: 提案=approval_status∈(pending,approved,rejected) 计数、通过率=
                 approved/(approved+rejected)；simworld 另加 sim_ai_activity 五动作累计
  处置记忆命中   resolution_memory: total、cited（cited_precedent_ids 非空非'[]'）、命中率
  llm_calls      count(*) 按 call_type 分组（simworld 缺表→null+reason）
【待拍板 decisions】
  待批提案       tasks WHERE approval_status='pending'；金额=proposal_params.est_cost_usd，
                 缺则回退父风险 affected_value_usd；等待起点=action_log 该 task 的
                 ProposeMitigation ok 时间（无记录→null 不编）；按金额降序、等待次序
  超期任务       count(*) WHERE status NOT IN('done','cancelled') AND date(due_at)<clock
                 （simworld tasks 无 due_at 列→null+reason）
  升级件         count(*) WHERE escalation_level>0 AND status NOT IN('done','cancelled')（列缺→null）
═══════════════════════════════════════════════════════════════════════════

panorama 聚合规则（单层>40 实体时聚合为分组节点，保画面可渲染）：
  customers>40→按 us_state 分组；suppliers>40→按 city 分组；shipments（仅未 delivered 票）
  >40→按航线 lane=origin_locode→destination_locode 分组；orders 层恒为"按客户聚合计数"
  （规格指定），customers 聚合时随其锚点分组；warehouses（5 个）恒实体级。
  异常锚定：open 风险按其锚列挂节点——shipment_id→货件节点（层聚合时挂 lane 组；已 delivered
  不在层内→进 unanchored 如实列出）、warehouse_id→仓节点、supplier_id（或经 po_id→
  purchase_orders.supplier_id 解析）→供应商节点。
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from agent.mcp_server import SensitiveFieldMasker
from agent.tools import MASK, _can_see_cost, own_team_amount_visible
from pipeline.ontology_runtime import build_role_perms, load_ontology

ZONES = ("money", "fulfillment", "customers", "suppliers", "inventory", "ai", "decisions")

# 待批队列"我组的"筛选合法角色集（V22 任务1）：= 本体 role_dict 动作 executors 出现的全部业务角色
# （build_role_perms 值域并集，本体单一权威源）去掉非用户角色 'system'——即 7 个可切换业务角色，
# 与前端 roleActors.ROLES 天然同集。用途：GET /cockpit/vitals 的可选 assignee_role 参数做白话 422
# 守卫（挡 curl 手打的乱角色/拼写错），不硬编码角色字面量、不随世界变。缺省不传 = 不筛（byte-identical）。
_ROSTER_ROLES = frozenset(
    r for perm in build_role_perms(load_ontology()).values() for r in perm
) - {"system"}
_RECON_RULES = ("R7", "R8", "R9", "R10", "R11", "R12", "R13")   # 采购对账差异规则族
_COST_RULES = ("R4", "R5", "R6")                                  # 费用稽核规则族
_DELAY_RULES = ("R1", "R2", "R3")                                 # 延误规则族
_LAYER_CAP = 40                                                    # 单层实体数上限，超过即聚合
_TASK_FLOW_ACTIONS = ("AssignTask", "ProposeMitigation", "ApproveMitigation", "RejectMitigation")

# 资金流阈值/窗口（V8-② F1，engine/finance_rules.py 同源单一来源，见 config/datagen.yaml
# finance 段）：钱区 net_cash_14d 与 R20 风险规则读同一份配置，阈值改一处两边同步，不平行
# 硬编码。cockpit 不 import main（避免循环导入，见文件尾路由工厂注释），故独立加载，同
# apps/api/main.py::_DATAGEN_CFG 加载方式但互不依赖，仅同源同一份 yaml 文件。
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
with open(_REPO_ROOT / "config" / "datagen.yaml", encoding="utf-8") as _fh:
    _FINANCE_CFG = yaml.safe_load(_fh)["finance"]


# ═══════════════════════════════════════════════════════════════════════════
# 世界无关性基础件：表/列探测、缺域标注、世界时钟、JSON id 列解析
# ═══════════════════════════════════════════════════════════════════════════
def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def _missing(reason: str) -> dict:
    """缺域指标的统一如实标注（红线 2）：值为 null + 人话 reason，绝不填 0 冒充。"""
    return {"value": None, "reason": f"该世界无此域数据（{reason}）"}


def _one(con: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row:
    return con.execute(sql, params).fetchone()


def _world_clock(con: sqlite3.Connection, tables: set[str]) -> str | None:
    """库内最大事件日（模块 docstring 的 clock 公式）。三账本可用者取 max；全缺 → null。"""
    candidates: list[str] = []
    if "risk_events" in tables:
        row = _one(con, "SELECT max(date(detected_at)) FROM risk_events")
        if row and row[0]:
            candidates.append(row[0])
    if "action_log" in tables:
        row = _one(con, "SELECT max(date(timestamp)) FROM action_log")
        if row and row[0]:
            candidates.append(row[0])
    if "sim_event_log" in tables:
        row = _one(con, "SELECT max(sim_date) FROM sim_event_log")
        if row and row[0]:
            candidates.append(row[0])
    return max(candidates) if candidates else None


def _json_ids(raw: Any) -> list[str]:
    """解析 affected_*_ids JSON 数组列；空/'[]'/坏值 → []（不 raise，脏数据如实略过）。"""
    if not raw or raw == "[]":
        return []
    try:
        val = json.loads(raw)
        return [str(v) for v in val] if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _mask_money(node: Any, role: str | None = None) -> None:
    """金额类聚合掩码（模块 docstring 脱敏层 b）：就地把键名以 _usd 结尾的值与
    margin_distribution 整块替换为 MASK。计数/比率/状态键不动。
    V21① 自队例外：role 传入且当前 dict 带 assignee_role（=待批提案行）并 own_team_amount_visible
    为真时，该行**提案金额键（_usd）保留真值**（自队金额可见，与 /objects·MCP masker 三面同源）；
    其余金额键与非自队行一律掩码。role=None（如证据端点 impact/敞口金额，V21① 边界外）→ 全量掩码
    （与改动前 byte-identical，不放宽订单行/客户敞口等其他金额）。"""
    if isinstance(node, dict):
        own_team_ok = (role is not None and "assignee_role" in node
                       and own_team_amount_visible(role, node.get("assignee_role")))
        for key, val in list(node.items()):
            if key == "margin_distribution":
                node[key] = MASK
            elif key.endswith("_usd") and val is not None:
                if not own_team_ok:               # 自队提案行的金额键保留真值（V21①）
                    node[key] = MASK
            else:
                _mask_money(val, role)
    elif isinstance(node, list):
        for item in node:
            _mask_money(item, role)


def _apply_role_masks(payload: dict, role: str) -> dict:
    """两层脱敏（模块 docstring）：先本体 sensitiveFieldRules 具名/嵌套规则，再金额聚合层
    （键名 _usd 后缀 + margin_distribution + 带 headline_unit='usd' 标记的区 headline）。
    V21①：金额聚合层传 role → 待批提案行（带 assignee_role）的金额按自队例外保留/掩码（其余金额照旧）。"""
    SensitiveFieldMasker(load_ontology(), role).mask_value(payload)
    if not _can_see_cost(role):
        _mask_money(payload, role)
        for zone in payload.get("zones", []):
            if zone.get("headline_unit") == "usd":
                zone["headline_value"] = MASK
    return payload


# ─── ai-flow 载荷自由文本金额脱敏（轮3-A P1，_can_see_cost 声明的执行缺口收紧）───
# 为什么这样建（≤5 行，AGENTS.md §4）：
#   · 泄漏面是**自由文本**（sim_ai_activity.detail 灌出的 summary/note 带 "$7157.16"），
#     _mask_money 只掩结构化 _usd 键管不到——补文本层，判定复用 agent/tools._can_see_cost 同一权威。
#   · 正则锚定 '$'+数字（对象编号如 RSK-SIM-00001 无 '$' 前缀，零误伤），替换为 $•••——保留
#     "这里有个金额"的语义信号（前端 parseAmount 不吃 ••• → 金额徽标自然消失，文案仍可读）。
_TEXT_AMOUNT_RE = re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?")
TEXT_AMOUNT_MASK = "$•••"


def _mask_text_amounts(node: Any) -> Any:
    """就地递归：str 值里的 '$金额' → '$•••'（dict/list 深走，非 str 标量原样）。返回同一节点。"""
    if isinstance(node, dict):
        for key, val in node.items():
            node[key] = _mask_text_amounts(val)
        return node
    if isinstance(node, list):
        return [_mask_text_amounts(item) for item in node]
    if isinstance(node, str):
        return _TEXT_AMOUNT_RE.sub(TEXT_AMOUNT_MASK, node)
    return node


def _shift_date(day: str, delta_days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=delta_days)).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# 时间轴回放（as_of）基础件：数据窗口、as_of 解析、风险活跃度重建、在途票时点复算
# （口径与诚实边界见模块 docstring「时间轴回放」节）
# ═══════════════════════════════════════════════════════════════════════════
def _world_window(con: sqlite3.Connection, tables: set[str]) -> tuple[str | None, str | None]:
    """回放滑条的数据窗口 [start, end]。end = _world_clock（"今天"，口径不变，刻意不含未来 ETA）；
    start = 同批事件账本 + shipment_milestones 的最早真实事件日——里程碑给验证世界一个可拖区间
    （其 detected_at/action_log 是单日批量快照，只用它们 start=end 滑条退化）。全缺 → (None, None)。"""
    end = _world_clock(con, tables)
    mins: list[str] = []
    for table, expr in (("risk_events", "min(date(detected_at))"),
                        ("action_log", "min(date(timestamp))"),
                        ("sim_event_log", "min(sim_date)"),
                        ("shipment_milestones", "min(date(event_time))")):
        if table in tables:
            row = _one(con, f"SELECT {expr} FROM {table}")
            if row and row[0]:
                mins.append(row[0])
    return (min(mins) if mins else None), end


def _resolve_as_of(as_of: str | None, world_clock: str | None,
                   window_start: str | None) -> tuple[str | None, bool]:
    """请求 as_of → (effective_clock, replay)。缺省/≥world_clock/非法串/world_clock 不可推导 →
    (world_clock, False)：现状不变、byte-identical。window_start≤as_of<world_clock → (as_of, True)：
    回放态。as_of<window_start → 夹到 window_start（不早于数据窗口）。非法日期不 500，退现状。"""
    if not as_of or not world_clock:
        return world_clock, False
    try:
        date.fromisoformat(as_of)
    except (ValueError, TypeError):
        return world_clock, False
    if as_of >= world_clock:
        return world_clock, False
    if window_start and as_of < window_start:
        as_of = window_start
    return as_of, True


def _risk_active(replay_risk: bool, clock: str | None) -> tuple[str, tuple]:
    """风险"活跃"过滤片段 + 参数。
    · 非回放（含验证世界回放——replay_risk 已由调用方 and world_is_sim 收窄）→ ("status='open'", ())：
      现状口径，拼出的 SQL 与改动前逐字等价 ⇒ 结果 byte-identical。
    · 回放（仅模拟世界，detected_at 真跨天）→ 按 detected_at/resolved_at 重建"截至 clock 仍未闭环"：
      detected_at≤clock 且 (resolved_at 空 或 >clock)。含当日仍 mitigating 的（当时确未闭环，诚实）。"""
    if replay_risk and clock:
        return ("date(detected_at) <= ? AND (resolved_at IS NULL OR resolved_at='' "
                "OR date(resolved_at) > ?)", (clock, clock))
    return ("status='open'", ())


def _in_transit_as_of(con: sqlite3.Connection, tables: set[str], clock: str | None) -> dict | None:
    """截至 clock 的在途票数（任务书点名的"真回放"口径）：有离港里程碑(event_time≤clock)且
    无到港/交付里程碑(event_time≤clock)的票。里程碑是不可变事件流、两世界都真分布 → 诚实可回放。
    缺 shipment_milestones 表或 clock 不可推导 → None（调用方不加此键）。"""
    if "shipment_milestones" not in tables or not clock:
        return None
    row = _one(con, """
        SELECT count(*) c FROM (
          SELECT m.shipment_id,
                 max(CASE WHEN m.event_type='departed' AND date(m.event_time)<=? THEN 1 ELSE 0 END) dep,
                 max(CASE WHEN m.event_type IN ('arrived','delivered')
                          AND date(m.event_time)<=? THEN 1 ELSE 0 END) arr
          FROM shipment_milestones m GROUP BY m.shipment_id)
        WHERE dep=1 AND arr=0""", (clock, clock))
    return {"count": row["c"] if row else 0,
            "basis": f"截至 {clock}：有离港里程碑、无到港/交付里程碑的票（里程碑事件流复算）"}


def _headline_as_of(zone: str, replay_risk: bool) -> str:
    """回放态每区 headline 的诚实归类（卡面小灰标据此显"显示当前值"）。replay_risk 已蕴含 world_is_sim。
    · 钱/客户 headline = 风险活跃度重建，仅 sim（replay_risk）真回算，验证世界静态快照 → current。
    · AI headline = 当日检测/提案数，两世界都按 effective_clock 逐日复算（detected_at/action_log 是真
      事件流，验证世界批量单日故过去日恒 0，仍是测量非造假）→ 恒 replayed。
    · 履约 OTD/供应商交期/库存击穿/待拍板存量件数 = 存量现值 → current。"""
    if zone in ("money", "customers"):
        return "replayed" if replay_risk else "current"
    if zone == "ai":
        return "replayed"
    return "current"


def _as_of_envelope(requested: str | None, effective: str | None, world_clock: str | None,
                    window: tuple[str | None, str | None], world_is_sim: bool,
                    replay_risk: bool) -> dict:
    """回放信封（仅回放态加入 payload）：机器可读的重算/存量分类 + 人话边界说明，进决策日志。
    replayable/current_state_only 按当前世界如实枚举（模拟世界才重建风险类；里程碑/时窗类两世界都算）。"""
    always_replayable = ["ai.today", "money.in_transit_as_of", "money.receivables.overdue",
                         "money.payables.overdue", "money.net_cash_14d", "decisions.overdue_tasks"]
    sim_replayable = ["money.fee_exposure", "customers.risk_exposure_set",
                      "suppliers.single_source_r14", "suppliers.recon_diff_r7_r13",
                      "suppliers.perf_degradation_r22", "suppliers.qual_expiry_r23",
                      "inventory.rescuable_risk_set", "ai.trend",
                      "fulfillment.trend", "panorama.alert_anchoring"]
    current_only = ["fulfillment.otd", "fulfillment.customs_blocked", "fulfillment.delay_histogram",
                    "money.margin_distribution", "money.in_transit_value", "money.receivables.base",
                    "money.payables.base", "suppliers.delivery_hit_rate", "suppliers.defect_top",
                    "inventory.safety_breaches", "inventory.count_variance", "customers.exposure_usd",
                    "customers.admission_funnel", "customers.health_cross", "decisions.pending_proposals",
                    "decisions.escalated_tasks", "ai.all_time", "panorama.nodes", "panorama.edges"]
    replayable = always_replayable + (sim_replayable if replay_risk else [])
    if not replay_risk:      # 验证世界回放：风险类无逐日史，归入存量如实标注
        current_only = sim_replayable + current_only
    note = ("回放态：风险类按 detected_at/resolved_at 时点重建、在途按里程碑复算、逾期/净流出/趋势按"
            "effective_clock 回算；存量类（对象表只存当前态、无历史版本）如实显示当前值并标注，绝不造假历史。")
    if not world_is_sim:
        note = ("验证世界是静态快照（风险/审计均单日批量、无逐日史）——除在途票(里程碑)与逾期(due_date)"
                "等真事件流指标外，各区 headline 均显示当前值。真时点回放请切模拟世界。" )
    return {"requested": requested, "effective": effective, "world_clock": world_clock,
            "window": {"start": window[0], "end": window[1]}, "is_replay": True,
            "world_is_sim": world_is_sim, "replayable": replayable,
            "current_state_only": current_only, "note": note}


# ═══════════════════════════════════════════════════════════════════════════
# 体征带七区 builder（每区 docstring = 该区指标的完整 SQL 口径，Daniel 回查锚点）
# ═══════════════════════════════════════════════════════════════════════════
def _payment_flow(con, direction: str, clock: str | None) -> dict:
    """钱区应收/应付水位共用（direction='in'→应收、'out'→应付，G2/V8-②）：
    SELECT count(*), sum(amount_usd) FROM payments WHERE direction=? AND status='scheduled'
    ——「在外/待付的钱」。overdue 子集：同条件再加 AND due_date < clock AND (paid_date IS NULL
    OR paid_date='')（"未 paid" 防御性双查——scheduled 行理论上 paid_date 恒空，但不单信
    status 字段，沿 R19 同款防御写法，见 engine/finance_rules.py detect_finance_risks）。
    clock 不可推导（该世界 risk_events/action_log 等账本皆空）→ overdue 单列 null+reason，
    基础水位不因此整体挂起——两者是独立可知程度（红线 3：能算多少就如实给多少，不因一个
    子指标缺前提而连坐全部）。"""
    row = _one(con, "SELECT count(*) c, sum(amount_usd) v FROM payments "
                    "WHERE direction=? AND status='scheduled'", (direction,))
    out: dict[str, Any] = {"count": row["c"], "amount_usd": round(row["v"] or 0.0, 2)}
    if clock:
        od = _one(con, "SELECT count(*) c, sum(amount_usd) v FROM payments "
                       "WHERE direction=? AND status='scheduled' AND due_date < ? "
                       "AND (paid_date IS NULL OR paid_date='')", (direction, clock))
        out["overdue"] = {"count": od["c"], "amount_usd": round(od["v"] or 0.0, 2)}
    else:
        out["overdue"] = _missing("世界时钟不可推导（缺 risk_events/action_log 等账本），"
                                  "无法判定 due_date 是否已过 clock")
    return out


def _zone_money(con, tables: set[str], clock: str | None,
                replay_risk: bool = False, replay: bool = False) -> dict:
    """钱区。七指标口径：
    · 费用异常敞口(headline)：SELECT count(*), sum(affected_value_usd) FROM risk_events
      WHERE rule_id IN ('R4','R5','R6') AND status='open'——open 的超收/计划外/重复计费合计。
    · 在途货值：SELECT sum(sa.allocated_qty * declared) FROM shipments s
      JOIN shipment_allocations sa ON sa.shipment_id=s.shipment_id
      JOIN sales_order_lines sol ON sol.so_line_id=sa.so_line_id
      JOIN skus k ON k.sku_id=sol.sku_id WHERE s.status='in_transit'，
      declared = skus.declared_value_usd（NULL/'' 的行不计入并单独计数；在途行全部无
      申报价值 → null+reason。实测两世界都如此：验证世界仅 15/35 个准入 sku 有申报价值
      且都不在途、simworld 全空——不回退 unit_price 售价冒充申报价值，歧义清单#7）。
    · 被拦截超收：同敞口 SQL 但 rule_id='R4' AND status='resolved'——已闭环的超收金额即
      "系统帮你拦下的钱"；验证世界风险全 open → 真实 0，非缺数。
    · 准入毛利率分布：SELECT bucket(gross_margin_rate), count(*) FROM cost_scenarios
      GROUP BY 1，桶=<0（亏损）/0-10%/10-20%/≥20%；simworld 无 cost_scenarios 表 → null。
    · 应收水位(receivables/G2/V8-②)：见 _payment_flow(direction='in') docstring——payments
      direction='in' AND status='scheduled' 的 count+Σamount_usd，overdue 子集加
      due_date<clock 且未 paid。
    · 应付水位(payables)：_payment_flow(direction='out')，同构——「要付的钱」。
    · 14 天净流出预警(net_cash_14d)：R20 同口径（engine/finance_rules.py detect_finance_risks
      的 R20 分支，config.finance.cash_watch_window_days/cash_watch_threshold_usd 单一来源，
      不平行硬编码）——窗口 (clock, clock+window_days] 内分别 SELECT sum(amount_usd) FROM
      payments WHERE status='scheduled' AND due_date>clock AND due_date<=win_hi，
      按 direction='out'/'in' 各查一次，net=out−in；breach=net>threshold。
      receivables/payables/net_cash_14d 三者缺 payments 表 → 整块 null+reason；
      overdue/net_cash_14d 另需 clock，clock 不可推导时单独 null+reason（基础水位仍照给）。
    alert_count = open R4-R6 计数（不含新三指标——保持费用敞口语义，任务书 G2 明示不改）。
    trend：无逐日敞口快照表 → null（两世界同，红线 3）。
    回放（replay_risk，仅 sim）：费用敞口按风险活跃度(_risk_active)重建为"截至 clock 仍未闭环"的
    R4-R6；affected_value_usd 是风险行检测时快照，对活跃集求和是测量非推算，诚实。另加 in_transit_as_of
    （里程碑复算的截至当日在途票数，两世界都算）。存量项（毛利率/在途货值/应收应付水位）不动、如实标存量。"""
    ph = ",".join("?" * len(_COST_RULES))
    rfrag, rp = _risk_active(replay_risk, clock)
    row = _one(con, f"SELECT count(*) c, sum(affected_value_usd) v FROM risk_events "
                    f"WHERE rule_id IN ({ph}) AND {rfrag}", (*_COST_RULES, *rp))
    exposure_count, exposure = row["c"], round(row["v"] or 0.0, 2)

    itv = _one(con, """
        SELECT sum(sa.allocated_qty * CASE WHEN k.declared_value_usd IS NULL
                                             OR k.declared_value_usd='' THEN NULL
                                           ELSE CAST(k.declared_value_usd AS REAL) END) v,
               count(*) rows_total,
               sum(CASE WHEN k.declared_value_usd IS NULL OR k.declared_value_usd=''
                        THEN 1 ELSE 0 END) rows_unvalued,
               count(DISTINCT s.shipment_id) shipments
        FROM shipments s
        JOIN shipment_allocations sa ON sa.shipment_id = s.shipment_id
        JOIN sales_order_lines sol ON sol.so_line_id = sa.so_line_id
        JOIN skus k ON k.sku_id = sol.sku_id
        WHERE s.status = 'in_transit'""")
    if itv["rows_total"] and itv["rows_total"] == itv["rows_unvalued"]:
        # 两世界实测都会走到这里（验证世界申报价值仅 15/35 个准入 sku 有、在途行全无值；
        # simworld 全空）——按红线 3 不回退售价冒充申报价值，如实 null，歧义清单#7。
        in_transit = {"value": None,
                      "reason": f"在途分配行涉及的 sku 均无申报价值（{itv['rows_unvalued']}/"
                                f"{itv['rows_total']} 行无值），无法按『分配行×qty×申报价值』"
                                f"口径计算在途货值"}
    else:
        in_transit = {"value_usd": round(itv["v"] or 0.0, 2),
                      "in_transit_shipments": itv["shipments"],
                      "allocation_rows": itv["rows_total"],
                      "rows_without_declared_value": itv["rows_unvalued"]}

    blocked = _one(con, "SELECT count(*) c, sum(affected_value_usd) v FROM risk_events "
                        "WHERE rule_id='R4' AND status='resolved'")

    if "cost_scenarios" in tables:
        buckets = {b: c for b, c in con.execute("""
            SELECT CASE WHEN gross_margin_rate < 0    THEN '亏损(<0)'
                        WHEN gross_margin_rate < 0.10 THEN '0-10%'
                        WHEN gross_margin_rate < 0.20 THEN '10-20%'
                        ELSE '≥20%' END bucket, count(*)
            FROM cost_scenarios WHERE gross_margin_rate IS NOT NULL GROUP BY bucket""")}
        margin = {"buckets": buckets, "scenarios_total": sum(buckets.values())}
    else:
        margin = _missing("缺 cost_scenarios 表，准入/成本域未灌")

    if "payments" in tables:
        receivables = _payment_flow(con, "in", clock)
        payables = _payment_flow(con, "out", clock)
        if clock:
            window_days = _FINANCE_CFG["cash_watch_window_days"]
            threshold = _FINANCE_CFG["cash_watch_threshold_usd"]
            win_hi = _shift_date(clock, window_days)
            out_win = _one(con, "SELECT sum(amount_usd) v FROM payments WHERE direction='out' "
                                "AND status='scheduled' AND due_date > ? AND due_date <= ?",
                           (clock, win_hi))["v"] or 0.0
            in_win = _one(con, "SELECT sum(amount_usd) v FROM payments WHERE direction='in' "
                               "AND status='scheduled' AND due_date > ? AND due_date <= ?",
                          (clock, win_hi))["v"] or 0.0
            net = round(out_win - in_win, 2)
            net_cash_14d: dict[str, Any] = {
                "value_usd": net, "window_days": window_days,
                "out_scheduled_usd": round(out_win, 2), "in_scheduled_usd": round(in_win, 2),
                "threshold_usd": threshold, "breach": net > threshold,
                "window": f"({clock}, {win_hi}]"}
        else:
            net_cash_14d = _missing("世界时钟不可推导（缺 risk_events/action_log 等账本），"
                                    "无法圈定未来窗口")
    else:
        reason = "缺 payments 表，资金流域未灌（V8-② F1，datagen/finance.py 未跑）"
        receivables = _missing(reason)
        payables = _missing(reason)
        net_cash_14d = _missing(reason)

    detail: dict[str, Any] = {
        "fee_exposure": {"value_usd": exposure, "open_risks": exposure_count,
                         "rules": list(_COST_RULES)},
        "in_transit_value": in_transit,
        "intercepted_overbilling": {"value_usd": round(blocked["v"] or 0.0, 2),
                                    "resolved_r4_risks": blocked["c"]},
        "margin_distribution": margin,
        "receivables": receivables,
        "payables": payables,
        "net_cash_14d": net_cash_14d,
    }
    if replay:                       # 回放态才加：里程碑复算的截至当日在途票数（两世界都算，诚实测量）
        it_as_of = _in_transit_as_of(con, tables, clock)
        if it_as_of is not None:
            detail["in_transit_as_of"] = it_as_of
    return {
        "zone": "money", "headline_label": "费用异常敞口",
        "headline_value": exposure, "headline_unit": "usd",
        "trend": None, "alert_count": exposure_count, "detail": detail,
    }


def _fetch_otd_lines(con) -> list[tuple[str, str | None]]:
    """OTD 行级明细：fulfilled 行 → (promised, actual)。actual=该行所有已到货件的最迟到达日；
    到达 = COALESCE(shipments.ata, 该货件最后一条 event_type='delivered' 里程碑 event_time)；
    未到货件不参与该行判定（docstring 口径，红线 3：不猜未来）。"""
    rows = con.execute("""
        SELECT sol.promised_delivery_date promised,
               max(CASE WHEN a.arrival IS NOT NULL THEN date(a.arrival) END) actual
        FROM sales_order_lines sol
        JOIN shipment_allocations sa ON sa.so_line_id = sol.so_line_id
        JOIN (SELECT s.shipment_id,
                     COALESCE(NULLIF(s.ata, ''),
                              (SELECT max(m.event_time) FROM shipment_milestones m
                               WHERE m.shipment_id = s.shipment_id
                                 AND m.event_type = 'delivered')) arrival
              FROM shipments s) a ON a.shipment_id = sa.shipment_id
        WHERE sol.line_status = 'fulfilled'
        GROUP BY sol.so_line_id, sol.promised_delivery_date""").fetchall()
    return [(r["promised"], r["actual"]) for r in rows]


def _otd_stats(lines: list[tuple[str, str | None]]) -> dict:
    measured = [(p, a) for p, a in lines if a and p]
    on_time = sum(1 for p, a in measured if a <= p)
    return {"fulfilled_lines": len(lines), "measured": len(measured), "on_time": on_time,
            "excluded_no_arrival": len(lines) - len(measured),
            "rate": round(on_time / len(measured), 4) if measured else None}


def _zone_fulfillment(con, tables: set[str], clock: str | None, world_is_sim: bool) -> dict:
    """履约区。三指标口径：
    · OTD(headline)：见 _fetch_otd_lines docstring——fulfilled 行中 actual≤promised 的比例，
      分母=有到达信息的行（excluded_no_arrival 如实另计）。
    · 延误分布：SELECT bucket(delay_days), count(*) FROM shipments WHERE delay_days>0
      GROUP BY 1，桶=1-3/4-7/8-14/15-29/≥30 天；含全部状态（历史已到票也计入分布）。
    · 清关卡点：SELECT count(*) FROM shipments WHERE customs_status='not_filed'
      AND status='in_transit'——在途且尚未申报的票数。
    alert_count = 在途且 delay_days>0 的票数（此刻正在延误的货）。
    trend：模拟世界有完整到达日期账本（ata 覆盖全部 delivered）→ 按 actual 日期切
      [clock-6,clock] vs [clock-13,clock-7] 两窗对比 OTD（口径同 headline，仅窗口切片；
      任一窗分母为 0 → null，不硬造）；验证世界（静态快照）→ null。"""
    lines = _fetch_otd_lines(con)
    otd = _otd_stats(lines)

    hist = {b: c for b, c in con.execute("""
        SELECT CASE WHEN delay_days <= 3  THEN '1-3天'
                    WHEN delay_days <= 7  THEN '4-7天'
                    WHEN delay_days <= 14 THEN '8-14天'
                    WHEN delay_days <= 29 THEN '15-29天'
                    ELSE '≥30天' END bucket, count(*)
        FROM shipments WHERE delay_days > 0 GROUP BY bucket""")}
    customs = _one(con, "SELECT count(*) c FROM shipments "
                        "WHERE customs_status='not_filed' AND status='in_transit'")["c"]
    delayed_now = _one(con, "SELECT count(*) c FROM shipments "
                            "WHERE status='in_transit' AND delay_days > 0")["c"]

    trend = None
    if world_is_sim and clock:
        cur_lo, prior_lo, prior_hi = (_shift_date(clock, -6), _shift_date(clock, -13),
                                      _shift_date(clock, -7))
        cur = _otd_stats([(p, a) for p, a in lines if a and cur_lo <= a <= clock])
        prior = _otd_stats([(p, a) for p, a in lines if a and prior_lo <= a <= prior_hi])
        if cur["measured"] and prior["measured"]:
            trend = {"metric": "otd_rate", "window_days": 7, "current": cur["rate"],
                     "prior": prior["rate"],
                     "delta": round(cur["rate"] - prior["rate"], 4),
                     "basis": f"到达日窗口 [{cur_lo},{clock}] vs [{prior_lo},{prior_hi}]，"
                              f"分母 {cur['measured']}/{prior['measured']} 行"}

    return {
        "zone": "fulfillment", "headline_label": "准交率 OTD",
        "headline_value": otd["rate"], "trend": trend, "alert_count": delayed_now,
        "detail": {"otd": otd,
                   "delay_histogram": {"buckets": hist, "delayed_shipments": sum(hist.values())},
                   "customs_blocked": {"value": customs,
                                       "definition": "customs_status='not_filed' 且在途"}},
    }


def _customer_risk_map(con, replay_risk: bool = False,
                       clock: str | None = None) -> tuple[dict[str, dict], int]:
    """open 风险 → affected_so_line_ids → so → customer 的聚合中间层（客户区两指标共用）。
    返回 ({customer_id: {name, tier, exposure_usd, open_risks(set), line_ids(set)}}, 波及行数)。
    敞口=被波及订单行**去重后** Σ(qty×unit_price_usd)——行金额是可回查的一手数（qty、单价
    都在 sales_order_lines），不做 risk.affected_value_usd 按客户均摊（歧义清单#3）。
    回放（replay_risk，仅 sim）：风险集按 _risk_active 时点重建（活跃客户数=可回放）；但敞口金额
    join 的是**当前**行(qty×现价)，属存量 → 信封 current_state_only 标 customers.exposure_usd。"""
    rfrag, rp = _risk_active(replay_risk, clock)
    risk_lines: dict[str, list[str]] = {}
    for r in con.execute(f"SELECT risk_event_id, affected_so_line_ids FROM risk_events "
                         f"WHERE {rfrag}", rp):
        ids = _json_ids(r["affected_so_line_ids"])
        if ids:
            risk_lines[r["risk_event_id"]] = ids
    all_lines = sorted({lid for ids in risk_lines.values() for lid in ids})
    if not all_lines:
        return {}, 0

    ph = ",".join("?" * len(all_lines))
    line_meta = {row["so_line_id"]: row for row in con.execute(f"""
        SELECT sol.so_line_id, sol.qty, sol.unit_price_usd,
               so.customer_id, c.customer_name, c.tier
        FROM sales_order_lines sol
        JOIN sales_orders so ON so.so_id = sol.so_id
        JOIN customers c ON c.customer_id = so.customer_id
        WHERE sol.so_line_id IN ({ph})""", all_lines)}

    agg: dict[str, dict] = {}
    for rid, ids in risk_lines.items():
        for lid in ids:
            meta = line_meta.get(lid)
            if meta is None:
                continue  # 行 id 悬空（脏数据）→ 如实略过，不猜
            entry = agg.setdefault(meta["customer_id"], {
                "customer_name": meta["customer_name"], "tier": meta["tier"],
                "exposure_usd": 0.0, "open_risks": set(), "line_ids": set()})
            if lid not in entry["line_ids"]:      # 去重：多个风险波及同一行只计一次金额
                entry["line_ids"].add(lid)
                entry["exposure_usd"] += (meta["qty"] or 0) * (meta["unit_price_usd"] or 0.0)
            entry["open_risks"].add(rid)
    return agg, len(all_lines)


def _zone_customers(con, tables: set[str], replay_risk: bool = False,
                    clock: str | None = None) -> dict:
    """客户区。三指标口径：
    · 风险敞口 Top 客户(headline=有敞口客户数)：见 _customer_risk_map docstring——
      open 风险沿 affected_so_line_ids→sales_order_lines→sales_orders→customers 聚合，
      敞口=去重行 Σ(qty×unit_price_usd)，按敞口降序 Top5。
    · 准入漏斗：SELECT status, count(*) FROM admission_cases GROUP BY status
      （按状态机序呈现）；simworld 无 admission_cases 表 → null+reason。
    · 客户健康度：tier × (客户总数[SELECT tier,count(*) FROM customers GROUP BY tier]、
      被 open 风险波及客户数、open 风险数) 交叉。tier 是本体敏感字段（visibleTo cs/manager），
      经 SensitiveFieldMasker 对无权角色自动掩码——同一套权限不是两套。
    alert_count = 有风险敞口的客户数。trend：无逐日敞口快照 → null。"""
    agg, lines_hit = _customer_risk_map(con, replay_risk, clock)
    top = sorted(agg.items(), key=lambda kv: kv[1]["exposure_usd"], reverse=True)[:5]
    top_list = [{"customer_id": cid, "customer_name": v["customer_name"],
                 "exposure_usd": round(v["exposure_usd"], 2),
                 "open_risks": len(v["open_risks"]), "affected_lines": len(v["line_ids"])}
                for cid, v in top]

    if "admission_cases" in tables:
        order = ["draft", "in_precheck", "plan_ready", "priced", "quote_with_conditions",
                 "approved", "needs_more_info", "rejected"]
        raw = {s: c for s, c in con.execute(
            "SELECT status, count(*) FROM admission_cases GROUP BY status")}
        funnel = {"by_status": {s: raw[s] for s in order if s in raw}
                  | {s: c for s, c in raw.items() if s not in order},
                  "cases_total": sum(raw.values())}
    else:
        funnel = _missing("缺 admission_cases 表，准入域未灌")

    tier_total = {t if t is not None else "unknown": c for t, c in con.execute(
        "SELECT tier, count(*) FROM customers GROUP BY tier")}
    tier_risk: dict[str, dict] = defaultdict(lambda: {"customers_at_risk": 0, "open_risks": 0})
    for v in agg.values():
        key = v["tier"] if v["tier"] is not None else "unknown"
        tier_risk[key]["customers_at_risk"] += 1
        tier_risk[key]["open_risks"] += len(v["open_risks"])
    health = [{"tier": t, "customers": tier_total[t],
               "customers_at_risk": tier_risk[t]["customers_at_risk"],
               "open_risks": tier_risk[t]["open_risks"]} for t in sorted(tier_total)]

    return {
        "zone": "customers", "headline_label": "风险敞口客户",
        "headline_value": len(agg), "trend": None, "alert_count": len(agg),
        "detail": {"top_exposure": top_list,
                   "affected_line_ids_total": lines_hit,
                   "admission_funnel": funnel,
                   "health_cross": health},
    }


# ─── 供应商合规维度（K·P1，轮3 林律"该把风险项排到我面前"）───────────────────────
# 合规字段只对 compliance/manager 角色附到供应商队列行——本体 Supplier.uflpa_risk_flag
# visibleTo=[compliance,manager]（ontology sensitiveFieldRules）；其他角色载荷里根本不带这些键
# （不是带着掩码——不开新可见性洞）。audit/docs 两枚状态本体无独立 visibleTo，随 UFLPA 同门附带=
# 严格窄于任一可能声明，不越权。资质异常只标"库里显式坏值"，None/未知不臆断标红（诚实不编造）。
COMPLIANCE_ROLES = ("compliance", "manager")
_SUPPLIER_COMP_COLS = ("uflpa_risk_flag", "factory_audit_status", "compliance_docs_status")
_AUDIT_BAD = {"not_started", "pending", "failed"}       # 工厂审计显式坏态（enum 其余=passed/waived=正常）
_DOCS_BAD = {"missing", "partial", "rejected"}            # 合规文件显式坏态（enum 其余=provided/verified=正常）


def _flag_true(v: Any) -> bool:
    """布尔旗标真值判定（库里 uflpa_risk_flag 可能是 '0'/'1' 文本、0/1 整数、true/false、None）。
    只认明确真值，其余（含 None/空/'0'）一律 False——不把缺失/未知当成命中（诚实非编造）。"""
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y")


def _qual_abnormal(audit: Any, docs: Any) -> bool:
    """资质是否异常：工厂审计或合规文件落在**显式坏态集**（None/未知不算异常，不臆断）。"""
    a = str(audit).strip().lower() if audit is not None else ""
    d = str(docs).strip().lower() if docs is not None else ""
    return a in _AUDIT_BAD or d in _DOCS_BAD


def _supplier_comp_available(con, tables: set[str]) -> bool:
    """本世界 suppliers 表是否带齐三枚合规列（世界无关性红线：缺列的世界不附字段、不 500）。"""
    return "suppliers" in tables and set(_SUPPLIER_COMP_COLS) <= _columns(con, "suppliers")


def _zone_suppliers(con, tables: set[str], replay_risk: bool = False,
                    clock: str | None = None, role: str | None = None,
                    sort_compliance: bool = False) -> dict:
    """供应商区。四指标口径：
    · 交期达成率(headline)：SELECT po.supplier_id, count(*), sum(达成) FROM purchase_orders po
      JOIN (SELECT po_id, min(received_date) first_recv FROM goods_receipts GROUP BY po_id) g
      ON g.po_id=po.po_id，达成 = first_recv ≤ po.expected_ready_date；分母=有收货的 PO
      （未收货 PO 不进分母——"实际"取 PO 首张 GRN 收货日，口径选择记歧义清单#5）；
      整体率=Σ达成/Σ分母，按供应商达成率升序排名（最差在前）Top10；
      simworld 无 goods_receipts 表 → null+reason。
    · 缺陷率 Top：SELECT po.supplier_id, avg(l.defect_ppm) FROM goods_receipt_lines l
      JOIN goods_receipts g ON g.grn_id=l.grn_id JOIN purchase_orders po ON po.po_id=g.po_id
      GROUP BY 1 ORDER BY 2 DESC LIMIT 5；simworld 缺表 → null+reason。
    · 单一依赖：SELECT count(*) FROM risk_events WHERE rule_id='R14' AND status='open'。
    · 对账差异：SELECT count(*), sum(affected_value_usd) FROM risk_events
      WHERE rule_id IN ('R7','R8','R9','R10','R11','R12','R13') AND status='open'。
    · 绩效劣化（V23① R22）：count(*), sum(affected_value_usd) WHERE rule_id='R22'——供应商级
      慢性交期劣化（口径与本区交期达成率同源）。
    · 资质预警（V23① R23）：count(*) WHERE rule_id='R23'——逐证过期/临期未续。
    alert_count = open R7-R14 + R22 + R23 计数（侦察结论：本区口径是显式规则清单不自动吃新
    rule_id，故 V23① 在此显式接入）。trend：无逐日快照 → null。
    回放（replay_risk，仅 sim）：单一依赖 R14 + 对账差异 R7-R13 按 _risk_active 时点重建（活跃风险计数/
    金额=可回放，affected_value_usd 是风险行快照）；交期达成率/缺陷率是 GRN 累计存量，不动、如实标存量。
    合规维度（K·P1）：role∈COMPLIANCE_ROLES 时 worst_suppliers 行附 uflpa_risk_flag(规整bool)/
    factory_audit_status/compliance_docs_status/qual_abnormal(派生bool，单一来源在后端)，detail 附
    compliance_dimension（全库 UFLPA/资质异常计数+口径 basis；零阳性附诚实 note）；sort_compliance=True
    → 全列表重排（UFLPA 命中→资质异常→交期升序）后再取 Top10（否则阳性沉在 10 名外永远浮不上来）。
    其他角色/缺省 sort：载荷与行序逐字节不变（不带合规键，非掩码——本体 visibleTo 语义是"载荷不含"）。"""
    rfrag, rp = _risk_active(replay_risk, clock)
    comp_ok = role in COMPLIANCE_ROLES and _supplier_comp_available(con, tables)
    comp_map: dict[str, dict] = {}
    if comp_ok:
        comp_map = {r["supplier_id"]: dict(r) for r in con.execute(
            "SELECT supplier_id, uflpa_risk_flag, factory_audit_status, "
            "compliance_docs_status FROM suppliers")}
    if "goods_receipts" in tables:
        per_supplier = [dict(r) for r in con.execute("""
            SELECT po.supplier_id, s.supplier_name, count(*) pos_measured,
                   sum(CASE WHEN g.first_recv <= po.expected_ready_date THEN 1 ELSE 0 END) on_time
            FROM purchase_orders po
            JOIN (SELECT po_id, min(received_date) first_recv
                  FROM goods_receipts GROUP BY po_id) g ON g.po_id = po.po_id
            JOIN suppliers s ON s.supplier_id = po.supplier_id
            GROUP BY po.supplier_id, s.supplier_name""")]
        measured = sum(r["pos_measured"] for r in per_supplier)
        on_time = sum(r["on_time"] for r in per_supplier)
        for r in per_supplier:
            r["rate"] = round(r["on_time"] / r["pos_measured"], 4) if r["pos_measured"] else None
        per_supplier.sort(key=lambda r: (r["rate"] is None, r["rate"]))
        if comp_ok:
            # K·P1：行附合规三列+派生 qual_abnormal（规则单一来源在后端，前端只读不复刻判定）。
            for r in per_supplier:
                c = comp_map.get(r["supplier_id"], {})
                r["uflpa_risk_flag"] = _flag_true(c.get("uflpa_risk_flag"))
                r["factory_audit_status"] = c.get("factory_audit_status")
                r["compliance_docs_status"] = c.get("compliance_docs_status")
                r["qual_abnormal"] = _qual_abnormal(c.get("factory_audit_status"),
                                                    c.get("compliance_docs_status"))
            if sort_compliance:
                # 全列表重排后才截 Top10——阳性若沉在交期第 11 名之后，缺省口径永远浮不上来。
                # 组内并列仍按交期升序（最差在前，stable sort + 显式尾键，与缺省口径同语义）。
                per_supplier.sort(key=lambda r: (not r["uflpa_risk_flag"],
                                                 not r["qual_abnormal"],
                                                 r["rate"] is None, r["rate"]))
        delivery = {"rate": round(on_time / measured, 4) if measured else None,
                    "pos_measured": measured, "on_time": on_time,
                    "worst_suppliers": per_supplier[:10]}
        headline = delivery["rate"]
        headline_reason = None
    else:
        delivery = _missing("缺 goods_receipts 表，采购收货域未灌")
        headline, headline_reason = None, delivery["reason"]

    if "goods_receipt_lines" in tables:
        defect = [dict(r) for r in con.execute("""
            SELECT po.supplier_id, s.supplier_name,
                   round(avg(l.defect_ppm), 1) avg_defect_ppm, count(*) grn_lines
            FROM goods_receipt_lines l
            JOIN goods_receipts g ON g.grn_id = l.grn_id
            JOIN purchase_orders po ON po.po_id = g.po_id
            JOIN suppliers s ON s.supplier_id = po.supplier_id
            WHERE l.defect_ppm IS NOT NULL
            GROUP BY po.supplier_id, s.supplier_name
            ORDER BY avg_defect_ppm DESC LIMIT 5""")]
        defect_top: Any = {"top": defect}
    else:
        defect_top = _missing("缺 goods_receipt_lines 表，采购收货域未灌")

    r14 = _one(con, f"SELECT count(*) c FROM risk_events "
                    f"WHERE rule_id='R14' AND {rfrag}", rp)["c"]
    ph = ",".join("?" * len(_RECON_RULES))
    recon = _one(con, f"SELECT count(*) c, sum(affected_value_usd) v FROM risk_events "
                      f"WHERE rule_id IN ({ph}) AND {rfrag}", (*_RECON_RULES, *rp))
    # V23① R22/R23：供应商级绩效劣化 + 逐证资质预警（本区口径是显式规则清单，不自动吃新
    # rule_id——此处显式接入；回放语义与 R14/recon 同款走 _risk_active 时点重建）。
    r22 = _one(con, f"SELECT count(*) c, sum(affected_value_usd) v FROM risk_events "
                    f"WHERE rule_id='R22' AND {rfrag}", rp)
    r23 = _one(con, f"SELECT count(*) c FROM risk_events "
                    f"WHERE rule_id='R23' AND {rfrag}", rp)["c"]

    zone = {
        "zone": "suppliers", "headline_label": "交期达成率",
        "headline_value": headline, "trend": None,
        "alert_count": r14 + recon["c"] + r22["c"] + r23,
        "detail": {"delivery_hit_rate": delivery, "defect_top": defect_top,
                   "single_source_r14": {"value": r14},
                   "recon_diff_r7_r13": {"open_risks": recon["c"],
                                         "amount_usd": round(recon["v"] or 0.0, 2)},
                   "perf_degradation_r22": {"open_risks": r22["c"],
                                            "amount_usd": round(r22["v"] or 0.0, 2)},
                   "qual_expiry_r23": {"open_risks": r23}},
    }
    if role in COMPLIANCE_ROLES:               # K·P1：合规维度摘要（其他角色载荷不含此键）
        if comp_ok:
            uflpa_total = sum(1 for c in comp_map.values()
                              if _flag_true(c.get("uflpa_risk_flag")))
            qual_total = sum(1 for c in comp_map.values()
                             if _qual_abnormal(c.get("factory_audit_status"),
                                               c.get("compliance_docs_status")))
            comp_dim: dict[str, Any] = {
                "available": True,
                "sort": "compliance" if sort_compliance else "default",
                "uflpa_flagged_total": uflpa_total,
                "qual_abnormal_total": qual_total,
                "suppliers_total": len(comp_map),
                "basis": ("UFLPA 旗标/工厂审计/合规文件三列来自 suppliers 表现查（全库计数含无收货记录"
                          "的供应商）；合规排序=UFLPA 命中优先、次按资质显式异常（审计 not_started/"
                          "pending/failed 或文件 missing/partial/rejected；未知/空不臆断）、再按交期"
                          "达成率升序。"),
            }
            if uflpa_total == 0:
                comp_dim["note"] = (f"当前无 UFLPA 标记供应商（全库 {len(comp_map)} 家现查为 0）"
                                    f"——合规排序按资质异常、再按交期兜底。")
            zone["detail"]["compliance_dimension"] = comp_dim
        else:
            zone["detail"]["compliance_dimension"] = {
                "available": False,
                "reason": "本世界 suppliers 表缺合规列（UFLPA/审计/文件），无合规维度可排——"
                          "如实缺，非零阳性。"}
    if headline_reason:
        zone["headline_reason"] = headline_reason
    return zone


def _zone_inventory(con, tables: set[str], replay_risk: bool = False,
                    clock: str | None = None) -> dict:
    """库存区。三指标口径：
    · 安全库存击穿(headline)：SELECT * FROM inventory_positions WHERE available_qty <
      safety_stock，计数 + 缺口(safety-available)降序清单（cap 20）。
    · 盘点差异：SELECT count(*), sum(abs(variance)) FROM cycle_counts WHERE variance != 0；
      simworld 无 cycle_counts 表 → null+reason（其 sim_cycle_counts 有账实数据，并入与否
      记歧义清单#4，本次保守不并）。
    · 在途补给可救性：open R1-R3 风险的 affected_so_line_ids 中 line_status∈(open,at_risk)
      的行，逐行只读复算 app/warehouse_actions.py::_spot_position 选仓逻辑（目的仓=
      risk.warehouse_id 或 shipment.destination_warehouse 的头寸优先；无头寸行回退全网
      available_qty 最大且>0 的头寸）：现货≥需求 → 可全救；0<现货<需求 → 可部分救。
      行独立判定，不模拟多行争抢同一头寸的先后耗减（歧义清单#6）。
    alert_count = 击穿数 + 盘点差异数（差异缺数时按 0 计入 alert，detail 如实标 null）。
    回放（replay_risk，仅 sim）：可救性复算的**风险集**按 _risk_active 时点重建（截至当日活跃的 R1-R3）；
    但可救性判定 join 的现货头寸/行状态是**当前**值（无库存历史版本）→ 属存量近似，信封标
    inventory.rescuable_risk_set 为 sim 可回放、safety_breaches/count_variance 为 current。"""
    rfrag, rp = _risk_active(replay_risk, clock)
    breaches = [dict(r) for r in con.execute("""
        SELECT inventory_position_id, sku_id, warehouse_id, available_qty, safety_stock,
               safety_stock - available_qty gap
        FROM inventory_positions WHERE available_qty < safety_stock
        ORDER BY gap DESC, inventory_position_id LIMIT 20""")]
    breach_count = _one(con, "SELECT count(*) c FROM inventory_positions "
                             "WHERE available_qty < safety_stock")["c"]

    if "cycle_counts" in tables:
        cc = _one(con, "SELECT count(*) c, sum(abs(variance)) t FROM cycle_counts "
                       "WHERE variance != 0")
        variance: Any = {"count": cc["c"], "abs_variance_units": cc["t"] or 0}
        variance_alert = cc["c"]
    else:
        variance = _missing("缺 cycle_counts 表（sim_cycle_counts 未并入，见歧义清单）")
        variance_alert = 0

    ph = ",".join("?" * len(_DELAY_RULES))
    stats = {"risks_checked": 0, "lines_checked": 0, "lines_fully_savable": 0,
             "lines_partially_savable": 0, "lines_no_stock": 0}
    for risk in con.execute(f"SELECT risk_event_id, warehouse_id, shipment_id, "
                            f"affected_so_line_ids FROM risk_events "
                            f"WHERE {rfrag} AND rule_id IN ({ph})", (*rp, *_DELAY_RULES)):
        line_ids = _json_ids(risk["affected_so_line_ids"])
        if not line_ids:
            continue
        stats["risks_checked"] += 1
        target_wh = risk["warehouse_id"]
        if not target_wh and risk["shipment_id"]:
            row = _one(con, "SELECT destination_warehouse FROM shipments WHERE shipment_id=?",
                       (risk["shipment_id"],))
            target_wh = row["destination_warehouse"] if row else None
        lph = ",".join("?" * len(line_ids))
        for sol in con.execute(f"SELECT so_line_id, sku_id, qty FROM sales_order_lines "
                               f"WHERE so_line_id IN ({lph}) "
                               f"AND line_status IN ('open','at_risk')", line_ids):
            stats["lines_checked"] += 1
            pos = None
            if target_wh:
                pos = _one(con, "SELECT available_qty FROM inventory_positions "
                                "WHERE sku_id=? AND warehouse_id=?", (sol["sku_id"], target_wh))
            if pos is None:
                pos = _one(con, "SELECT available_qty FROM inventory_positions "
                                "WHERE sku_id=? AND available_qty>0 "
                                "ORDER BY available_qty DESC, inventory_position_id LIMIT 1",
                           (sol["sku_id"],))
            avail = pos["available_qty"] if pos else 0
            if avail >= (sol["qty"] or 0) and avail > 0:
                stats["lines_fully_savable"] += 1
            elif avail > 0:
                stats["lines_partially_savable"] += 1
            else:
                stats["lines_no_stock"] += 1

    return {
        "zone": "inventory", "headline_label": "安全库存击穿 SKU",
        "headline_value": breach_count, "trend": None,
        "alert_count": breach_count + variance_alert,
        "detail": {"safety_breaches": {"count": breach_count, "positions": breaches},
                   "count_variance": variance,
                   "rescuable": stats},
    }


def _zone_ai(con, tables: set[str], clock: str | None, world_is_sim: bool) -> dict:
    """AI 运营账区。四指标口径（今日 = 世界时钟 clock，见模块 docstring 公式）：
    · 今日(headline)：检测数 = SELECT count(*) FROM risk_events WHERE date(detected_at)=clock
      （两世界同一来源：引擎/模拟检测都落 risk_events）；提案/批准/驳回 = 验证世界从
      action_log 按 action IN ('ProposeMitigation','ApproveMitigation','RejectMitigation')
      AND date(timestamp)=clock AND result='ok' 计数（注：验证世界 seed 的存量提案不带时间戳，
      故今日计 0 是账本如实读数）；模拟世界从 sim_ai_activity 按 activity∈(propose,approve,
      reject) AND sim_date=clock 计数。今日通过率 = 今日 approve/(approve+reject)，
      今日无决策 → null（绝不拿历史冒充今日）。
    · 累计：tasks 表——提案=approval_status IN ('pending','approved','rejected') 计数、
      通过率=approved/(approved+rejected)（无决策→null）；模拟世界另附 sim_ai_activity
      全期五动作（detect/propose/approve/reject/close）计数与最近活动日。
    · 处置记忆命中：SELECT count(*), sum(cited_precedent_ids NOT IN ('','[]')) FROM
      resolution_memory（status='active'不过滤——全量账本）；命中率=cited/total（0 行→null）。
    · llm_calls：SELECT call_type, count(*) FROM llm_calls GROUP BY 1 + 总数；
      simworld 无 llm_calls 表 → null+reason。
    alert_count = 今日 status IN ('error','degraded') 的 llm_calls 数（缺表→0）。
    trend：模拟世界检测流水有完整日期 → risk_events 按 detected_at 切 7 天窗对比检测数；
    验证世界（单日快照）→ null。"""
    detections_today = 0
    if clock:
        detections_today = _one(con, "SELECT count(*) c FROM risk_events "
                                     "WHERE date(detected_at)=?", (clock,))["c"]

    proposals = approvals = rejections = 0
    if world_is_sim and "sim_ai_activity" in tables and clock:
        counts = {a: c for a, c in con.execute(
            "SELECT activity, count(*) FROM sim_ai_activity WHERE sim_date=? GROUP BY activity",
            (clock,))}
        proposals, approvals, rejections = (counts.get("propose", 0), counts.get("approve", 0),
                                            counts.get("reject", 0))
    elif "action_log" in tables and clock:
        counts = {a: c for a, c in con.execute(
            "SELECT action, count(*) FROM action_log WHERE date(timestamp)=? AND result='ok' "
            "AND action IN ('ProposeMitigation','ApproveMitigation','RejectMitigation') "
            "GROUP BY action", (clock,))}
        proposals, approvals, rejections = (counts.get("ProposeMitigation", 0),
                                            counts.get("ApproveMitigation", 0),
                                            counts.get("RejectMitigation", 0))
    decided_today = approvals + rejections
    today = {"date": clock, "detections": detections_today, "proposals": proposals,
             "approvals": approvals, "rejections": rejections,
             "approval_rate": round(approvals / decided_today, 4) if decided_today else None}

    t = _one(con, "SELECT sum(approval_status IN ('pending','approved','rejected')) p, "
                  "sum(approval_status='approved') a, sum(approval_status='rejected') r, "
                  "sum(approval_status='pending') pd FROM tasks")
    decided = (t["a"] or 0) + (t["r"] or 0)
    all_time: dict[str, Any] = {
        "proposals": t["p"] or 0, "approved": t["a"] or 0, "rejected": t["r"] or 0,
        "pending": t["pd"] or 0,
        "approval_rate": round((t["a"] or 0) / decided, 4) if decided else None}
    if "sim_ai_activity" in tables:
        sim_counts = {a: c for a, c in con.execute(
            "SELECT activity, count(*) FROM sim_ai_activity GROUP BY activity")}
        all_time["sim_activity"] = sim_counts
        all_time["last_ai_activity_date"] = _one(
            con, "SELECT max(sim_date) m FROM sim_ai_activity")["m"]

    if "resolution_memory" in tables:
        m = _one(con, "SELECT count(*) c, sum(cited_precedent_ids IS NOT NULL "
                      "AND cited_precedent_ids NOT IN ('','[]')) hit FROM resolution_memory")
        memory: Any = {"total": m["c"], "with_cited_precedents": m["hit"] or 0,
                       "hit_rate": round((m["hit"] or 0) / m["c"], 4) if m["c"] else None}
    else:
        memory = _missing("缺 resolution_memory 表")

    alert = 0
    if "llm_calls" in tables:
        by_type = {ct: c for ct, c in con.execute(
            "SELECT call_type, count(*) FROM llm_calls GROUP BY call_type")}
        llm: Any = {"total": sum(by_type.values()), "by_call_type": by_type}
        if clock:
            alert = _one(con, "SELECT count(*) c FROM llm_calls "
                              "WHERE date(created_at)=? AND status IN ('error','degraded')",
                         (clock,))["c"]
    else:
        llm = _missing("缺 llm_calls 表，AI 调用审计未灌")

    trend = None
    if world_is_sim and clock:
        cur_lo, prior_lo, prior_hi = (_shift_date(clock, -6), _shift_date(clock, -13),
                                      _shift_date(clock, -7))
        cur = _one(con, "SELECT count(*) c FROM risk_events "
                        "WHERE date(detected_at) BETWEEN ? AND ?", (cur_lo, clock))["c"]
        prior = _one(con, "SELECT count(*) c FROM risk_events "
                          "WHERE date(detected_at) BETWEEN ? AND ?", (prior_lo, prior_hi))["c"]
        trend = {"metric": "detections", "window_days": 7, "current": cur, "prior": prior,
                 "delta": cur - prior,
                 "basis": f"detected_at 窗口 [{cur_lo},{clock}] vs [{prior_lo},{prior_hi}]"}

    return {
        "zone": "ai", "headline_label": "AI 今日",
        "headline_value": f"{detections_today} 检 / {proposals} 提案",
        "trend": trend, "alert_count": alert,
        "detail": {"today": today, "all_time": all_time,
                   "resolution_memory": memory, "llm_calls": llm},
    }


def _zone_decisions(con, tables: set[str], clock: str | None,
                    assignee_role: str | None = None) -> dict:
    """待拍板区（老板收件箱）。三指标口径：
    · 待批提案(headline=件数)：SELECT * FROM tasks WHERE approval_status='pending'；
      金额 = proposal_params JSON 的 est_cost_usd，缺则回退父风险 affected_value_usd
      （两来源都标注在 amount_source）；等待起点 = action_log 中该 task 的
      ProposeMitigation result='ok' 最新 timestamp（无记录 → null，不编时长）；
      排序 = 金额降序（null 殿后）、再按等待起点升序（等得久的在前）。cap 50
      （P1/P2 修复：原 cap 20 静默截断——headline 计全量 22、列表只回 20，画面"卡片 22 vs
      列表 20 行"对不上；改 50 让常见量级全显，并加 pending_total=全量数供前端头部注明"共 N
      条"、>50 时如实标"显示前 50 条"防静默截断）。
    · 超期任务：SELECT count(*) FROM tasks WHERE status NOT IN ('done','cancelled')
      AND date(due_at) < clock；simworld tasks 无 due_at 列 → null+reason。
    · 升级件：SELECT count(*) FROM tasks WHERE escalation_level>0 AND status NOT IN
      ('done','cancelled')；列缺 → null+reason。
    alert_count = 超期数 + 升级数（缺数按 0 计入 alert，detail 如实标 null）。

    可选 assignee_role（V22 任务1"我组的"）：缺省 None → 逐字节不变（老板收件箱=全部 pending）；
    传入 → 只对**待批提案列表**按 assignee_role 过滤（headline_value/pending_total 随之为过滤后件数，
    卡片数=列表行数照旧自洽）。刻意只筛待批提案：超期/升级件是任务生命周期的独立信号（header 徽标
    已注明"不是待批提案数"），不随本筛变，也不改任何金额掩码（掩码仍由 _apply_role_masks 按 X-Role 判）。
    合法性由路由层 _ROSTER_ROLES 守卫（非法值 422 白话），本函数只做过滤、信任入参已校验。"""
    task_cols = _columns(con, "tasks")
    pending_rows = [dict(r) for r in con.execute(
        "SELECT task_id, risk_event_id, title, priority, proposed_action, proposal_params, "
        "assignee_role FROM tasks WHERE approval_status='pending'")]
    if assignee_role is not None:                     # "我组的"筛选：只留指派给该角色的待批提案
        pending_rows = [r for r in pending_rows if r["assignee_role"] == assignee_role]

    proposed_at: dict[str, str] = {}
    if "action_log" in tables and pending_rows:
        ph = ",".join("?" * len(pending_rows))
        proposed_at = {r["target_object_id"]: r["ts"] for r in con.execute(
            f"SELECT target_object_id, max(timestamp) ts FROM action_log "
            f"WHERE action='ProposeMitigation' AND result='ok' "
            f"AND target_object_id IN ({ph}) GROUP BY target_object_id",
            [t["task_id"] for t in pending_rows])}

    items = []
    for t in pending_rows:
        params = {}
        try:
            params = json.loads(t["proposal_params"]) if t["proposal_params"] else {}
        except (ValueError, TypeError):
            params = {}
        amount, source = params.get("est_cost_usd"), "proposal_params.est_cost_usd"
        if amount is None and t["risk_event_id"]:
            row = _one(con, "SELECT affected_value_usd FROM risk_events WHERE risk_event_id=?",
                       (t["risk_event_id"],))
            amount = row["affected_value_usd"] if row else None
            source = "risk_events.affected_value_usd"
        items.append({"task_id": t["task_id"], "risk_event_id": t["risk_event_id"],
                      "title": t["title"], "priority": t["priority"],
                      "proposed_action": t["proposed_action"],
                      "assignee_role": t["assignee_role"],
                      "amount_usd": round(amount, 2) if amount is not None else None,
                      "amount_source": source if amount is not None else None,
                      "waiting_since": proposed_at.get(t["task_id"])})
    items.sort(key=lambda i: (i["amount_usd"] is None, -(i["amount_usd"] or 0.0),
                              i["waiting_since"] or "9999", i["task_id"]))

    if "due_at" in task_cols and clock:
        overdue: Any = {"value": _one(
            con, "SELECT count(*) c FROM tasks WHERE status NOT IN ('done','cancelled') "
                 "AND due_at IS NOT NULL AND date(due_at) < ?", (clock,))["c"]}
    else:
        overdue = _missing("tasks 无 due_at 列（模拟世界任务不带 SLA 期限）")
    if "escalation_level" in task_cols:
        escalated: Any = {"value": _one(
            con, "SELECT count(*) c FROM tasks WHERE escalation_level > 0 "
                 "AND status NOT IN ('done','cancelled')")["c"]}
    else:
        escalated = _missing("tasks 无 escalation_level 列")

    return {
        "zone": "decisions", "headline_label": "待批提案",
        "headline_value": len(items), "trend": None,
        "alert_count": (overdue.get("value") or 0) + (escalated.get("value") or 0),
        # pending_total=全量待批数（=headline_value），列表切到 50（P1/P2：cap 20→50 消除
        # "卡片数 vs 列表行数"不一致；前端据 pending_total 头部注"共 N 条"并在 >50 时诚实标截断）。
        "detail": {"pending_proposals": items[:50], "pending_total": len(items),
                   "overdue_tasks": overdue, "escalated_tasks": escalated},
    }


# ═══════════════════════════════════════════════════════════════════════════
# 小全景（panorama）builder
# ═══════════════════════════════════════════════════════════════════════════
def _group_nodes(rows: list[dict], layer: str, key_field: str, label_fmt: str) -> list[dict]:
    """>40 实体的分组聚合（模块 docstring 聚合规则）：按 key_field 分组为 {id, label, count}。"""
    groups: dict[str, int] = defaultdict(int)
    for r in rows:
        groups[r[key_field] or "未知"] += 1
    return [{"id": f"{layer}:{k}", "layer": layer, "label": label_fmt.format(key=k, n=n),
             "group_key": k, "count": n, "alert_count": 0, "alerts": []}
            for k, n in sorted(groups.items())]


def _build_panorama(con, tables: set[str], replay_risk: bool = False,
                    clock: str | None = None) -> dict:
    """五层节点 + 本体关系投影边 + 异常锚定 + 迷你指标。全部口径：
    · customers 层：SELECT customer_id, customer_name, us_state FROM customers；>40 →
      按 us_state 分组（分组键选 us_state 而非 tier——tier 是敏感字段，不进分组标签）。
    · orders 层（恒聚合，规格指定"按客户聚合计数"）：SELECT so.customer_id,
      count(DISTINCT so.so_id), count(sol), sum(at_risk), sum(open) FROM sales_orders so
      LEFT JOIN sales_order_lines sol GROUP BY customer_id；customers 聚合时锚点随组。
    · shipments 层：status != 'delivered' 的票（在途/清关/计划/到港），实体节点带
      lane=origin_locode→destination_locode、eta_current、delay_days、containers（
      SELECT shipment_id,count(*) FROM containers GROUP BY 1）与该票在途货值；>40 → 按 lane
      分组节点（count/delayed_count/containers/在途货值合计）。
    · suppliers 层：SELECT supplier_id, supplier_name, city, lead_time_days FROM suppliers
      （不选敏感列 uflpa_risk_flag）；>40 → 按 city 分组。
    · warehouses 层：SELECT warehouse_id, region, type FROM warehouses + 迷你指标击穿计数
      （SELECT warehouse_id,count(*) FROM inventory_positions WHERE available<safety GROUP BY 1）。
    · 边（本体关系投影，via 注明所投影关系链）：customer→orders 组（customer_has_order）；
      orders 组→货件节点（so_has_line+allocation_of_line+allocation_on_shipment 链，
      SELECT DISTINCT so.customer_id, sa.shipment_id ... WHERE s.status!='delivered'）；
      supplier→货件（shipment_from_po 投影：shipments.po_ids（管道分隔）→purchase_orders.
      supplier_id）；货件→仓（shipment_to_warehouse：destination_warehouse）。
      货件层聚合时上述边的货件端并到 lane 组（count 累加）。
    · 异常锚定：open 风险 → shipment_id 对应实体/lane 组节点；已 delivered（不在层内）→
      unanchored 如实列出（带锚说明）；warehouse_id → 仓节点；supplier_id（缺则经 po_id→
      purchase_orders.supplier_id）→ 供应商节点。节点 alerts 各 cap 5（alert_count 为全量）。
    · 在途货值（迷你指标）＝钱区同一公式按票聚合；申报价值全空世界 → 该字段 null（不编）。"""
    customers = [dict(r) for r in con.execute(
        "SELECT customer_id, customer_name, us_state FROM customers ORDER BY customer_id")]
    suppliers = [dict(r) for r in con.execute(
        "SELECT supplier_id, supplier_name, city, lead_time_days FROM suppliers "
        "ORDER BY supplier_id")]
    warehouses = [dict(r) for r in con.execute(
        "SELECT warehouse_id, region, type FROM warehouses ORDER BY warehouse_id")]
    shipments = [dict(r) for r in con.execute("""
        SELECT shipment_id, origin_port_locode, destination_port_locode, destination_warehouse,
               status, eta_current, delay_days, customs_status
        FROM shipments WHERE status != 'delivered' ORDER BY shipment_id""")]

    containers_by_shp = {sid: c for sid, c in con.execute(
        "SELECT shipment_id, count(*) FROM containers GROUP BY shipment_id")} \
        if "containers" in tables else {}
    value_by_shp = {r["shipment_id"]: (round(r["v"], 2) if r["v"] is not None else None)
                    for r in con.execute("""
        SELECT sa.shipment_id, sum(sa.allocated_qty *
               CASE WHEN k.declared_value_usd IS NULL OR k.declared_value_usd='' THEN NULL
                    ELSE CAST(k.declared_value_usd AS REAL) END) v
        FROM shipment_allocations sa
        JOIN sales_order_lines sol ON sol.so_line_id = sa.so_line_id
        JOIN skus k ON k.sku_id = sol.sku_id
        JOIN shipments s ON s.shipment_id = sa.shipment_id
        WHERE s.status = 'in_transit' GROUP BY sa.shipment_id""")}

    def lane_of(s: dict) -> str:
        return f"{s['origin_port_locode'] or '?'}→{s['destination_port_locode'] or '?'}"

    aggregated: list[str] = []

    # —— customers / orders 层 ——
    if len(customers) > _LAYER_CAP:
        aggregated.append("customers")
        cust_nodes = _group_nodes(customers, "customers", "us_state", "客户×{n}（{key}）")
        cust_anchor = {c["customer_id"]: f"customers:{c['us_state'] or '未知'}"
                       for c in customers}
    else:
        cust_nodes = [{"id": f"customer:{c['customer_id']}", "layer": "customers",
                       "label": c["customer_name"], "us_state": c["us_state"],
                       "alert_count": 0, "alerts": []} for c in customers]
        cust_anchor = {c["customer_id"]: f"customer:{c['customer_id']}" for c in customers}

    order_rows = con.execute("""
        SELECT so.customer_id, count(DISTINCT so.so_id) so_count, count(sol.so_line_id) line_count,
               sum(CASE WHEN sol.line_status='at_risk' THEN 1 ELSE 0 END) at_risk_lines,
               sum(CASE WHEN sol.line_status='open' THEN 1 ELSE 0 END) open_lines
        FROM sales_orders so
        LEFT JOIN sales_order_lines sol ON sol.so_id = so.so_id
        GROUP BY so.customer_id""").fetchall()
    order_nodes: dict[str, dict] = {}
    for r in order_rows:
        anchor = cust_anchor.get(r["customer_id"])
        if anchor is None:
            continue
        node = order_nodes.setdefault(anchor, {
            "id": f"orders:{anchor}", "layer": "orders", "label": "",
            "so_count": 0, "line_count": 0, "at_risk_lines": 0, "open_lines": 0,
            "alert_count": 0, "alerts": []})
        node["so_count"] += r["so_count"]
        node["line_count"] += r["line_count"]
        node["at_risk_lines"] += r["at_risk_lines"] or 0
        node["open_lines"] += r["open_lines"] or 0
    for node in order_nodes.values():
        node["label"] = f"订单×{node['so_count']}"

    # —— shipments 层（>40 → lane 分组）——
    if len(shipments) > _LAYER_CAP:
        aggregated.append("shipments")
        lanes: dict[str, dict] = {}
        for s in shipments:
            lane = lanes.setdefault(lane_of(s), {
                "id": f"lane:{lane_of(s)}", "layer": "shipments", "label": lane_of(s),
                "count": 0, "delayed_count": 0, "container_count": 0,
                "in_transit_value_usd": None, "statuses": defaultdict(int),
                "alert_count": 0, "alerts": []})
            lane["count"] += 1
            lane["statuses"][s["status"]] += 1
            if (s["delay_days"] or 0) > 0:
                lane["delayed_count"] += 1
            lane["container_count"] += containers_by_shp.get(s["shipment_id"], 0)
            v = value_by_shp.get(s["shipment_id"])
            if v is not None:
                lane["in_transit_value_usd"] = round((lane["in_transit_value_usd"] or 0.0) + v, 2)
        for lane in lanes.values():
            lane["statuses"] = dict(lane["statuses"])
        ship_nodes = [lanes[k] for k in sorted(lanes)]
        ship_anchor = {s["shipment_id"]: f"lane:{lane_of(s)}" for s in shipments}
        ship_wh = {f"lane:{lane_of(s)}": s["destination_warehouse"] for s in shipments}
    else:
        ship_nodes = [{"id": f"shipment:{s['shipment_id']}", "layer": "shipments",
                       "label": s["shipment_id"], "lane": lane_of(s), "status": s["status"],
                       "eta_current": s["eta_current"], "delay_days": s["delay_days"],
                       "customs_status": s["customs_status"],
                       "container_count": containers_by_shp.get(s["shipment_id"], 0),
                       "in_transit_value_usd": value_by_shp.get(s["shipment_id"]),
                       "alert_count": 0, "alerts": []} for s in shipments]
        ship_anchor = {s["shipment_id"]: f"shipment:{s['shipment_id']}" for s in shipments}
        ship_wh = {f"shipment:{s['shipment_id']}": s["destination_warehouse"] for s in shipments}

    # —— suppliers / warehouses 层 ——
    if len(suppliers) > _LAYER_CAP:
        aggregated.append("suppliers")
        sup_nodes = _group_nodes(suppliers, "suppliers", "city", "供应商×{n}（{key}）")
        sup_anchor = {s["supplier_id"]: f"suppliers:{s['city'] or '未知'}" for s in suppliers}
    else:
        sup_nodes = [{"id": f"supplier:{s['supplier_id']}", "layer": "suppliers",
                      "label": s["supplier_name"], "city": s["city"],
                      "lead_time_days": s["lead_time_days"],
                      "alert_count": 0, "alerts": []} for s in suppliers]
        sup_anchor = {s["supplier_id"]: f"supplier:{s['supplier_id']}" for s in suppliers}

    breach_by_wh = {w: c for w, c in con.execute(
        "SELECT warehouse_id, count(*) FROM inventory_positions "
        "WHERE available_qty < safety_stock GROUP BY warehouse_id")}
    wh_nodes = [{"id": f"warehouse:{w['warehouse_id']}", "layer": "warehouses",
                 "label": w["warehouse_id"], "region": w["region"], "type": w["type"],
                 "safety_breach_count": breach_by_wh.get(w["warehouse_id"], 0),
                 "alert_count": 0, "alerts": []} for w in warehouses]
    wh_anchor = {w["warehouse_id"]: f"warehouse:{w['warehouse_id']}" for w in warehouses}

    # —— 边（本体关系投影，聚合端点自动折叠 + count 累加）——
    edge_acc: dict[tuple[str, str, str], int] = defaultdict(int)
    for anchor, node in order_nodes.items():
        edge_acc[(anchor, node["id"], "customer_has_order")] += node["so_count"]
    for r in con.execute("""
        SELECT so.customer_id, sa.shipment_id, count(*) links
        FROM shipment_allocations sa
        JOIN sales_order_lines sol ON sol.so_line_id = sa.so_line_id
        JOIN sales_orders so ON so.so_id = sol.so_id
        JOIN shipments s ON s.shipment_id = sa.shipment_id
        WHERE s.status != 'delivered'
        GROUP BY so.customer_id, sa.shipment_id"""):
        cust_node, shp_node = cust_anchor.get(r["customer_id"]), ship_anchor.get(r["shipment_id"])
        if cust_node and shp_node and cust_node in order_nodes:
            edge_acc[(order_nodes[cust_node]["id"], shp_node,
                      "so_has_line+allocation_of_line+allocation_on_shipment")] += r["links"]

    po_supplier = {p: s for p, s in con.execute(
        "SELECT po_id, supplier_id FROM purchase_orders")} \
        if "purchase_orders" in tables else {}
    po_ids_by_shp = {r["shipment_id"]: r["po_ids"] for r in con.execute(
        "SELECT shipment_id, po_ids FROM shipments "
        "WHERE status != 'delivered' AND po_ids IS NOT NULL AND po_ids != ''")}
    for sid, po_ids in po_ids_by_shp.items():
        shp_node = ship_anchor.get(sid)
        if not shp_node:
            continue
        for po_id in str(po_ids).split("|"):
            sup_node = sup_anchor.get(po_supplier.get(po_id.strip()))
            if sup_node:
                edge_acc[(sup_node, shp_node, "shipment_from_po")] += 1
    for shp_node_id, wh in ship_wh.items():
        wh_node = wh_anchor.get(wh)
        if wh_node:
            edge_acc[(shp_node_id, wh_node, "shipment_to_warehouse")] += 1
    edges = [{"source": s, "target": t, "via": via, "count": c}
             for (s, t, via), c in sorted(edge_acc.items())]

    # —— 异常锚定（open 风险 → 节点；锚不在图内 → unanchored 如实列出）——
    # 回放（replay_risk，仅 sim）：锚定的风险集按 _risk_active 时点重建为"截至 clock 仍活跃"；
    # 节点/边是当前态拓扑（无历史版本），故仅重算 alert 集，节点存量如实（信封 panorama.nodes/edges）。
    rfrag, rp = _risk_active(replay_risk, clock)
    nodes_by_id = {n["id"]: n for n in
                   (cust_nodes + list(order_nodes.values()) + ship_nodes + sup_nodes + wh_nodes)}
    unanchored: list[dict] = []
    for r in con.execute(f"SELECT risk_event_id, rule_id, type, severity, shipment_id, "
                         f"warehouse_id, supplier_id, po_id FROM risk_events "
                         f"WHERE {rfrag} ORDER BY risk_event_id", rp):
        alert = {"risk_event_id": r["risk_event_id"], "rule_id": r["rule_id"],
                 "type": r["type"], "severity": r["severity"]}
        node_id = None
        if r["shipment_id"]:
            node_id = ship_anchor.get(r["shipment_id"])
        if node_id is None and r["warehouse_id"]:
            node_id = wh_anchor.get(r["warehouse_id"])
        if node_id is None:
            supplier_id = r["supplier_id"] or po_supplier.get(r["po_id"])
            if supplier_id:
                node_id = sup_anchor.get(supplier_id)
        node = nodes_by_id.get(node_id) if node_id else None
        if node is not None:
            node["alert_count"] += 1
            if len(node["alerts"]) < 5:
                node["alerts"].append(alert)
        else:
            anchor_desc = (f"shipment:{r['shipment_id']}（已 delivered，不在活跃层）"
                           if r["shipment_id"] else "无 shipment/warehouse/supplier/po 锚")
            unanchored.append(alert | {"anchor": anchor_desc})

    layers = {
        "customers": {"granularity": "group" if "customers" in aggregated else "entity",
                      "nodes": cust_nodes},
        "orders": {"granularity": "group", "nodes": sorted(order_nodes.values(),
                                                           key=lambda n: n["id"]),
                   "note": "规格指定恒按客户聚合计数"},
        "shipments": {"granularity": "group" if "shipments" in aggregated else "entity",
                      "nodes": ship_nodes,
                      "note": "仅未 delivered 票；已到票的 open 风险见 alerts_unanchored"},
        "suppliers": {"granularity": "group" if "suppliers" in aggregated else "entity",
                      "nodes": sup_nodes},
        "warehouses": {"granularity": "entity", "nodes": wh_nodes},
    }
    return {
        "layers": layers, "edges": edges, "alerts_unanchored": unanchored[:50],
        "meta": {"layer_cap": _LAYER_CAP, "aggregated_layers": aggregated,
                 "layer_counts": {k: len(v["nodes"]) for k, v in layers.items()},
                 "edge_count": len(edges),
                 "open_risks_total": _one(con, f"SELECT count(*) c FROM risk_events "
                                               f"WHERE {rfrag}", rp)["c"],
                 "alerts_unanchored_total": len(unanchored)},
    }


# ═══════════════════════════════════════════════════════════════════════════
# AI 工作流条目「执行方式」徽标（L-UX 轮2 诚实跳过项的清偿，不变量11 标注义务）
# ═══════════════════════════════════════════════════════════════════════════
# 为什么这样建（≤5 行，AGENTS.md §4）：
#   · 前端要标注「确定性剧本 vs 真模型」，但 ai-flow 载荷原本无 mode 字段——本函数只在**可判定**
#     的条目上补 mode（'deterministic'|'llm'），判不了的绝不编造（宁缺勿造，同 provenance 的诚实空态）。
#   · 唯一可靠的 ai_action→run 桥接是 commands 台账的幂等键：runtime 写步骤的键形如 run:{run_id}:{step_no}
#     （agent/runtime.py::_execute），且 commands.object_id == action_log.target_object_id（assign/propose
#     两写动作 object_id 即 task_id，两表同值）。据此从 run 的 think 步 result_json.mode 现查该动作的执行方式。
#   · 纯读、零写、不改任何事件语义；缺 commands/agent_run_steps 表的世界→空映射→无条目带 mode（byte-identical）。
def _runtime_ai_action_modes(con, tables: set[str]) -> dict[tuple[str, str], str]:
    """预建 {(action, object_id): mode} 映射——把 runtime 派生的 ai_action 连回其 run 的 think 步执行方式。

    桥接链：action_log(ai-agent 写) ── (action, target_object_id) ── commands(idempotency_key='run:RUN:step')
            ── run_id ── agent_run_steps(kind='think').result_json.mode。
    只收无歧义键（同一 (action, object_id) 落在多个 run 且 mode 冲突时不入表——判不了不编造）。
    缺表世界返回空 dict（调用方据此不给任何条目补 mode，与改动前载荷逐字等价）。"""
    if "commands" not in tables or "agent_run_steps" not in tables:
        return {}
    # run_id → mode（每 run 取首个 think 步；一 run 一 think、其 mode 全程恒定，见 runtime._think）
    run_mode: dict[str, str] = {}
    for r in con.execute("SELECT run_id, result_json FROM agent_run_steps "
                         "WHERE kind='think' ORDER BY run_id, step_no"):
        if r["run_id"] in run_mode or not r["result_json"]:
            continue
        try:
            m = (json.loads(r["result_json"]) or {}).get("mode")
        except (ValueError, TypeError):
            m = None
        if m in ("llm", "deterministic"):
            run_mode[r["run_id"]] = m
    # (action, object_id) → 该键涉及的 mode 集合（经 run-keyed 命令桥接）
    keyed: dict[tuple[str, str], set[str]] = {}
    for r in con.execute("SELECT action, object_id, idempotency_key FROM commands "
                         "WHERE idempotency_key LIKE 'run:%' AND object_id IS NOT NULL"):
        parts = r["idempotency_key"].split(":")     # run:{run_id}:{step_no}
        m = run_mode.get(parts[1]) if len(parts) >= 2 else None
        if m is not None:
            keyed.setdefault((r["action"], r["object_id"]), set()).add(m)
    # 只保留唯一 mode 的键（歧义键剔除，不编造）
    return {k: next(iter(v)) for k, v in keyed.items() if len(v) == 1}


# ═══════════════════════════════════════════════════════════════════════════
# AI 工作流时间线（ai-flow）builder
# ═══════════════════════════════════════════════════════════════════════════
def _build_ai_flow(con, tables: set[str], limit: int, as_of: str | None = None) -> list[dict]:
    """四来源按时间倒序合并（规格 C），每条 {ts, kind, summary, ref_object, detail, sim}：
    · llm_calls（kind='llm_call'）：SELECT * ORDER BY created_at DESC——AI 调用遥测
      （call_type/provider/model/status/时长），ref=trace_id；缺表世界跳过该来源。
    · action_log actor='ai-agent'（kind='ai_action'）：AI 经 MCP/工具真实执行的动作审计行
      （SELECT * WHERE actor='ai-agent' ORDER BY timestamp DESC），params_json 解析为 dict
      进 detail.proposal_params（使本体 est_cost_usd 嵌套脱敏规则可作用）。
    · tasks 提案状态流转（kind='task_flow'）：action_log 中 action IN ('AssignTask',
      'ProposeMitigation','ApproveMitigation','RejectMitigation') AND result='ok'
      AND actor != 'ai-agent'（ai-agent 行已由上一来源覆盖，排除防双计）。
    · sim_ai_activity（simworld 的 AI 闭环留痕，kind=activity 原值 detect/propose/approve/
      reject/close，带 sim=true 徽标字段）：SELECT * ORDER BY sim_date DESC, ai_event_id DESC。
    排序键 = (ts 字符串, 各来源内行序) 倒序；sim_date 为日期粒度（无时分秒），与带时间戳来源
    同日混排时排在该日时间戳条目之前，docstring 如实声明不补造时刻。
    回放（as_of，见模块 docstring）：各来源按自身时间戳 ≤ as_of 过滤——事件日志天然可回放（只看"截至
    当日已发生"的留痕）；as_of=None（缺省）→ 无过滤，与改动前逐字等价 ⇒ byte-identical。
    执行方式徽标（mode，不变量11）：**可判定**的条目补 mode（'deterministic'|'llm'）——llm_call 天然 'llm'；
    ai_action 经 _runtime_ai_action_modes 从其 run 的 think 步现查。判不了的条目（task_flow / sim / 无 run
    桥接的 ai_action）不带 mode 字段（不编造）。缺 runtime 表的世界→映射空→无条目带 mode（载荷 byte-identical）。"""
    def _ts_filter(expr: str, has_where: bool) -> tuple[str, tuple]:
        """ts≤as_of 过滤片段。as_of=None → ('',())：拼出的 SQL 与原句逐字相同。"""
        if not as_of:
            return "", ()
        return (f"{'AND' if has_where else 'WHERE'} {expr} <= ? ", (as_of,))

    items: list[tuple[str, str, dict]] = []      # (ts, tiebreak, item)
    ai_action_modes = _runtime_ai_action_modes(con, tables)  # (action, object_id) → mode（判不了的键不在表中）

    if "llm_calls" in tables:
        f, p = _ts_filter("date(created_at)", False)
        for r in con.execute(
                "SELECT call_id, trace_id, call_type, provider, model, status, duration_ms, "
                "est_input_tokens, est_output_tokens, created_at FROM llm_calls "
                f"{f}ORDER BY created_at DESC, call_id DESC LIMIT ?", (*p, limit)):
            items.append((r["created_at"], f"llm:{r['call_id']:012d}", {
                "ts": r["created_at"], "kind": "llm_call",
                "summary": f"AI 调用 {r['call_type']} via {r['provider']}"
                           f"{'/' + r['model'] if r['model'] else ''}（{r['status']}）",
                "ref_object": r["trace_id"], "sim": False,
                "mode": "llm",                     # llm_call 天然是真模型调用（含降级/失败的出境尝试）
                "detail": {"call_type": r["call_type"], "provider": r["provider"],
                           "model": r["model"], "status": r["status"],
                           "duration_ms": r["duration_ms"],
                           "est_input_tokens": r["est_input_tokens"],
                           "est_output_tokens": r["est_output_tokens"]}}))

    def _parse_params(raw: Any) -> Any:
        try:
            return json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            return {"unparsed": True}

    if "action_log" in tables:
        f, p = _ts_filter("date(timestamp)", True)
        for r in con.execute(
                "SELECT log_id, actor, role, action, target_object_id, params_json, "
                "timestamp, result FROM action_log WHERE actor='ai-agent' "
                f"{f}ORDER BY timestamp DESC, log_id DESC LIMIT ?", (*p, limit)):
            item = {
                "ts": r["timestamp"], "kind": "ai_action",
                "summary": f"AI（role={r['role']}）执行 {r['action']} → "
                           f"{r['target_object_id']}（{r['result']}）",
                "ref_object": r["target_object_id"], "sim": False,
                "detail": {"actor": r["actor"], "role": r["role"], "action": r["action"],
                           "result": r["result"],
                           "proposal_params": _parse_params(r["params_json"])}}
            mode = ai_action_modes.get((r["action"], r["target_object_id"]))
            if mode:                               # 判得了才补（runtime 派生的 ai_action）；判不了不带 mode
                item["mode"] = mode
            items.append((r["timestamp"], f"log:{r['log_id']:012d}", item))
        ph = ",".join("?" * len(_TASK_FLOW_ACTIONS))
        f2, p2 = _ts_filter("date(timestamp)", True)
        for r in con.execute(
                f"SELECT log_id, actor, role, action, target_object_id, params_json, "
                f"timestamp, result FROM action_log WHERE action IN ({ph}) "
                f"AND result='ok' AND actor != 'ai-agent' "
                f"{f2}ORDER BY timestamp DESC, log_id DESC LIMIT ?",
                (*_TASK_FLOW_ACTIONS, *p2, limit)):
            items.append((r["timestamp"], f"log:{r['log_id']:012d}", {
                "ts": r["timestamp"], "kind": "task_flow",
                "summary": f"{r['action']} → {r['target_object_id']}"
                           f"（{r['actor']}/{r['role']}）",
                "ref_object": r["target_object_id"], "sim": False,
                "detail": {"actor": r["actor"], "role": r["role"], "action": r["action"],
                           "result": r["result"],
                           "proposal_params": _parse_params(r["params_json"])}}))

    if "sim_ai_activity" in tables:
        f, p = _ts_filter("sim_date", False)
        for r in con.execute(
                "SELECT ai_event_id, sim_date, actor, activity, risk_event_id, task_id, "
                f"detail FROM sim_ai_activity {f}ORDER BY sim_date DESC, ai_event_id DESC LIMIT ?",
                (*p, limit)):
            ref = r["task_id"] or r["risk_event_id"]
            items.append((r["sim_date"], f"sim:{r['ai_event_id']}", {
                "ts": r["sim_date"], "kind": r["activity"],
                "summary": f"[sim] {r['actor']} {r['activity']}"
                           f"{' ' + (r['detail'] or '')}".rstrip(),
                "ref_object": ref, "sim": True,
                "detail": {"actor": r["actor"], "activity": r["activity"],
                           "risk_event_id": r["risk_event_id"], "task_id": r["task_id"],
                           "note": r["detail"]}}))

    items.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [it for _, _, it in items[:limit]]


# ═══════════════════════════════════════════════════════════════════════════
# 数字溯源（provenance，U2）：?provenance=1 时为每指标附 {caliber(口径白话), sources(来源
# 表/对象), sample_ids(≤3 样例 id 供跳透视镜)}。缺省关 → 无此键，载荷 byte-identical。
# caliber 白话由各 _zone_* / _build_* docstring 的 SQL 口径提炼成用户语言（不新造口径，只翻译）；
# sample_ids 按当前世界现查（sim/verify 自然各异），缺数/查询异常 → [] 如实空，绝不编造 id。
# provenance 无敏感字段键（caliber/sources/sample_ids 及区名），过 _apply_role_masks 原样穿透。
# ═══════════════════════════════════════════════════════════════════════════
def _sample_ids(con, sql: str, params: tuple = ()) -> list:
    """样例 id 现查（SQL 自带 LIMIT）：取首列。任何 sqlite 异常（缺表/坏 JSON/列缺）→ [] 兜底
    ——provenance 是附加提示，绝不因取样失败让主端点 500，也绝不用假 id 冒充。"""
    try:
        return [r[0] for r in con.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _prov(caliber: str, sources: list, sample_ids: list) -> dict:
    return {"caliber": caliber, "sources": sources, "sample_ids": sample_ids}


def _provenance_vitals(con, tables: set[str], clock: str | None) -> dict:
    """七区卡 headline 指标的溯源（U2 前端范围=七区卡）。每区 caliber=该区 headline 口径白话，
    sources=来源表/对象，sample_ids=当前世界现查的 ≤3 样例（可跳透视镜）。缺数世界样例自然为空。"""
    ph = ",".join("?" * len(_COST_RULES))
    cust_samples = _sample_ids(con, f"""
        SELECT DISTINCT so.customer_id FROM risk_events r, json_each(r.affected_so_line_ids) je
        JOIN sales_order_lines sol ON sol.so_line_id = je.value
        JOIN sales_orders so ON so.so_id = sol.so_id
        WHERE r.status='open' AND r.affected_so_line_ids IS NOT NULL
          AND r.affected_so_line_ids NOT IN ('', '[]') LIMIT 3""")
    ai_samples = _sample_ids(con, "SELECT risk_event_id FROM risk_events "
                                  "WHERE date(detected_at)=? ORDER BY risk_event_id LIMIT 3",
                             (clock,)) if clock else []
    return {
        "money": _prov(
            "费用异常敞口 = 当前未闭环（open）的超收/计划外/重复计费风险（规则 R4/R5/R6）影响金额合计"
            "——'系统这一刻替你盯着、还没消掉的可疑费用'。",
            ["risk_events（rule_id∈R4/R5/R6 且 status=open）"],
            _sample_ids(con, f"SELECT risk_event_id FROM risk_events WHERE rule_id IN ({ph}) "
                             f"AND status='open' ORDER BY risk_event_id LIMIT 3", _COST_RULES)),
        "fulfillment": _prov(
            "准交率 OTD = 已履约订单行里，实际到货不晚于承诺交期的比例；分母只算有到货信息的行"
            "（没到货信息的不硬算）。",
            ["sales_order_lines（line_status=fulfilled）", "shipments.ata / shipment_milestones（到货日）"],
            _sample_ids(con, "SELECT so_line_id FROM sales_order_lines "
                             "WHERE line_status='fulfilled' ORDER BY so_line_id LIMIT 3")),
        "customers": _prov(
            "风险敞口客户 = open 风险波及的订单行回溯到的客户去重计数；每户敞口=其被波及行的 "
            "qty×单价 去重合计。",
            ["risk_events.affected_so_line_ids → sales_order_lines → sales_orders → customers"],
            cust_samples),
        "suppliers": _prov(
            "交期达成率 = 有收货记录的采购单里，首次收货不晚于预期就绪日的比例；未收货的采购单不进"
            "分母。缺收货域的世界该指标不点亮（如实标 null）。",
            ["purchase_orders", "goods_receipts（首张 GRN 收货日）"],
            _sample_ids(con, "SELECT DISTINCT po.supplier_id FROM purchase_orders po "
                             "JOIN goods_receipts g ON g.po_id=po.po_id "
                             "ORDER BY po.supplier_id LIMIT 3")),
        "inventory": _prov(
            "安全库存击穿 = 可用量低于安全库存的库存头寸计数（缺口=安全库存−可用量，缺口大者优先）。",
            ["inventory_positions（available_qty < safety_stock）"],
            _sample_ids(con, "SELECT inventory_position_id FROM inventory_positions "
                             "WHERE available_qty < safety_stock "
                             "ORDER BY (safety_stock-available_qty) DESC, inventory_position_id LIMIT 3")),
        "ai": _prov(
            "AI 今日 = 世界时钟当天的风险检测数与提案数（检测=detected_at 当天的风险；提案=当天的 "
            "AI 提案/批/驳）。验证世界过去日为单日批量快照，故过去日恒 0 属账本如实。",
            ["risk_events（detected_at=世界时钟当天）", "action_log / sim_ai_activity（当天动作）"],
            ai_samples),
        "decisions": _prov(
            "待批提案 = 审批状态为 pending 的任务件数（老板收件箱里等着拍板的处置提案）。",
            ["tasks（approval_status=pending）"],
            _sample_ids(con, "SELECT task_id FROM tasks WHERE approval_status='pending' "
                             "ORDER BY task_id LIMIT 3")),
    }


def _provenance_panorama(con, tables: set[str]) -> dict:
    """小全景三块（节点/边/异常锚定）的溯源。节点/边为拓扑无单一 id 样例（sample_ids=[]）；
    异常锚定给 open 风险样例（可跳透视镜看该风险）。"""
    return {
        "nodes": _prov(
            "五层节点=客户/订单/货件/供应商/仓库；单层实体>40 时聚合为分组节点（客户按州、供应商按"
            "城市、货件按航线），保画面可渲染。",
            ["customers", "sales_orders/sales_order_lines", "shipments", "suppliers", "warehouses"],
            []),
        "edges": _prov(
            "边=本体关系投影（客户下单→订单→分配到货件→采购来源供应商→入目的仓），聚合层自动折叠"
            "并累加 count。",
            ["shipment_allocations", "purchase_orders", "shipments.po_ids"],
            []),
        "alerts": _prov(
            "异常锚定=open 风险挂到其货件/仓/供应商节点；已交付货件的风险进 unanchored 如实列出。",
            ["risk_events（status=open）"],
            _sample_ids(con, "SELECT risk_event_id FROM risk_events WHERE status='open' "
                             "ORDER BY risk_event_id LIMIT 3")),
    }


def _provenance_ai_flow(con, tables: set[str]) -> dict:
    """AI 工作流时间线的溯源：四来源按时间倒序合并，sources 只列当前世界真实在库的来源。"""
    present = sorted({"llm_calls", "action_log", "sim_ai_activity"} & tables)
    return {
        "timeline": _prov(
            "AI 工作流时间线=LLM 调用遥测 + AI 动作审计（actor=ai-agent）+ 提案流转 + 模拟世界 AI "
            "留痕，按时间戳倒序合并；缺表的来源自动跳过。",
            present or ["（该世界无任一 AI 工作流来源表）"],
            _sample_ids(con, "SELECT trace_id FROM llm_calls WHERE trace_id IS NOT NULL "
                             "ORDER BY created_at DESC LIMIT 3") if "llm_calls" in tables else []),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 付款锚风险影响归并（轮3-D P1：R19/R21 摘要有客户/订单号、结构化区却 0 条无可归并）
# ═══════════════════════════════════════════════════════════════════════════
# 为什么这样建（≤5 行，AGENTS.md §4）：
#   · R19/R21 锚在 payment 不在订单行——验证世界把 payment_id 写进 affected_so_line_ids（engine
#     幂等自然键），sim 世界该列如实留空、锚在任务 proposal_params（payment_id / customer_id）。
#   · 归并链全走实际 schema：payment → ref（sales_order/supplier_invoice）→ 对手方（customer/
#     supplier），零文本解析；恢复不唯一（歧义）或链路真缺 → rows=[] + 白话 note，不编造。
#   · R21 补列同据同额的姊妹付款（重复付款异常的"另一笔"），让"哪张单据被重复付了"看得见。
_PAYMENT_ANCHOR_RULES = ("R19", "R21")


def _payment_row(con, pay_id: str, clock: str | None) -> dict | None:
    """单笔付款 → 影响行。逾期天数=世界时钟−到期日（仅未回款的应收，负值/缺日期不硬算）。"""
    r = _one(con, "SELECT payment_id, direction, counterparty_type, counterparty_id, "
                  "ref_type, ref_id, amount_usd, due_date, status FROM payments "
                  "WHERE payment_id=?", (pay_id,))
    if not r:
        return None
    row = dict(r)
    overdue = None
    if (clock and row["due_date"] and row["direction"] == "in"
            and row["status"] != "paid"):
        try:
            delta = (date.fromisoformat(clock[:10])
                     - date.fromisoformat(str(row["due_date"])[:10])).days
            overdue = delta if delta > 0 else None
        except ValueError:
            overdue = None                 # 脏日期如实略过，不 raise
    row["overdue_days"] = overdue
    return row


def _resolve_anchor_payments(con, tables: set[str], risk: sqlite3.Row) -> list[str]:
    """风险 → 锚付款 id 列表。两条路，都是 schema 键，零猜测：
    ① affected_so_line_ids 里的 PAY-* id（验证世界 engine 写法）；
    ② 该风险任务的 proposal_params：payment_id 直取（sim R21），或 customer_id+金额对
       payments 唯一匹配（sim R19；命中≠1 视为歧义 → 放弃，不编造）。"""
    ids = [i for i in _json_ids(risk["affected_so_line_ids"]) if i.startswith("PAY")]
    if ids or "tasks" not in tables:
        return ids
    resolved: list[str] = []
    for t in con.execute("SELECT proposal_params FROM tasks WHERE risk_event_id=?",
                         (risk["risk_event_id"],)):
        try:
            params = json.loads(t["proposal_params"]) if t["proposal_params"] else {}
        except (ValueError, TypeError):
            continue
        if not isinstance(params, dict):
            continue
        if params.get("payment_id"):
            resolved.append(str(params["payment_id"]))
            continue
        if params.get("customer_id") and risk["affected_value_usd"] is not None:
            hits = [r["payment_id"] for r in con.execute(
                "SELECT payment_id FROM payments WHERE direction='in' AND status='scheduled' "
                "AND counterparty_id=? AND round(amount_usd,2)=round(?,2)",
                (params["customer_id"], risk["affected_value_usd"]))]
            if len(hits) == 1:             # 唯一命中才算数——歧义即放弃（诚实空态）
                resolved.append(hits[0])
    return list(dict.fromkeys(resolved))   # 去重保序


def _payment_impact(con, tables: set[str], risk: sqlite3.Row, clock: str | None) -> dict:
    """付款锚影响块：rows=归并行（客户/供应商 × 单据 × 金额 × 逾期天数），空则带白话 note。"""
    if "payments" not in tables:
        return {"anchor": "payment", "rows": [],
                "note": "该世界缺 payments 表——付款归并链无从建立（如实空，非 0 条）。"}
    rows: list[dict] = []
    seen: set[str] = set()
    for pid in _resolve_anchor_payments(con, tables, risk):
        row = _payment_row(con, pid, clock)
        if not row or row["payment_id"] in seen:
            continue
        seen.add(row["payment_id"])
        row["is_anchor"] = True
        rows.append(row)
        if risk["rule_id"] == "R21":       # 重复付款：补同据同额姊妹笔，让"哪张单据被重复付"可见
            for s in con.execute(
                    "SELECT payment_id FROM payments WHERE ref_type=? AND ref_id=? "
                    "AND round(amount_usd,2)=round(?,2) AND payment_id != ? "
                    "ORDER BY payment_id", (row["ref_type"], row["ref_id"],
                                            row["amount_usd"], row["payment_id"])):
                if s["payment_id"] in seen:
                    continue
                sib = _payment_row(con, s["payment_id"], clock)
                if sib:
                    sib["is_anchor"] = False
                    seen.add(sib["payment_id"])
                    rows.append(sib)
    out: dict[str, Any] = {"anchor": "payment", "rows": rows}
    if not rows:
        out["note"] = ("锚付款在本库无法唯一定位（链路数据缺失或歧义）——如实空，不猜。"
                       "摘要文本仍以风险 root_cause 为准。")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 路由工厂：main.py 尾部挂载（cockpit 不 import main → 零循环导入；
# 复用 main 的 get_db_path/get_ro_connection 依赖 ⇒ 测试 dependency_overrides 自动生效）
# ═══════════════════════════════════════════════════════════════════════════
def build_cockpit_router(get_db_path: Callable, get_ro_connection: Callable,
                         infer_world: Callable[[str], str]) -> APIRouter:
    router = APIRouter(prefix="/cockpit", tags=["cockpit"])

    @router.get("/vitals")
    def cockpit_vitals(as_of: str | None = Query(
                           default=None, description="回放时点 YYYY-MM-DD；缺省=世界时钟今天=现状不变"),
                       provenance: bool = Query(
                           default=False, description="?provenance=1 为七区卡 headline 附口径白话/"
                                                      "来源表/样例id；缺省关=byte-identical"),
                       assignee_role: str | None = Query(
                           default=None, description="待批提案队列\"我组的\"筛选：只保留指派给该角色的"
                                                     "待批提案（仅影响 decisions 区，其余区不变）；缺省=全部"
                                                     "pending（老板收件箱，byte-identical）。合法值=业务角色。"),
                       sort: str | None = Query(
                           default=None, description="供应商队列排序（K·P1）：sort=compliance（仅 "
                                                     "compliance/manager 可用）=UFLPA 命中优先→资质异常→"
                                                     "交期升序；缺省=交期升序（byte-identical）。"),
                       x_role: str = Header(default="ops", alias="X-Role"),
                       con: sqlite3.Connection = Depends(get_ro_connection),
                       db_path: str = Depends(get_db_path)) -> dict:
        """公司体征带：七掌控区各一枚体征块（zone/headline/trend/alert_count/detail）。
        指标 SQL 口径见模块 docstring 总表与各 _zone_* builder docstring；脱敏两层见 _apply_role_masks；
        trend/世界无关性红线见模块 docstring。as_of 回放口径见模块 docstring「时间轴回放」节——缺省
        byte-identical；回放态附 window/as_of 信封/每区 headline_as_of，clock 恒为世界今天不随拖动改。
        provenance=1（U2）→ 附独立 provenance 键（每区 caliber/sources/sample_ids）；缺省无此键。
        assignee_role（V22 任务1）→ 仅筛 decisions 区待批提案列表（"我组的"），非法值 422 白话；
        缺省不传=不筛，全响应 byte-identical。"""
        if assignee_role is not None and assignee_role not in _ROSTER_ROLES:
            raise HTTPException(
                422, detail=f"未知 assignee_role '{assignee_role}'——待批提案筛选仅支持业务角色："
                            f"{'、'.join(sorted(_ROSTER_ROLES))}。缺省不传该参数=返回全部待批提案。")
        if sort is not None:                   # K·P1：合规排序参数门（fail-fast 白话，同 X-World 惯例）
            if sort != "compliance":
                raise HTTPException(
                    422, detail=f"未知 sort 值 '{sort}'——当前仅支持 sort=compliance"
                                f"（供应商队列按合规风险排序）。缺省不传=按交期达成率升序。")
            if x_role not in COMPLIANCE_ROLES:
                raise HTTPException(
                    422, detail=f"合规维度需合规或经理角色——当前是 {x_role}，无 UFLPA/资质"
                                f"可见权（本体 Supplier.uflpa_risk_flag 仅对 compliance/manager "
                                f"可见）。请切到合规或经理角色后再用合规排序。")
        tables = _tables(con)
        window_start, world_clock = _world_window(con, tables)
        effective_clock, replay = _resolve_as_of(as_of, world_clock, window_start)
        world = infer_world(db_path)
        world_is_sim = "sim_event_log" in tables       # 以库内证据判定，不信文件名
        replay_risk = replay and world_is_sim          # 验证世界静态快照不重建风险类（见模块 docstring）
        zones = [
            _zone_money(con, tables, effective_clock, replay_risk, replay),
            _zone_fulfillment(con, tables, effective_clock, world_is_sim),
            _zone_customers(con, tables, replay_risk, effective_clock),
            _zone_suppliers(con, tables, replay_risk, effective_clock,
                            role=x_role, sort_compliance=(sort == "compliance")),
            _zone_inventory(con, tables, replay_risk, effective_clock),
            _zone_ai(con, tables, effective_clock, world_is_sim),
            _zone_decisions(con, tables, effective_clock, assignee_role),
        ]
        payload: dict[str, Any] = {
            "world": world, "clock": world_clock, "role": x_role,
            "window": {"start": window_start, "end": world_clock}, "zones": zones}
        if replay:
            for z in zones:
                z["headline_as_of"] = _headline_as_of(z["zone"], replay_risk)
            payload["as_of"] = _as_of_envelope(as_of, effective_clock, world_clock,
                                               (window_start, world_clock), world_is_sim, replay_risk)
        if provenance:                     # U2：独立键，不动既有字段；缺省不加 → byte-identical
            payload["provenance"] = _provenance_vitals(con, tables, world_clock)
        return _apply_role_masks(payload, x_role)

    @router.get("/panorama")
    def cockpit_panorama(as_of: str | None = Query(
                             default=None, description="回放时点 YYYY-MM-DD；缺省=现状"),
                         provenance: bool = Query(
                             default=False, description="?provenance=1 附节点/边/异常锚定的口径白话/"
                                                        "来源；缺省关=byte-identical"),
                         x_role: str = Header(default="ops", alias="X-Role"),
                         con: sqlite3.Connection = Depends(get_ro_connection),
                         db_path: str = Depends(get_db_path)) -> dict:
        """小全景分层图数据：五层节点+关系投影边+异常锚定+迷你指标。
        口径与聚合规则（>40 分组）见 _build_panorama docstring。as_of 回放：仅异常锚定的风险集按时点
        重建（sim），节点/边为当前态拓扑如实（无历史版本）；缺省 byte-identical。
        provenance=1（U2）→ 附独立 provenance 键（nodes/edges/alerts 溯源）；缺省无此键。"""
        tables = _tables(con)
        window_start, world_clock = _world_window(con, tables)
        effective_clock, replay = _resolve_as_of(as_of, world_clock, window_start)
        world_is_sim = "sim_event_log" in tables
        replay_risk = replay and world_is_sim
        payload: dict[str, Any] = {
            "world": infer_world(db_path), "clock": world_clock, "role": x_role,
            "window": {"start": window_start, "end": world_clock}} \
            | _build_panorama(con, tables, replay_risk, effective_clock)
        if replay:
            payload["as_of"] = _as_of_envelope(as_of, effective_clock, world_clock,
                                               (window_start, world_clock), world_is_sim, replay_risk)
        if provenance:                     # U2：独立键，缺省不加 → byte-identical
            payload["provenance"] = _provenance_panorama(con, tables)
        return _apply_role_masks(payload, x_role)

    @router.get("/ai-flow")
    def cockpit_ai_flow(limit: int = Query(default=50, ge=1, le=500),
                        as_of: str | None = Query(
                            default=None, description="回放时点 YYYY-MM-DD；缺省=现状"),
                        provenance: bool = Query(
                            default=False, description="?provenance=1 附时间线来源溯源；缺省关="
                                                       "byte-identical"),
                        x_role: str = Header(default="ops", alias="X-Role"),
                        con: sqlite3.Connection = Depends(get_ro_connection),
                        db_path: str = Depends(get_db_path)) -> dict:
        """AI 工作流时间线：llm_calls / ai-agent 审计 / 提案流转 / sim 留痕按 ts 倒序合并。
        来源与排序口径见 _build_ai_flow docstring。limit 默认 50（1..500）。as_of 回放：各来源按自身
        时间戳≤as_of 过滤（事件日志天然可回放）；缺省 byte-identical。
        provenance=1（U2）→ 附独立 provenance 键（timeline 溯源）；缺省无此键。"""
        tables = _tables(con)
        window_start, world_clock = _world_window(con, tables)
        effective_clock, replay = _resolve_as_of(as_of, world_clock, window_start)
        world_is_sim = "sim_event_log" in tables
        flow = _build_ai_flow(con, tables, limit, effective_clock if replay else None)
        payload: dict[str, Any] = {
            "world": infer_world(db_path), "role": x_role, "limit": limit, "count": len(flow),
            "window": {"start": window_start, "end": world_clock},
            "sources_present": sorted({"llm_calls", "action_log", "sim_ai_activity"} & tables),
            "items": flow}
        if replay:
            payload["as_of"] = _as_of_envelope(as_of, effective_clock, world_clock,
                                               (window_start, world_clock), world_is_sim,
                                               replay and world_is_sim)
        if provenance:                     # U2：独立键，缺省不加 → byte-identical
            payload["provenance"] = _provenance_ai_flow(con, tables)
        payload = _apply_role_masks(payload, x_role)
        # 轮3-A（P1 执行缺口收紧）：非成本角色（含缺省 ops，与既有掩码语义对齐）再过文本层——
        # summary/note 等自由文本里的 "$金额" → $•••；finance/manager（_can_see_cost）不受影响。
        if not _can_see_cost(x_role):
            payload = _mask_text_amounts(payload)
        return payload

    @router.get("/risk-impact/{risk_event_id}")
    def cockpit_risk_impact(risk_event_id: str,
                            x_role: str = Header(default="ops", alias="X-Role"),
                            con: sqlite3.Connection = Depends(get_ro_connection),
                            db_path: str = Depends(get_db_path)) -> dict:
        """付款锚风险（R19/R21）影响归并（轮3-D）：payment → 单据（sales_order/supplier_invoice）
        → 对手方（客户/供应商），结构化补齐"客户 X · 订单 Y · 金额 Z（· 逾期 N 天）"。
        非付款锚风险 → anchor='so_line' + rows=[]（订单行归并走既有 /objects 链路，本端点不重复）。
        金额掩码跟随现行角色规则（amount_usd 键过 _apply_role_masks，finance/manager 见真值）。"""
        tables = _tables(con)
        if "risk_events" not in tables:
            raise HTTPException(404, detail="该世界无 risk_events 表——无风险可归并。")
        risk = _one(con, "SELECT risk_event_id, rule_id, affected_so_line_ids, "
                         "affected_value_usd FROM risk_events WHERE risk_event_id=?",
                    (risk_event_id,))
        if not risk:
            raise HTTPException(404, detail=f"风险 {risk_event_id} 在当前世界不存在——"
                                            f"确认编号或先切到正确的世界（验证/模拟）。")
        _, world_clock = _world_window(con, tables)
        payload: dict[str, Any] = {
            "world": infer_world(db_path), "role": x_role,
            "risk_event_id": risk["risk_event_id"], "rule_id": risk["rule_id"],
            "basis": ("R19/R21 锚在付款：归并链＝付款→单据（销售订单/供应商发票）→对手方"
                      "（客户/供应商）；逾期天数＝世界时钟−应收到期日（仅未回款应收）。"),
        }
        has_pay_anchor = (risk["rule_id"] in _PAYMENT_ANCHOR_RULES
                          or any(i.startswith("PAY")
                                 for i in _json_ids(risk["affected_so_line_ids"])))
        if has_pay_anchor:
            payload.update(_payment_impact(con, tables, risk, world_clock))
        else:
            payload.update({"anchor": "so_line", "rows": [],
                            "note": "非付款锚风险：订单行/客户归并走既有对象读链路，"
                                    "本端点仅服务 R19/R21 付款锚风险族。"})
        return _apply_role_masks(payload, x_role)

    @router.get("/customs-queue")
    def cockpit_customs_queue(limit: int = Query(default=50, ge=1, le=50),
                              x_role: str = Header(default="ops", alias="X-Role"),
                              con: sqlite3.Connection = Depends(get_ro_connection),
                              db_path: str = Depends(get_db_path)) -> dict:
        """清关卡点逐票队列（轮3-G P0：卡片"21 票"只有数字无清单、原文案导流去不存在的入口）。
        口径与七区卡完全同源：customs_status='not_filed' 且 status='in_transit'（fulfillment 区
        customs_blocked 的同一条 WHERE）。每票：目的港/卡点天数/关联 PO 数/该票 open 风险最高严重度。
        卡点天数＝世界时钟−该票最后里程碑日（无里程碑退 last_event_time，再退 ETD；全缺如实 null）。
        纯读现查、cap 50 + total 如实（防静默截断惯例）。"""
        tables = _tables(con)
        payload: dict[str, Any] = {
            "world": infer_world(db_path), "role": x_role,
            "basis": ("卡点＝未报关（not_filed）且在途；卡点天数＝世界时钟−最后里程碑日"
                      "（无里程碑退最后事件时间，再退离港日；全缺如实标空）；严重度＝该票"
                      "未闭环风险最高档；按卡点天数降序。"),
        }
        if "shipments" not in tables:
            payload.update({"total": 0, "count": 0, "items": [],
                            "note": "该世界缺 shipments 表——无在途票据可查。"})
            return _apply_role_masks(payload, x_role)
        _, world_clock = _world_window(con, tables)
        last_ms: dict[str, str] = {}
        if "shipment_milestones" in tables:
            last_ms = {r["shipment_id"]: r["last_time"] for r in con.execute(
                "SELECT shipment_id, max(event_time) last_time FROM shipment_milestones "
                "GROUP BY shipment_id")}
        sev_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
        risk_by_ship: dict[str, dict] = {}
        if "risk_events" in tables:
            for r in con.execute("SELECT shipment_id, severity, count(*) n FROM risk_events "
                                 "WHERE status='open' AND shipment_id IS NOT NULL "
                                 "AND shipment_id != '' GROUP BY shipment_id, severity"):
                cur = risk_by_ship.setdefault(r["shipment_id"], {"severity": None, "open_risks": 0})
                cur["open_risks"] += r["n"]
                if (cur["severity"] is None
                        or sev_rank.get(r["severity"], -1) > sev_rank.get(cur["severity"], -1)):
                    cur["severity"] = r["severity"]
        items: list[dict] = []
        for s in con.execute(
                "SELECT shipment_id, destination_port, etd, last_event_time, po_ids "
                "FROM shipments WHERE customs_status='not_filed' AND status='in_transit'"):
            stuck_since = last_ms.get(s["shipment_id"]) or s["last_event_time"] or s["etd"]
            stuck_days = None
            if world_clock and stuck_since:
                try:
                    stuck_days = (date.fromisoformat(world_clock[:10])
                                  - date.fromisoformat(str(stuck_since)[:10])).days
                except ValueError:
                    stuck_days = None      # 脏日期如实空，不硬算
            risk = risk_by_ship.get(s["shipment_id"], {"severity": None, "open_risks": 0})
            items.append({
                "shipment_id": s["shipment_id"],
                "destination_port": s["destination_port"],
                "stuck_days": stuck_days,
                "stuck_since": str(stuck_since)[:10] if stuck_since else None,
                # po_ids 为管道分隔串（与 panorama/build_ontology 同一解析口径，非 JSON 列）
                "po_count": len([p for p in str(s["po_ids"] or "").split("|") if p]),
                "severity": risk["severity"],
                "open_risks": risk["open_risks"],
            })
        items.sort(key=lambda r: (r["stuck_days"] is None, -(r["stuck_days"] or 0),
                                  r["shipment_id"]))
        payload.update({"total": len(items), "count": min(limit, len(items)),
                        "items": items[:limit]})
        return _apply_role_masks(payload, x_role)

    return router
