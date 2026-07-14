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
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import APIRouter, Depends, Header, Query

from agent.mcp_server import SensitiveFieldMasker
from agent.tools import MASK, _can_see_cost
from pipeline.ontology_runtime import load_ontology

ZONES = ("money", "fulfillment", "customers", "suppliers", "inventory", "ai", "decisions")
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


def _mask_money(node: Any) -> None:
    """金额类聚合掩码（模块 docstring 脱敏层 b）：就地把键名以 _usd 结尾的值与
    margin_distribution 整块替换为 MASK。计数/比率/状态键不动。"""
    if isinstance(node, dict):
        for key, val in list(node.items()):
            if key == "margin_distribution":
                node[key] = MASK
            elif key.endswith("_usd") and val is not None:
                node[key] = MASK
            else:
                _mask_money(val)
    elif isinstance(node, list):
        for item in node:
            _mask_money(item)


def _apply_role_masks(payload: dict, role: str) -> dict:
    """两层脱敏（模块 docstring）：先本体 sensitiveFieldRules 具名/嵌套规则，再金额聚合层
    （键名 _usd 后缀 + margin_distribution + 带 headline_unit='usd' 标记的区 headline）。"""
    SensitiveFieldMasker(load_ontology(), role).mask_value(payload)
    if not _can_see_cost(role):
        _mask_money(payload)
        for zone in payload.get("zones", []):
            if zone.get("headline_unit") == "usd":
                zone["headline_value"] = MASK
    return payload


def _shift_date(day: str, delta_days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=delta_days)).isoformat()


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


def _zone_money(con, tables: set[str], clock: str | None) -> dict:
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
    trend：无逐日敞口快照表 → null（两世界同，红线 3）。"""
    ph = ",".join("?" * len(_COST_RULES))
    row = _one(con, f"SELECT count(*) c, sum(affected_value_usd) v FROM risk_events "
                    f"WHERE rule_id IN ({ph}) AND status='open'", _COST_RULES)
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

    return {
        "zone": "money", "headline_label": "费用异常敞口",
        "headline_value": exposure, "headline_unit": "usd",
        "trend": None, "alert_count": exposure_count,
        "detail": {
            "fee_exposure": {"value_usd": exposure, "open_risks": exposure_count,
                             "rules": list(_COST_RULES)},
            "in_transit_value": in_transit,
            "intercepted_overbilling": {"value_usd": round(blocked["v"] or 0.0, 2),
                                        "resolved_r4_risks": blocked["c"]},
            "margin_distribution": margin,
            "receivables": receivables,
            "payables": payables,
            "net_cash_14d": net_cash_14d,
        },
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


def _customer_risk_map(con) -> tuple[dict[str, dict], int]:
    """open 风险 → affected_so_line_ids → so → customer 的聚合中间层（客户区两指标共用）。
    返回 ({customer_id: {name, tier, exposure_usd, open_risks(set), line_ids(set)}}, 波及行数)。
    敞口=被波及订单行**去重后** Σ(qty×unit_price_usd)——行金额是可回查的一手数（qty、单价
    都在 sales_order_lines），不做 risk.affected_value_usd 按客户均摊（歧义清单#3）。"""
    risk_lines: dict[str, list[str]] = {}
    for r in con.execute("SELECT risk_event_id, affected_so_line_ids FROM risk_events "
                         "WHERE status='open'"):
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


def _zone_customers(con, tables: set[str]) -> dict:
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
    agg, lines_hit = _customer_risk_map(con)
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


def _zone_suppliers(con, tables: set[str]) -> dict:
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
    alert_count = open R7-R14 计数。trend：无逐日快照 → null。"""
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

    r14 = _one(con, "SELECT count(*) c FROM risk_events "
                    "WHERE rule_id='R14' AND status='open'")["c"]
    ph = ",".join("?" * len(_RECON_RULES))
    recon = _one(con, f"SELECT count(*) c, sum(affected_value_usd) v FROM risk_events "
                      f"WHERE rule_id IN ({ph}) AND status='open'", _RECON_RULES)

    zone = {
        "zone": "suppliers", "headline_label": "交期达成率",
        "headline_value": headline, "trend": None, "alert_count": r14 + recon["c"],
        "detail": {"delivery_hit_rate": delivery, "defect_top": defect_top,
                   "single_source_r14": {"value": r14},
                   "recon_diff_r7_r13": {"open_risks": recon["c"],
                                         "amount_usd": round(recon["v"] or 0.0, 2)}},
    }
    if headline_reason:
        zone["headline_reason"] = headline_reason
    return zone


def _zone_inventory(con, tables: set[str]) -> dict:
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
    alert_count = 击穿数 + 盘点差异数（差异缺数时按 0 计入 alert，detail 如实标 null）。"""
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
                            f"WHERE status='open' AND rule_id IN ({ph})", _DELAY_RULES):
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


