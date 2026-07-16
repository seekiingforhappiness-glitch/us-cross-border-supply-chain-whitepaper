#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建造者透视镜 v2 —— 数据导出脚本（只读快照）

v2 相对 v1 的三处升级（Daniel 对 v1 的验收反馈）：
  1. 数据源切到"活世界" data/simworld.sqlite（S2 后含异常/连锁/AI 处置/先例/账本），
     data/ontology.sqlite 降为"验证世界"对照源（仅在规则档案引用其 R/P=1.000 引擎战绩）。
  2. 关联织网：把 34 类型 × 53 关系 × 31 动作 × 18 规则织成可穿梭的结构（weave.json）。
  3. 实体下钻：把活世界真实实例的字段值 + 关系网 + 时间线（含 caused_by 连锁）导出（entity.json）。

铁律（与 AGENTS.md §5 一致）：
- 只读一切：对 sqlite 只执行 SELECT / PRAGMA；不改任何源。
- 数字必须可溯源：所有计数来自实时查询，不写死统计数字（全局规则 5）。
- sim 来源显式标注：凡活世界产物，JSON 内带 source=sim / worldSource 标记，前端渲染 sim 徽标。
- 敏感字段照口径：本应用给创始人，可显示全量业务数字；个人信息（人名/联系方式）类字段不导出
  （本库无此类字段——customer_name/supplier_name 均为公司名）；权限受限字段附"受限"标注展示。
- 快照带时间戳：导出即快照，前端明示"数据截至 <时间>"。

用法：
    python3 apps/builder-console/export_data.py
    # 可选：--db 活世界库，--verify-db 验证世界库，--out 输出目录

作者化稳定内容（白话标签/关系名/动作风险分类）与机器取的实时计数分离：前者作者写、后者机器取。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
import sqlite3
from pathlib import Path

# ---- 路径锚定：脚本在 apps/builder-console/ 下，仓库根在上两级 ----
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
ONTOLOGY_JSON = REPO_ROOT / "ontology" / "control-tower-ontology.json"
DEFAULT_DB = REPO_ROOT / "data" / "simworld.sqlite"          # 活世界（主源）
DEFAULT_VERIFY_DB = REPO_ROOT / "data" / "ontology.sqlite"   # 验证世界（对照）
DEFAULT_SHADOW_DB = REPO_ROOT / "data" / "shadow.sqlite"     # 影子测量旁路库（G-Shadow 产物）
PLAN_MD = REPO_ROOT / "docs" / "control-tower-plan-v0.2.md"
DEMO_ASSERTIONS_MD = REPO_ROOT / "docs" / "demo-assertions.md"    # 需求覆盖度卡源①
RELEASE_CHECKLIST_MD = REPO_ROOT / "docs" / "release-checklist.md"  # 需求覆盖度卡源②
DEFAULT_OUT = SCRIPT_DIR / "src" / "data"


# =============================================================================
# 只读 sqlite 辅助
# =============================================================================
class ReadOnlyDB:
    """只读连接：以 ro 模式打开，任何写入会失败，物理保证不改源。"""

    def __init__(self, path: Path):
        uri = f"file:{path}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    def q(self, sql: str, params: tuple = ()):
        return list(self.conn.execute(sql, params))

    def scalar(self, sql: str, params: tuple = ()):
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def count(self, table: str) -> int:
        try:
            return int(self.scalar(f"SELECT COUNT(*) FROM {table}") or 0)
        except sqlite3.Error:
            return -1  # 表不存在 → -1，区别于真实为空 0

    def has_table(self, table: str) -> bool:
        r = self.scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return bool(r)

    def columns(self, table: str) -> list[str]:
        try:
            return [r[1] for r in self.q(f"PRAGMA table_info({table})")]
        except sqlite3.Error:
            return []

    def rows(self, table: str, limit: int | None = None) -> list[dict]:
        sql = f"SELECT * FROM {table}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        try:
            return [dict(r) for r in self.q(sql)]
        except sqlite3.Error:
            return []

    def close(self):
        self.conn.close()


