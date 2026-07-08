"""标准对象视图兜底层（Foundry "Standard Object View" 范式）。

设计动机（≤5 行「为什么这样建」）：
- 五个核心对象（RiskEvent/AdmissionCase/Task/Invoice/PurchaseOrder）有富工作台（app/object_workbench.py）；
  其余 19 种对象类型不必个个手配富视图，本模块给它们一个**自动生成的通用标准视图**（属性 + 关联对象，
  只读），让"对象图可导航、每个对象都有归宿"。
- 尽量从 ontology JSON（对象清单/主键/敏感字段规则）+ 库表 schema 推断，减少硬编码；关联对象优先复用
  engine.graph 的 object_relationships registry（双向），registry 未覆盖的声明式 FK 边用小补丁补齐。
- 纯函数、零 Streamlit 依赖，可单测；脱敏复用 agent.tools 既有 helper（_can_see_tier/_can_see_cost/
  COST_FIELDS/MASK），不复制权限。**只读**：不产出任何 action、不涉及 agent（富工作台才有）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from agent.tools import _can_see_tier, _can_see_cost, COST_FIELDS, MASK

# 六个核心对象走富工作台；route_object 对它们返回 'rich'，其余注册类型返回 'standard'。
# PurchaseOrder 为采购切片（Build 3）新增的富对象（PO 三方对账工作台 + permission-aware agent）；
# Warehouse 为仓储切片新增的富对象（库存头寸/预留/盘点 + 锚定 R16-R18 + permission-aware agent）。
RICH_OBJECT_TYPES = {"RiskEvent", "AdmissionCase", "Task", "Invoice", "PurchaseOrder", "Warehouse"}

_ONTOLOGY_PATH = Path(__file__).resolve().parent.parent / "ontology" / "control-tower-ontology.json"

# 单组关联对象最多展开的条目数（避免 Customer→300 SalesOrder 之类刷屏；count 仍给全量）。
LINK_ITEM_CAP = 25

# 呈现层金额/成本脱敏字段集（finance/manager 可见，与 mask_cost / Invoice 富工作台 INVOICE_COST_FIELDS
# 同规）：COST_FIELDS 是 CostScenario 的报价/毛利集；再并入发票/基准类账面金额字段。Sku/PO 的
# 目录价 declared_value_usd 属商品目录数据、当前 app 未脱敏，故不纳入（避免过度掩码）。
MONEY_FIELDS = set(COST_FIELDS) | {"amount_usd", "unit_price_usd", "baseline_usd",
                                   "total_usd", "diff_usd", "rate_usd"}


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _table_for(object_type: str) -> str:
    """对象类型 → 库表名：CamelCase → snake_case + 's'（本 ontology 全部对象都满足此约定，
    已对照 sqlite schema 校验：SalesOrderLine→sales_order_lines、CostScenario→cost_scenarios …）。"""
    return _camel_to_snake(object_type) + "s"


def _load_ontology() -> dict:
    with open(_ONTOLOGY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _build_type_meta(onto: dict) -> dict:
    """从 ontology objects 清单派生 type → {table, pk, title_key}（对象清单单一事实源，减少硬编码）。"""
    meta = {}
    for obj in onto.get("objects", []):
        t = obj["type"]
        meta[t] = {
            # 显式 obj["table"] 优先（acronym 类型如 RFQ/RFQLine 的 CamelCase→snake 会误拆为
            # r_f_q，故 ontology 直接声明 table；未声明则回退 _table_for 约定，既有对象零改动）。
            "table": obj.get("table") or _table_for(t),
            "pk": obj["primaryKey"],
            "title_key": obj.get("titleKey") or None,
        }
    return meta


def _build_field_visibility(onto: dict) -> dict:
    """从 ontology sensitiveFieldRules 派生 (object_type, field) → 可见角色集（仅取字面列名规则；
    通配/嵌套规则如 CostScenario '*_usd and margin fields'、Task 'proposal_params.est_cost_usd'
    由 MONEY_FIELDS 规则统一处理）。脱敏本身复用 _can_see_tier/_can_see_cost。"""
    vis = {}
    for rule in onto.get("sensitiveFieldRules", []):
        field = rule.get("field")
        obj = rule.get("object")
        if not field or not obj or "." in field or "*" in field or " " in field:
            continue
        vis[(obj, field)] = set(rule.get("visibleTo", []))
    return vis


_ONTO = _load_ontology()
TYPE_META = _build_type_meta(_ONTO)
_FIELD_VISIBILITY = _build_field_visibility(_ONTO)

# 每个非核心类型的「关键展示字段」（UI 摘要指标用；properties 仍返回全部列）。缺省则回退全部非主键列。
KEY_FIELDS = {
    "Supplier": ["supplier_name", "city", "factory_audit_status", "compliance_docs_status",
                 "uflpa_risk_flag"],
    "Sku": ["sku_name", "category", "sku_status", "supplier_id", "battery_flag"],
    "Customer": ["customer_name", "tier", "business_model", "ior_capability", "credit_terms"],
    "SalesOrder": ["customer_id", "order_date", "status"],
    "SalesOrderLine": ["so_id", "sku_id", "qty", "line_status", "promised_delivery_date"],
    "PurchaseOrder": ["supplier_id", "sku_id", "qty", "status", "expected_ready_date"],
    "Shipment": ["status", "incoterm", "eta_current", "delay_days", "destination_port_locode"],
    "ShipmentMilestone": ["shipment_id", "event_type", "event_time", "source_system"],
    "ShipmentAllocation": ["shipment_id", "so_line_id", "allocated_qty"],
    "Container": ["shipment_id", "container_type", "free_days", "is_primary"],
    "LogisticsPlan": ["admission_case_id", "route_type", "incoterm", "estimated_transit_days",
                      "sla_risk"],
    "CostScenario": ["logistics_plan_id", "scenario_type", "quote_price_usd", "gross_margin_usd",
                     "gross_margin_rate"],
    "ComplianceFinding": ["admission_case_id", "finding_type", "severity", "evidence_status",
                          "recommendation"],
    "ExpectedCost": ["shipment_id", "charge_code", "container_no", "baseline_usd"],
    "InvoiceLine": ["invoice_id", "charge_code", "container_no", "amount_usd"],
    # P1 采购域对象（Build 1）：标准视图展示字段
    "PoLine": ["po_id", "sku_id", "qty", "unit_price_usd", "line_status", "expected_ready_date"],
    "GoodsReceipt": ["po_id", "received_date", "status"],
    "GoodsReceiptLine": ["grn_id", "po_line_id", "received_qty", "accepted_qty",
                         "rejected_qty", "qc_status", "defect_ppm", "received_date"],
    "SupplierInvoice": ["supplier_id", "po_id", "vendor_invoice_no", "total_usd", "status"],
    "SupplierInvoiceLine": ["supplier_invoice_id", "po_line_id", "qty", "unit_price_usd", "amount_usd"],
    # P2 采购富化对象（决策日志 P2）：标准视图展示字段
    "PurchasePayment": ["po_id", "payment_type", "amount_usd", "paid_date", "exposure_status"],
    "SupplierQualification": ["supplier_id", "cert_type", "status", "valid_to", "evidence_status"],
    # W1 仓储域对象（决策日志 W1）：标准视图展示字段
    "Warehouse": ["type", "operator", "region", "capacity_units"],
    "InventoryPosition": ["sku_id", "warehouse_id", "available_qty", "reserved_qty",
                          "in_transit_qty", "safety_stock"],
    "InventoryReservation": ["so_line_id", "inventory_position_id", "qty", "status"],
    "CycleCount": ["inventory_position_id", "warehouse_id", "system_qty", "counted_qty",
                   "variance", "status"],
    # P3 采购富化2 询价对象（决策日志 P3）：标准视图展示字段
    "RFQ": ["sku_id", "status", "created_date"],
    "RFQLine": ["rfq_id", "sku_id", "qty"],
    "Quote": ["rfq_id", "supplier_id", "unit_price_usd", "status"],
}

# 非核心对象注册表：type → (表名, 主键列, 关键展示字段)。表名/主键来自 ontology 派生（TYPE_META），
# 关键字段来自上方 KEY_FIELDS。这是任务要求的 OBJECT_REGISTRY，覆盖除四核心外的全部对象类型。
OBJECT_REGISTRY = {
    t: (TYPE_META[t]["table"], TYPE_META[t]["pk"], KEY_FIELDS.get(t, []))
    for t in TYPE_META if t not in RICH_OBJECT_TYPES
}

# object_relationships registry 未覆盖的声明式 FK 边（对照 registry 现有 relationship_type 补齐，
# 让每个对象都有可导航邻居）。spec = (关系名, 目标类型, 模式, 列名)；
#   fk_out：本对象该列存目标主键（读本行取值）；fk_in：目标表该列存本对象主键（反查目标表）。
FK_SUPPLEMENT = {
    "Supplier": [("supplier_provides", "Sku", "fk_in", "supplier_id"),
                 ("supplier_has_qualification", "SupplierQualification", "fk_in", "supplier_id")],
    "Sku": [("supplier_provides", "Supplier", "fk_out", "supplier_id")],
    "PurchaseOrder": [("po_for_sku", "Sku", "fk_out", "sku_id")],
    "ShipmentAllocation": [("allocation_to_shipment", "Shipment", "fk_out", "shipment_id"),
                           ("allocation_to_line", "SalesOrderLine", "fk_out", "so_line_id")],
    "LogisticsPlan": [("case_has_plan", "AdmissionCase", "fk_out", "admission_case_id"),
                      ("plan_has_scenario", "CostScenario", "fk_in", "logistics_plan_id")],
    "CostScenario": [("plan_has_scenario", "LogisticsPlan", "fk_out", "logistics_plan_id")],
    "ComplianceFinding": [("case_has_finding", "AdmissionCase", "fk_out", "admission_case_id")],
    "InvoiceLine": [("line_bills_container", "Container", "fk_out", "container_no")],
    "Container": [("line_bills_container", "InvoiceLine", "fk_in", "container_no")],
    "Customer": [("case_for_customer", "AdmissionCase", "fk_in", "customer_id")],
    # P2 采购富化：预付款挂 PO、资质挂 Supplier（PurchaseOrder 富工作台走 rich，反向边挂在从表侧；
    # Supplier→SupplierQualification 反向边已并入上方 Supplier 条目）
    "PurchasePayment": [("payment_for_po", "PurchaseOrder", "fk_out", "po_id")],
    "SupplierQualification": [("qualification_for_supplier", "Supplier", "fk_out", "supplier_id")],
    # W1 仓储：Warehouse←position/cycle_count；position→Warehouse/Sku、←reservation/cycle_count；
    # reservation→position/SalesOrderLine；cycle_count→position/Warehouse（对象图可双向导航）
    "Warehouse": [("position_in_warehouse", "InventoryPosition", "fk_in", "warehouse_id"),
                  ("cycle_count_in_warehouse", "CycleCount", "fk_in", "warehouse_id")],
    "InventoryPosition": [("position_in_warehouse", "Warehouse", "fk_out", "warehouse_id"),
                          ("position_for_sku", "Sku", "fk_out", "sku_id"),
                          ("reservation_on_position", "InventoryReservation", "fk_in",
                           "inventory_position_id"),
                          ("cycle_count_on_position", "CycleCount", "fk_in",
                           "inventory_position_id")],
    "InventoryReservation": [("reservation_on_position", "InventoryPosition", "fk_out",
                              "inventory_position_id"),
                             ("reservation_for_line", "SalesOrderLine", "fk_out", "so_line_id")],
    "CycleCount": [("cycle_count_on_position", "InventoryPosition", "fk_out",
                    "inventory_position_id"),
                   ("cycle_count_in_warehouse", "Warehouse", "fk_out", "warehouse_id")],
    # P3 采购富化2：RFQ→RFQLine/Quote/Sku；RFQLine→RFQ/Sku；Quote→RFQ/Supplier（对象图可双向导航）
    "RFQ": [("rfq_has_line", "RFQLine", "fk_in", "rfq_id"),
            ("rfq_has_quote", "Quote", "fk_in", "rfq_id"),
            ("rfq_for_sku", "Sku", "fk_out", "sku_id")],
    "RFQLine": [("rfq_has_line", "RFQ", "fk_out", "rfq_id"),
                ("rfq_line_for_sku", "Sku", "fk_out", "sku_id")],
    "Quote": [("rfq_has_quote", "RFQ", "fk_out", "rfq_id"),
              ("quote_from_supplier", "Supplier", "fk_out", "supplier_id")],
}


def route_object(object_type: str) -> str:
    """对象类型路由：四核心 → 'rich'（富工作台）；其余注册类型 → 'standard'（本兜底视图）；
    未注册类型 → 'unknown'（UI 优雅提示）。"""
    if object_type in RICH_OBJECT_TYPES:
        return "rich"
    if object_type in TYPE_META:
        return "standard"
    return "unknown"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone()[0] > 0


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _is_hidden(object_type: str, field: str, role: str) -> bool:
    """字段级脱敏判定（复用既有 helper）：Customer.tier → _can_see_tier；金额/成本字段 → _can_see_cost；
    其余 ontology 敏感字段（credit_terms/risk_tier/uflpa_risk_flag/conditions）→ 按 visibleTo 集合。"""
    if (object_type, field) == ("Customer", "tier"):
        return not _can_see_tier(role)
    if field in MONEY_FIELDS:
        return not _can_see_cost(role)
    allowed = _FIELD_VISIBILITY.get((object_type, field))
    if allowed is not None:
        return role not in allowed
    return False


def _title_of(conn: sqlite3.Connection, object_type: str, object_id: str) -> str:
    """对象展示标题：有 titleKey 取该字段值，否则回退主键 id。"""
    meta = TYPE_META.get(object_type)
    if not meta or not meta["title_key"]:
        return object_id
    if not _table_exists(conn, meta["table"]):
        return object_id
    if meta["title_key"] not in _columns(conn, meta["table"]):
        return object_id
    row = conn.execute(
        f"SELECT {meta['title_key']} FROM {meta['table']} WHERE {meta['pk']}=?",
        (object_id,)).fetchone()
    return (row[0] if row and row[0] is not None else object_id)


def _titles_bulk(conn: sqlite3.Connection, object_type: str, ids: list) -> dict:
    """批量取一批同类型对象的标题，减少逐条查询。"""
    meta = TYPE_META.get(object_type)
    if not meta or not ids:
        return {}
    if not meta["title_key"] or not _table_exists(conn, meta["table"]) \
            or meta["title_key"] not in _columns(conn, meta["table"]):
        return {i: i for i in ids}
    ph = ",".join("?" * len(ids))
    out = {}
    for row in conn.execute(
            f"SELECT {meta['pk']}, {meta['title_key']} FROM {meta['table']} "
            f"WHERE {meta['pk']} IN ({ph})", ids):
        out[row[0]] = row[1] if row[1] is not None else row[0]
    return {i: out.get(i, i) for i in ids}


def _registry_edges(conn: sqlite3.Connection, object_type: str, object_id: str) -> list:
    """object_relationships registry 双向边：out（本对象为 source）+ in（本对象为 target）。
    复用 M5 关系登记表——engine.graph.explain_path 也读它，此处只做单跳邻居枚举。"""
    edges = []
    if not _table_exists(conn, "object_relationships"):
        return edges
    for row in conn.execute(
            """SELECT relationship_type, target_type, target_id FROM object_relationships
               WHERE source_type=? AND source_id=? ORDER BY relationship_type, target_id""",
            (object_type, object_id)):
        edges.append((row[0], "out", row[1], row[2]))
    for row in conn.execute(
            """SELECT relationship_type, source_type, source_id FROM object_relationships
               WHERE target_type=? AND target_id=? ORDER BY relationship_type, source_id""",
            (object_type, object_id)):
        edges.append((row[0], "in", row[1], row[2]))
    return edges


def _fk_edges(conn: sqlite3.Connection, object_type: str, object_id: str, row: dict) -> list:
    """registry 未覆盖的声明式 FK 边（FK_SUPPLEMENT），让每类对象都有可导航邻居。"""
    edges = []
    for label, target_type, mode, col in FK_SUPPLEMENT.get(object_type, []):
        tmeta = TYPE_META.get(target_type)
        if not tmeta:
            continue
        if mode == "fk_out":
            val = row.get(col)
            if val is not None and val != "":
                edges.append((label, "out", target_type, val))
        elif mode == "fk_in":
            ttable, tpk = tmeta["table"], tmeta["pk"]
            if not _table_exists(conn, ttable) or col not in _columns(conn, ttable):
                continue
            for r in conn.execute(
                    f"SELECT {tpk} FROM {ttable} WHERE {col}=? ORDER BY {tpk}", (object_id,)):
                edges.append((label, "in", target_type, r[0]))
    return edges


def _group_links(conn: sqlite3.Connection, edges: list) -> list:
    """把 (关系, 方向, 目标类型, 目标id) 边去重、按 (目标类型, 关系, 方向) 分组，附标题 + 路由，
    每组截断到 LINK_ITEM_CAP（count 仍为全量）。返回 UI 可直接渲染/导航的关联对象组列表。"""
    groups = {}
    for rel, direction, ttype, tid in edges:
        key = (ttype, rel, direction)
        bucket = groups.setdefault(key, [])
        if tid not in bucket:
            bucket.append(tid)
    out = []
    for (ttype, rel, direction), ids in sorted(groups.items()):
        titles = _titles_bulk(conn, ttype, ids[:LINK_ITEM_CAP])
        out.append({
            "relationship": rel,
            "direction": direction,
            "object_type": ttype,
            "route": route_object(ttype),
            "count": len(ids),
            "items": [{"object_id": i, "title": titles.get(i, i)} for i in ids[:LINK_ITEM_CAP]],
        })
    return out


def build_standard_view(conn: sqlite3.Connection, object_type: str, object_id: str, role: str) -> dict:
    """通用标准对象视图（只读）：属性（按 role 脱敏）+ 关联对象（registry 双向边 + 已知 FK）。

    - 覆盖除四核心外的全部对象类型；核心类型也可调用（返回 route='rich' + note 提示走富工作台）。
    - 脱敏复用 agent.tools 既有 helper（_can_see_tier/_can_see_cost/COST_FIELDS/MASK），不复制权限。
    - **只读**：返回结构不含任何 action / available_actions 键，read_only 恒 True，不涉及 agent。
    - 未知对象类型 / 不存在的 id 优雅返回 {"error": ...}。
    """
    meta = TYPE_META.get(object_type)
    if not meta:
        return {"error": f"未知对象类型 {object_type}（不在 ontology 对象清单中）", "read_only": True}
    table, pk = meta["table"], meta["pk"]
    if not _table_exists(conn, table):
        return {"error": f"对象类型 {object_type} 对应表 {table} 不存在", "read_only": True}
    row = conn.execute(f"SELECT * FROM {table} WHERE {pk}=?", (object_id,)).fetchone()
    if row is None:
        return {"error": f"{object_type} {object_id} 不存在", "read_only": True}
    row = dict(row)

    # 属性：全部列，逐字段按 role 脱敏
    properties, masked_fields = {}, []
    for field, value in row.items():
        if _is_hidden(object_type, field, role):
            properties[field] = MASK
            masked_fields.append(field)
        else:
            properties[field] = value

    key_fields = [f for f in KEY_FIELDS.get(object_type, []) if f in row] \
        or [c for c in row if c != pk]

    edges = _registry_edges(conn, object_type, object_id) + \
        _fk_edges(conn, object_type, object_id, row)
    linked_objects = _group_links(conn, edges)

    view = {
        "object_type": object_type,
        "object_id": object_id,
        "route": route_object(object_type),
        "title": _title_of(conn, object_type, object_id),
        "properties": properties,
        "key_fields": key_fields,
        "masked_fields": masked_fields,
        "linked_objects": linked_objects,
        "role": role,
        "read_only": True,  # 标准视图只读：不暴露任何 action、不涉及 agent（富工作台才有）
    }
    if object_type in RICH_OBJECT_TYPES:
        view["note"] = f"{object_type} 有富工作台（route=rich）；本标准视图仅作只读兜底导航。"
    return view


def navigable_types() -> list:
    """UI 对象浏览器可选的对象类型（全部 26 类，按类型名排序）。核心类型也在列——route_object 决定
    进富工作台还是标准视图。"""
    return sorted(TYPE_META.keys())


# ---------- Streamlit 渲染（延迟 import st；仅 UI 用，纯逻辑测试不触及）----------
def render_standard_view(conn, object_type, object_id, role, render_table, on_navigate=None):
    """渲染通用标准视图（只读）：标题 + 关键指标 + 全属性表（脱敏）+ 关联对象组（可点击导航）。

    on_navigate(object_type, object_id)：点击关联对象时的回调（UI 侧写 session_state + rerun）。
    **无任何 action / agent**——标准视图只读。render_table 复用 streamlit_app 的暗色表格渲染器。
    """
    import streamlit as st

    view = build_standard_view(conn, object_type, object_id, role)
    if "error" in view:
        st.warning(view["error"])
        return
    st.markdown(f"### 📄 标准对象视图 · {view['object_type']} · {view['object_id']}")
    st.caption("Foundry「标准对象视图」兜底：非核心对象自动生成的只读视图（属性 + 关联对象，可导航）。"
               "无动作、无 agent——富工作台（RiskEvent/AdmissionCase/Task/Invoice）才有 action 与对象级 AI。")
    if view.get("note"):
        st.info(view["note"] + "（如需动作/AI，请到对应富工作台标签操作。）")

    title = view["title"]
    if title and title != view["object_id"]:
        st.markdown(f"**{title}**")

    # 关键指标（key_fields，脱敏后的值）
    kfs = [f for f in view["key_fields"]][:5]
    if kfs:
        cols = st.columns(len(kfs))
        for col, f in zip(cols, kfs):
            col.metric(f, "" if view["properties"].get(f) is None else str(view["properties"].get(f)))

    # 全部属性（脱敏）
    st.markdown("**属性（按角色脱敏）**")
    render_table([{"字段": f, "值": ("" if v is None else v)} for f, v in view["properties"].items()])
    if view["masked_fields"]:
        st.caption(f"🔒 对角色 {role} 脱敏字段：{'、'.join(view['masked_fields'])}"
                   "（复用 mask_tier/mask_cost 同规：finance/manager 见成本、cs/manager 见 tier 等）。")

    # 关联对象（可点击导航到其视图）
    st.markdown("**关联对象（对象图单跳邻居，可点击导航）**")
    if not view["linked_objects"]:
        st.caption("（无登记的关联对象）")
    for grp in view["linked_objects"]:
        arrow = "→" if grp["direction"] == "out" else "←"
        st.markdown(f"`{grp['relationship']}` {arrow} **{grp['object_type']}**"
                    f"（{grp['route']}）· 共 {grp['count']} 个"
                    + ("，仅列前 %d" % LINK_ITEM_CAP if grp["count"] > LINK_ITEM_CAP else ""))
        for it in grp["items"]:
            label = f"{it['object_id']}" + (f"｜{it['title']}" if it["title"] != it["object_id"] else "")
            key = f"objnav_{object_type}_{object_id}_{grp['relationship']}_{grp['direction']}_{it['object_id']}"
            if on_navigate is not None:
                if st.button(f"{arrow} {label}", key=key):
                    on_navigate(grp["object_type"], it["object_id"])
            else:
                st.markdown(f"- {arrow} {label}")