def _zone_decisions(con, tables: set[str], clock: str | None) -> dict:
    """待拍板区（老板收件箱）。三指标口径：
    · 待批提案(headline=件数)：SELECT * FROM tasks WHERE approval_status='pending'；
      金额 = proposal_params JSON 的 est_cost_usd，缺则回退父风险 affected_value_usd
      （两来源都标注在 amount_source）；等待起点 = action_log 中该 task 的
      ProposeMitigation result='ok' 最新 timestamp（无记录 → null，不编时长）；
      排序 = 金额降序（null 殿后）、再按等待起点升序（等得久的在前）。cap 20。
    · 超期任务：SELECT count(*) FROM tasks WHERE status NOT IN ('done','cancelled')
      AND date(due_at) < clock；simworld tasks 无 due_at 列 → null+reason。
    · 升级件：SELECT count(*) FROM tasks WHERE escalation_level>0 AND status NOT IN
      ('done','cancelled')；列缺 → null+reason。
    alert_count = 超期数 + 升级数（缺数按 0 计入 alert，detail 如实标 null）。"""
    task_cols = _columns(con, "tasks")
    pending_rows = [dict(r) for r in con.execute(
        "SELECT task_id, risk_event_id, title, priority, proposed_action, proposal_params, "
        "assignee_role FROM tasks WHERE approval_status='pending'")]

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
        "detail": {"pending_proposals": items[:20],
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


def _build_panorama(con, tables: set[str]) -> dict:
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
    nodes_by_id = {n["id"]: n for n in
                   (cust_nodes + list(order_nodes.values()) + ship_nodes + sup_nodes + wh_nodes)}
    unanchored: list[dict] = []
    for r in con.execute("SELECT risk_event_id, rule_id, type, severity, shipment_id, "
                         "warehouse_id, supplier_id, po_id FROM risk_events "
                         "WHERE status='open' ORDER BY risk_event_id"):
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
                 "open_risks_total": _one(con, "SELECT count(*) c FROM risk_events "
                                               "WHERE status='open'")["c"],
                 "alerts_unanchored_total": len(unanchored)},
    }


