#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建造者透视镜 v1 —— 数据导出脚本（只读快照）

职责：读 ontology JSON / 宪法文档 / data/ontology.sqlite 三表 + 运行账本，
把六个视图需要的语义结构与真实计数烘焙成 src/data/*.json，供前端构建时消费。

铁律（与 AGENTS.md §5 一致）：
- 只读一切：对 sqlite 只执行 SELECT / PRAGMA；不改任何源。
- 数字必须可溯源：所有计数来自实时查询，不写死统计数字（全局规则 5）。
- 敏感字段照脱敏口径：本应用给创始人，可显示全量业务数字；个人信息类字段一律不导出。
- 快照带时间戳：导出即快照，前端明示"数据截至 <时间>"。

用法：
    python3 apps/builder-console/export_data.py
    # 可选：--db 指定库，--out 指定输出目录

宪法九条 / 冻结区 / 五资产的"条文/白话/防的事故"为作者化的稳定内容，
条文摘自 docs/superpowers/specs/2026-07-12-product-spec-v3-consolidated.md §12，
计数则实时注入——两者分离，稳定文本作者写、数字机器取。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
from pathlib import Path

# ---- 路径锚定：脚本在 apps/builder-console/ 下，仓库根在上两级 ----
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
ONTOLOGY_JSON = REPO_ROOT / "ontology" / "control-tower-ontology.json"
DEFAULT_DB = REPO_ROOT / "data" / "ontology.sqlite"
DEFAULT_OUT = SCRIPT_DIR / "src" / "data"


# =============================================================================
# 只读 sqlite 辅助
# =============================================================================
class ReadOnlyDB:
    """只读连接：以 immutable/ro 模式打开，任何写入会失败，物理保证不改源。"""

    def __init__(self, path: Path):
        uri = f"file:{path}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    def q(self, sql: str, params: tuple = ()):  # -> list[sqlite3.Row]
        return list(self.conn.execute(sql, params))

    def scalar(self, sql: str, params: tuple = ()):
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def count(self, table: str) -> int:
        try:
            return int(self.scalar(f"SELECT COUNT(*) FROM {table}") or 0)
        except sqlite3.Error:
            return -1  # 表不存在 → -1 明确区别于 0（真实为空）

    def close(self):
        self.conn.close()


def load_ontology() -> dict:
    with open(ONTOLOGY_JSON, encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# 业务域划分（作者化分类，非数据结构直投）——把 34 对象归到 5 场景 + 共享核 + 协调
# =============================================================================
DOMAINS = {
    "core": {
        "name": "共享核对象",
        "plain": "五个场景都要用到的地基：谁供货、卖什么、卖给谁。整个系统只有一份，不同角色看到的是同一个对象。",
        "objects": ["Supplier", "Sku", "Customer"],
    },
    "delay": {
        "name": "延误风险控制塔",
        "plain": "一票货从订舱到交付，中途晚点了会连累哪些客户订单——系统沿对象图算给你看。",
        "scene": "ship",
        "objects": [
            "SalesOrder", "SalesOrderLine", "PurchaseOrder", "Shipment",
            "ShipmentMilestone", "ShipmentAllocation", "RiskEvent", "Task", "Container",
        ],
    },
    "cost": {
        "name": "运费对账",
        "plain": "货代开来的账单，逐条比对该收多少——多算的、没预算的、重复的，自动挑出来。",
        "scene": "invoice",
        "objects": ["Invoice", "InvoiceLine", "ExpectedCost"],
    },
    "admission": {
        "name": "SKU 准入闸门",
        "plain": "一个新品要不要卖进美国——合规查一遍、物流方案搭一遍、成本算一遍，再决定报不报价。",
        "scene": "gate",
        "objects": ["AdmissionCase", "ComplianceFinding", "LogisticsPlan", "CostScenario"],
    },
    "procurement": {
        "name": "采购三方对账",
        "plain": "下单、收货、供应商开票，三张纸对得上才付钱——对不上的（少收、涨价、超收、缺资质）拦下来。",
        "scene": "dock",
        "objects": [
            "PoLine", "GoodsReceipt", "GoodsReceiptLine", "SupplierInvoice",
            "SupplierInvoiceLine", "PurchasePayment", "SupplierQualification",
            "RFQ", "RFQLine", "Quote",
        ],
    },
    "warehouse": {
        "name": "仓储库存",
        "plain": "美国仓里每个货位有多少能卖、多少被占、盘点差多少——低于安全库存就报警。",
        "scene": "warehouse",
        "objects": ["Warehouse", "InventoryPosition", "InventoryReservation", "CycleCount"],
    },
    "coordination": {
        "name": "跨方协调线程",
        "plain": "跟工厂/货代/客户来回催办的每一次沟通，都记成一条有状态的线程，不靠微信记忆。",
        "scene": "thread",
        "objects": ["CoordinationThread"],
    },
}

# 每个对象对应的真实表名（用于取真实记录数）
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
}


def obj_by_type(onto: dict) -> dict:
    return {o["type"]: o for o in onto["objects"]}


# =============================================================================
# 视图 1：系统结构（三层等距）
# =============================================================================
def build_structure(onto: dict, db: ReadOnlyDB) -> dict:
    objs = obj_by_type(onto)
    total_records = 0
    domain_cards = []
    for key, dom in DOMAINS.items():
        obj_types = dom["objects"]
        recs = 0
        for t in obj_types:
            tbl = OBJECT_TABLE.get(t)
            if tbl:
                c = db.count(tbl)
                if c > 0:
                    recs += c
        total_records += recs
        domain_cards.append({
            "id": key,
            "name": dom["name"],
            "plain": dom["plain"],
            "scene": dom.get("scene", "core"),
            "objectCount": len(obj_types),
            "recordCount": recs,
            "objects": obj_types,
            "shared": key == "core",
        })

    # 顶层：智能接入层
    n_actions = len(onto["actions"])
    n_rules = len(onto["riskRules"])
    intelligence = [
        {
            "id": "ai-colleague", "name": "AI 同事", "icon": "brain",
            "plain": "读得懂对象图的助手：解释一个风险的根因、起草处置建议。它只提案，从不拍板——"
                     "只能调用注册好的工具函数碰对象，碰不到冻结区。",
            "connectsTo": ["delay", "cost", "admission", "procurement", "warehouse"],
        },
        {
            "id": "automation", "name": "自动化引擎", "icon": "gears",
            "plain": f"{n_rules} 条风险规则 + {n_actions} 个动作组成的流水线：里程碑一进来，自动定位"
                     "受影响订单、建风险事件、派任务。机器做感知与执行，人做判断与授权。",
            "connectsTo": ["delay", "cost", "procurement", "warehouse"],
        },
        {
            "id": "evaluator", "name": "评估器", "icon": "gauge",
            "plain": "对着真值集打分的裁判：查全率/查准率、提案采纳率、重放一致性。"
                     "指标不达标只许改规则，禁止改指标——这是诚实文化的闸门。",
            "connectsTo": ["delay", "admission"],
        },
    ]

    # 底层：治理层
    n_roles = len(onto["roles"])
    n_sensitive = len(onto.get("sensitiveFieldRules", []))
    audit_rows = db.count("action_log")
    governance = [
        {
            "id": "constitution", "name": "宪法九条", "icon": "pillar",
            "plain": "受托责任 / 身份边界 / 诚实文化三组共九条，是永不改的地基。"
                     "审批、关闭、花钱、合规裁决属于冻结区，AI 永远够不着。",
            "guards": ["delay", "cost", "admission", "procurement", "warehouse", "coordination"],
        },
        {
            "id": "rbac", "name": "权限绑定", "icon": "keys",
            "plain": f"{n_roles} 个角色 + {n_sensitive} 条敏感字段规则。权限压在数据访问层，"
                     "不是 UI 层——无权的字段渲染成「无权查看」，绝不留空（留空会被误读成「没风险」）。",
            "guards": ["delay", "cost", "admission", "procurement", "warehouse"],
        },
        {
            "id": "audit", "name": "审计流水", "icon": "ledger",
            "plain": f"每个动作都写一行账：谁、什么角色、对哪个对象、做了什么、结果如何。"
                     f"当前快照 {audit_rows} 行，是决策血缘可回放的底账。",
            "guards": ["delay", "cost", "admission", "procurement", "warehouse", "coordination"],
        },
    ]

    return {
        "title": "这个系统是什么结构",
        "question": "这个系统是什么结构？",
        "subtitle": "三层立体：上面 AI 怎么接入、中间业务世界长什么样、下面谁在兜底。悬停任意元素看白话。",
        "layers": {
            "intelligence": {"name": "智能接入层", "plain": "AI 与自动化从这里接入业务——是页面主角，不是角落里的按钮。", "items": intelligence},
            "business": {"name": "业务世界", "plain": "五个真实场景域 + 一层共享核对象。这是被建模的现实，不是节点连线图。", "items": domain_cards},
            "governance": {"name": "治理层", "plain": "宪法、权限、审计——托住上面两层，划定 AI 的边界。", "items": governance},
        },
        "totals": {
            "objects": len(onto["objects"]),
            "links": len(onto["links"]),
            "actions": n_actions,
            "riskRules": n_rules,
            "roles": n_roles,
            "records": total_records,
        },
    }


# =============================================================================
# 视图 2：一票货的一生（业务旅程）
# =============================================================================
def pick_fields(obj: dict, names: list[str]) -> list[dict]:
    """从对象属性字典里挑指定字段，附类型与描述（白话优先取 description）。"""
    props = {p["name"]: p for p in obj.get("properties", [])}
    out = []
    for n in names:
        p = props.get(n)
        if not p:
            out.append({"name": n, "type": "?", "desc": ""})
            continue
        out.append({
            "name": n,
            "type": p.get("type", ""),
            "desc": p.get("description", ""),
            "values": p.get("values"),
        })
    return out


def build_journey(onto: dict, db: ReadOnlyDB) -> dict:
    objs = obj_by_type(onto)

    # 真实主角案例：SHP-2026-0068 / RSK-0038 —— 一票 27 天击穿承诺的延误
    case_shipment = "SHP-2026-0068"
    case_risk = "RSK-0038"

    def cnt(t):
        tbl = OBJECT_TABLE.get(t)
        return db.count(tbl) if tbl else -1

    # 处置漏斗的真实计数（提案 / 审批 / 结案 / 先例）
    n_proposed = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE proposed_action IS NOT NULL AND proposed_action != ''") or 0)
    n_approved = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE approval_status='approved'") or 0)
    n_done = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE status='done'") or 0)

    # 站点定义：每站一个主对象 + 推动它的动作 + 谁能碰
    role_names = {
        "ops": "运营", "cs": "客户成功", "manager": "经理", "system": "系统",
        "sales": "销售", "compliance": "合规", "finance": "财务", "procurement": "采购",
    }
    actions_by_id = {a["id"]: a for a in onto["actions"]}

    def actors(action_id: str) -> list[str]:
        a = actions_by_id.get(action_id, {})
        return [role_names.get(x, x) for x in a.get("executors", [])]

    stations = [
        {
            "id": "so", "step": "01", "title": "订单诞生", "object": "SalesOrder",
            "plain": "客户下了一张销售订单，拆成若干订单行（每行一个 SKU、一个承诺交期）。这是一切承诺的起点。",
            "fields": pick_fields(objs["SalesOrderLine"], ["so_line_id", "sku_id", "qty", "promised_delivery_date", "original_promised_date", "line_status"]),
            "canDo": {"action": "—", "by": ["客户成功"], "note": "订单行 owner=客户成功；承诺交期是后面所有风险判断的基准线。"},
            "count": cnt("SalesOrderLine"), "countLabel": "订单行",
        },
        {
            "id": "po", "step": "02", "title": "向工厂下单", "object": "PurchaseOrder",
            "plain": "为了履约，向供应商下采购单，约定预计齐货日。货还没动，但承诺链已经拉起来了。",
            "fields": pick_fields(objs["PurchaseOrder"], ["po_id", "supplier_id", "sku_id", "qty", "expected_ready_date", "status"]),
            "canDo": {"action": "—", "by": ["运营"], "note": "采购单 owner=运营。"},
            "count": cnt("PurchaseOrder"), "countLabel": "采购单",
        },
        {
            "id": "shipment", "step": "03", "title": "订舱起运", "object": "Shipment",
            "plain": "一票运输把一个或多个采购单装上船。记下船司、航次、起止港、初始 ETA。本案：宁波→长滩，OOCL，LCL 拼箱。",
            "fields": pick_fields(objs["Shipment"], ["shipment_id", "mode", "carrier_name", "origin_port", "destination_port", "eta_initial", "eta_current", "status"]),
            "canDo": {"action": "IngestMilestone (A1)", "by": actors("A1"), "note": "起运事件由系统摄入，触发 planned→in_transit。"},
            "count": cnt("Shipment"), "countLabel": "运输票",
            "example": case_shipment,
        },
        {
            "id": "milestone", "step": "04", "title": "里程碑流", "object": "ShipmentMilestone",
            "plain": "船在动，事件不断进来：起运、转船、ETA 变更、到港、清关……本案 ETA 被连改四次，从 8/02 一路推到 8/31。",
            "fields": pick_fields(objs["ShipmentMilestone"], ["milestone_id", "event_type", "event_classifier", "event_time", "event_locode", "source_system"]),
            "canDo": {"action": "IngestMilestone (A1)", "by": actors("A1"), "note": "重复上报存下但跳过副作用；乱序到达按事件时间归位。"},
            "count": cnt("ShipmentMilestone"), "countLabel": "里程碑",
        },
        {
            "id": "allocation", "step": "05", "title": "分配到订单行", "object": "ShipmentAllocation",
            "plain": "这票货到底供哪些订单行？分配对象是影响传播的枢纽——顺着它，一次 ETA 变更能算到每一个受牵连的客户承诺。",
            "fields": pick_fields(objs["ShipmentAllocation"], ["allocation_id", "shipment_id", "so_line_id", "allocated_qty"]),
            "canDo": {"action": "（数据构建期建立）", "by": ["系统"], "note": "milestone→shipment→allocation→so_line→customer 就是系统的灵魂那条链。"},
            "count": cnt("ShipmentAllocation"), "countLabel": "分配关系",
        },
        {
            "id": "risk", "step": "06", "title": "风险检出", "object": "RiskEvent",
            "plain": "ETA + 清关缓冲 + 尾程缓冲 > 承诺交期 → 击穿。本案 RSK-0038：击穿承诺 27 天，牵连 6 条订单行，$44,737 敞口，判 critical。",
            "fields": pick_fields(objs["RiskEvent"], ["risk_event_id", "type", "rule_id", "severity", "affected_so_line_ids", "root_cause", "status"]),
            "canDo": {"action": "CreateRiskEvent (A2)", "by": actors("A2"), "note": "系统检出并把受影响订单行标为 at_risk；改期后二次击穿会再次报警。"},
            "count": cnt("RiskEvent"), "countLabel": "风险事件",
            "example": case_risk,
        },
        {
            "id": "task", "step": "07", "title": "派任务", "object": "Task",
            "plain": "风险变成一张落到某个角色桌上的任务：优先级、到期时间、指派角色。谁该动手，一目了然。",
            "fields": pick_fields(objs["Task"], ["task_id", "risk_event_id", "assignee_role", "priority", "due_at", "status"]),
            "canDo": {"action": "AssignTask (A3)", "by": actors("A3"), "note": "任务 owner=运营；系统可自动派，人可改派。"},
            "count": cnt("Task"), "countLabel": "任务",
        },
        {
            "id": "propose", "step": "08", "title": "提案处置", "object": "Task",
            "plain": "运营（或 AI 起草）提一个方案：加急 / 改期 / 接受延误，三选一带参数。本案提案：改空运，估价 $3,036，新 ETA 8/15。",
            "fields": pick_fields(objs["Task"], ["proposed_action", "proposal_params", "proposal_actor_role", "approval_status"]),
            "canDo": {"action": "ProposeMitigation (A4)", "by": actors("A4"), "note": "提案人 ≠ 审批人（maker-checker）；AI 可起草，但提交权在人。"},
            "count": n_proposed, "countLabel": "提案",
        },
        {
            "id": "approve", "step": "09", "title": "审批（冻结区）", "object": "Task",
            "plain": "只有经理能批。花 $3,036 空运，救回 $44,737 敞口——经济账划算，批准。这一步 AI 永远够不着。",
            "fields": pick_fields(objs["Task"], ["approval_status", "approved_by_role", "action_taken"]),
            "canDo": {"action": "ApproveMitigation (A5)", "by": actors("A5"), "note": "冻结区：审批权永属于人；驳回则任务退回 assigned，提案留痕。"},
            "count": n_approved, "countLabel": "已审批",
            "frozen": True,
        },
        {
            "id": "close", "step": "10", "title": "关闭结案", "object": "RiskEvent",
            "plain": "处置执行、状态回写、记录耗时与结果，风险事件关闭。闭环走完：milestone→risk→task→action→审计全链可追。",
            "fields": pick_fields(objs["RiskEvent"], ["status", "resolved_at", "outcome", "resolution_summary"]),
            "canDo": {"action": "CloseRiskEvent (A6)", "by": actors("A6"), "note": "关闭前校验关联任务全部终态；关闭本身也在冻结区（花钱执行/结案属人）。"},
            "count": n_done, "countLabel": "已结案",
        },
        {
            "id": "precedent", "step": "11", "title": "沉淀先例", "object": "PrecedentCase",
            "plain": "结案四件套（看到什么/提了什么/怎么决定/结果如何）写进处置记忆，成为下次同类事件的可引用先例——系统从这里开始越用越强。",
            "fields": [
                {"name": "memory_id", "type": "string", "desc": "先例主键"},
                {"name": "cited_precedent_ids", "type": "string", "desc": "当时引用了哪些更早的先例"},
                {"name": "decision", "type": "enum", "desc": "人的决定：approve/reject/modify"},
                {"name": "outcome_resolved", "type": "string", "desc": "回填的真实结果"},
                {"name": "quality_label", "type": "enum", "desc": "事后质量标注，驱动打法卡晋升"},
            ],
            "canDo": {"action": "（结案后回填）", "by": ["系统 + 人回填结果"], "note": "先例 → 打法卡 → 评估集 → 自主权，五资产的第一环。详见视图四。"},
            "count": db.count("resolution_memory"), "countLabel": "处置记忆",
        },
    ]

    return {
        "title": "一票货的一生",
        "question": "业务是怎么被建模的？",
        "subtitle": "不按对象类型罗列，而是跟着一票真实的货走完全程。主角：SHP-2026-0068，一票 ETA 被推迟 29 天的拼箱。",
        "caseHeadline": {
            "shipment": case_shipment, "risk": case_risk,
            "line": "宁波 → 长滩 · OOCL · LCL 拼箱 · ETA 8/02 → 8/31（推迟 29 天）· 击穿 6 条客户承诺 · $44,737 敞口",
        },
        "stations": stations,
    }


# =============================================================================
# 视图 3：什么永远不会变（宪法九条 + 冻结区）
# =============================================================================
def build_constitution(onto: dict, db: ReadOnlyDB) -> dict:
    # 条文摘自 spec v3 §12（三组九条）；白话与"防的事故"为作者化解释。
    groups = [
        {
            "group": "一、受托责任", "plain": "客户把数据托给你，第一件事是别把它弄丢、弄混、弄外泄。",
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
            ],
        },
        {
            "group": "二、身份边界", "plain": "AI 能干很多活，但有三件事永远是人的：对外署名、花钱拍板、担责留痕。",
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
            ],
        },
        {
            "group": "三、诚实文化", "plain": "指标难看照实记，AI 查不到就说查不到，机制被绕过一律回滚复盘——诚实优先于好看。",
            "articles": [
                {"no": "⑦", "text": "指标不达预期记录修正，不改口径。",
                 "plain": "查准率没到 85% 就记下没到，去修规则，绝不偷偷把 85% 的标准降到 80%。",
                 "prevents": "防改指标冒充达标——自欺欺人的头号死法。"},
                {"no": "⑧", "text": "AI 事实性陈述可溯源，查不到直说。",
                 "plain": "AI 说的每个业务事实都能指回具体对象 ID；没有依据就回「查不到」，不编。",
                 "prevents": "防 AI 自信地编一个具体数字/先例（全局规则 5）。"},
                {"no": "⑨", "text": "进化资产变更留版本可回滚；机制被绕过无论结果好坏一律回滚 + 复盘；创始人管理员动作留痕并每周披露。",
                 "plain": "打法卡/自主权的每次改动都能回退；有人抄近路绕过流程，哪怕结果是好的也回滚、也复盘；创始人自己的管理员操作也要留痕、每周给设计伙伴看。",
                 "prevents": "防「结果好就默许绕过」，一次侥幸会长成系统性漏洞。"},
            ],
        },
    ]

    # 冻结区：动作风险分类里最右一档（spec §12 前文 + notes-decision-rights）
    freeze_zone = {
        "name": "冻结区（FORBIDDEN）",
        "plain": "这些动作对 AI 根本不存在——不是「权限不够」，是工具函数压根没注册。永不自动晋级。",
        "items": [
            {"name": "审批处置", "why": "花钱/改承诺/认风险的授权，必须与提案分离（自己批自己 = 舞弊）。"},
            {"name": "关闭结案", "why": "宣布一件事了结是判断 + 担责，不是自动化。"},
            {"name": "花钱执行", "why": "加急空运、付款——不可逆地动真金白银。"},
            {"name": "合规裁决", "why": "归类/申报错可构成刑责，法律要求独立的人来判。"},
            {"name": "自由文本对外发送", "why": "对客户/工厂署名沟通，只能人批签发或预审模板。"},
            {"name": "权限与审计配置", "why": "改谁能干什么、改账本本身——动地基的动作。"},
        ],
    }

    # 三档决策权（notes-decision-rights §3）——理解冻结区的尺子
    decision_tiers = [
        {"tier": "🔵 机器 / AI 自动", "plain": "检测·富化·分诊·起草建议·执行（人批之后）。AI 只提案，从不结案。"},
        {"tier": "🟠 人：判断（谁持有可换）", "plain": "派单·提案处置·关闭。可换绑定，部分低风险可上移给 AI。"},
        {"tier": "🔴 人：审批闸（不复刻，强制）", "plain": "审批处置·报价审批·驳回。安全关键、最小权限、职责分离、AI 永不可及。"},
    ]

    return {
        "title": "什么永远不会变",
        "question": "什么永远不会变？",
        "subtitle": "宪法九条 —— 每条：条文 / 白话 / 它防的事故。这是产品的冻结地基，改它须人批且留版本。",
        "groups": groups,
        "freezeZone": freeze_zone,
        "decisionTiers": decision_tiers,
        "source": "docs/superpowers/specs/2026-07-12-product-spec-v3-consolidated.md §12（宪法九条·最终版）",
    }


# =============================================================================
# 视图 4：系统怎么越用越强（五资产飞轮）
# =============================================================================
def build_flywheel(onto: dict, db: ReadOnlyDB) -> dict:
    n_memory = db.count("resolution_memory")
    # 已回填结果的记忆
    n_memory_backfilled = 0
    if n_memory > 0:
        n_memory_backfilled = int(db.scalar(
            "SELECT COUNT(*) FROM resolution_memory WHERE outcome_resolved IS NOT NULL AND outcome_resolved != ''") or 0)

    # 潜在先例：已审批通过、可沉淀的处置
    n_approved = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE approval_status='approved'") or 0)
    n_done = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE status='done'") or 0)
    n_proposed = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE proposed_action IS NOT NULL AND proposed_action != ''") or 0)

    assets = [
        {
            "id": "precedent", "order": 1, "name": "先例（处置记忆）",
            "plain": "一次人工审批结案后，把「看到什么/提了什么/怎么决定/结果如何」四件套存下来。",
            "metricLabel": "已沉淀记忆", "metric": n_memory,
            "sub": f"其中已回填真实结果 {n_memory_backfilled} 条",
            "table": "resolution_memory",
        },
        {
            "id": "playbook", "order": 2, "name": "打法卡（SkillAsset）",
            "plain": "同类处置攒够 5 次，系统聚类提议一张打法卡草稿（一件事怎么做的可审计说明文）。人批准后才进检索。",
            "metricLabel": "触发阈值", "metric": 5, "metricUnit": "次/类",
            "sub": "AI 只提议新卡/改卡，人批准后生效——治理式进化",
            "table": None,
        },
        {
            "id": "evalset", "order": 3, "name": "评估集",
            "plain": "把处置正误固化成可回放的真值题：查全/查准、提案采纳率、重放一致性。",
            "metricLabel": "风险规则真值", "metric": len(onto["riskRules"]), "metricUnit": "条",
            "sub": "R1–R18 合成世界真值，与真实效果分开报告",
            "table": None,
        },
        {
            "id": "autonomy", "order": 4, "name": "自主权（AutonomyGrant）",
            "plain": "一类处置的提案-采纳一致率够高、够久，才提议把它从「人批」升一档到「低风险自动」。逐类、可回退。",
            "metricLabel": "当前自动结案", "metric": 0, "metricUnit": "类",
            "sub": "刻意留白：加早了丢控制，加晚了浪费 AI（决策权 §7 悬而未决）",
            "table": None,
        },
        {
            "id": "freeze", "order": 5, "name": "冻结区",
            "plain": "无论一致率多高，审批/关闭/花钱/合规永不晋级。飞轮转得再快，也撞不穿这堵墙。",
            "metricLabel": "永不晋级动作", "metric": 6, "metricUnit": "类",
            "sub": "见视图三·冻结区",
            "table": None,
        },
    ]

    # 真实飞轮当前所在处：漏斗
    funnel = [
        {"stage": "系统检出风险", "count": db.count("risk_events"), "plain": "自动感知偏离"},
        {"stage": "派发任务", "count": db.count("tasks"), "plain": "落到角色桌上"},
        {"stage": "提出处置方案", "count": n_proposed, "plain": "运营/AI 起草"},
        {"stage": "经理审批通过", "count": n_approved, "plain": "冻结区·人拍板"},
        {"stage": "沉淀为先例", "count": n_memory, "plain": "写进处置记忆"},
    ]

    # 诚实状态：飞轮第一环的真实进度
    honest_state = (
        f"当前快照：{n_approved} 次审批通过、{n_done} 次结案，但处置记忆表 {n_memory} 条——"
        "飞轮的第一环（审批→先例的自动回填）在本快照尚未接通。这是真实进度，不是演示态。"
    ) if n_memory == 0 else (
        f"当前快照：已沉淀 {n_memory} 条先例，其中 {n_memory_backfilled} 条已回填真实结果。"
    )

    return {
        "title": "系统怎么越用越强",
        "question": "系统怎么越用越强？",
        "subtitle": "五个资产接力：一次人工审批如何变成下次的先例，五次同类如何触发打法卡，一致率如何驱动自主权晋升。",
        "assets": assets,
        "funnel": funnel,
        "honestState": honest_state,
    }


# =============================================================================
# 视图 5：AI 干了多少活、花了多少钱（运行账本）
# =============================================================================
def build_ai_activity(onto: dict, db: ReadOnlyDB) -> dict:
    n_llm = db.count("llm_calls")
    llm_stats = {"total": n_llm, "byType": [], "byStatus": [], "estTokens": 0, "degraded": 0}
    if n_llm > 0:
        llm_stats["byType"] = [dict(r) for r in db.q(
            "SELECT call_type AS type, COUNT(*) AS n FROM llm_calls GROUP BY call_type ORDER BY n DESC")]
        llm_stats["byStatus"] = [dict(r) for r in db.q(
            "SELECT status, COUNT(*) AS n FROM llm_calls GROUP BY status")]
        llm_stats["estTokens"] = int(db.scalar(
            "SELECT COALESCE(SUM(est_input_tokens + est_output_tokens),0) FROM llm_calls") or 0)
        llm_stats["degraded"] = int(db.scalar(
            "SELECT COUNT(*) FROM llm_calls WHERE status='degraded'") or 0)

    # 自动化账本：action_log（系统真正跑过的动作）
    action_dist = [dict(r) for r in db.q(
        "SELECT action, COUNT(*) AS n FROM action_log GROUP BY action ORDER BY n DESC")]
    action_total = db.count("action_log")
    action_by_role = [dict(r) for r in db.q(
        "SELECT role, COUNT(*) AS n FROM action_log GROUP BY role ORDER BY n DESC")]
    action_span = db.q("SELECT MIN(as_of_date) AS mn, MAX(as_of_date) AS mx FROM action_log")[0]

    # 提案-采纳漏斗（真实）
    n_risk = db.count("risk_events")
    n_task = db.count("tasks")
    n_proposed = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE proposed_action IS NOT NULL AND proposed_action != ''") or 0)
    n_approved = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE approval_status='approved'") or 0)
    n_pending = int(db.scalar("SELECT COUNT(*) FROM tasks WHERE approval_status='pending'") or 0)

    # 动作分类白话
    action_plain = {
        "MatchInvoice": "三方对账：逐条比对账单，命中异常就建风险",
        "CreateRiskEvent": "建风险事件：沿对象图定位受影响订单",
        "AssignTask": "派任务：把风险落到某个角色桌上",
        "OpenCoordination": "开协调线程：对外催办记成有状态的线程",
    }
    for a in action_dist:
        a["plain"] = action_plain.get(a["action"], "")

    return {
        "title": "AI 干了多少活、花了多少钱",
        "question": "AI 干了多少活、花了多少钱？",
        "subtitle": "运行账本快照。区分两类：自动化引擎跑过的动作（已发生），与 LLM 推理调用（花 token 的部分）。",
        "llm": llm_stats,
        "llmNote": (
            "本快照 llm_calls 表 0 条——LLM 推理遥测尚未接入运行库。这是诚实的空账："
            "自动化引擎（规则/对账/派单）已跑起来并留痕，但 AI 推理（简报/解析/提案）的 token 账还没开始记。"
        ) if n_llm <= 0 else "LLM 推理调用遥测。est_tokens 为估算，非计费真值。",
        "automation": {
            "total": action_total,
            "byAction": action_dist,
            "byRole": action_by_role,
            "span": {"from": action_span["mn"], "to": action_span["mx"]},
            "plain": "action_log 是系统动作的底账：每行=一次真实发生的动作。全部 role=system，说明当前跑的是自动化层，人工动作尚未接入这张账。",
        },
        "funnel": [
            {"stage": "检出风险", "count": n_risk, "note": "自动感知"},
            {"stage": "派任务", "count": n_task, "note": "自动路由"},
            {"stage": "提案", "count": n_proposed, "note": "起草处置"},
            {"stage": "待审批", "count": n_pending, "note": "等人拍板"},
            {"stage": "已采纳", "count": n_approved, "note": "经理批准"},
        ],
        "funnelPlain": "提案-采纳漏斗：从 152 个风险，收敛到 2 个已被人采纳的处置。越往下越窄，说明人的注意力被真正稀缺地用在了刀刃上。",
    }


# =============================================================================
# 视图 6：一个决策是怎么发生的（决策血缘回放）
# =============================================================================
def build_decision_lineage(onto: dict, db: ReadOnlyDB) -> dict:
    # 真实一案：TSK-1870FA8ED8 / RSK-0038 / SHP-2026-0068（已审批结案的完整四件套）
    risk_id = "RSK-0038"
    ship_id = "SHP-2026-0068"

    risk = db.q("SELECT * FROM risk_events WHERE risk_event_id=?", (risk_id,))
    risk = dict(risk[0]) if risk else {}
    ship = db.q("SELECT shipment_id, mode, carrier_name, origin_port, destination_port, etd, eta_initial, eta_current, status FROM shipments WHERE shipment_id=?", (ship_id,))
    ship = dict(ship[0]) if ship else {}
    milestones = [dict(r) for r in db.q(
        "SELECT event_type, event_classifier, event_time, event_locode FROM shipment_milestones WHERE shipment_id=? ORDER BY event_time", (ship_id,))]
    task = db.q("SELECT task_id, risk_event_id, title, assignee_role, priority, proposed_action, proposal_params, approval_status, approved_by_role, action_taken, status FROM tasks WHERE risk_event_id=? AND status='done' LIMIT 1", (risk_id,))
    task = dict(task[0]) if task else {}

    # 关联客户/订单行数量（受影响面）
    affected_lines = []
    try:
        affected_lines = json.loads(risk.get("affected_so_line_ids") or "[]")
    except (json.JSONDecodeError, TypeError):
        affected_lines = []

    prop_params = {}
    try:
        prop_params = json.loads(task.get("proposal_params") or "{}")
    except (json.JSONDecodeError, TypeError):
        prop_params = {}

    est_cost = prop_params.get("est_cost_usd")
    exposure = risk.get("affected_value_usd")

    # 四件套
    quartet = [
        {
            "phase": "① 当时看到什么", "actor": "系统 / 自动化引擎", "tier": "🔵",
            "plain": "一票宁波→长滩的拼箱，ETA 被连续四次往后改，从 8/02 推到 8/31。系统沿对象图算出：清关+尾程缓冲后，击穿 6 条客户订单行的承诺交期，最坏一条超 27 天。",
            "evidence": {
                "shipment": ship,
                "milestones": milestones,
                "affectedLines": affected_lines,
                "exposureUsd": exposure,
                "severity": risk.get("severity"),
                "rootCause": risk.get("root_cause"),
            },
        },
        {
            "phase": "② AI 提了什么", "actor": f"{task.get('assignee_role','ops')} + AI 起草", "tier": "🔵→🟠",
            "plain": "运营（AI 起草）提案：把这票从海运改空运抢救。估算空运成本，给出预期新 ETA。方案是「提议」，不是「执行」——提交后进审批闸。",
            "proposal": {
                "action": task.get("proposed_action"),
                "params": prop_params,
                "estCostUsd": est_cost,
                "expectedNewEta": prop_params.get("expected_new_eta"),
            },
        },
        {
            "phase": "③ 人怎么决定", "actor": f"{task.get('approved_by_role','manager')}（冻结区）", "tier": "🔴",
            "plain": (f"经济账：花 ${est_cost:,.0f} 空运，救回 ${exposure:,.0f} 敞口——救援成本约为敞口的 "
                      f"{(est_cost/exposure*100):.1f}%，划算。经理批准。这一步 AI 永远够不着。") if (est_cost and exposure) else "经理审批。这一步 AI 永远够不着。",
            "decision": {
                "approvalStatus": task.get("approval_status"),
                "approvedByRole": task.get("approved_by_role"),
                "actionTaken": task.get("action_taken"),
            },
        },
        {
            "phase": "④ 结果如何", "actor": "系统回写 + 待回填", "tier": "🔵",
            "plain": "审批通过 → 执行状态回写 → 任务 done。闭环走完：milestone→risk→task→action→审计全链可追。下一步应把这一案沉淀成先例（处置记忆），但本快照该表尚空——飞轮第一环待接通（见视图四）。",
            "outcome": {
                "taskStatus": task.get("status"),
                "precedentRecorded": db.count("resolution_memory") > 0,
            },
        },
    ]

    return {
        "title": "一个决策是怎么发生的",
        "question": "一个决策是怎么发生的？",
        "subtitle": "从运行库里挑一桩真实、已结案的处置，分四步回放：看到什么 → AI 提了什么 → 人怎么决定 → 结果如何。",
        "caseId": task.get("task_id", "TSK-1870FA8ED8"),
        "riskId": risk_id,
        "shipmentId": ship_id,
        "quartet": quartet,
        "sourceNote": "数据取自 tasks / risk_events / shipments / shipment_milestones 四表真实行。resolution_memory 为空，故第④步的先例沉淀标注为「待回填」。",
    }


# =============================================================================
# 主流程
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="建造者透视镜数据导出（只读）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="ontology.sqlite 路径")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="JSON 输出目录")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    onto = load_ontology()
    db = ReadOnlyDB(Path(args.db))

    exported_at = _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")

    # 全表计数（快照透明度）
    key_tables = [
        "suppliers", "skus", "customers", "sales_orders", "sales_order_lines",
        "purchase_orders", "shipments", "shipment_milestones", "shipment_allocations",
        "risk_events", "tasks", "invoices", "coordination_threads",
        "action_log", "llm_calls", "resolution_memory",
    ]
    table_counts = {t: db.count(t) for t in key_tables}

    meta = {
        "app": "建造者透视镜 v1",
        "tagline": "把系统的内在设计，摊开给建造者看",
        "exportedAt": exported_at,
        "ontologyVersion": onto.get("version"),
        "dbPath": "data/ontology.sqlite",
        "tableCounts": table_counts,
        "readOnly": True,
        "note": "本页所有数字来自导出时刻的只读快照，非实时。空表如实显示为空。",
    }

    outputs = {
        "meta.json": meta,
        "structure.json": build_structure(onto, db),
        "journey.json": build_journey(onto, db),
        "constitution.json": build_constitution(onto, db),
        "flywheel.json": build_flywheel(onto, db),
        "ai_activity.json": build_ai_activity(onto, db),
        "decision_lineage.json": build_decision_lineage(onto, db),
    }

    for fname, data in outputs.items():
        path = out_dir / fname
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  写出 {path.relative_to(REPO_ROOT)}  ({path.stat().st_size:,} bytes)")

    db.close()
    print(f"\n✓ 导出完成 · 数据截至 {exported_at}")
    print(f"  快照透明度：resolution_memory={table_counts['resolution_memory']} · "
          f"llm_calls={table_counts['llm_calls']} · action_log={table_counts['action_log']}")


if __name__ == "__main__":
    main()