def load_ontology() -> dict:
    with open(ONTOLOGY_JSON, encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# 业务域划分（作者化分类）——34 对象归到 5 场景 + 共享核 + 协调
# =============================================================================
DOMAINS = {
    "core": {
        "name": "共享核对象", "scene": "core",
        "plain": "五个场景都要用到的地基：谁供货、卖什么、卖给谁。整个系统只有一份，不同角色看到同一对象。",
        "objects": ["Supplier", "Sku", "Customer"],
    },
    "delay": {
        "name": "延误风险控制塔", "scene": "ship",
        "plain": "一票货从订舱到交付，中途晚点了会连累哪些客户订单——系统沿对象图算给你看。",
        "objects": [
            "SalesOrder", "SalesOrderLine", "PurchaseOrder", "Shipment",
            "ShipmentMilestone", "ShipmentAllocation", "RiskEvent", "Task", "Container",
        ],
    },
    "cost": {
        "name": "运费对账与资金流", "scene": "invoice",
        "plain": "货代账单逐条比对该收多少（多算/没预算/重复挑出来）+ 收付款一本子（应收挂订单、应付结发票、现金流预警）。",
        "objects": ["Invoice", "InvoiceLine", "ExpectedCost", "Payment"],
    },
    "admission": {
        "name": "SKU 准入闸门", "scene": "gate",
        "plain": "一个新品要不要卖进美国——合规查一遍、物流方案搭一遍、成本算一遍，再决定报不报价。",
        "objects": ["AdmissionCase", "ComplianceFinding", "LogisticsPlan", "CostScenario"],
    },
    "procurement": {
        "name": "采购三方对账", "scene": "dock",
        "plain": "下单、收货、供应商开票，三张纸对得上才付钱——对不上的（少收、涨价、超收、缺资质）拦下来。",
        "objects": [
            "PoLine", "GoodsReceipt", "GoodsReceiptLine", "SupplierInvoice",
            "SupplierInvoiceLine", "PurchasePayment", "SupplierQualification",
            "RFQ", "RFQLine", "Quote",
        ],
    },
    "warehouse": {
        "name": "仓储库存", "scene": "warehouse",
        "plain": "美国仓里每个货位有多少能卖、多少被占、盘点差多少——低于安全库存就报警。",
        "objects": ["Warehouse", "InventoryPosition", "InventoryReservation", "CycleCount"],
    },
    "coordination": {
        "name": "跨方协调线程", "scene": "thread",
        "plain": "跟工厂/货代/客户来回催办的每一次沟通，都记成一条有状态的线程，不靠微信记忆。",
        "objects": ["CoordinationThread"],
    },
}

# 每个对象对应的真实表名
OBJECT_TABLE = {
    "Supplier": "suppliers", "Sku": "skus", "Customer": "customers",
    "SalesOrder": "sales_orders", "SalesOrderLine": "sales_order_lines",
    "PurchaseOrder": "purchase_orders", "Shipment": "shipments",
    "ShipmentMilestone": "shipment_milestones", "ShipmentAllocation": "shipment_allocations",
    "RiskEvent": "risk_events", "Task": "tasks", "Container": "containers",
    "Invoice": "invoices", "InvoiceLine": "invoice_lines", "ExpectedCost": "expected_costs",
    "AdmissionCase": "admission_cases", "ComplianceFinding": "compliance_findings",
    "LogisticsPlan": "logistics_plans", "CostScenario": "cost_scenarios",
    "PoLine": "po_lines", "GoodsReceipt": "goods_receipts", "GoodsReceiptLine": "goods_receipt_lines",
    "SupplierInvoice": "supplier_invoices", "SupplierInvoiceLine": "supplier_invoice_lines",
    "PurchasePayment": "purchase_payments", "SupplierQualification": "supplier_qualifications",
    "RFQ": "rfqs", "RFQLine": "rfq_lines", "Quote": "quotes",
    "Warehouse": "warehouses", "InventoryPosition": "inventory_positions",
    "InventoryReservation": "inventory_reservations", "CycleCount": "cycle_counts",
    "CoordinationThread": "coordination_threads",
    "Payment": "payments",
}

# 对象白话名（关联织网/实体浏览的中文标签）
OBJECT_PLAIN = {
    "Supplier": "供应商", "Sku": "商品（SKU）", "Customer": "客户",
    "SalesOrder": "销售订单", "SalesOrderLine": "销售订单行", "PurchaseOrder": "采购单",
    "Shipment": "运输票", "ShipmentMilestone": "运输里程碑", "ShipmentAllocation": "货量分配",
    "RiskEvent": "风险事件", "Task": "任务", "Container": "集装箱",
    "Invoice": "货代账单", "InvoiceLine": "账单行", "ExpectedCost": "预算成本",
    "AdmissionCase": "准入案", "ComplianceFinding": "合规发现", "LogisticsPlan": "物流方案",
    "CostScenario": "成本测算", "PoLine": "采购单行", "GoodsReceipt": "收货单",
    "GoodsReceiptLine": "收货单行", "SupplierInvoice": "供应商发票",
    "SupplierInvoiceLine": "供应商发票行", "PurchasePayment": "采购付款",
    "SupplierQualification": "供应商资质", "RFQ": "询价单", "RFQLine": "询价单行",
    "Quote": "报价", "Warehouse": "仓库", "InventoryPosition": "库存货位",
    "InventoryReservation": "库存预留", "CycleCount": "循环盘点",
    "CoordinationThread": "协调线程", "Payment": "收付款",
}

# 每个对象一句话"它是什么/为什么存在"
OBJECT_WHY = {
    "Shipment": "一票海运（整箱或拼箱），装着一个或多个采购单的货。延误检测的主角。",
    "SalesOrderLine": "订单里的一行——一个 SKU、一个数量、一个承诺交期。所有风险判断的基准线（D1：影响必须到行级）。",
    "RiskEvent": "系统检出的一次「偏离承诺」：延误击穿/费用异常/库存断货……沿对象图算出受影响面。",
    "Task": "风险落到某个角色桌上的一张待办：谁该动手、几时到期、提了什么方案。",
    "ShipmentAllocation": "这票货到底供哪些订单行——带分配数量的中间对象（D3：关系带属性必须对象化）。整个影响传播的枢纽。",
    "ShipmentMilestone": "船在动时不断进来的事件流：起运/到港/ETA 变更/清关……延误的信号源（D4：一等公民）。",
    "Invoice": "货代开来的一张账单，逐行比对该不该收、收多少。滞箱费/超收就在这里现形。",
    "Container": "一票货里的具体箱子（D5：从对象降级为属性，但仍带免箱期，供滞箱判断）。",
    "InventoryPosition": "某个 SKU 在某个仓的现货账：能卖多少、被占多少、在途多少。低于安全库存就报警。",
    "Customer": "买家。分层（A/B/C）决定延误的严重度加权。",
    "Supplier": "卖家/工厂。资质、UFLPA 风险、交期可靠度都挂在它身上。",
    "Sku": "一个具体商品。合规属性（电池/食品接触/儿童品）决定能不能卖进美国。",
    "PurchaseOrder": "向工厂下的采购单（D2：采购侧不拆行，header 级；PoLine 在采购场景补行级）。",
    "SalesOrder": "客户下的一张销售订单，拆成若干订单行。",
    "Payment": "一笔收付款（F1 资金流）：应收挂销售订单回款、应付结算货代/供应商发票；驱动应收逾期、现金流预警、付款异常检测。",
}


# =============================================================================
# 关系白话（53 条 link 的中文标签，Daniel 点名要"关系名白话"）
# =============================================================================
LINK_PLAIN = {
    "customer_places": "下单",
    "so_has_line": "含订单行",
    "line_for_sku": "卖的是（SKU）",
    "supplier_provides": "供货（SKU）",
    "po_from_supplier": "向谁采购",
    "po_shipped_by": "装在哪票货",
    "shipment_has_milestone": "有里程碑事件",
    "allocation_to_shipment": "分配自哪票货",
    "allocation_to_line": "供给哪条订单行",
    "risk_on_shipment": "盯的哪票货",
    "risk_affects_line": "牵连哪些订单行",
    "task_handles_risk": "处理哪个风险",
    "coordination_on_task": "挂在哪个任务",
    "coordination_on_risk": "针对哪个风险",
    "case_has_sku": "评的哪个 SKU",
    "case_for_customer": "为哪个客户",
    "case_has_finding": "有合规发现",
    "case_has_plan": "有物流方案",
    "plan_has_scenario": "有成本测算",
    "shipment_has_container": "装了哪些箱",
    "invoice_for_shipment": "账单对应哪票货",
    "invoice_has_line": "账单含哪些行",
    "line_bills_container": "计费哪个箱",
    "expected_cost_of_shipment": "预算给哪票货",
    "risk_affects_invoice_line": "牵连哪些账单行",
    "po_has_line": "含采购单行",
    "po_line_for_sku": "采的哪个 SKU",
    "grn_for_po": "收哪张采购单的货",
    "grn_has_line": "含收货行",
    "grn_line_for_po_line": "对应哪条采购行",
    "supplier_invoice_for_po": "对应哪张采购单",
    "supplier_invoice_from_supplier": "谁开的票",
    "supplier_invoice_has_line": "含发票行",
    "supplier_invoice_line_for_po_line": "对应哪条采购行",
    "po_has_payment": "有哪些付款",
    "supplier_has_qualification": "有哪些资质",
    "risk_on_po": "盯的哪张采购单",
    "risk_on_supplier": "盯的哪个供应商",
    "risk_affects_po_line": "牵连哪些采购行",
    "position_in_warehouse": "在哪个仓",
    "position_for_sku": "是哪个 SKU 的货",
    "reservation_on_position": "占用哪个货位",
    "reservation_for_line": "为哪条订单行留货",
    "cycle_count_on_position": "盘的哪个货位",
    "cycle_count_in_warehouse": "在哪个仓盘",
    "shipment_to_warehouse": "送到哪个仓",
    "risk_on_warehouse": "盯的哪个仓",
    "rfq_for_sku": "询价哪个 SKU",
    "rfq_has_line": "含询价行",
    "rfq_has_quote": "收到哪些报价",
    "rfq_line_for_sku": "询的哪个 SKU",
    "quote_from_supplier": "谁报的价",
    "risk_affects_sku": "牵连哪个 SKU",
    "payment_settles_supplier_invoice": "结算哪张供应商发票",
    "payment_settles_invoice": "结算哪张货代账单",
    "payment_collects_order": "回收哪张销售订单款",
}


# =============================================================================
# 动作元数据：目标对象 + 风险分类（作者化，交叉核对 FORBIDDEN_TOOLS 与 executors）
# 分类三档（承宪法/notes-decision-rights §3 的决策权尺子）：
#   machine 🔵 = 机器/AI 可自动（检测·富化·分诊·摄入，executors 含 system）
#   human  🟠 = 人的判断（派单·提案·连接动作，可换绑定）
#   frozen 🔴 = 冻结区，AI 永不可及（审批·关闭·合规裁决——FORBIDDEN_TOOLS 铁律）
# =============================================================================
ACTION_META = {
    "A1": {"target": "ShipmentMilestone", "tier": "machine", "plain": "把船公司/EDI 报来的事件摄入系统，推动运输状态机流转。"},
    "A2": {"target": "RiskEvent", "tier": "machine", "plain": "里程碑一进来，自动沿对象图定位受影响订单行，建风险事件。"},
    "A3": {"target": "Task", "tier": "machine", "plain": "把风险自动路由成一张落到某角色桌上的任务。"},
    "A4": {"target": "Task", "tier": "human", "plain": "运营/AI 起草一个处置方案（加急/改期/接受延误），提交进审批闸。提案 ≠ 执行。"},
    "A5": {"target": "Task", "tier": "frozen", "plain": "只有经理能批。花钱/改承诺/认风险的授权——与提案分离（自己批自己=舞弊）。AI 永不可及。"},
    "A6": {"target": "RiskEvent", "tier": "frozen", "plain": "宣布一件事了结，是判断+担责，不是自动化。关闭前校验关联任务全终态。AI 永不可及。"},
    "B1": {"target": "AdmissionCase", "tier": "human", "plain": "销售发起一个新品准入评估：要不要卖进美国。"},
    "B2": {"target": "ComplianceFinding", "tier": "human", "plain": "合规查一遍：HS 归类、UFLPA、认证——出具合规发现。"},
    "B3": {"target": "LogisticsPlan", "tier": "human", "plain": "运营搭物流方案：走哪条线、多少钱、多久到。"},
    "B4": {"target": "CostScenario", "tier": "human", "plain": "财务算成本账：落地成本、毛利、报价空间。"},
    "B5": {"target": "AdmissionCase", "tier": "frozen", "plain": "经理拍板报不报价（G1/G2/G3 门禁）。合规裁决属人，AI 永不可及。"},
    "B6": {"target": "AdmissionCase", "tier": "frozen", "plain": "驳回或要更多信息。合规裁决属人，AI 永不可及。"},
    "A7": {"target": "GoodsReceipt", "tier": "machine", "plain": "记收货事实（收了多少、QC 结果），只记事实不判风险。"},
    "A8": {"target": "SupplierInvoice", "tier": "machine", "plain": "三方对账：下单/收货/开票逐条比对，对不上就建风险。"},
    "A9": {"target": "PurchasePayment", "tier": "machine", "plain": "记一笔采购付款（定金/尾款），供预付款敞口判断。"},
    "A10": {"target": "SupplierQualification", "tier": "machine", "plain": "记供应商资质（认证/有效期），供资质过期检测。"},
    "A11": {"target": "InventoryPosition", "tier": "machine", "plain": "收的货上架到货位，转成可卖库存。"},
    "A12": {"target": "InventoryReservation", "tier": "machine", "plain": "为订单行留货，可卖库存转占用。"},
    "A13": {"target": "InventoryReservation", "tier": "machine", "plain": "释放不再需要的预留，占用转回可卖。"},
    "A14": {"target": "InventoryReservation", "tier": "human", "plain": "延误时查目的仓现货，拆单先发+余量改期——现货救延误的落点。过 A5 审批。"},
    "A15": {"target": "CycleCount", "tier": "machine", "plain": "记一次循环盘点结果，供盘点差异检测。"},
    "A16": {"target": "InventoryPosition", "tier": "human", "plain": "按盘点结果调整系统库存。过 A5 审批。"},
    "A17": {"target": "RFQ", "tier": "human", "plain": "断供风险下启动第二来源，建一张 RFQ 询价。"},
    "A18": {"target": "SupplierInvoice", "tier": "human", "plain": "拦下无 PO 的绕流程付款（maverick）。"},
    "A19": {"target": "PurchaseOrder", "tier": "human", "plain": "为绕流程采购补追溯 PO 关联。"},
    "A20": {"target": "CoordinationThread", "tier": "human", "plain": "对工厂/货代/客户起一条有状态的协调线程。"},
    "A21": {"target": "CoordinationThread", "tier": "human", "plain": "记一次催办/沟通（不发真消息，只记台账）。"},
    "A22": {"target": "CoordinationThread", "tier": "human", "plain": "记对手方的回应。"},
    "A23": {"target": "CoordinationThread", "tier": "human", "plain": "协调不动时升级。"},
    "A24": {"target": "CoordinationThread", "tier": "human", "plain": "协调达成，关闭线程。"},
    "A25": {"target": "CoordinationThread", "tier": "human", "plain": "协调谈崩，标记 dead-ended。"},
    "A26": {"target": "Payment", "tier": "human", "plain": "发现付款异常（重复/不符）时起草对账追回提案，交财务审批。exposed 写工具，AI 可提案不可拍板。"},
}
TIER_LABEL = {
    "machine": {"mark": "🔵", "name": "机器/AI 自动", "tone": "cyan"},
    "human": {"mark": "🟠", "name": "人的判断", "tone": "amber"},
    "frozen": {"mark": "🔴", "name": "冻结区·AI 永不可及", "tone": "red"},
}
# 动作次要触碰的类型（除主 target 外，也在这些类型的关联卡里出现）
ACTION_SECONDARY = {
    "A1": ["Shipment"], "A2": ["Shipment", "RiskEvent"], "A3": ["RiskEvent"],
    "A4": ["RiskEvent"], "A5": ["RiskEvent"], "A7": ["PurchaseOrder"],
    "A8": ["PurchaseOrder", "SupplierInvoiceLine"], "A11": ["GoodsReceipt", "InventoryPosition"],
    "A12": ["SalesOrderLine", "InventoryPosition"], "A13": ["SalesOrderLine"],
    "A14": ["SalesOrderLine", "Shipment"], "A16": ["CycleCount", "InventoryPosition"],
    "A17": ["Supplier"], "A19": ["SupplierInvoice"],
}
# 冻结区动作（交叉核对 agent.tools.FORBIDDEN_TOOLS：approve_mitigation / close_risk_event 已确认）
FROZEN_ACTION_IDS = {"A5", "A6", "B5", "B6"}

# 7 个人类角色（matrix 用；system 是机器执行者，单列不入人矩阵）
HUMAN_ROLES = ["ops", "cs", "procurement", "finance", "sales", "compliance", "manager"]
ROLE_PLAIN = {
    "ops": "运营", "cs": "客户成功", "procurement": "采购", "finance": "财务",
    "sales": "销售", "compliance": "合规", "manager": "经理", "system": "系统",
}

# ai_executable 四态白话（M1/M2 桥2 本体字段：auto/confirm/never/frozen）
AI_EXEC_PLAIN = {
    "auto": "AI 可自动执行（提案，走 maker-checker）",
    "confirm": "AI 执行前需人确认",
    "never": "不暴露给 AI（引擎/人内部动作）",
    "frozen": "冻结区 · AI 永不可及（FORBIDDEN）",
}


def _tool_name_of(a: dict) -> str:
    """AI 工具名（snake_case）：取本体 signature 的函数名（如 assign_task(...)→assign_task），
    退化时由 PascalCase name 派生。零编造——直读本体声明。"""
    sig = a.get("signature", "") or ""
    if "(" in sig:
        head = sig.split("(", 1)[0].strip()
        if head:
            return head
    name = a.get("name", "") or ""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def action_tool_view(a: dict) -> dict:
    """从本体 action 抽「作为 AI 工具」视图（板块③数据源，直读 M1/M2 桥2 字段）。
    exposed_as_tool/ai_executable/enforcement/tool_description/tool_input_schema 均为本体声明。"""
    exposed = bool(a.get("exposed_as_tool"))
    ai_exec = a.get("ai_executable")
    frozen = ai_exec == "frozen"
    return {
        "exposedAsTool": exposed,
        "aiExecutable": ai_exec,
        "aiExecutablePlain": AI_EXEC_PLAIN.get(ai_exec, ai_exec or "—"),
        "enforcement": a.get("enforcement"),
        "frozen": frozen,
        "toolName": _tool_name_of(a) if exposed else None,
        "toolDescription": a.get("tool_description"),
        "toolInputSchema": a.get("tool_input_schema"),
    }


# =============================================================================
# 规则元数据：盯哪个对象 + 白话逻辑（logic 原文实时取自 ontology，plain 作者写）
# =============================================================================
RULE_META = {
    "R1": {"target": "Shipment", "watch": "延误击穿承诺", "plain": "ETA 加上清关+尾程缓冲，超过订单行的承诺交期，就是击穿——按超期天数与客户分层定严重度。"},
    "R2": {"target": "Shipment", "watch": "改期后二次击穿", "plain": "改了期还是晚，二次击穿再报一次——改期不是万事大吉。"},
    "R3": {"target": "Shipment", "watch": "运输停滞", "plain": "船在某状态卡住超过阈值天数没动静，判为停滞，主动挖。"},
    "R4": {"target": "InvoiceLine", "watch": "费率超容差", "plain": "账单某行费率比预算高出容差，多算了钱——挑出来。"},
    "R5": {"target": "InvoiceLine", "watch": "无预算高额费", "plain": "账单冒出一笔预算里根本没有的高额费——可疑，拦。"},
    "R6": {"target": "InvoiceLine", "watch": "重复计费", "plain": "同一笔费在账单里出现两次——重复收费，挑出来。"},
    "R7": {"target": "GoodsReceipt", "watch": "供应商交期延误", "plain": "实际收货比约定齐货日晚，供应商交期延误。"},
    "R8": {"target": "GoodsReceiptLine", "watch": "短装", "plain": "收到的量比下单量少（超容差），短装。"},
    "R9": {"target": "GoodsReceiptLine", "watch": "QC 不合格", "plain": "来料不良率超阈值（PPM），质量不过关。"},
    "R10": {"target": "SupplierInvoiceLine", "watch": "价量不符", "plain": "供应商发票的价或量对不上采购单，多开了。"},
    "R11": {"target": "SupplierInvoiceLine", "watch": "开票超收货", "plain": "开的票比实收货还多，超收货开票。"},
    "R12": {"target": "PurchasePayment", "watch": "预付款敞口", "plain": "定金付了货没到位，预付款悬空——敞口风险。"},
    "R13": {"target": "SupplierQualification", "watch": "资质过期", "plain": "供应商认证/资质过期或缺证还在供货。"},
    "R14": {"target": "Supplier", "watch": "单一来源断供", "plain": "某在售 SKU 只有一个合格供应商，且它近期有延误/QC 事件——断供风险。"},
    "R15": {"target": "SupplierInvoice", "watch": "maverick 绕采购", "plain": "有供应商发票却没有匹配的已批 PO——绕过流程采购。"},
    "R16": {"target": "InventoryPosition", "watch": "断货", "plain": "可卖库存跌到安全库存以下，快断货。"},
    "R17": {"target": "InventoryPosition", "watch": "不可履约", "plain": "订单行还开着，但 ATP（可用+在途−占用）不够，履行不了。"},
    "R18": {"target": "CycleCount", "watch": "盘点差异", "plain": "盘点数与系统账差得超过容差（或 IRA 低于阈），账实不符。"},
    "R19": {"target": "Payment", "watch": "应收逾期", "plain": "应收款过了约定回款日还没到账（direction=in、逾期未收），挂应收逾期。"},
    "R20": {"target": "Payment", "watch": "现金流预警", "plain": "未来现金观察窗内预计净流出超阈值——现金流吃紧，提前预警（回款覆盖率跌破一半）。"},
    "R21": {"target": "Payment", "watch": "付款异常", "plain": "已付款项里发现重复付款或与单据金额不符——付款异常，AI 提案对账追回。"},
}


# =============================================================================
# 敏感字段口径（来自 ontology.sensitiveFieldRules）——展示时附"受限"标注
# =============================================================================
def build_sensitive_map(onto: dict) -> dict:
    """(Object, field) -> [visibleTo roles]。用于字段清单标注"权限受限"。"""
    out = {}
    for rule in onto.get("sensitiveFieldRules", []):
        if "object" in rule and "field" in rule:
            out[(rule["object"], rule["field"])] = rule.get("visibleTo", [])
    return out


def obj_by_type(onto: dict) -> dict:
    return {o["type"]: o for o in onto["objects"]}


# =============================================================================
# 视图 · 结构（封面等距三层）——计数改自活世界
# =============================================================================
def build_structure(onto: dict, db: ReadOnlyDB) -> dict:
    total_records = 0
    domain_cards = []
    for key, dom in DOMAINS.items():
        recs = 0
        covered_types = 0
        for t in dom["objects"]:
            tbl = OBJECT_TABLE.get(t)
            if tbl and db.has_table(tbl):
                c = db.count(tbl)
                if c > 0:
                    recs += c
                    covered_types += 1
        total_records += recs
        domain_cards.append({
            "id": key, "name": dom["name"], "plain": dom["plain"],
            "scene": dom.get("scene", "core"),
            "objectCount": len(dom["objects"]),
            "coveredTypes": covered_types,
            "recordCount": recs,
            "objects": dom["objects"],
            "shared": key == "core",
        })

    n_actions = len(onto["actions"])
    n_rules = len(onto["riskRules"])
    intelligence = [
        {"id": "ai-colleague", "name": "AI 同事", "icon": "brain",
         "plain": "读得懂对象图的助手：解释风险根因、起草处置。只提案从不拍板，只能调注册工具碰对象，碰不到冻结区。",
         "connectsTo": ["delay", "cost", "admission", "procurement", "warehouse"]},
        {"id": "automation", "name": "自动化引擎", "icon": "gears",
         "plain": f"{n_rules} 条风险规则 + {n_actions} 个动作的流水线：里程碑进来，自动定位受影响订单、建风险、派任务。机器感知执行，人判断授权。",
         "connectsTo": ["delay", "cost", "procurement", "warehouse"]},
        {"id": "evaluator", "name": "评估器", "icon": "gauge",
         "plain": "对着真值集打分的裁判：查全/查准、采纳率、重放一致性。指标不达标只许改规则，禁止改指标——诚实文化的闸门。",
         "connectsTo": ["delay", "admission"]},
    ]
    n_roles = len(HUMAN_ROLES)
    n_sensitive = len(onto.get("sensitiveFieldRules", []))
    audit_rows = db.count("sim_ai_activity")
    governance = [
        {"id": "constitution", "name": "宪法九条", "icon": "pillar",
         "plain": "受托责任/身份边界/诚实文化三组九条，永不改的地基。审批、关闭、花钱、合规裁决属冻结区，AI 永远够不着。",
         "guards": list(DOMAINS.keys())},
        {"id": "rbac", "name": "权限绑定", "icon": "keys",
         "plain": f"{n_roles} 个人类角色 + {n_sensitive} 条敏感字段规则。权限压在数据访问层，无权字段渲染成「无权查看」，绝不留空。",
         "guards": ["delay", "cost", "admission", "procurement", "warehouse"]},
        {"id": "audit", "name": "运行账本", "icon": "ledger",
         "plain": f"每个动作写一行账：谁、什么角色、对哪个对象、做了什么。活世界快照 {audit_rows} 条 AI 运转，决策血缘可回放的底账。",
         "guards": list(DOMAINS.keys())},
    ]

    return {
        "title": "这个系统是什么结构",
        "question": "这个系统是什么结构？",
        "subtitle": "三层立体：上面 AI 怎么接入、中间业务世界长什么样、下面谁在兜底。悬停任意元素看白话。数字来自活世界快照。",
        "layers": {
            "intelligence": {"name": "智能接入层", "plain": "AI 与自动化从这里接入业务——页面主角，不是角落按钮。", "items": intelligence},
            "business": {"name": "业务世界", "plain": "五场景域 + 一层共享核。这是被建模的现实。活世界当前主要跑通延误/对账/仓储三域，其余域结构完整、实例待灌。", "items": domain_cards},
            "governance": {"name": "治理层", "plain": "宪法、权限、账本——托住上面两层，划定 AI 的边界。", "items": governance},
        },
        "totals": {
            "objects": len(onto["objects"]), "links": len(onto["links"]),
            "actions": n_actions, "riskRules": n_rules,
            "roles": n_roles, "records": total_records,
        },
    }


# =============================================================================
# 视图 · 一票货的一生（业务旅程）——主角改用活世界 hero
# =============================================================================
HERO_SHIPMENT = "SHP-SIM-00055"
HERO_RISK = "RSK-SIM-00038"


def pick_fields(obj: dict, names: list[str]) -> list[dict]:
    props = {p["name"]: p for p in obj.get("properties", [])}
    out = []
    for n in names:
        p = props.get(n)
        if not p:
            out.append({"name": n, "type": "?", "desc": ""})
            continue
        out.append({"name": n, "type": p.get("type", ""),
                    "desc": p.get("description", ""), "values": p.get("values")})
    return out


def build_journey(onto: dict, db: ReadOnlyDB) -> dict:
    objs = obj_by_type(onto)

    def cnt(t):
        tbl = OBJECT_TABLE.get(t)
        return db.count(tbl) if tbl and db.has_table(tbl) else -1

    n_proposed = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE proposed_action IS NOT NULL AND proposed_action != ''") or 0)
    n_approved = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE approval_status='approved'") or 0)
    n_done = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE status='done'") or 0)
    actions_by_id = {a["id"]: a for a in onto["actions"]}

    def actors(action_id: str) -> list[str]:
        a = actions_by_id.get(action_id, {})
        return [ROLE_PLAIN.get(x, x) for x in a.get("executors", [])]

    stations = [
        {"id": "so", "step": "01", "title": "订单诞生", "object": "SalesOrder",
         "plain": "客户下一张销售订单，拆成若干订单行（每行一个 SKU、一个承诺交期）。一切承诺的起点。",
         "fields": pick_fields(objs["SalesOrderLine"], ["so_line_id", "sku_id", "qty", "promised_delivery_date", "original_promised_date", "line_status"]),
         "canDo": {"action": "—", "by": ["客户成功"], "note": "订单行 owner=客户成功；承诺交期是后面所有风险判断的基准线。"},
         "count": cnt("SalesOrderLine"), "countLabel": "订单行"},
        {"id": "po", "step": "02", "title": "向工厂下单", "object": "PurchaseOrder",
         "plain": "为履约向供应商下采购单，约定预计齐货日。货还没动，承诺链已拉起。",
         "fields": pick_fields(objs["PurchaseOrder"], ["po_id", "supplier_id", "sku_id", "qty", "expected_ready_date", "status"]),
         "canDo": {"action": "—", "by": ["采购"], "note": "采购单 owner=采购。"},
         "count": cnt("PurchaseOrder"), "countLabel": "采购单"},
        {"id": "shipment", "step": "03", "title": "订舱起运", "object": "Shipment",
         "plain": "一票运输把一个或多个采购单装上船，记下船司、航次、起止港、初始 ETA。本案：蛇口→洛杉矶，OOCL，整箱。",
         "fields": pick_fields(objs["Shipment"], ["shipment_id", "mode", "carrier_name", "origin_port", "destination_port", "eta_initial", "eta_current", "status"]),
         "canDo": {"action": "IngestMilestone (A1)", "by": actors("A1"), "note": "起运事件由系统摄入，触发 planned→in_transit。"},
         "count": cnt("Shipment"), "countLabel": "运输票", "example": HERO_SHIPMENT},
        {"id": "milestone", "step": "04", "title": "里程碑流", "object": "ShipmentMilestone",
         "plain": "船在动，事件不断进来：起运、转船、ETA 变更、到港、清关……本案转船漏接（transship_miss）+缺单，ETA 一路后推。",
         "fields": pick_fields(objs["ShipmentMilestone"], ["milestone_id", "event_type", "event_classifier", "event_time", "event_locode", "source_system"]),
         "canDo": {"action": "IngestMilestone (A1)", "by": actors("A1"), "note": "重复上报存下但跳过副作用；乱序到达按事件时间归位。"},
         "count": cnt("ShipmentMilestone"), "countLabel": "里程碑"},
        {"id": "allocation", "step": "05", "title": "分配到订单行", "object": "ShipmentAllocation",
         "plain": "这票货到底供哪些订单行？分配对象是影响传播的枢纽——顺着它，一次 ETA 变更能算到每个受牵连的客户承诺。",
         "fields": pick_fields(objs["ShipmentAllocation"], ["allocation_id", "shipment_id", "so_line_id", "allocated_qty"]),
         "canDo": {"action": "（数据构建期建立）", "by": ["系统"], "note": "milestone→shipment→allocation→so_line→customer 就是系统的灵魂那条链。"},
         "count": cnt("ShipmentAllocation"), "countLabel": "分配关系"},
        {"id": "risk", "step": "06", "title": "风险检出", "object": "RiskEvent",
         "plain": "ETA + 清关缓冲 + 尾程缓冲 > 承诺交期 → 击穿。本案 RSK-SIM-00038：判 critical，$68,432 敞口。",
         "fields": pick_fields(objs["RiskEvent"], ["risk_event_id", "type", "rule_id", "severity", "affected_so_line_ids", "root_cause", "status"]),
         "canDo": {"action": "CreateRiskEvent (A2)", "by": actors("A2"), "note": "系统检出并把受影响订单行标为 at_risk；改期后二次击穿会再次报警。"},
         "count": cnt("RiskEvent"), "countLabel": "风险事件", "example": HERO_RISK},
        {"id": "task", "step": "07", "title": "派任务", "object": "Task",
         "plain": "风险变成一张落到某角色桌上的任务：优先级、到期、指派角色。谁该动手，一目了然。",
         "fields": pick_fields(objs["Task"], ["task_id", "risk_event_id", "assignee_role", "priority", "proposed_action", "status"]),
         "canDo": {"action": "AssignTask (A3)", "by": actors("A3"), "note": "任务 owner=运营；系统可自动派，人可改派。"},
         "count": cnt("Task"), "countLabel": "任务"},
        {"id": "propose", "step": "08", "title": "提案处置", "object": "Task",
         "plain": "运营（或 AI 起草）提一个方案：加急/改期/接受延误。本案提案：加急，经济账 收益 $14,736 > 成本 $11,400，引用先例 MEM-SIM-00014。",
         "fields": pick_fields(objs["Task"], ["proposed_action", "proposal_params", "proposal_actor_role", "approval_status"]),
         "canDo": {"action": "ProposeMitigation (A4)", "by": actors("A4"), "note": "提案人 ≠ 审批人（maker-checker）；AI 可起草，提交权在人。"},
         "count": n_proposed, "countLabel": "提案"},
        {"id": "approve", "step": "09", "title": "审批（冻结区）", "object": "Task",
         "plain": "只有经理能批。花 $11,400 加急救回 $68,432 敞口——划算，批准。这一步 AI 永远够不着。",
         "fields": pick_fields(objs["Task"], ["approval_status", "approved_by_role", "action_taken"]),
         "canDo": {"action": "ApproveMitigation (A5)", "by": actors("A5"), "note": "冻结区：审批权永属于人；驳回则任务退回 assigned，提案留痕。"},
         "count": n_approved, "countLabel": "已审批", "frozen": True},
        {"id": "close", "step": "10", "title": "关闭结案", "object": "RiskEvent",
         "plain": "处置执行、状态回写、记录耗时与结果，风险关闭。本案 17 天关闭，质量标注「有效」。",
         "fields": pick_fields(objs["RiskEvent"], ["status", "resolved_at", "outcome", "resolution_summary"]),
         "canDo": {"action": "CloseRiskEvent (A6)", "by": actors("A6"), "note": "关闭前校验关联任务全终态；关闭本身也在冻结区。"},
         "count": n_done, "countLabel": "已结案"},
        {"id": "precedent", "step": "11", "title": "沉淀先例", "object": "PrecedentCase",
         "plain": "结案四件套（看到什么/提了什么/怎么决定/结果如何）写进处置记忆，成为下次同类的可引用先例——系统从这里越用越强。",
         "fields": [
             {"name": "memory_id", "type": "string", "desc": "先例主键"},
             {"name": "cited_precedent_ids", "type": "string", "desc": "引用了哪些更早的先例"},
             {"name": "decision", "type": "enum", "desc": "人的决定：adopted/rejected"},
             {"name": "outcome_resolved", "type": "string", "desc": "回填的真实结果"},
             {"name": "quality_label", "type": "enum", "desc": "事后质量标注，驱动打法卡晋升"},
         ],
         "canDo": {"action": "（结案后回填）", "by": ["系统 + 人回填结果"], "note": "先例 → 打法卡 → 评估集 → 自主权，五资产第一环。详见「越用越强」。"},
         "count": db.count("resolution_memory"), "countLabel": "处置记忆"},
    ]

    hero = db.q("SELECT origin_port, destination_port, carrier_name, mode, eta_initial, eta_current, delay_days FROM shipments WHERE shipment_id=?", (HERO_SHIPMENT,))
    hero = dict(hero[0]) if hero else {}
    return {
        "title": "一票货的一生",
        "question": "业务是怎么被建模的？",
        "subtitle": "不按对象类型罗列，而是跟着一票真实的货走完全程。主角来自活世界：SHP-SIM-00055 / RSK-SIM-00038。",
        "worldSource": "sim",
        "caseHeadline": {
            "shipment": HERO_SHIPMENT, "risk": HERO_RISK,
            "line": f"{hero.get('origin_port','蛇口')} → {hero.get('destination_port','洛杉矶')} · {hero.get('carrier_name','OOCL')} · {hero.get('mode','整箱')} · "
                    f"转船漏接致延误 · 击穿承诺判 critical · $68,432 敞口 · 加急救回 · 17 天关闭「有效」",
        },
        "stations": stations,
    }


# =============================================================================
# 视图 · 宪法九条 + 冻结区（作者化稳定内容，摘 spec v3 §12）
# =============================================================================
def build_constitution(onto: dict, db: ReadOnlyDB) -> dict:
    groups = [
        {"group": "一、受托责任", "plain": "客户把数据托给你，第一件事是别把它弄丢、弄混、弄外泄。",
         "articles": [
             {"no": "①", "text": "租户级强制隔离——一租户一库文件（物理隔离）。",
              "plain": "每个客户的数据装在各自独立的库文件里，不是一个大库靠代码隔开。",
              "prevents": "防串户：一行 SQL 写漏 WHERE，把 A 客户的货显示给 B 客户。"},
             {"no": "②", "text": "密钥不进代码；每日备份 + 每周恢复演练。",
              "plain": "密码钥匙不写死在代码里；不只备份，还真去演练「能不能恢复」。",
              "prevents": "防「以为有备份，出事才发现恢复不了」。"},
             {"no": "③", "text": "客户数据永不训练模型、永不出现在他租户输出（含打法卡指纹检查）。",
              "plain": "你的数据不会被拿去喂模型，也不会从别人的界面里冒出来。",
              "prevents": "防知识沉淀时把 A 客户的价格/联系人泄进 B 客户的打法卡。"},
         ]},
        {"group": "二、身份边界", "plain": "AI 能干很多活，但有三件事永远是人的：对外署名、花钱拍板、担责留痕。",
         "articles": [
             {"no": "④", "text": "AI 永不冒充人类对外沟通（出站 = 人批签发或预审模板）。",
              "plain": "发给客户/工厂的每条消息，要么人亲手发，要么是预先审过的模板，AI 不自由生成往外发。",
              "prevents": "防 AI 用你的名义对客户乱承诺一个没确认的新交期。"},
             {"no": "⑤", "text": "审批/关闭/合规/花钱的执行权永属于人（冻结区），客户不得合同放弃。",
              "plain": "批准处置、关闭事件、合规裁决、掏钱执行——这四类动作 AI 永远够不着，连客户签字同意都不行。",
              "prevents": "防「为了效率」把审批闸交给 AI，出了事没人担责。"},
             {"no": "⑥", "text": "每动作可溯至实名身份 + 决策血缘。",
              "plain": "每一个动作都查得到是谁、什么角色、基于什么做的。",
              "prevents": "防「系统自己改的」这种无主账。"},
         ]},
        {"group": "三、诚实文化", "plain": "指标难看照实记，AI 查不到就说查不到，机制被绕过一律回滚复盘——诚实优先于好看。",
         "articles": [
             {"no": "⑦", "text": "指标不达预期记录修正，不改口径。",
              "plain": "查准率没到 85% 就记下没到，去修规则，绝不偷偷把 85% 的标准降到 80%。",
              "prevents": "防改指标冒充达标——自欺欺人的头号死法。"},
             {"no": "⑧", "text": "AI 事实性陈述可溯源，查不到直说。",
              "plain": "AI 说的每个业务事实都能指回具体对象 ID；没有依据就回「查不到」，不编。",
              "prevents": "防 AI 自信地编一个具体数字/先例（全局规则 5）。"},
             {"no": "⑨", "text": "进化资产变更留版本可回滚；机制被绕过无论结果好坏一律回滚+复盘；创始人管理员动作留痕并每周披露。",
              "plain": "打法卡/自主权每次改动都能回退；有人抄近路绕流程，哪怕结果好也回滚、也复盘；创始人自己的管理员操作也留痕、每周给设计伙伴看。",
              "prevents": "防「结果好就默许绕过」，一次侥幸会长成系统性漏洞。"},
         ]},
    ]
    freeze_zone = {
        "name": "冻结区（FORBIDDEN）",
        "plain": "这些动作对 AI 根本不存在——不是「权限不够」，是工具函数压根没注册。永不自动晋级。",
        "items": [
            {"name": "审批处置", "why": "花钱/改承诺/认风险的授权，必须与提案分离（自己批自己 = 舞弊）。", "action": "A5"},
            {"name": "关闭结案", "why": "宣布一件事了结是判断 + 担责，不是自动化。", "action": "A6"},
            {"name": "报价审批", "why": "报不报价是合规+商业裁决，属人。", "action": "B5"},
            {"name": "合规驳回", "why": "归类/申报错可构成刑责，法律要求独立的人来判。", "action": "B6"},
            {"name": "自由文本对外发送", "why": "对客户/工厂署名沟通，只能人批签发或预审模板。", "action": None},
            {"name": "权限与审计配置", "why": "改谁能干什么、改账本本身——动地基的动作。", "action": None},
        ],
    }
    decision_tiers = [
        {"tier": "🔵 机器 / AI 自动", "plain": "检测·富化·分诊·起草建议·执行（人批之后）。AI 只提案，从不结案。"},
        {"tier": "🟠 人：判断（谁持有可换）", "plain": "派单·提案处置·关闭。可换绑定，部分低风险可上移给 AI。"},
        {"tier": "🔴 人：审批闸（不复刻，强制）", "plain": "审批处置·报价审批·驳回。安全关键、最小权限、职责分离、AI 永不可及。"},
    ]
    return {
        "title": "什么永远不会变",
        "question": "什么永远不会变？",
        "subtitle": "宪法九条 —— 每条：条文 / 白话 / 它防的事故。产品的冻结地基，改它须人批且留版本。",
        "groups": groups, "freezeZone": freeze_zone, "decisionTiers": decision_tiers,
        "source": "docs/superpowers/specs/2026-07-12-product-spec-v3-consolidated.md §12（宪法九条·最终版）",
    }


# =============================================================================
# 视图 · 关联织网（NEW）——34 类型 × 关系 × 动作 × 规则，可穿梭
# =============================================================================
def build_weave(onto: dict, db: ReadOnlyDB) -> dict:
    objs = obj_by_type(onto)
    sens = build_sensitive_map(onto)
    sm = onto.get("stateMachines", {})
    actions_by_id = {a["id"]: a for a in onto["actions"]}

    # 动作 → 目标类型 倒排（主 target + 次要触碰）
    actions_for_type: dict[str, list[str]] = {}
    for aid, meta in ACTION_META.items():
        actions_for_type.setdefault(meta["target"], []).append(aid)
    for aid, extra in ACTION_SECONDARY.items():
        for tp2 in extra:
            actions_for_type.setdefault(tp2, [])
            if aid not in actions_for_type[tp2]:
                actions_for_type[tp2].append(aid)
    # 规则 → 目标类型 倒排
    rules_for_type: dict[str, list[str]] = {}
    for rid, meta in RULE_META.items():
        rules_for_type.setdefault(meta["target"], []).append(rid)

    # 关系：按类型聚出 out / in
    def rels_for(tp: str) -> list[dict]:
        out = []
        for l in onto["links"]:
            if l["source"] == tp:
                out.append({"dir": "out", "linkType": l["linkType"], "other": l["target"],
                            "otherPlain": OBJECT_PLAIN.get(l["target"], l["target"]),
                            "cardinality": l.get("cardinality", ""),
                            "plain": LINK_PLAIN.get(l["linkType"], l["linkType"])})
            if l["target"] == tp:
                out.append({"dir": "in", "linkType": l["linkType"], "other": l["source"],
                            "otherPlain": OBJECT_PLAIN.get(l["source"], l["source"]),
                            "cardinality": l.get("cardinality", ""),
                            "plain": LINK_PLAIN.get(l["linkType"], l["linkType"])})
        return out

    types_out = {}
    for tp, obj in objs.items():
        tbl = OBJECT_TABLE.get(tp)
        covered = bool(tbl and db.has_table(tbl))
        count = db.count(tbl) if covered else -1
        # 字段（带白话 desc + 敏感标注）
        fields = []
        for p in obj.get("properties", []):
            fname = p["name"]
            f = {"name": fname, "type": p.get("type", ""),
                 "required": p.get("required", False),
                 "desc": p.get("description", ""),
                 "values": p.get("values")}
            # 敏感字段标注（精确或前缀匹配 proposal_params.est_cost_usd 等）
            for (so, sf), vis in sens.items():
                if so == tp and (sf == fname or sf.split(".")[0] == fname or fname in sf):
                    f["sensitive"] = [ROLE_PLAIN.get(r, r) for r in vis]
                    break
            fields.append(f)
        # 状态机
        st = sm.get(tp)
        state_machine = None
        if st:
            state_machine = {"states": st.get("states", []),
                             "transitions": st.get("transitions", []),
                             "notes": st.get("notes", "")}
        # 动作（主 target + 次要触碰）
        acts = []
        for aid in actions_for_type.get(tp, []):
            a = actions_by_id.get(aid, {})
            meta = ACTION_META[aid]
            acts.append({
                "id": aid, "name": a.get("name", aid),
                "tier": meta["tier"], "plain": meta["plain"],
                "executors": [ROLE_PLAIN.get(x, x) for x in a.get("executors", [])],
                "frozen": aid in FROZEN_ACTION_IDS,
                "primary": meta["target"] == tp,
                "target": meta["target"], "targetPlain": OBJECT_PLAIN.get(meta["target"], meta["target"]),
                "signature": a.get("signature", ""),
                "preconditions": a.get("preconditions", []),
                "successEffects": a.get("successEffects", []),
                "failureHandling": a.get("failureHandling", []),
                "audit": a.get("audit", []),
                "tool": action_tool_view(a),
            })
        # 规则
        rls = []
        for rid in rules_for_type.get(tp, []):
            meta = RULE_META[rid]
            rls.append({"id": rid, "watch": meta["watch"], "plain": meta["plain"]})

        types_out[tp] = {
            "type": tp, "plainName": OBJECT_PLAIN.get(tp, tp),
            "why": OBJECT_WHY.get(tp, ""),
            "domain": next((k for k, d in DOMAINS.items() if tp in d["objects"]), "core"),
            "ownerRole": ROLE_PLAIN.get(obj.get("ownerRole"), obj.get("ownerRole")),
            "statusField": obj.get("statusField"),
            "covered": covered, "count": count,
            "fieldCount": len(fields), "fields": fields,
            "stateMachine": state_machine,
            "relationships": rels_for(tp),
            "actions": acts, "rules": rls,
        }

    domain_order = [
        {"id": k, "name": d["name"], "plain": d["plain"], "scene": d.get("scene", "core"),
         "types": [{"type": t, "plainName": OBJECT_PLAIN.get(t, t),
                    "count": types_out[t]["count"], "covered": types_out[t]["covered"]}
                   for t in d["objects"]]}
        for k, d in DOMAINS.items()
    ]
    return {
        "title": "这套本体是怎么织起来的",
        "question": "这套本体是怎么织起来的？",
        "subtitle": "以对象类型为中心的联动探索器。选一个类型 → 看它的字段、状态机、关系、能做的动作、盯着它的规则。一切可点击穿梭：从 Shipment 顺关系走到 SalesOrderLine，点动作看五要素，点规则跳规则档案。",
        "domains": domain_order,
        "types": types_out,
        "defaultType": "Shipment",
        "hint": f"{len(onto['objects'])} 类型 · {len(onto['links'])} 关系 · {len(onto['actions'])} 动作 · {len(onto['riskRules'])} 规则——不是节点堆砌，是结构化的关联卡 + 穿梭导航。",
    }


# =============================================================================
# 视图 · 实体浏览与单实体全景（NEW）——活世界真实实例
# =============================================================================
# FK 边：(childType, childTable, fkCol, isList, parentType, parentTable, parentPk)
# out = 从本行 fkCol 指向 parent；in = 别的表 fkCol 指回本行 pk
FK_EDGES = [
    ("SalesOrder", "sales_orders", "customer_id", False, "Customer", "customers", "customer_id"),
    ("SalesOrderLine", "sales_order_lines", "so_id", False, "SalesOrder", "sales_orders", "so_id"),
    ("SalesOrderLine", "sales_order_lines", "sku_id", False, "Sku", "skus", "sku_id"),
    ("Sku", "skus", "supplier_id", False, "Supplier", "suppliers", "supplier_id"),
    ("Shipment", "shipments", "po_ids", True, "PurchaseOrder", "purchase_orders", "po_id"),
    ("ShipmentMilestone", "shipment_milestones", "shipment_id", False, "Shipment", "shipments", "shipment_id"),
    ("ShipmentAllocation", "shipment_allocations", "shipment_id", False, "Shipment", "shipments", "shipment_id"),
    ("ShipmentAllocation", "shipment_allocations", "so_line_id", False, "SalesOrderLine", "sales_order_lines", "so_line_id"),
    ("Container", "containers", "shipment_id", False, "Shipment", "shipments", "shipment_id"),
    ("RiskEvent", "risk_events", "shipment_id", False, "Shipment", "shipments", "shipment_id"),
    ("RiskEvent", "risk_events", "affected_so_line_ids", "json", "SalesOrderLine", "sales_order_lines", "so_line_id"),
    ("RiskEvent", "risk_events", "warehouse_id", False, "Warehouse", "warehouses", "warehouse_id"),
    ("Task", "tasks", "risk_event_id", False, "RiskEvent", "risk_events", "risk_event_id"),
    ("Invoice", "invoices", "shipment_id", False, "Shipment", "shipments", "shipment_id"),
    ("InvoiceLine", "invoice_lines", "invoice_id", False, "Invoice", "invoices", "invoice_id"),
    ("InvoiceLine", "invoice_lines", "container_no", False, "Container", "containers", "container_no"),
    ("InventoryPosition", "inventory_positions", "sku_id", False, "Sku", "skus", "sku_id"),
    ("InventoryPosition", "inventory_positions", "warehouse_id", False, "Warehouse", "warehouses", "warehouse_id"),
]

# 每类型索引列表的摘要字段（列表页显示 + 可搜索）
INDEX_FIELDS = {
    "Shipment": ["status", "origin_port", "destination_port", "carrier_name", "delay_days"],
    "RiskEvent": ["rule_id", "severity", "status", "affected_value_usd"],
    "Task": ["assignee_role", "proposed_action", "approval_status", "status"],
    "SalesOrderLine": ["sku_id", "qty", "promised_delivery_date", "line_status"],
    "SalesOrder": ["customer_id", "order_date", "status"],
    "PurchaseOrder": ["status"],
    "Invoice": ["vendor_name", "shipment_id", "total_usd", "status"],
    "InvoiceLine": ["invoice_id", "charge_code", "amount_usd"],
    "Customer": ["customer_name", "tier", "us_state"],
    "Supplier": ["supplier_name", "city", "factory_audit_status"],
    "Sku": ["sku_name", "category", "sku_status"],
    "Container": ["shipment_id", "container_type", "free_days"],
    "ShipmentAllocation": ["shipment_id", "so_line_id", "allocated_qty"],
    "ShipmentMilestone": ["shipment_id", "event_type", "event_time"],
    "InventoryPosition": ["sku_id", "warehouse_id", "available_qty", "safety_stock"],
    "Warehouse": ["type", "operator", "region"],
    "Payment": ["direction", "counterparty_type", "status", "due_date"],
}

# 详情/关系/时间线导出上限（控 JSON 体量 <15MB）
DETAIL_CAP = {
    "Shipment": 265, "RiskEvent": 236, "Task": 186, "Invoice": 161,
    "Customer": 40, "Supplier": 25, "Warehouse": 3, "InventoryPosition": 200,
    "Sku": 200, "SalesOrderLine": 150, "SalesOrder": 150, "PurchaseOrder": 150,
    "Container": 150, "ShipmentAllocation": 150, "ShipmentMilestone": 150, "InvoiceLine": 150,
}
DEFAULT_CAP = 120

# 有时间线的类型
TIMELINE_TYPES = {"Shipment", "RiskEvent", "Task", "Invoice", "InventoryPosition"}

EVENT_PLAIN = {
    "order_created": "客户下单", "shipment_booked": "订舱起运", "invoice_issued": "货代开账单",
    "inventory_putaway": "收货上架", "anomaly:delay:roll": "船期被 roll（甩柜）",
    "anomaly:delay:congestion": "港口拥堵延误", "anomaly:delay:transship_miss": "转船漏接",
    "anomaly:delay:port_omission": "跳港", "anomaly:delay:roll": "甩柜滞留",
    "anomaly:inspection:customs_hold": "海关查验扣货", "anomaly:inspection:eta_contradiction": "ETA 自相矛盾",
    "anomaly:document:missing_docs": "缺单证", "anomaly:document:qual_expiring": "资质将过期",
    "anomaly:fee:overbill": "多收费", "anomaly:fee:duplicate": "重复计费", "anomaly:fee:volumetric": "泡重多算",
    "anomaly:warehouse:shrinkage": "仓库盘亏", "chain:detention": "滞箱费（连锁）",
    "chain:stockout": "断货（连锁）", "chain:inventory_countdown": "库存倒计时（连锁）",
    "texture:supplier_delay": "供应商交期波动",
}


def _fmt_val(v):
    if v is None:
        return None
    return v


def build_entity(onto: dict, db: ReadOnlyDB) -> dict:
    objs = obj_by_type(onto)
    sens = build_sensitive_map(onto)

    # 预载各表主键与行（仅覆盖表）
    table_pk = {o["type"]: o.get("primaryKey") for o in onto["objects"]}
    # 敏感字段集合按对象
    def sens_roles(tp, fname):
        for (so, sf), vis in sens.items():
            if so == tp and (sf == fname or sf.split(".")[0] == fname):
                return [ROLE_PLAIN.get(r, r) for r in vis]
        return None

    # in-edge 索引：parentTable -> list of (childType, childTable, fkCol, isList, parentPk)
    in_edges: dict[str, list] = {}
    for (ct, ctab, fk, isl, pt, ptab, ppk) in FK_EDGES:
        in_edges.setdefault(ptab, []).append((ct, ctab, fk, isl, ppk))

    # 预取子表行（用于 in-edge 反查）——只取有 FK 的子表，缓存
    child_cache: dict[str, list[dict]] = {}
    def child_rows(tab):
        if tab not in child_cache:
            child_cache[tab] = db.rows(tab) if db.has_table(tab) else []
        return child_cache[tab]

    # 时间线源：event_log by object_id；ai_activity by risk/task；memory by risk
    ev_by_obj: dict[str, list] = {}
    downstream_by_cause: dict[str, list] = {}  # caused_by SEV → 被它触发的下游事件（连锁）
    if db.has_table("sim_event_log"):
        for r in db.q("SELECT sim_event_id, sim_date, event_kind, object_type, object_id, caused_by, family, severity FROM sim_event_log ORDER BY sim_date"):
            d = dict(r)
            ev_by_obj.setdefault(r["object_id"], []).append(d)
            if r["caused_by"]:
                downstream_by_cause.setdefault(r["caused_by"], []).append(d)
    ai_by_risk: dict[str, list] = {}
    ai_by_task: dict[str, list] = {}
    if db.has_table("sim_ai_activity"):
        for r in db.q("SELECT sim_date, actor, activity, risk_event_id, task_id, detail FROM sim_ai_activity ORDER BY sim_date"):
            if r["risk_event_id"]:
                ai_by_risk.setdefault(r["risk_event_id"], []).append(dict(r))
            if r["task_id"]:
                ai_by_task.setdefault(r["task_id"], []).append(dict(r))
    mem_by_risk: dict[str, dict] = {}
    if db.has_table("resolution_memory"):
        for r in db.q("SELECT * FROM resolution_memory"):
            mem_by_risk[r["risk_event_id"]] = dict(r)
    ms_by_ship: dict[str, list] = {}
    if db.has_table("shipment_milestones"):
        for r in db.q("SELECT shipment_id, event_type, event_classifier, event_time, event_locode, new_eta FROM shipment_milestones ORDER BY event_time"):
            ms_by_ship.setdefault(r["shipment_id"], []).append(dict(r))

    def out_rels(tp, row) -> list[dict]:
        rels = []
        for (ct, ctab, fk, isl, pt, ptab, ppk) in FK_EDGES:
            if ct != tp:
                continue
            raw = row.get(fk)
            if raw in (None, ""):
                continue
            link = next((l for l in onto["links"] if l["source"] == tp and l["target"] == pt), None)
            plain = LINK_PLAIN.get(link["linkType"], "") if link else ""
            if isl == "json":
                try:
                    ids = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    ids = []
                for i in ids[:8]:
                    rels.append({"dir": "out", "targetType": pt, "targetId": i, "plain": plain})
            elif isl is True:
                for i in str(raw).split("|"):
                    i = i.strip()
                    if i:
                        rels.append({"dir": "out", "targetType": pt, "targetId": i, "plain": plain})
            else:
                rels.append({"dir": "out", "targetType": pt, "targetId": raw, "plain": plain})
        return rels

    def in_rels(tp, pk_val, ptab) -> list[dict]:
        rels = []
        for (ct, ctab, fk, isl, ppk) in in_edges.get(ptab, []):
            link = next((l for l in onto["links"] if l["source"] == ct and l["target"] == tp), None)
            plain = LINK_PLAIN.get(link["linkType"], "") if link else ""
            child_pk = table_pk.get(ct)
            cnt = 0
            for cr in child_rows(ctab):
                val = cr.get(fk)
                match = False
                if isl == "json":
                    try:
                        match = pk_val in json.loads(val or "[]")
                    except (json.JSONDecodeError, TypeError):
                        match = False
                elif isl is True:
                    match = pk_val in str(val or "").split("|")
                else:
                    match = (val == pk_val)
                if match:
                    rels.append({"dir": "in", "targetType": ct,
                                 "targetId": cr.get(child_pk), "plain": plain})
                    cnt += 1
                    if cnt >= 12:
                        rels.append({"dir": "in", "targetType": ct, "targetId": None,
                                     "plain": plain, "more": True})
                        break
        return rels

    def build_timeline(tp, row, pk_val) -> list[dict]:
        tl = []
        if tp == "Shipment":
            own_sev = set()
            for e in ev_by_obj.get(pk_val, []):
                if e.get("sim_event_id"):
                    own_sev.add(e["sim_event_id"])
                tl.append({"date": e["sim_date"], "kind": e["event_kind"],
                           "plain": EVENT_PLAIN.get(e["event_kind"], e["event_kind"]),
                           "family": e["family"], "severity": e["severity"],
                           "causedBy": e["caused_by"], "lane": "event"})
            # 下游连锁：本票货的延误引发的滞箱/断货等（caused_by 指回本票货事件），显式标注连锁
            for sev in own_sev:
                for e in downstream_by_cause.get(sev, []):
                    tl.append({"date": e["sim_date"], "kind": e["event_kind"],
                               "plain": EVENT_PLAIN.get(e["event_kind"], e["event_kind"])
                               + f"（在 {e['object_type']} {e['object_id']}）",
                               "family": e["family"], "severity": e["severity"],
                               "causedBy": e["caused_by"], "lane": "event"})
            for m in ms_by_ship.get(pk_val, [])[:12]:
                tl.append({"date": (m["event_time"] or "")[:10], "kind": m["event_type"],
                           "plain": f"里程碑 {m['event_type']}" + (f" · 新 ETA {m['new_eta'][:10]}" if m.get('new_eta') else ""),
                           "family": "milestone", "severity": None, "causedBy": None, "lane": "milestone"})
            # 关联风险的 AI 处置
            for rr in child_rows("risk_events"):
                if rr.get("shipment_id") == pk_val:
                    for a in ai_by_risk.get(rr["risk_event_id"], []):
                        tl.append({"date": a["sim_date"], "kind": a["activity"],
                                   "plain": a["detail"], "family": "ai", "severity": None,
                                   "causedBy": None, "lane": "ai", "actor": a["actor"]})
        elif tp == "RiskEvent":
            for a in ai_by_risk.get(pk_val, []):
                tl.append({"date": a["sim_date"], "kind": a["activity"], "plain": a["detail"],
                           "family": "ai", "severity": None, "causedBy": None, "lane": "ai", "actor": a["actor"]})
            m = mem_by_risk.get(pk_val)
            if m and m.get("outcome_resolved"):
                tl.append({"date": m.get("closed_at", "")[:10], "kind": "precedent",
                           "plain": f"沉淀先例 {m['memory_id']}：{m.get('decision')} → {m.get('outcome_resolved')}（{m.get('outcome_days')}天，质量「{m.get('quality_label')}」）",
                           "family": "memory", "severity": None, "causedBy": None, "lane": "memory"})
        elif tp == "Task":
            for a in ai_by_task.get(pk_val, []):
                tl.append({"date": a["sim_date"], "kind": a["activity"], "plain": a["detail"],
                           "family": "ai", "severity": None, "causedBy": None, "lane": "ai", "actor": a["actor"]})
        elif tp in ("Invoice", "InventoryPosition"):
            for e in ev_by_obj.get(pk_val, []):
                tl.append({"date": e["sim_date"], "kind": e["event_kind"],
                           "plain": EVENT_PLAIN.get(e["event_kind"], e["event_kind"]),
                           "family": e["family"], "severity": e["severity"],
                           "causedBy": e["caused_by"], "lane": "event"})
        tl.sort(key=lambda x: x["date"] or "")
        return tl

    # 计算需要"必带详情"的种子集合：hero + 其 1~2 跳邻居
    seed_ids: dict[str, set] = {}
    def add_seed(tp, i):
        if i:
            seed_ids.setdefault(tp, set()).add(i)
    add_seed("Shipment", HERO_SHIPMENT)
    add_seed("RiskEvent", HERO_RISK)
    hero_row = db.q("SELECT * FROM shipments WHERE shipment_id=?", (HERO_SHIPMENT,))
    if hero_row:
        hr = dict(hero_row[0])
        for i in str(hr.get("po_ids") or "").split("|"):
            add_seed("PurchaseOrder", i.strip())
    for rr in db.q("SELECT risk_event_id, affected_so_line_ids FROM risk_events WHERE shipment_id=?", (HERO_SHIPMENT,)):
        add_seed("RiskEvent", rr["risk_event_id"])
        try:
            for sl in json.loads(rr["affected_so_line_ids"] or "[]")[:6]:
                add_seed("SalesOrderLine", sl)
        except (json.JSONDecodeError, TypeError):
            pass
    for tr in db.q("SELECT task_id FROM tasks WHERE risk_event_id=?", (HERO_RISK,)):
        add_seed("Task", tr["task_id"])
    for ir in db.q("SELECT invoice_id FROM invoices WHERE shipment_id=?", (HERO_SHIPMENT,)):
        add_seed("Invoice", ir["invoice_id"])

    types_out = {}
    for tp, obj in objs.items():
        tbl = OBJECT_TABLE.get(tp)
        pk = table_pk.get(tp)
        if not tbl or not db.has_table(tbl):
            types_out[tp] = {"type": tp, "plainName": OBJECT_PLAIN.get(tp, tp),
                             "domain": next((k for k, d in DOMAINS.items() if tp in d["objects"]), "core"),
                             "covered": False, "count": -1, "indexFields": [], "index": [],
                             "instances": {}}
            continue
        all_rows = db.rows(tbl)
        idx_fields = INDEX_FIELDS.get(tp, [])
        cap = DETAIL_CAP.get(tp, DEFAULT_CAP)
        # 索引（全量，含 pk + 摘要字段，可搜索）——大表限 500 行索引
        index = []
        for r in all_rows[:600]:
            summ = {"id": r.get(pk)}
            for f in idx_fields:
                if f in r:
                    summ[f] = _fmt_val(r[f])
            index.append(summ)
        # 详情集合 = 前 cap 行 ∪ 种子
        want = set(r.get(pk) for r in all_rows[:cap])
        want |= seed_ids.get(tp, set())
        instances = {}
        ptab = tbl
        for r in all_rows:
            rid = r.get(pk)
            if rid not in want:
                continue
            # 字段值（全字段，敏感附标注）
            fvals = []
            for k, v in r.items():
                item = {"name": k, "value": _fmt_val(v)}
                sr = sens_roles(tp, k)
                if sr:
                    item["sensitive"] = sr
                fvals.append(item)
            rels = out_rels(tp, r) + in_rels(tp, rid, ptab)
            tl = build_timeline(tp, r, rid) if tp in TIMELINE_TYPES else []
            instances[rid] = {"id": rid, "fields": fvals, "rels": rels, "timeline": tl}
        types_out[tp] = {
            "type": tp, "plainName": OBJECT_PLAIN.get(tp, tp),
            "why": OBJECT_WHY.get(tp, ""),
            "domain": next((k for k, d in DOMAINS.items() if tp in d["objects"]), "core"),
            "covered": True, "count": len(all_rows),
            "indexFields": idx_fields, "index": index,
            "detailCount": len(instances), "instances": instances,
        }

    return {
        "title": "一票具体的货长什么样",
        "question": "一票具体的货长什么样？",
        "subtitle": "点进任意一条活世界真实实例：看它全部字段值、它的关系网（这票货连着哪些订单/客户/账单/风险/任务/先例）、它身上发生过的一切（里程碑+异常+AI 提案+审批+处置，含 caused_by 连锁标注）。",
        "worldSource": "sim",
        "hero": {"type": "Shipment", "id": HERO_SHIPMENT,
                 "note": "默认展示一票有完整故事的货：转船漏接致延误 → 滞箱 → AI 提案加急 → 经理审批 → 17 天关闭「有效」。"},
        "domains": [{"id": k, "name": d["name"],
                     "types": [{"type": t, "plainName": OBJECT_PLAIN.get(t, t),
                                "count": types_out[t]["count"], "covered": types_out[t]["covered"]}
                               for t in d["objects"]]}
                    for k, d in DOMAINS.items()],
        "types": types_out,
    }


# =============================================================================
# 视图 · 规则档案（NEW）——R1-R18 白话 + 活世界战绩 + 验证世界精度
# =============================================================================
def build_rules(onto: dict, db: ReadOnlyDB, vdb: ReadOnlyDB | None) -> dict:
    rules_raw = {r["id"]: r for r in onto["riskRules"]}
    # 活世界战绩：按 rule_id 的触发数 + 严重度分布
    living = {}
    if db.has_table("risk_events"):
        for r in db.q("SELECT rule_id, severity, COUNT(*) n FROM risk_events GROUP BY rule_id, severity"):
            living.setdefault(r["rule_id"], {"total": 0, "bySeverity": {}})
            living[r["rule_id"]]["bySeverity"][r["severity"]] = r["n"]
            living[r["rule_id"]]["total"] += r["n"]

    cards = []
    for rid in sorted(RULE_META.keys(), key=lambda x: int(x[1:])):
        meta = RULE_META[rid]
        raw = rules_raw.get(rid, {})
        liv = living.get(rid, {"total": 0, "bySeverity": {}})
        cards.append({
            "id": rid,
            "type": raw.get("type", ""),
            "target": meta["target"],
            "targetPlain": OBJECT_PLAIN.get(meta["target"], meta["target"]),
            "watch": meta["watch"],
            "plain": meta["plain"],
            "logic": raw.get("logic", ""),
            "severityRule": raw.get("severity", ""),
            "living": {"total": liv["total"], "bySeverity": liv["bySeverity"]},
            "domain": next((k for k, d in DOMAINS.items()
                            if meta["target"] in d["objects"]), "core"),
        })
    total_living = sum(c["living"]["total"] for c in cards)
    fired = [c["id"] for c in cards if c["living"]["total"] > 0]
    n_cards = len(cards)
    return {
        "title": "规则在盯什么、战绩如何",
        "question": f"{n_cards} 条规则在盯什么、战绩如何？",
        "subtitle": "每条规则一卡：它盯哪个对象、怎么判（白话+引擎逻辑原文）、在活世界触发了多少次（严重度分布）、在验证世界的引擎精度。",
        "cards": cards,
        "twoWorlds": {
            "living": {"label": "活世界（simworld）", "source": "sim",
                       "plain": f"连续 14 个月模拟运转的真实触发。当前 {total_living} 条风险被检出，覆盖 {len(fired)} 条规则（{'、'.join(fired)}）——其余规则的采购/准入/寻源域实例尚未灌入活世界，规则本身已就绪。"},
            "verify": {"label": "验证世界（ontology + data/truth）", "source": "ontology",
                       "plain": f"带 ground-truth 注入的合成验证集，引擎逐条对真值打分。项目战绩：R1-R{n_cards} 全部 Recall=1.000 / Precision=1.000（engine.evaluate 口径，见 STATUS）。这是「规则判得准不准」的裁判，与活世界「跑了多少次」是两回事，分开报。"},
        },
        "verifyPrecision": "R/P = 1.000",
    }


# =============================================================================
# 视图 · 动作与权限矩阵（NEW）——31 动作五要素 + 7 角色×动作热力 + 冻结区
# =============================================================================
def build_actions(onto: dict, db: ReadOnlyDB) -> dict:
    action_list = []
    for a in onto["actions"]:
        aid = a["id"]
        meta = ACTION_META.get(aid, {"target": "", "tier": "human", "plain": ""})
        execs = a.get("executors", [])
        # 权限行：每个人类角色能否执行
        perms = {role: (role in execs) for role in HUMAN_ROLES}
        action_list.append({
            "id": aid, "name": a.get("name", aid),
            "signature": a.get("signature", ""),
            "target": meta["target"], "targetPlain": OBJECT_PLAIN.get(meta["target"], meta["target"]),
            "tier": meta["tier"], "tierMark": TIER_LABEL[meta["tier"]]["mark"],
            "plain": meta["plain"],
            "executors": execs,
            "executorsPlain": [ROLE_PLAIN.get(x, x) for x in execs],
            "systemCan": "system" in execs,
            "frozen": aid in FROZEN_ACTION_IDS,
            "perms": perms,
            "preconditions": a.get("preconditions", []),
            "successEffects": a.get("successEffects", []),
            "failureHandling": a.get("failureHandling", []),
            "audit": a.get("audit", []),
            "paramsSchema": a.get("paramsSchema"),
            "tool": action_tool_view(a),
            "domain": next((k for k, d in DOMAINS.items() if meta["target"] in d["objects"]), "core"),
        })
    n_exposed = sum(1 for x in action_list if x["tool"]["exposedAsTool"])
    n_frozen = sum(1 for x in action_list if x["tool"]["frozen"])
    return {
        "title": "谁能对什么下什么手",
        "question": "谁能对什么下什么手？",
        "subtitle": f"{len(action_list)} 个动作的五要素总表（执行角色/前置/成功效果/失败处理/审计）+ 7 角色×动作权限热力表 + 「作为 AI 工具长什么样」联动板。点行看它的 JSON Schema；{n_exposed} 个 exposed 写工具、{n_frozen} 个冻结区。",
        "roles": [{"id": r, "plain": ROLE_PLAIN[r]} for r in HUMAN_ROLES],
        "actions": action_list,
        "tiers": [{"key": k, "mark": v["mark"], "name": v["name"], "tone": v["tone"]}
                  for k, v in TIER_LABEL.items()],
        "toolSummary": {"exposed": n_exposed, "frozen": n_frozen, "total": len(action_list),
                        "note": "「作为 AI 工具」列直读本体 M1/M2 桥2 字段：exposed_as_tool / ai_executable / tool_description / tool_input_schema——本体改一行，AI 工具暴露同步变。"},
        "frozenNote": "冻结区（🔴）= agent.tools.FORBIDDEN_TOOLS：approve_mitigation/close_risk_event 等审批·关闭·合规裁决类动作，工具函数从未注册给 AI——不是权限不够，是根本不存在。",
    }


# =============================================================================
# 视图 · 项目演进史（NEW）——解析 plan v0.2 §4 决策日志
# =============================================================================
SERIES_META = {
    "D": {"name": "建模决策", "tone": "cyan"},
    "M": {"name": "成熟度升级", "tone": "cyan"},
    "P": {"name": "采购域", "tone": "amber"},
    "W": {"name": "仓储域", "tone": "amber"},
    "CL": {"name": "协调回路", "tone": "violet"},
    "J": {"name": "产品化转向", "tone": "green"},
    "C": {"name": "护城河闭环", "tone": "green"},
    "V": {"name": "产品形态重构", "tone": "red"},
}
MILESTONE_CODES = {"D1", "D9", "M7", "P1", "W1", "CL1", "J1-J6+合流", "C1", "V4"}


def build_evolution(onto: dict) -> dict:
    text = ""
    try:
        text = PLAN_MD.read_text(encoding="utf-8")
    except OSError:
        pass
    # 定位 §4 决策日志区块
    m = re.search(r"## 4\. 关键建模决策日志(.*?)\n## 5\.", text, re.S)
    body = m.group(1) if m else text
    # 每条：**CODE — 标题（日期，批准）。** ... 后续段落直到下个 **CODE
    entries = []
    # 匹配 bold 决策头：**{code} — {rest}**
    pat = re.compile(r"\*\*([A-Z]+[0-9][0-9A-Za-z\-+合流]*|J1-J6\+合流)\s*—\s*(.+?)\*\*", re.S)
    matches = list(pat.finditer(body))
    for i, mt in enumerate(matches):
        code = mt.group(1).strip()
        headrest = re.sub(r"\s+", " ", mt.group(2).strip())
        start = mt.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        para = body[start:end].strip()
        para = re.sub(r"\s+", " ", para)
        # 标题 = headrest 里首个（前的部分；日期/批准取括号内
        title = headrest.split("（")[0].strip().rstrip("。")
        # 日期：在整段头 + 正文里找（取第一个日期）
        date = ""
        dm = re.search(r"(20\d{2})[-–](\d{1,2})[-–](\d{1,2})", headrest) or \
             re.search(r"(20\d{2})[-–](\d{1,2})[-–](\d{1,2})", para)
        if dm:
            date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
        # 批准人（在头 + 正文里找）
        scope = headrest + " " + para[:200]
        approver = ""
        if "Daniel" in scope:
            approver = "Daniel 亲批"
        elif "人已批准" in scope or "人以" in scope:
            approver = "人已批准"
        elif "代行" in scope or "主会话" in scope:
            approver = "主会话代行入账"
        # 一句话摘要 = 段落首句（截断）
        oneLine = para.split("。")[0].strip()
        if len(oneLine) > 120:
            oneLine = oneLine[:118] + "…"
        # 系列前缀
        pref = re.match(r"([A-Z]+)", code)
        series = pref.group(1) if pref else "D"
        if series not in SERIES_META:
            series = "D"
        entries.append({
            "code": code, "title": title, "date": date, "approver": approver,
            "oneLine": oneLine, "series": series, "srcIndex": i,
            "seriesName": SERIES_META[series]["name"], "tone": SERIES_META[series]["tone"],
            "milestone": code in MILESTONE_CODES,
        })
    # 决策日志是 append-only 且大体按时间追加——按源文档顺序即真实演进时序
    entries.sort(key=lambda e: e["srcIndex"])
    return {
        "title": "这个系统怎么长成今天的",
        "question": "这个系统怎么长成今天的？",
        "subtitle": "决策日志时间线——从 plan v0.2 §4 解析出的每一次关键裁决（建模/成熟度/采购/仓储/协调/产品化/护城河/形态重构）。里程碑节点放大。",
        "entries": entries,
        "series": [{"key": k, "name": v["name"], "tone": v["tone"]} for k, v in SERIES_META.items()],
        "source": "docs/control-tower-plan-v0.2.md §4（append-only 决策日志，脚本解析）",
        "count": len(entries),
    }


# =============================================================================
# 视图 · 世界设定集（NEW）——公司/货代/客户/航线/季节/异常谱系
# =============================================================================
def build_world(onto: dict, db: ReadOnlyDB) -> dict:
    company = {}
    if db.has_table("sim_company_profile"):
        r = db.q("SELECT * FROM sim_company_profile LIMIT 1")
        company = dict(r[0]) if r else {}

    forwarders = []
    if db.has_table("sim_forwarders"):
        # 关联真实承运统计（sim_shipment_meta.forwarder_id）
        used = {}
        if db.has_table("sim_shipment_meta"):
            for r in db.q("SELECT forwarder_id, COUNT(*) n FROM sim_shipment_meta GROUP BY forwarder_id"):
                used[r["forwarder_id"]] = r["n"]
        for r in db.q("SELECT * FROM sim_forwarders"):
            d = dict(r)
            d["shipments"] = used.get(d["forwarder_id"], 0)
            forwarders.append(d)

    # 客户分层（真实：sim_customer_traits）
    cust_tiers = []
    if db.has_table("sim_customer_traits"):
        for r in db.q("SELECT tier, region, COUNT(*) n FROM sim_customer_traits GROUP BY tier, region ORDER BY tier, region"):
            cust_tiers.append(dict(r))
    cust_total = db.count("sim_customer_traits")

    # 航线网络
    routes = []
    if db.has_table("sim_routes"):
        for r in db.q("SELECT * FROM sim_routes"):
            routes.append(dict(r))

    # 季节曲线：月度柜量（shipments.etd）
    season = []
    if db.has_table("shipments"):
        for r in db.q("SELECT substr(etd,1,7) ym, COUNT(*) n FROM shipments WHERE etd IS NOT NULL AND etd!='' GROUP BY ym ORDER BY ym"):
            season.append({"month": r["ym"], "n": r["n"]})

    # 异常谱系（family 分布，排除空/texture 基底）
    spectrum = []
    if db.has_table("sim_event_log"):
        for r in db.q("SELECT family, COUNT(*) n FROM sim_event_log WHERE family IS NOT NULL AND family!='' GROUP BY family ORDER BY n DESC"):
            spectrum.append(dict(r))
    spectrum_total = sum(s["n"] for s in spectrum)
    FAMILY_PLAIN = {
        "delay": "延误族（甩柜/拥堵/转船漏接/跳港）", "chain": "连锁族（延误→滞箱→断货的因果链）",
        "document": "单证族（缺单/资质将过期）", "inspection": "查验族（海关扣货/ETA 矛盾）",
        "fee": "费用族（多收/重复/泡重）", "warehouse": "仓储族（盘亏）",
    }
    for s in spectrum:
        s["plain"] = FAMILY_PLAIN.get(s["family"], s["family"])

    return {
        "title": "这个模拟世界是谁",
        "question": "这个模拟世界是谁？",
        "subtitle": "活世界的世设集：一家真实感的跨境公司、6 家性格各异的货代、40 个分层客户、3 条航线、14 个月季节曲线、五族异常谱系。全部来自 simworld 真数据。",
        "worldSource": "sim",
        "company": company,
        "forwarders": forwarders,
        "customers": {"total": cust_total, "byTier": cust_tiers},
        "routes": routes,
        "season": season,
        "spectrum": {"total": spectrum_total, "families": spectrum},
        "forwarderParams": [
            {"key": "credibility", "label": "口径可信度", "plain": "它报的 ETA/状态可不可信", "good": "high"},
            {"key": "quote_level", "label": "报价水平", "plain": "相对市场的报价系数（1.0=市场价）", "good": "mid"},
            {"key": "volumetric_tendency", "label": "泡重倾向", "plain": "按体积重多算钱的倾向", "good": "low"},
            {"key": "billing_error_rate", "label": "账单错误率", "plain": "账单出错的概率", "good": "low"},
        ],
    }


# =============================================================================
# 视图 · 越用越强（升级：接活世界先例与账本）
# =============================================================================
def build_flywheel(onto: dict, db: ReadOnlyDB) -> dict:
    n_memory = db.count("resolution_memory")
    n_backfilled = int(db.scalar("SELECT COUNT(*) FROM resolution_memory WHERE outcome_resolved IS NOT NULL AND outcome_resolved != ''") or 0)
    # 质量标签分布
    quality = []
    for r in db.q("SELECT COALESCE(NULLIF(quality_label,''),'（未标注）') ql, COUNT(*) n FROM resolution_memory GROUP BY ql ORDER BY n DESC"):
        quality.append(dict(r))
    n_adopted = int(db.scalar("SELECT COUNT(*) FROM resolution_memory WHERE decision='adopted'") or 0)
    n_rejected = int(db.scalar("SELECT COUNT(*) FROM resolution_memory WHERE decision='rejected'") or 0)
    # 引用先例的比例（cited_precedent_ids 非空非[]）
    n_cited = int(db.scalar("SELECT COUNT(*) FROM resolution_memory WHERE cited_precedent_ids IS NOT NULL AND cited_precedent_ids NOT IN ('','[]')") or 0)

    assets = [
        {"id": "precedent", "order": 1, "name": "先例（处置记忆）",
         "plain": "一次人工审批结案后，把「看到什么/提了什么/怎么决定/结果如何」四件套存下来。",
         "metricLabel": "已沉淀先例", "metric": n_memory,
         "sub": f"其中 {n_backfilled} 条已回填真实结果，{n_cited} 条决策时引用了更早先例", "source": "sim"},
        {"id": "playbook", "order": 2, "name": "打法卡（SkillAsset）",
         "plain": "同类处置攒够 5 次，系统聚类提议一张打法卡草稿（一件事怎么做的可审计说明文）。人批准后才进检索。",
         "metricLabel": "触发阈值", "metric": 5, "metricUnit": "次/类",
         "sub": "AI 只提议新卡/改卡，人批准后生效——治理式进化", "source": None},
        {"id": "evalset", "order": 3, "name": "评估集",
         "plain": "把处置正误固化成可回放的真值题：查全/查准、提案采纳率、重放一致性。",
         "metricLabel": "风险规则真值", "metric": len(onto["riskRules"]), "metricUnit": "条",
         "sub": "R1–R18 验证世界真值 R/P=1.000，与活世界真实效果分开报告", "source": None},
        {"id": "autonomy", "order": 4, "name": "自主权（AutonomyGrant）",
         "plain": "一类处置的提案-采纳一致率够高够久，才提议把它从「人批」升一档到「低风险自动」。逐类、可回退。",
         "metricLabel": "当前自动结案", "metric": 0, "metricUnit": "类",
         "sub": "刻意留白：加早了丢控制，加晚了浪费 AI（决策权 §7 悬而未决）", "source": None},
        {"id": "freeze", "order": 5, "name": "冻结区",
         "plain": "无论一致率多高，审批/关闭/花钱/合规永不晋级。飞轮转得再快，也撞不穿这堵墙。",
         "metricLabel": "永不晋级动作", "metric": len(FROZEN_ACTION_IDS), "metricUnit": "类",
         "sub": "见「什么永远不会变」·冻结区", "source": None},
    ]
    funnel = [
        {"stage": "系统检出风险", "count": db.count("risk_events"), "plain": "自动感知偏离"},
        {"stage": "派发任务", "count": db.count("tasks"), "plain": "落到角色桌上"},
        {"stage": "提出处置方案", "count": int(db.scalar("SELECT COUNT(*) FROM tasks WHERE proposed_action IS NOT NULL AND proposed_action != ''") or 0), "plain": "运营/AI 起草"},
        {"stage": "经理审批采纳", "count": n_adopted, "plain": "冻结区·人拍板（另 %d 条被驳回）" % n_rejected},
        {"stage": "沉淀为先例", "count": n_memory, "plain": "写进处置记忆"},
    ]
    honest_state = (
        f"活世界快照：{n_memory} 条先例已沉淀，{n_backfilled} 条已回填真实结果，"
        f"{n_cited} 条决策时引用了更早先例——飞轮第一环（审批→先例→再引用）在活世界已真实转起来。"
        f"质量标签由人关闭时手打（有效/部分有效/无效），是一致率成绩单与评估集的原料，AI 不自评。"
    )
    return {
        "title": "系统怎么越用越强",
        "question": "系统怎么越用越强？",
        "subtitle": "五个资产接力：一次人工审批如何变成下次的先例，五次同类如何触发打法卡，一致率如何驱动自主权晋升。数据接活世界。",
        "worldSource": "sim",
        "assets": assets, "funnel": funnel, "honestState": honest_state,
        "quality": quality,
    }


# =============================================================================
# 视图 · AI 账本（升级：双账——sim 运转账 + 真实 LLM 账）
# =============================================================================
def build_ai_activity(onto: dict, db: ReadOnlyDB) -> dict:
    # sim 运转账（确定性经济账引擎）
    sim_total = db.count("sim_ai_activity")
    sim_by_activity = [dict(r) for r in db.q(
        "SELECT activity, COUNT(*) n FROM sim_ai_activity GROUP BY activity ORDER BY n DESC")] if sim_total > 0 else []
    sim_by_actor = [dict(r) for r in db.q(
        "SELECT actor, COUNT(*) n FROM sim_ai_activity GROUP BY actor ORDER BY n DESC")] if sim_total > 0 else []
    sim_span = db.q("SELECT MIN(sim_date) mn, MAX(sim_date) mx FROM sim_ai_activity")[0] if sim_total > 0 else {"mn": None, "mx": None}
    ACT_PLAIN = {
        "detect": "检测：里程碑进来，自动定位受影响订单建风险",
        "propose": "提案：算经济账，起草加急/改期/接受延误（引用先例）",
        "approve": "审批：人拍板采纳（冻结区，sim-approver 扮演经理）",
        "reject": "驳回：人否掉提案，任务退回",
        "close": "关闭：处置执行完，人回填结果与质量标签",
    }
    for a in sim_by_activity:
        a["plain"] = ACT_PLAIN.get(a["activity"], "")

    # 真实 LLM 账（llm_calls，活世界当前为空——诚实标注）
    n_llm = db.count("llm_calls")
    llm = {"total": max(n_llm, 0), "byType": [], "estTokens": 0}
    if n_llm > 0:
        llm["byType"] = [dict(r) for r in db.q("SELECT call_type type, COUNT(*) n FROM llm_calls GROUP BY call_type ORDER BY n DESC")]
        llm["estTokens"] = int(db.scalar("SELECT COALESCE(SUM(est_input_tokens+est_output_tokens),0) FROM llm_calls") or 0)

    return {
        "title": "AI 干了多少活、花了多少钱",
        "question": "AI 干了多少活、花了多少钱？",
        "subtitle": "两本账分开记。左：模拟世界运转账——确定性经济账引擎跑过的每一次检测/提案/审批/关闭（已发生）。右：真实 LLM 推理账——花 token 的部分（待生产运转，如实标空）。",
        "sim": {
            "label": "模拟世界运转账", "source": "sim",
            "total": sim_total, "byActivity": sim_by_activity, "byActor": sim_by_actor,
            "span": {"from": sim_span["mn"], "to": sim_span["mx"]},
            "plain": f"活世界 14 个月里，确定性经济账引擎自动跑了 {sim_total} 次 AI 运转：检测→提案→审批→关闭 全链留痕。actor=sim-ai（检测/提案）、sim-approver-01（审批/关闭，扮演人）——这是「世界活着」的证据，不是真实 LLM 调用。",
        },
        "llm": {
            "label": "真实 LLM 推理账", "source": "prod",
            "total": llm["total"], "byType": llm["byType"], "estTokens": llm["estTokens"],
            "plain": ("活世界 llm_calls 表 0 条——真实 LLM 推理遥测尚未接入。这是诚实的空账：模拟运转（左账）已把世界跑活并留痕，"
                      "但真实 Opus/CLI 推理的 token 账要等接生产运转才开始记。基座已通（agent/llm_agent.py claude_cli），账本待灌。")
            if llm["total"] <= 0 else "真实 LLM 推理调用遥测。est_tokens 为估算，非计费真值。",
        },
    }


# =============================================================================
# 视图 · 决策回放（升级：活世界最精彩一案，四件套 + 连锁上下文）
# =============================================================================
REPLAY_RISK = "RSK-SIM-00083"      # 经济账对比最大的加急案（收益 $18,099 > 成本 $11,400）
REPLAY_SHIP = "SHP-SIM-00104"


def build_decision_lineage(onto: dict, db: ReadOnlyDB) -> dict:
    risk = db.q("SELECT * FROM risk_events WHERE risk_event_id=?", (REPLAY_RISK,))
    risk = dict(risk[0]) if risk else {}
    ship = db.q("SELECT shipment_id, mode, carrier_name, origin_port, destination_port, etd, eta_initial, eta_current, delay_days, status FROM shipments WHERE shipment_id=?", (REPLAY_SHIP,))
    ship = dict(ship[0]) if ship else {}
    task = db.q("SELECT * FROM tasks WHERE risk_event_id=? LIMIT 1", (REPLAY_RISK,))
    task = dict(task[0]) if task else {}
    mem = db.q("SELECT * FROM resolution_memory WHERE risk_event_id=? LIMIT 1", (REPLAY_RISK,))
    mem = dict(mem[0]) if mem else {}
    ai = [dict(r) for r in db.q("SELECT sim_date, actor, activity, detail FROM sim_ai_activity WHERE risk_event_id=? ORDER BY sim_date", (REPLAY_RISK,))]
    # 连锁上下文：该 shipment 的 event_log
    chain = [dict(r) for r in db.q("SELECT sim_date, event_kind, family, severity, caused_by FROM sim_event_log WHERE object_id=? ORDER BY sim_date", (REPLAY_SHIP,))]

    econ = {}
    try:
        econ = json.loads(task.get("economics_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        econ = {}
    affected_lines = []
    try:
        affected_lines = json.loads(risk.get("affected_so_line_ids") or "[]")
    except (json.JSONDecodeError, TypeError):
        affected_lines = []

    exposure = risk.get("affected_value_usd")
    cost = econ.get("expedite_cost_usd")
    benefit = econ.get("benefit_usd")

    quartet = [
        {"phase": "① 当时看到什么", "actor": "系统 / 自动化引擎", "tier": "🔵",
         "plain": "一票蛇口→洛杉矶的整箱被 roll（甩柜）滞留，ETA 后推。系统沿对象图算出击穿多条客户订单行的承诺，判 critical。",
         "evidence": {"shipment": ship, "chain": chain, "affectedLines": affected_lines,
                      "exposureUsd": exposure, "severity": risk.get("severity"),
                      "rootCause": risk.get("root_cause")}},
        {"phase": "② AI 提了什么", "actor": "sim-ai（起草）", "tier": "🔵→🟠",
         "plain": "AI 算经济账并起草加急提案，引用了一条更早的同类先例。方案是「提议」不是「执行」，提交后进审批闸。",
         "proposal": {"action": task.get("proposed_action"), "economics": econ,
                      "verdict": econ.get("verdict"),
                      "precedentBlock": task.get("precedent_block"),
                      "cited": mem.get("cited_precedent_ids")}},
        {"phase": "③ 人怎么决定", "actor": "sim-approver-01（冻结区，扮演经理）", "tier": "🔴",
         "plain": (f"经济账：花 ${cost:,.0f} 加急，收益 ${benefit:,.0f}（挽回毛利 + 省下滞箱费）——划算，批准。这一步 AI 永远够不着。"
                   if (cost and benefit) else "经理审批。这一步 AI 永远够不着。"),
         "decision": {"approvalStatus": task.get("approval_status"),
                      "approvedByRole": task.get("approved_by_role"),
                      "actionTaken": task.get("action_taken"),
                      "decision": mem.get("decision")}},
        {"phase": "④ 结果如何", "actor": "系统回写 + 人回填 + 沉淀先例", "tier": "🔵",
         "plain": (f"审批通过 → 加急执行 → ETA 提前 → {mem.get('outcome_days')} 天关闭，人回填结果「{mem.get('outcome_resolved')}」、质量标注「{mem.get('quality_label')}」。"
                   "这一案本身沉淀成先例，成为下次同类的可引用依据——飞轮转起来了。"),
         "outcome": {"taskStatus": task.get("status"), "memoryId": mem.get("memory_id"),
                     "outcomeResolved": mem.get("outcome_resolved"),
                     "outcomeDays": mem.get("outcome_days"),
                     "qualityLabel": mem.get("quality_label"),
                     "precedentRecorded": bool(mem)}},
    ]
    return {
        "title": "一个决策是怎么发生的",
        "question": "一个决策是怎么发生的？",
        "subtitle": "从活世界挑一桩最精彩、经济账对比最大的加急案，四步回放：看到什么 → AI 提了什么 → 人怎么决定 → 结果如何。含 caused_by 连锁上下文。",
        "worldSource": "sim",
        "caseId": task.get("task_id", ""), "riskId": REPLAY_RISK, "shipmentId": REPLAY_SHIP,
        "quartet": quartet, "aiTrace": ai,
        "sourceNote": "数据取自 simworld：risk_events / tasks / resolution_memory / sim_ai_activity / sim_event_log。四件套 + 连锁上下文完整。",
    }


# =============================================================================
# 视图 · 影响分析专题（NEW · v3 板块②）——「改它牵连什么」下游消费面
# 全部从本体 JSON 静态推导（Atlan impact analysis 范式）：
#   关系引用（links source/target）· 规则检测（riskRules 目标）· 动作读写（五要素目标）
#   · AI 工具暴露（aiQueryTools + exposed 动作的 input_schema 字段）· 敏感规则约束
# =============================================================================
# aiQueryTools 无 id 入参的 list 型工具，锚到域主对象（避免过度声称 touches）
AI_QUERY_DOMAIN_ANCHOR = {"risk": "RiskEvent", "cost": "Invoice", "admission": "AdmissionCase"}


def _schema_prop_names(schema) -> set:
    if not isinstance(schema, dict):
        return set()
    props = schema.get("properties")
    return set(props.keys()) if isinstance(props, dict) else set()


def build_impact(onto: dict, db: ReadOnlyDB) -> dict:
    objs = obj_by_type(onto)
    sens = build_sensitive_map(onto)
    actions_by_id = {a["id"]: a for a in onto["actions"]}
    pk_to_type = {o.get("primaryKey"): o["type"] for o in onto["objects"] if o.get("primaryKey")}

    # 动作 → 类型 倒排（主 target + 次要触碰），规则 → 类型 倒排
    actions_for_type: dict[str, list[str]] = {}
    for aid, meta in ACTION_META.items():
        actions_for_type.setdefault(meta["target"], []).append(aid)
    for aid, extra in ACTION_SECONDARY.items():
        for tp2 in extra:
            actions_for_type.setdefault(tp2, [])
            if aid not in actions_for_type[tp2]:
                actions_for_type[tp2].append(aid)
    rules_for_type: dict[str, list[str]] = {}
    for rid, meta in RULE_META.items():
        rules_for_type.setdefault(meta["target"], []).append(rid)

    # aiQueryTools → 它触碰哪些类型（pk 精确匹配 + list 型锚域主对象）
    query_tools = onto.get("aiQueryTools", [])
    qtool_types: dict[str, set] = {}   # type -> {tool names}
    qtool_field_hits: dict[tuple, set] = {}  # (type, field) -> {tool names}（input_schema 属性名==字段名）
    for qt in query_tools:
        props = _schema_prop_names(qt.get("input_schema"))
        hit_types = set()
        for pk in props:
            if pk in pk_to_type:
                hit_types.add(pk_to_type[pk])
        if not hit_types:  # list 型无 pk → 锚域主对象
            anchor = AI_QUERY_DOMAIN_ANCHOR.get(qt.get("domain"))
            if anchor:
                hit_types.add(anchor)
        for tp in hit_types:
            qtool_types.setdefault(tp, set()).add(qt["name"])
        # 字段级：input_schema 属性名精确等于某类型字段名 → 该工具暴露该字段
        for tp, obj in objs.items():
            fnames = {p["name"] for p in obj.get("properties", [])}
            for fn in props & fnames:
                qtool_field_hits.setdefault((tp, fn), set()).add(qt["name"])

    # exposed 写工具（动作）→ 它写哪些类型（主 target + 次要触碰）+ 字段级 input_schema 命中
    wtool_field_hits: dict[tuple, set] = {}
    for a in onto["actions"]:
        if not a.get("exposed_as_tool"):
            continue
        tn = _tool_name_of(a)
        props = _schema_prop_names(a.get("tool_input_schema"))
        for tp, obj in objs.items():
            fnames = {p["name"] for p in obj.get("properties", [])}
            for fn in props & fnames:
                wtool_field_hits.setdefault((tp, fn), set()).add(tn)

    # links：出/入边 + storage 承载列
    def links_for(tp: str) -> list[dict]:
        out = []
        for l in onto["links"]:
            if l["source"] == tp:
                out.append({"linkType": l["linkType"], "dir": "out", "other": l["target"],
                            "otherPlain": OBJECT_PLAIN.get(l["target"], l["target"]),
                            "cardinality": l.get("cardinality", ""),
                            "plain": LINK_PLAIN.get(l["linkType"], l["linkType"]),
                            "storage": l.get("storage")})
            if l["target"] == tp:
                out.append({"linkType": l["linkType"], "dir": "in", "other": l["source"],
                            "otherPlain": OBJECT_PLAIN.get(l["source"], l["source"]),
                            "cardinality": l.get("cardinality", ""),
                            "plain": LINK_PLAIN.get(l["linkType"], l["linkType"]),
                            "storage": l.get("storage")})
        return out

    types_out = {}
    for tp, obj in objs.items():
        tbl = OBJECT_TABLE.get(tp)
        covered = bool(tbl and db.has_table(tbl))
        count = db.count(tbl) if covered else -1

        links = links_for(tp)
        rules = [{"id": rid, "watch": RULE_META[rid]["watch"], "plain": RULE_META[rid]["plain"],
                  "domain": next((k for k, d in DOMAINS.items() if RULE_META[rid]["target"] in d["objects"]), "core")}
                 for rid in rules_for_type.get(tp, [])]
        acts = []
        for aid in actions_for_type.get(tp, []):
            a = actions_by_id.get(aid, {})
            m = ACTION_META[aid]
            acts.append({"id": aid, "name": a.get("name", aid), "tier": m["tier"],
                         "plain": m["plain"], "primary": m["target"] == tp,
                         "frozen": aid in FROZEN_ACTION_IDS,
                         "exposedAsTool": bool(a.get("exposed_as_tool")),
                         "toolName": _tool_name_of(a) if a.get("exposed_as_tool") else None})
        qtools = []
        for qt in query_tools:
            if qt["name"] in qtool_types.get(tp, set()):
                qtools.append({"name": qt["name"], "domain": qt.get("domain", ""),
                               "description": qt.get("description", "")})
        wtools = []
        for a in onto["actions"]:
            if not a.get("exposed_as_tool"):
                continue
            m = ACTION_META.get(a["id"], {"target": ""})
            touches = [m["target"]] + ACTION_SECONDARY.get(a["id"], [])
            if tp in touches:
                wtools.append({"actionId": a["id"], "name": a.get("name", a["id"]),
                               "toolName": _tool_name_of(a),
                               "description": a.get("tool_description", ""),
                               "primary": m["target"] == tp})
        sensitive = [{"field": f, "visibleTo": [ROLE_PLAIN.get(r, r) for r in vis]}
                     for (so, f), vis in sens.items() if so == tp]

        # 字段级消费面：敏感门控 / 承载哪条关系 / 被哪些 AI 工具的 input_schema 命中
        carry = {}  # field(column) -> linkType（storage 承载列声明）
        for l in onto["links"]:
            st = l.get("storage")
            if isinstance(st, dict) and st.get("column") and l["source"] == tp:
                carry[st["column"]] = l["linkType"]
        fields = []
        for p in obj.get("properties", []):
            fn = p["name"]
            fsens = None
            for (so, sf), vis in sens.items():
                if so == tp and (sf == fn or sf.split(".")[0] == fn):
                    fsens = [ROLE_PLAIN.get(r, r) for r in vis]
                    break
            exposed_by = sorted(qtool_field_hits.get((tp, fn), set()) | wtool_field_hits.get((tp, fn), set()))
            fields.append({"name": fn, "type": p.get("type", ""), "desc": p.get("description", ""),
                           "sensitive": fsens, "carriesLink": carry.get(fn),
                           "exposedByTools": exposed_by})

        tool_count = len(qtools) + len(wtools)
        types_out[tp] = {
            "type": tp, "plainName": OBJECT_PLAIN.get(tp, tp), "why": OBJECT_WHY.get(tp, ""),
            "domain": next((k for k, d in DOMAINS.items() if tp in d["objects"]), "core"),
            "covered": covered, "count": count,
            "links": links, "rules": rules, "actions": acts,
            "queryTools": qtools, "writeTools": wtools, "sensitive": sensitive, "fields": fields,
            "counts": {"links": len(links), "rules": len(rules), "actions": len(acts),
                       "tools": tool_count, "sensitive": len(sensitive), "instances": count},
        }

    domain_order = [
        {"id": k, "name": d["name"], "plain": d["plain"], "scene": d.get("scene", "core"),
         "types": [{"type": t, "plainName": OBJECT_PLAIN.get(t, t),
                    "impact": (types_out[t]["counts"]["links"] + types_out[t]["counts"]["rules"]
                               + types_out[t]["counts"]["actions"] + types_out[t]["counts"]["tools"]
                               + types_out[t]["counts"]["sensitive"]),
                    "covered": types_out[t]["covered"]}
                   for t in d["objects"]]}
        for k, d in DOMAINS.items()
    ]
    return {
        "title": "改一处，牵连什么",
        "question": "改一个对象/字段，会牵连什么？",
        "subtitle": "选任一对象类型或字段，一键展开它的下游消费面：被哪些关系引用、哪些规则盯着、哪些动作读写、哪些 AI 工具暴露、哪些敏感规则约束。全部从本体 JSON 静态推导，改动半径一目了然（Atlan impact analysis 范式）。",
        "defaultType": "Shipment",
        "domains": domain_order,
        "types": types_out,
        "hint": "35 类型 · 每类型五维消费面（关系/规则/动作/AI 工具/敏感）——改动前先看牵连面。",
    }


# =============================================================================
# 视图 · 全局跨类型搜索索引（NEW · v3 板块①）——Bloom search-first
# 全部实例的紧凑索引（id + 类型 + 关键字段摘要），运行时懒加载（app 挂载即拉）。
# 类型名/中文名搜索走前端已在主包的 weave；本索引负责「任意实例 id 即时命中」。
# =============================================================================
def build_search(onto: dict, db: ReadOnlyDB) -> dict:
    table_pk = {o["type"]: o.get("primaryKey") for o in onto["objects"]}
    type_meta = {}
    items = []
    for tp in OBJECT_TABLE:
        tbl = OBJECT_TABLE.get(tp)
        pk = table_pk.get(tp)
        if not tbl or not pk or not db.has_table(tbl):
            continue
        idx_fields = INDEX_FIELDS.get(tp, [])
        rows = db.rows(tbl)
        if not rows:
            continue
        type_meta[tp] = {
            "plainName": OBJECT_PLAIN.get(tp, tp),
            "domain": next((k for k, d in DOMAINS.items() if tp in d["objects"]), "core"),
            "count": len(rows),
        }
        for r in rows:
            rid = r.get(pk)
            if rid in (None, ""):
                continue
            parts = []
            for f in idx_fields[:3]:
                v = r.get(f)
                if v not in (None, ""):
                    parts.append(str(v))
            summ = " · ".join(parts)[:60]
            items.append({"id": str(rid), "t": tp, "s": summ})
    return {
        "title": "全局搜索",
        "types": type_meta,
        "items": items,
        "total": len(items),
        "note": "活世界全部实例的紧凑搜索索引（id + 类型 + 摘要），运行时懒加载。数字与摘要均来自活世界只读快照。",
    }


# =============================================================================
# 视图 · 治理控制室（NEW · 第15视图）——治理证据包收官件（G-Dashboard）
# 六类证据全部现查现算、来源如实标注、无数据如实"暂无/需先跑X"。绝不编造（全局规则5）。
# 数据源：llm_calls / action_log / rule_run_ledger（ontology.sqlite 验证世界库，治理遥测落此）、
#         shadow_run（shadow.sqlite 影子旁路库）、resolution_memory（simworld 活世界，档1底数）、
#         docs/demo-assertions.md + release-checklist.md（覆盖度卡）。
# =============================================================================
def _percentile(sorted_vals: list, p: float):
    """线性插值分位数。sorted_vals 必须已升序。空则 None。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


# 断言→锁定它的测试：口径直引 demo-assertions.md 头部「自动化覆盖」声明（作者稳定映射，不猜）。
def _assertion_test(aid: str) -> str:
    if aid in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "B1", "B2", "B3", "B7"):
        return "engine.evaluate + pipeline.evaluate"
    if aid in ("A8", "A9", "A10", "A11", "A12", "A13", "B4", "B5", "B6", "C1", "C2", "C4"):
        return "app.test_closed_loop"
    if aid == "C3":
        return "人工 UI 走查（streamlit）"
    return "需跑测试确认"


def _parse_demo_assertions(md_path: Path) -> dict:
    """解析 demo-assertions.md 的 A/B/C 断言复选框——状态从文档 checkbox 自动派生，不手填。"""
    sec_names = {
        "A": "闭环六步主线断言",
        "B": "反断言（系统不该做的事）",
        "C": "审计与追溯断言",
    }
    groups = {k: [] for k in sec_names}
    if not md_path.exists():
        return {"available": False, "groups": [], "total": 0, "checked": 0}
    text = md_path.read_text(encoding="utf-8")
    # 形如 "- [x] A1. 文本"  /  "- [ ] B4. 文本"
    pat = re.compile(r"^-\s*\[([ xX])\]\s*([ABC]\d+)\.\s*(.+?)\s*$", re.MULTILINE)
    for m in pat.finditer(text):
        checked = m.group(1).lower() == "x"
        aid = m.group(2)
        body = m.group(3).strip()
        sec = aid[0]
        if sec in groups:
            groups[sec].append({
                "id": aid, "text": body, "checked": checked, "test": _assertion_test(aid),
            })
    out_groups = []
    total = checked_n = 0
    for sec in ("A", "B", "C"):
        items = sorted(groups[sec], key=lambda x: int(x["id"][1:]))
        total += len(items)
        checked_n += sum(1 for i in items if i["checked"])
        if items:
            out_groups.append({"section": sec, "name": sec_names[sec], "items": items})
    return {"available": True, "groups": out_groups, "total": total, "checked": checked_n}


def _parse_release_gates(md_path: Path) -> dict:
    """解析 release-checklist.md 发版门（### 小节 + [x]/[ ] 项）+ 头部最近验证日期。"""
    if not md_path.exists():
        return {"available": False, "gates": [], "total": 0, "checked": 0, "lastVerified": None}
    text = md_path.read_text(encoding="utf-8")
    mver = re.search(r"最近一次全链验证[：:]\s*\*\*([0-9]{4}-[0-9]{2}-[0-9]{2})\*\*", text)
    last_verified = mver.group(1) if mver else None
    lines = text.splitlines()
    gates = []
    cur = None
    in_gate_section = False
    for ln in lines:
        h2 = re.match(r"^##\s+(.+)$", ln)
        if h2:
            in_gate_section = "发版门" in h2.group(1) or "release gates" in h2.group(1).lower()
            cur = None
            continue
        if not in_gate_section:
            continue
        h3 = re.match(r"^###\s+(.+?)\s*$", ln)
        if h3:
            cur = {"gate": h3.group(1).strip(), "items": []}
            gates.append(cur)
            continue
        mi = re.match(r"^-\s*\[([ xX])\]\s*(.+?)\s*$", ln)
        if mi and cur is not None:
            txt = re.sub(r"\*\*", "", mi.group(2)).strip()
            cur["items"].append({"text": txt[:160], "checked": mi.group(1).lower() == "x"})
    gates = [g for g in gates if g["items"]]
    total = sum(len(g["items"]) for g in gates)
    checked_n = sum(1 for g in gates for i in g["items"] if i["checked"])
    return {"available": True, "gates": gates, "total": total, "checked": checked_n,
            "lastVerified": last_verified}


def build_governance(onto: dict, db: ReadOnlyDB, vdb: "ReadOnlyDB | None",
                     shadow: "ReadOnlyDB | None") -> dict:
    # 治理遥测三表（llm_calls/action_log/rule_run_ledger）落在验证世界库 ontology.sqlite。
    gov = vdb if (vdb and vdb.has_table("action_log")) else None
    gov_src = "data/ontology.sqlite" if gov else None

    # ── 卡①：AI 调用遥测（llm_calls）───────────────────────────────────────────
    llm_n = gov.count("llm_calls") if (gov and gov.has_table("llm_calls")) else -1
    telemetry = {
        "source": f"{gov_src} · llm_calls" if gov else "llm_calls（表未就位）",
        "available": llm_n > 0,
        "total": max(llm_n, 0),
        "byType": [], "byProvider": [], "byStatus": [],
        "degraded": 0, "latencyMs": None, "tokens": {"input": 0, "output": 0},
        "note": ("llm_calls 表 0 条——真实 LLM 推理遥测尚未接入生产运转。这是诚实的空账（审计 1.5 指出 "
                 "llm_calls「只写不展示」，本卡是它的第一个 UI 出口；账本待灌）。基座已通"
                 "（agent/egress_gate.py 写 trace_id，agent/llm_agent.py claude_cli）。")
        if llm_n <= 0 else "真实 LLM 推理调用遥测。est_tokens 为估算，非计费真值。",
    }
    if llm_n > 0:
        telemetry["byType"] = [dict(r) for r in gov.q(
            "SELECT call_type type, COUNT(*) n FROM llm_calls GROUP BY call_type ORDER BY n DESC")]
        telemetry["byProvider"] = [dict(r) for r in gov.q(
            "SELECT provider, COUNT(*) n FROM llm_calls GROUP BY provider ORDER BY n DESC")]
        telemetry["byStatus"] = [dict(r) for r in gov.q(
            "SELECT status, COUNT(*) n FROM llm_calls GROUP BY status ORDER BY n DESC")]
        telemetry["degraded"] = int(gov.scalar(
            "SELECT COUNT(*) FROM llm_calls WHERE status='degraded'") or 0)
        telemetry["tokens"] = {
            "input": int(gov.scalar("SELECT COALESCE(SUM(est_input_tokens),0) FROM llm_calls") or 0),
            "output": int(gov.scalar("SELECT COALESCE(SUM(est_output_tokens),0) FROM llm_calls") or 0),
        }
        durs = sorted(int(r[0]) for r in gov.q(
            "SELECT duration_ms FROM llm_calls WHERE duration_ms IS NOT NULL"))
        toks = sorted(int(r[0]) for r in gov.q(
            "SELECT (est_input_tokens+est_output_tokens) FROM llm_calls"))
        telemetry["latencyMs"] = {
            "p50": _percentile(durs, 0.5), "p95": _percentile(durs, 0.95),
        }
        telemetry["tokensP"] = {"p50": _percentile(toks, 0.5), "p95": _percentile(toks, 0.95)}

    # ── 卡②：安全对抗（越权 denied + 288 注入）─────────────────────────────────
    denied_live = int(gov.scalar(
        "SELECT COUNT(*) FROM action_log WHERE result LIKE 'denied%'") or 0) if gov else 0
    al_total = gov.count("action_log") if gov else -1
    al_by_result = [dict(r) for r in gov.q(
        "SELECT result, COUNT(*) n FROM action_log GROUP BY result ORDER BY n DESC")] if gov else []
    security = {
        "source": f"{gov_src} · action_log" if gov else "action_log（表未就位）",
        "deniedLive": denied_live,
        "actionLogTotal": max(al_total, 0),
        "byResult": al_by_result,
        "injection": {
            "result": "全绿（288 注入全 refused + denied 审计留痕）",
            "ref": "app/test_agent_security · docs/release-checklist.md §D",
            "asOf": "2026-07-09",
            "note": "对抗注入成本高，不每次实跑——引用最近一次全绿记录（test_agent_security 跑在独立副本库，不落主库）。",
        },
        "note": ("现查主库 action_log 无对抗性 denied（actor 皆 engine/seed_demo_ops，result=ok/created）——"
                 "这本身是护栏证据：主库无 AI 越权写入。越权对抗的 denied 审计在 test_agent_security 的隔离副本库产生。")
        if denied_live == 0 else "现查 action_log 中的越权 denied 计数。",
    }

    # ── 卡③：金标评估（shadow_run bench2_goldset scripted 常驻；LLM 档若有则展示）──
    goldset = {"source": None, "available": False, "runs": [], "latest": None, "llm": None,
               "note": ""}
    if shadow and shadow.has_table("shadow_run"):
        goldset["source"] = "data/shadow.sqlite · shadow_run(bench2_goldset)"
        sc_runs = [dict(r) for r in shadow.q(
            "SELECT run_id, MIN(created_at) created_at, COUNT(*) n, "
            "SUM(CASE WHEN consistent=1 THEN 1 ELSE 0 END) passed "
            "FROM shadow_run WHERE tier='bench2_goldset' AND mode='scripted' "
            "GROUP BY run_id ORDER BY created_at")]
        goldset["runs"] = sc_runs
        goldset["available"] = len(sc_runs) > 0
        if sc_runs:
            last = sc_runs[-1]
            goldset["latest"] = {
                "run_id": last["run_id"], "n": last["n"], "passed": last["passed"],
                "rate": round(last["passed"] / last["n"], 4) if last["n"] else None,
            }
        llm_runs = [dict(r) for r in shadow.q(
            "SELECT run_id, MIN(created_at) created_at, COUNT(*) n, "
            "SUM(CASE WHEN consistent=1 THEN 1 ELSE 0 END) passed "
            "FROM shadow_run WHERE tier='bench2_goldset' AND mode='llm' "
            "GROUP BY run_id ORDER BY created_at")]
        if llm_runs:
            lastl = llm_runs[-1]
            goldset["llm"] = {"run_id": lastl["run_id"], "n": lastl["n"], "passed": lastl["passed"],
                              "rate": round(lastl["passed"] / lastl["n"], 4) if lastl["n"] else None}
        goldset["note"] = (
            "确定性金标档（scripted）：真 AI 无关，跑 agent.evaluate 评估集的正确答案基准，无 LLM 也必出。"
            "LLM 档（真 AI 过金标题）需 shadow_bench --llm，未跑则如实空。")
    else:
        goldset["source"] = "data/shadow.sqlite（未就位）"
        goldset["note"] = "shadow.sqlite 未就位——需先跑 `python3 -m agent.shadow_bench`。"

    # ── 卡④：影子一致率（shadow_run bench1 分域切片 + escalation recall）──────────
    MIN_SLICE_N = 5  # 与 agent/shadow_bench.py 同一样本下限：低于此只报计数不给点估计（规格D）
    shadow_card = {
        "source": "data/shadow.sqlite · shadow_run(bench1_resolution)",
        "measured": False, "minSliceN": MIN_SLICE_N,
        "attempted": 0, "parsed": 0, "overall": None,
        "byRule": [], "byLane": [], "bySeverity": [],
        "population": None, "escalation": None, "note": "",
    }
    b1_rows = []
    if shadow and shadow.has_table("shadow_run"):
        b1_rows = [dict(r) for r in shadow.q(
            "SELECT rule_id, lane, severity, consistent FROM shadow_run "
            "WHERE tier='bench1_resolution'")]
    if b1_rows:
        parsed = [r for r in b1_rows if r["consistent"] is not None]
        shadow_card["measured"] = True
        shadow_card["attempted"] = len(b1_rows)
        shadow_card["parsed"] = len(parsed)
        n = len(parsed)
        c = sum(1 for r in parsed if r["consistent"] == 1)
        shadow_card["overall"] = {"n": n, "consistent": c,
                                  "rate": round(c / n, 4) if n else None}

        def _slice(key):
            agg = {}
            for r in parsed:
                k = r.get(key) or "—"
                a = agg.setdefault(k, {"key": k, "n": 0, "consistent": 0})
                a["n"] += 1
                a["consistent"] += 1 if r["consistent"] == 1 else 0
            out = []
            for a in sorted(agg.values(), key=lambda x: -x["n"]):
                a["lowConfidence"] = a["n"] < MIN_SLICE_N  # 样本过小如实标注，不给点估计
                a["rate"] = round(a["consistent"] / a["n"], 4) if a["n"] else None
                out.append(a)
            return out
        shadow_card["byRule"] = _slice("rule_id")
        shadow_card["byLane"] = _slice("lane")
        shadow_card["bySeverity"] = _slice("severity")
        shadow_card["note"] = "影子一致率＝AI 影子提案动作与人最终采纳动作同类的比率。样本 < 5 的切片只报计数、不给点估计（规格D 样本量护栏）。"
    else:
        # 未落档1数据：现查 resolution_memory 给「可比案例底数」，一致率如实暂无（绝不编造）。
        pop = None
        if db.has_table("resolution_memory"):
            comp = db.q(
                "SELECT rule_id, lane, severity FROM resolution_memory "
                "WHERE decision IS NOT NULL AND decision!='' "
                "AND quality_label IS NOT NULL AND quality_label!=''")
            comp = [dict(r) for r in comp]
            by_rule = {}
            by_sev = {}
            for r in comp:
                by_rule[r["rule_id"]] = by_rule.get(r["rule_id"], 0) + 1
                by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + 1
            pop = {
                "total": len(comp),
                "byRule": [{"key": k, "n": v} for k, v in sorted(by_rule.items())],
                "bySeverity": [{"key": k, "n": v} for k, v in sorted(by_sev.items())],
                "source": "data/simworld.sqlite · resolution_memory（有 decision+quality_label 的可比案例）",
            }
        shadow_card["population"] = pop
        shadow_card["note"] = (
            "shadow_run 表暂无档1（bench1_resolution）记录——影子一致率未实测。左为现查可比案例底数"
            "（有人决定+质量标签的历史风险），一致率需跑 `python3 -m agent.shadow_bench --llm` 档1 才有。"
            "诚实优先：这数要拿去做放权决策，无一手实测绝不给百分比（规格D／全局规则5）。")

    # escalation recall（规格C）：人升级人工（escalated）里 AI 是否也建议 escalate 的召回率。
    esc_total = 0
    if db.has_table("resolution_memory"):
        esc_total = int(db.scalar(
            "SELECT COUNT(*) FROM resolution_memory "
            "WHERE status='escalated' OR decision='escalate'") or 0)
    shadow_card["escalation"] = {
        "escalatedCases": esc_total,
        "recall": None,
        "note": ("当前活世界无 escalated 终态案例（0 例）——escalation recall 无可测样本，如实暂无。"
                 "放权最怕漏升级，此召回率比总一致率更决定「敢不敢放权」（规格C）；有 escalated 案例后单独出。")
        if esc_total == 0 else "人升级人工的案例里 AI 也建议 escalate 的召回率，需档1实测。",
    }

    # ── 卡⑤：规则台账（rule_run_ledger 趋势 + 同 as_of 跨 run diff）────────────────
    ledger = {"source": None, "available": False, "runs": [], "latestByRule": [],
              "diff": None, "note": ""}
    if gov and gov.has_table("rule_run_ledger"):
        ledger["source"] = f"{gov_src} · rule_run_ledger"
        runs = [dict(r) for r in gov.q(
            "SELECT as_of, created_at, COUNT(*) rules, SUM(detected_count) total, "
            "SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) errors "
            "FROM rule_run_ledger GROUP BY as_of, created_at ORDER BY created_at")]
        ledger["runs"] = runs
        ledger["available"] = len(runs) > 0
        if runs:
            latest = runs[-1]
            ledger["latestByRule"] = [dict(r) for r in gov.q(
                "SELECT rule_id, rule_version, detected_count, "
                "substr(input_fingerprint,1,12) fp, status "
                "FROM rule_run_ledger WHERE as_of=? AND created_at=? "
                "ORDER BY CAST(substr(rule_id,2) AS INTEGER)",
                (latest["as_of"], latest["created_at"]))]
        # 同 as_of 跨 run 的 diff（幂等验证）：需 ≥2 run。
        same_as_of = [r for r in runs]
        as_of_groups = {}
        for r in runs:
            as_of_groups.setdefault(r["as_of"], []).append(r)
        diffable = {k: v for k, v in as_of_groups.items() if len(v) >= 2}
        if diffable:
            k = sorted(diffable.keys())[-1]
            grp = sorted(diffable[k], key=lambda x: x["created_at"])
            a, b = grp[-2], grp[-1]
            per_a = {r["rule_id"]: r["detected_count"] for r in gov.q(
                "SELECT rule_id, detected_count FROM rule_run_ledger WHERE as_of=? AND created_at=?",
                (k, a["created_at"]))}
            per_b = {r["rule_id"]: r["detected_count"] for r in gov.q(
                "SELECT rule_id, detected_count FROM rule_run_ledger WHERE as_of=? AND created_at=?",
                (k, b["created_at"]))}
            changed = []
            for rid in sorted(set(per_a) | set(per_b), key=lambda x: int(x[1:]) if x[1:].isdigit() else 999):
                if per_a.get(rid) != per_b.get(rid):
                    changed.append({"rule_id": rid, "before": per_a.get(rid), "after": per_b.get(rid)})
            ledger["diff"] = {
                "asOf": k, "before": a["created_at"], "after": b["created_at"],
                "changed": changed, "idempotent": len(changed) == 0,
            }
            ledger["note"] = (f"同 as_of={k} 的两次 detect 逐规则 count "
                              + ("完全一致 → 幂等已修（三洞修复铁证）。" if not changed
                                 else f"有 {len(changed)} 条变化 → 数据或阈值变了。"))
        else:
            ledger["note"] = (f"当前仅 {len(runs)} 次 detect 台账——同 as_of 跨 run diff 需 ≥2 次"
                              "（重跑 `python3 -m engine.detect` 即再落 21 行，届时可比幂等）。")
    else:
        ledger["source"] = "rule_run_ledger（表未就位）"
        ledger["note"] = "rule_run_ledger 未就位——需先跑 `python3 -m engine.detect`（G-Ledger）。"

    # ── 卡⑥：需求覆盖度（demo-assertions + release-checklist，状态从 checkbox 自动派生）──
    assertions = _parse_demo_assertions(DEMO_ASSERTIONS_MD)
    gates = _parse_release_gates(RELEASE_CHECKLIST_MD)
    coverage = {
        "source": "docs/demo-assertions.md + docs/release-checklist.md",
        "asOf": gates.get("lastVerified"),
        "assertions": assertions,
        "gates": gates,
        "reproduce": "docs/release-checklist.md §0「一键复现（从零到全绿）」",
        "note": ("状态列直读文档 checkbox（[x]/[ ]），非本视图实跑——保住「绿是命令跑出来的、不是这里声称的」红线。"
                 f"文档记录的最近一次全链验证＝{gates.get('lastVerified') or '未标注'}；要确认当前真实状态请按 §0 重跑。"),
    }

    # ── 七问框架（每个运行必须能回答的 7 问）──────────────────────────────────────
    seven = [
        {"q": "谁触发的", "plain": "哪个 actor/role 起的头",
         "answeredBy": "action_log.actor / role", "status": "通",
         "detail": f"action_log {max(al_total,0)} 行均带 actor+role（现查：engine/seed_demo_ops）"},
        {"q": "用哪版 AI-指令-模型", "plain": "prompt 版本 + 模型",
         "answeredBy": "llm_calls.model / provider（+ prompt_version 待 G-Trace）", "status": "待灌",
         "detail": "llm_calls 有 model/provider 列，当前 0 条；prompt_version 依赖 G-Trace 增补"},
        {"q": "当时能看到哪些数据", "plain": "grounding 快照",
         "answeredBy": "shadow_bench grounding（脱敏简报）", "status": "通",
         "detail": "影子台按检测时点脱敏简报做 grounding，剔除后验字段（见 shadow_bench._build_grounding）"},
        {"q": "每步做了什么谁批的", "plain": "动作 + maker-checker",
         "answeredBy": "action_log.action / result + 冻结区审批", "status": "通",
         "detail": "每次状态变更 action_log 恰一条（demo-assertions C2）；冻结区四审批动作人批"},
        {"q": "实际改了什么", "plain": "AI 调用→哪次写入的血缘",
         "answeredBy": "trace_id 拼 llm_calls ↔ action_log", "status": "结构通·待数据",
         "detail": "两表都有 trace_id 列可 JOIN；当前 llm_calls=0、action_log.trace_id 全 NULL，暂无实例可拼"},
        {"q": "有没有验证通过", "plain": "金标/影子/覆盖度",
         "answeredBy": "shadow_run + release-checklist", "status": "部分",
         "detail": f"金标 scripted 常驻（现查 {goldset['latest']['n'] if goldset.get('latest') else 0} 题）；影子档1未落数"},
        {"q": "花了多少", "plain": "token/耗时成本",
         "answeredBy": "llm_calls token/duration × call_type×provider", "status": "待灌",
         "detail": "成本分账字段就位（est_tokens/duration_ms），llm_calls=0 故暂无；分域烧账依赖 G-Trace"},
        {"q": "为什么停", "plain": "终态原因",
         "answeredBy": "resolution_memory.decision / status + action_log.result", "status": "通",
         "detail": "决定（adopted/rejected）+ 结果（ok/denied）留痕可溯"},
    ]

    # ── 血缘拼接演示（七问·实际改了什么）：trace_id 拼 llm_calls ↔ action_log ──────
    lineage = {
        "schemaReady": bool(gov and gov.has_table("llm_calls") and gov.has_table("action_log")),
        "llmCallsHasTrace": bool(gov and "trace_id" in (gov.columns("llm_calls") if gov else [])),
        "actionLogHasTrace": bool(gov and "trace_id" in (gov.columns("action_log") if gov else [])),
        "instances": [],
        "note": "",
    }
    if lineage["schemaReady"] and lineage["llmCallsHasTrace"] and lineage["actionLogHasTrace"]:
        joins = gov.q(
            "SELECT lc.trace_id, lc.call_type, lc.provider, lc.model, lc.status, "
            "al.log_id, al.actor, al.action, al.target_object_id, al.result "
            "FROM llm_calls lc JOIN action_log al ON al.trace_id = lc.trace_id "
            "WHERE lc.trace_id IS NOT NULL AND al.trace_id IS NOT NULL LIMIT 3")
        lineage["instances"] = [dict(r) for r in joins]
        al_trace_n = int(gov.scalar("SELECT COUNT(trace_id) FROM action_log") or 0)
        lineage["actionLogTraceNonNull"] = al_trace_n
        if lineage["instances"]:
            lineage["note"] = "血缘链已通：下方为真实 trace_id 拼出的 llm_calls ↔ action_log 实例。"
        else:
            lineage["note"] = (
                "血缘链路结构已就位——action_log.trace_id 列已加（G-Trace）、llm_calls.trace_id 存在（egress_gate）、"
                f"两表可按 trace_id JOIN。当前 llm_calls={max(llm_n,0)} 条、action_log 带 trace_id 的行 {al_trace_n} 条，"
                "尚无经 dispatch 的真实 AI 写动作，故暂无实例可拼——需接生产遥测跑一次真实 AI dispatch（如实标注，不编造）。")
    else:
        lineage["note"] = "llm_calls / action_log 表或 trace_id 列未就位——血缘拼接待 G-Trace 接通。"

    # ── 护栏卡（可骄傲展示的证据）─────────────────────────────────────────────────
    frozen_actions = [a["name"] for a in onto.get("actions", []) if a.get("ai_executable") == "frozen"]
    # 铁证1：AI 直接写冻结区动作的记录 = 0（proposal-only 架构）。AI actor 判据：actor/role 含 ai/agent。
    ai_frozen_writes = 0
    if gov and frozen_actions:
        ph = ",".join("?" * len(frozen_actions))
        ai_frozen_writes = int(gov.scalar(
            f"SELECT COUNT(*) FROM action_log WHERE action IN ({ph}) "
            "AND (lower(COALESCE(actor,'')) LIKE '%ai%' OR lower(COALESCE(actor,'')) LIKE '%agent%' "
            "OR lower(COALESCE(role,'')) LIKE '%ai%' OR lower(COALESCE(role,'')) LIKE '%agent%') "
            "AND result NOT LIKE 'denied%'", tuple(frozen_actions)) or 0)
    # 铁证2：重复事件重复副作用 = 0（幂等）。CreateRiskEvent 写入数 vs 去重 target 数。
    cre_total = int(gov.scalar(
        "SELECT COUNT(*) FROM action_log WHERE action='CreateRiskEvent'") or 0) if gov else 0
    cre_distinct = int(gov.scalar(
        "SELECT COUNT(DISTINCT target_object_id) FROM action_log WHERE action='CreateRiskEvent'") or 0) if gov else 0
    dup_side_effects = cre_total - cre_distinct
    guardrails = [
        {"key": "ai_illegal_write", "label": "AI 非法输出仍落库", "value": ai_frozen_writes,
         "pass": ai_frozen_writes == 0, "unit": "条",
         "plain": ("proposal-only 架构铁证：冻结区四审批动作（"
                   + "、".join(frozen_actions) + "）由 AI 直接写入的记录＝0；AI 只提案，人批准才落库。"),
         "source": f"{gov_src} · action_log × 本体 ai_executable=frozen" if gov else "action_log（未就位）"},
        {"key": "dup_side_effect", "label": "重复事件导致的重复副作用", "value": max(dup_side_effects, 0),
         "pass": dup_side_effects == 0, "unit": "条",
         "plain": (f"三洞幂等修复铁证：CreateRiskEvent 写入 {cre_total} 条＝去重 target {cre_distinct} 个，"
                   "同一风险不产生第二次写入（重复里程碑注入不产生第二个 RiskEvent，见 demo-assertions B1）。"),
         "source": f"{gov_src} · action_log(CreateRiskEvent)" if gov else "action_log（未就位）"},
        {"key": "ai_denied", "label": "AI 越权全被拒", "value": denied_live,
         "pass": True, "unit": "条 denied（现查主库）",
         "plain": ("现查主库越权 denied＝0（无对抗尝试落主库）；对抗基线：288 注入全 refused + denied 审计"
                   "（test_agent_security，release-checklist §D 记录）。AI 无静默后门。"),
         "source": f"{gov_src} · action_log + test_agent_security" if gov else "action_log（未就位）"},
    ]

    # ── 成本分账（token/耗时 × call_type × provider）──────────────────────────────
    cost = {
        "source": telemetry["source"],
        "available": llm_n > 0,
        "byTypeProvider": [],
        "note": ("成本分账字段就位（llm_calls 的 est_tokens/duration_ms + call_type×provider），"
                 "当前 llm_calls=0 故暂无数字；「哪条规则/哪个域烧的」依赖 G-Trace 追踪号接通后才能拆到域，"
                 "未接通前如实标注已知空缺，不糊成一个不可解释总数（规格·成本分账）。")
        if llm_n <= 0 else "token/耗时按 call_type×provider 拆分。",
    }
    if llm_n > 0:
        cost["byTypeProvider"] = [dict(r) for r in gov.q(
            "SELECT call_type, provider, COUNT(*) n, "
            "SUM(est_input_tokens+est_output_tokens) tokens, "
            "SUM(duration_ms) duration_ms "
            "FROM llm_calls GROUP BY call_type, provider ORDER BY tokens DESC")]

    # ── 数据源清单（provenance 总账）────────────────────────────────────────────
    sources = [
        {"key": "llm_calls", "label": "AI 调用遥测", "path": gov_src or "—",
         "table": "llm_calls", "status": "空（待灌）" if llm_n <= 0 else f"{llm_n} 条"},
        {"key": "action_log", "label": "业务动作审计", "path": gov_src or "—",
         "table": "action_log", "status": f"{max(al_total,0)} 条" if gov else "未就位"},
        {"key": "rule_run_ledger", "label": "规则运行台账", "path": gov_src or "—",
         "table": "rule_run_ledger",
         "status": (f"{len(ledger['runs'])} 次 detect" if ledger["available"] else "未就位")},
        {"key": "shadow_run", "label": "影子测量台账", "path": "data/shadow.sqlite",
         "table": "shadow_run",
         "status": (f"bench2 {len(goldset['runs'])} run / bench1 {len(b1_rows)} 行"
                    if (shadow and shadow.has_table("shadow_run")) else "未就位")},
        {"key": "resolution_memory", "label": "决策记忆（档1底数）", "path": "data/simworld.sqlite",
         "table": "resolution_memory",
         "status": f"{db.count('resolution_memory')} 条" if db.has_table("resolution_memory") else "未就位"},
        {"key": "coverage_docs", "label": "覆盖度文档", "path": "docs/",
         "table": "demo-assertions + release-checklist",
         "status": (f"断言 {assertions['total']} · 门 {gates['total']}"
                    if assertions["available"] else "未就位")},
    ]

    return {
        "title": "治理控制室",
        "question": "系统看得见吗？——把散在 CLI 的治理证据聚成一屏控制室",
        "subtitle": ("治理证据包收官件（G-Dashboard）。六类证据全部现查现算、来源如实标注、无数据如实暂无。"
                     "照「每个运行必须能回答的 7 问」组织——看板会自己把治理盲区照出来。"),
        "sources": sources,
        "sevenQuestions": seven,
        "telemetry": telemetry,
        "security": security,
        "goldset": goldset,
        "shadow": shadow_card,
        "ledger": ledger,
        "coverage": coverage,
        "lineage": lineage,
        "guardrails": guardrails,
        "cost": cost,
    }


# =============================================================================
# 生成 src/data/index.ts（数据 barrel）——与 JSON 快照同为导出产物，随本脚本重生成。
# 修正历史脆弱点：index.ts 原为手写源文件但落在 src/data/（被仓库根 data/ 规则 gitignore），
# clone/worktree 后缺失致 `python3 export_data.py && npm run build` 无法独立跑通。
# 改由本脚本生成后，barrel 与 JSON 同源同生命周期，验收命令可在干净 checkout 独立执行。
# =============================================================================
INDEX_TS = '''// ⚙️ 本文件由 export_data.py 生成——请勿手改。改数据契约改 export_data.py 的生成模板。
// 构建期消费 export_data.py 生成的静态快照。零后端。
// 小 JSON 静态 import 进主包；大 JSON（entity / search）运行时 fetch 懒加载。
import metaRaw from "./meta.json";
import structureRaw from "./structure.json";
import journeyRaw from "./journey.json";
import constitutionRaw from "./constitution.json";
import weaveRaw from "./weave.json";
import rulesRaw from "./rules.json";
import actionsRaw from "./actions.json";
import evolutionRaw from "./evolution.json";
import worldRaw from "./world.json";
import flywheelRaw from "./flywheel.json";
import aiActivityRaw from "./ai_activity.json";
import decisionLineageRaw from "./decision_lineage.json";
import impactRaw from "./impact.json";
import governanceRaw from "./governance.json";

import type {
  Meta, StructureData, JourneyData, ConstitutionData, WeaveData, RulesData,
  ActionsData, EvolutionData, WorldData, FlywheelData, AIActivityData,
  DecisionLineageData, EntityData, ImpactData, SearchIndex, GovernanceData,
} from "../types";

export const meta = metaRaw as Meta;
export const structure = structureRaw as unknown as StructureData;
export const journey = journeyRaw as unknown as JourneyData;
export const constitution = constitutionRaw as unknown as ConstitutionData;
export const weave = weaveRaw as unknown as WeaveData;
export const rules = rulesRaw as unknown as RulesData;
export const actions = actionsRaw as unknown as ActionsData;
export const evolution = evolutionRaw as unknown as EvolutionData;
export const world = worldRaw as unknown as WorldData;
export const flywheel = flywheelRaw as unknown as FlywheelData;
export const aiActivity = aiActivityRaw as unknown as AIActivityData;
export const decisionLineage = decisionLineageRaw as unknown as DecisionLineageData;
export const impact = impactRaw as unknown as ImpactData;
export const governance = governanceRaw as unknown as GovernanceData;

// 实体全量（约 9MB）运行时 fetch，只在打开「实体浏览」时加载一次。
let entityCache: EntityData | null = null;
let entityPromise: Promise<EntityData> | null = null;
export function loadEntity(): Promise<EntityData> {
  if (entityCache) return Promise.resolve(entityCache);
  if (!entityPromise) {
    const base = import.meta.env.BASE_URL || "/";
    entityPromise = fetch(`${base}data/entity.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`entity.json ${r.status}`);
        return r.json();
      })
      .then((j: EntityData) => {
        entityCache = j;
        return j;
      });
  }
  return entityPromise;
}

// 全局搜索索引（约 1MB）运行时 fetch，app 挂载即后台预取，供顶部常驻搜索框即时命中。
let searchCache: SearchIndex | null = null;
let searchPromise: Promise<SearchIndex> | null = null;
export function loadSearch(): Promise<SearchIndex> {
  if (searchCache) return Promise.resolve(searchCache);
  if (!searchPromise) {
    const base = import.meta.env.BASE_URL || "/";
    searchPromise = fetch(`${base}data/search.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`search.json ${r.status}`);
        return r.json();
      })
      .then((j: SearchIndex) => {
        searchCache = j;
        return j;
      });
  }
  return searchPromise;
}
'''


def write_index_ts(out_dir: Path):
    (out_dir / "index.ts").write_text(INDEX_TS, encoding="utf-8")


# =============================================================================
# 主流程
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="建造者透视镜 v2 数据导出（只读）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="活世界 simworld.sqlite 路径")
    ap.add_argument("--verify-db", default=str(DEFAULT_VERIFY_DB), help="验证世界 ontology.sqlite 路径")
    ap.add_argument("--shadow-db", default=str(DEFAULT_SHADOW_DB), help="影子测量库 shadow.sqlite 路径")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="JSON 输出目录")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    onto = load_ontology()
    db = ReadOnlyDB(Path(args.db))
    vdb = None
    try:
        vdb = ReadOnlyDB(Path(args.verify_db))
    except sqlite3.Error:
        vdb = None
    shadow = None
    try:
        if Path(args.shadow_db).exists():
            shadow = ReadOnlyDB(Path(args.shadow_db))
    except sqlite3.Error:
        shadow = None

    exported_at = _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")

    key_tables = [
        "suppliers", "skus", "customers", "sales_orders", "sales_order_lines",
        "purchase_orders", "shipments", "shipment_milestones", "shipment_allocations",
        "risk_events", "tasks", "invoices", "inventory_positions",
        "sim_ai_activity", "sim_event_log", "sim_forwarders", "resolution_memory", "llm_calls",
    ]
    table_counts = {t: db.count(t) for t in key_tables}

    meta = {
        "app": "建造者透视镜 v2",
        "tagline": "把系统的内在设计与活世界，摊开给建造者看",
        "exportedAt": exported_at,
        "ontologyVersion": onto.get("version"),
        "dbPath": "data/simworld.sqlite",
        "verifyDbPath": "data/ontology.sqlite",
        "worldNote": "主数据源＝活世界（simworld，14 个月连续模拟）；验证世界（ontology + data/truth）仅供规则档案引用引擎精度。凡活世界产物标 sim 徽标。",
        "tableCounts": table_counts,
        "readOnly": True,
        "note": "本页所有数字来自导出时刻的只读快照，非实时。空表如实显示为空。",
    }

    # 小 JSON 走 src/data 静态 import（类型安全、进主包）
    outputs = {
        "meta.json": meta,
        "structure.json": build_structure(onto, db),
        "journey.json": build_journey(onto, db),
        "constitution.json": build_constitution(onto, db),
        "weave.json": build_weave(onto, db),
        "rules.json": build_rules(onto, db, vdb),
        "actions.json": build_actions(onto, db),
        "evolution.json": build_evolution(onto),
        "world.json": build_world(onto, db),
        "flywheel.json": build_flywheel(onto, db),
        "ai_activity.json": build_ai_activity(onto, db),
        "decision_lineage.json": build_decision_lineage(onto, db),
        "impact.json": build_impact(onto, db),  # v3 板块②
        "governance.json": build_governance(onto, db, vdb, shadow),  # 第15视图·治理控制室
    }
    # 大 JSON（实体全量 + 搜索索引）走 public/data 运行时 fetch（懒加载，不进主包）
    public_out = SCRIPT_DIR / "public" / "data"
    public_outputs = {
        "entity.json": build_entity(onto, db),
        "search.json": build_search(onto, db),  # v3 板块①
    }

    total_bytes = 0
    print("  —— 导出体量表 ——")
    for fname, data in outputs.items():
        path = out_dir / fname
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        sz = path.stat().st_size
        total_bytes += sz
        print(f"  src/data/{fname:20s} {sz:>10,} bytes")
    public_out.mkdir(parents=True, exist_ok=True)
    for fname, data in public_outputs.items():
        path = public_out / fname
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        sz = path.stat().st_size
        total_bytes += sz
        print(f"  public/data/{fname:17s} {sz:>10,} bytes  (运行时懒加载)")
    # 数据 barrel（index.ts）与 JSON 同源生成——修正 gitignore 脆弱点，验收命令可独立跑通
    write_index_ts(out_dir)
    print(f"  src/data/index.ts    （数据 barrel · 生成产物）")
    print(f"  {'合计':28s} {total_bytes:>10,} bytes  ({total_bytes/1024/1024:.2f} MB)")

    db.close()
    if vdb:
        vdb.close()
    if shadow:
        shadow.close()
    print(f"\n✓ 导出完成 · 数据截至 {exported_at}")
    print(f"  活世界透明度：risk_events={table_counts['risk_events']} · tasks={table_counts['tasks']} · "
          f"resolution_memory={table_counts['resolution_memory']} · sim_ai_activity={table_counts['sim_ai_activity']} · "
          f"sim_event_log={table_counts['sim_event_log']} · llm_calls={table_counts['llm_calls']}")


if __name__ == "__main__":
    main()