# ═══════════════════════════════════════════════════════════════════════════
# AI 工作流时间线（ai-flow）builder
# ═══════════════════════════════════════════════════════════════════════════
def _build_ai_flow(con, tables: set[str], limit: int) -> list[dict]:
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
    同日混排时排在该日时间戳条目之前，docstring 如实声明不补造时刻。"""
    items: list[tuple[str, str, dict]] = []      # (ts, tiebreak, item)

    if "llm_calls" in tables:
        for r in con.execute(
                "SELECT call_id, trace_id, call_type, provider, model, status, duration_ms, "
                "est_input_tokens, est_output_tokens, created_at FROM llm_calls "
                "ORDER BY created_at DESC, call_id DESC LIMIT ?", (limit,)):
            items.append((r["created_at"], f"llm:{r['call_id']:012d}", {
                "ts": r["created_at"], "kind": "llm_call",
                "summary": f"AI 调用 {r['call_type']} via {r['provider']}"
                           f"{'/' + r['model'] if r['model'] else ''}（{r['status']}）",
                "ref_object": r["trace_id"], "sim": False,
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
        for r in con.execute(
                "SELECT log_id, actor, role, action, target_object_id, params_json, "
                "timestamp, result FROM action_log WHERE actor='ai-agent' "
                "ORDER BY timestamp DESC, log_id DESC LIMIT ?", (limit,)):
            items.append((r["timestamp"], f"log:{r['log_id']:012d}", {
                "ts": r["timestamp"], "kind": "ai_action",
                "summary": f"AI（role={r['role']}）执行 {r['action']} → "
                           f"{r['target_object_id']}（{r['result']}）",
                "ref_object": r["target_object_id"], "sim": False,
                "detail": {"actor": r["actor"], "role": r["role"], "action": r["action"],
                           "result": r["result"],
                           "proposal_params": _parse_params(r["params_json"])}}))
        ph = ",".join("?" * len(_TASK_FLOW_ACTIONS))
        for r in con.execute(
                f"SELECT log_id, actor, role, action, target_object_id, params_json, "
                f"timestamp, result FROM action_log WHERE action IN ({ph}) "
                f"AND result='ok' AND actor != 'ai-agent' "
                f"ORDER BY timestamp DESC, log_id DESC LIMIT ?",
                (*_TASK_FLOW_ACTIONS, limit)):
            items.append((r["timestamp"], f"log:{r['log_id']:012d}", {
                "ts": r["timestamp"], "kind": "task_flow",
                "summary": f"{r['action']} → {r['target_object_id']}"
                           f"（{r['actor']}/{r['role']}）",
                "ref_object": r["target_object_id"], "sim": False,
                "detail": {"actor": r["actor"], "role": r["role"], "action": r["action"],
                           "result": r["result"],
                           "proposal_params": _parse_params(r["params_json"])}}))

    if "sim_ai_activity" in tables:
        for r in con.execute(
                "SELECT ai_event_id, sim_date, actor, activity, risk_event_id, task_id, "
                "detail FROM sim_ai_activity ORDER BY sim_date DESC, ai_event_id DESC LIMIT ?",
                (limit,)):
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
# 路由工厂：main.py 尾部挂载（cockpit 不 import main → 零循环导入；
# 复用 main 的 get_db_path/get_ro_connection 依赖 ⇒ 测试 dependency_overrides 自动生效）
# ═══════════════════════════════════════════════════════════════════════════
def build_cockpit_router(get_db_path: Callable, get_ro_connection: Callable,
                         infer_world: Callable[[str], str]) -> APIRouter:
    router = APIRouter(prefix="/cockpit", tags=["cockpit"])

    @router.get("/vitals")
    def cockpit_vitals(x_role: str = Header(default="ops", alias="X-Role"),
                       con: sqlite3.Connection = Depends(get_ro_connection),
                       db_path: str = Depends(get_db_path)) -> dict:
        """公司体征带：七掌控区各一枚体征块（zone/headline/trend/alert_count/detail）。
        指标 SQL 口径见模块 docstring 总表与各 _zone_* builder docstring；
        脱敏两层见 _apply_role_masks；trend/世界无关性红线见模块 docstring。"""
        tables = _tables(con)
        clock = _world_clock(con, tables)
        world = infer_world(db_path)
        world_is_sim = "sim_event_log" in tables      # 以库内证据判定，不信文件名
        payload = {
            "world": world, "clock": clock, "role": x_role,
            "zones": [
                _zone_money(con, tables, clock),
                _zone_fulfillment(con, tables, clock, world_is_sim),
                _zone_customers(con, tables),
                _zone_suppliers(con, tables),
                _zone_inventory(con, tables),
                _zone_ai(con, tables, clock, world_is_sim),
                _zone_decisions(con, tables, clock),
            ],
        }
        return _apply_role_masks(payload, x_role)

    @router.get("/panorama")
    def cockpit_panorama(x_role: str = Header(default="ops", alias="X-Role"),
                         con: sqlite3.Connection = Depends(get_ro_connection),
                         db_path: str = Depends(get_db_path)) -> dict:
        """小全景分层图数据：五层节点+关系投影边+异常锚定+迷你指标。
        口径与聚合规则（>40 分组）见 _build_panorama docstring。"""
        tables = _tables(con)
        payload = {"world": infer_world(db_path), "clock": _world_clock(con, tables),
                   "role": x_role} | _build_panorama(con, tables)
        return _apply_role_masks(payload, x_role)

    @router.get("/ai-flow")
    def cockpit_ai_flow(limit: int = Query(default=50, ge=1, le=500),
                        x_role: str = Header(default="ops", alias="X-Role"),
                        con: sqlite3.Connection = Depends(get_ro_connection),
                        db_path: str = Depends(get_db_path)) -> dict:
        """AI 工作流时间线：llm_calls / ai-agent 审计 / 提案流转 / sim 留痕按 ts 倒序合并。
        来源与排序口径见 _build_ai_flow docstring。limit 默认 50（1..500）。"""
        tables = _tables(con)
        flow = _build_ai_flow(con, tables, limit)
        payload = {"world": infer_world(db_path), "role": x_role, "limit": limit,
                   "count": len(flow),
                   "sources_present": sorted(
                       {"llm_calls", "action_log", "sim_ai_activity"} & tables),
                   "items": flow}
        return _apply_role_masks(payload, x_role)

    return router
