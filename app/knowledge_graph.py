"""知识图谱呈现层（纯只读切片）：本体地图（类型级）+ 对象邻域（实例级 trace 视图）。

设计动机（≤5 行「为什么这样建」）：
- 决策纪要 §2：上帝视角是理解/监督工具不是操作台；理解的原子是一条 trace——本模块只画图不给动作，
  杀手价值是让"同一对象在多角色手里传递"肉眼可见（风险→任务→协调 一条线）。
- 零新依赖：产出 DOT 字符串交 st.graphviz_chart 前端渲染（Streamlit 1.53 自带，无需 graphviz 二进制）。
- 类型图数据源分层：object_relationships 登记表 DISTINCT 提炼的**实存关系**画实线，ontology links
  声明关系画虚线——34 个对象类型全为节点，按业务域着色分簇（"系统的地图"，服务新人上手）。
- 邻域图参照 engine/graph.explain_path 的 BFS/visited/深度上限，双向化 + 并入 standard_object_view
  的 FK_SUPPLEMENT 声明边（同一套"关联对象"口径）；节点只含 类型/ID/状态 三样，绝不放金额/成本/tier。
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path

try:  # 包上下文（python3 -m app.*）
    from app import standard_object_view as sov
    from app.data_scope import (audit_in_scope, audit_region_index,
                                risk_in_region_scope, scope_for_role)
except ImportError:  # streamlit run app/streamlit_app.py：脚本目录在 sys.path
    import standard_object_view as sov
    from data_scope import (audit_in_scope, audit_region_index,
                            risk_in_region_scope, scope_for_role)

_ONTOLOGY_PATH = Path(__file__).resolve().parent.parent / "ontology" / "control-tower-ontology.json"

# ---------- 业务域分组与配色（34 类型 → 7 域；暗色主题可读：深色填充 + 亮字）----------
# RiskEvent/Task 由延误场景诞生、现已跨场景复用（采购/仓储风险同样生成），着延误运营色并在图例注明。
DOMAINS = ("delay", "cost", "admission", "procurement", "warehouse", "coordination", "master")
DOMAIN_LABELS = {
    "delay": "延误运营", "cost": "费用稽核", "admission": "准入合规",
    "procurement": "采购", "warehouse": "仓储库存", "coordination": "协调回路",
    "master": "共享主数据",
}
DOMAIN_COLORS = {
    "delay": "#155e63", "cost": "#6b4a12", "admission": "#4a2f5e",
    "procurement": "#14532d", "warehouse": "#7c2d12", "coordination": "#701a3c",
    "master": "#1e3a5f",
}
DOMAIN_OF = {
    # 延误运营（含跨场景复用的 RiskEvent/Task）
    "Shipment": "delay", "ShipmentMilestone": "delay", "ShipmentAllocation": "delay",
    "Container": "delay", "RiskEvent": "delay", "Task": "delay",
    # 费用稽核
    "Invoice": "cost", "InvoiceLine": "cost", "ExpectedCost": "cost",
    # 准入合规
    "AdmissionCase": "admission", "ComplianceFinding": "admission",
    "LogisticsPlan": "admission", "CostScenario": "admission",
    # 采购
    "PurchaseOrder": "procurement", "PoLine": "procurement", "GoodsReceipt": "procurement",
    "GoodsReceiptLine": "procurement", "SupplierInvoice": "procurement",
    "SupplierInvoiceLine": "procurement", "PurchasePayment": "procurement",
    "SupplierQualification": "procurement", "RFQ": "procurement", "RFQLine": "procurement",
    "Quote": "procurement",
    # 仓储库存
    "Warehouse": "warehouse", "InventoryPosition": "warehouse",
    "InventoryReservation": "warehouse", "CycleCount": "warehouse",
    # 协调回路
    "CoordinationThread": "coordination",
    # 共享主数据
    "Supplier": "master", "Sku": "master", "Customer": "master",
    "SalesOrder": "master", "SalesOrderLine": "master",
}

# 邻域节点标签允许的「状态列」白名单（按序取第一个存在的列）。节点只呈现 类型+ID+状态 三样——
# 金额/成本/tier/价格等敏感字段在结构上就进不了 DOT（不读、不拼）。
STATUS_FIELD_CANDIDATES = ("status", "state", "line_status", "sku_status",
                           "qc_status", "evidence_status", "exposure_status")

# 富对象锚定边补丁：standard_object_view.FK_SUPPLEMENT 只声明了非核心对象的边；此处补齐富对象
# （Task/RiskEvent/AdmissionCase）与采购单据链的物理外键边，关系名全部取自 ontology links 既有
# linkType（不发明新关系）。格式与 FK_SUPPLEMENT 一致：(关系名, 目标类型, 模式, 列名)。
RICH_FK_SUPPLEMENT = {
    "Task": [("task_handles_risk", "RiskEvent", "fk_out", "risk_event_id")],
    "RiskEvent": [("risk_on_po", "PurchaseOrder", "fk_out", "po_id"),
                  ("risk_on_supplier", "Supplier", "fk_out", "supplier_id"),
                  ("risk_on_warehouse", "Warehouse", "fk_out", "warehouse_id")],
    "AdmissionCase": [("case_has_sku", "Sku", "fk_out", "sku_id")],
    "PoLine": [("po_has_line", "PurchaseOrder", "fk_out", "po_id"),
               ("po_line_for_sku", "Sku", "fk_out", "sku_id")],
    "GoodsReceipt": [("grn_for_po", "PurchaseOrder", "fk_out", "po_id")],
    "GoodsReceiptLine": [("grn_has_line", "GoodsReceipt", "fk_out", "grn_id"),
                         ("grn_line_for_po_line", "PoLine", "fk_out", "po_line_id")],
    "SupplierInvoice": [("supplier_invoice_for_po", "PurchaseOrder", "fk_out", "po_id"),
                        ("supplier_invoice_from_supplier", "Supplier", "fk_out", "supplier_id")],
    "SupplierInvoiceLine": [("supplier_invoice_has_line", "SupplierInvoice", "fk_out",
                             "supplier_invoice_id"),
                            ("supplier_invoice_line_for_po_line", "PoLine", "fk_out",
                             "po_line_id")],
}

# 邻域图节点数上限（呈现层防刷屏，同 standard_object_view.LINK_ITEM_CAP 精神；超限截断并标注）。
MAX_NEIGHBORHOOD_NODES = 60


# ---------- DOT 基础 ----------
def _dot_quote(s) -> str:
    """DOT 双引号字符串安全转义：反斜杠、双引号、换行——对象 ID 含特殊字符不破坏 DOT 语法。"""
    text = "" if s is None else str(s)
    text = (text.replace("\\", "\\\\").replace('"', '\\"')
                .replace("\r", "\\n").replace("\n", "\\n"))
    return f'"{text}"'


def _load_ontology() -> dict:
    with open(_ONTOLOGY_PATH, encoding="utf-8") as fh:
        return json.load(fh)


_ONTO = _load_ontology()
OBJECT_TYPES = [o["type"] for o in _ONTO.get("objects", [])]
# linkType → (source_type, target_type)：声明边清单 + 邻域边的箭头方向对齐本体声明方向
LINK_DIRECTION = {l["linkType"]: (l["source"], l["target"]) for l in _ONTO.get("links", [])}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone()[0] > 0


def _registry_triples(conn: sqlite3.Connection) -> list:
    """object_relationships 登记表 DISTINCT (source_type, target_type, relationship_type)——
    实例数据中真实存在的关系（任务要求的优先数据源）。表缺失（空库前）→ 空集。"""
    if not _table_exists(conn, "object_relationships"):
        return []
    return [tuple(r) for r in conn.execute(
        """SELECT DISTINCT source_type, target_type, relationship_type
           FROM object_relationships ORDER BY source_type, target_type, relationship_type""")]


# ---------- 视图一：本体地图（类型级）----------
def build_ontology_map_dot(conn: sqlite3.Connection) -> dict:
    """类型级本体地图：34 个对象类型为节点（按业务域着色分簇），关系为边。

    边分两层：实线 = object_relationships 登记表 DISTINCT 提炼（实例数据实存）；
    虚线 = ontology links 声明、登记表暂未实证。返回 {dot, nodes, edges, registry_edges,
    declared_only_edges}——测试与 UI 计数同源。纯读，不写库。
    """
    registry = _registry_triples(conn)
    registry_keys = {(s, t, r) for s, t, r in registry}

    # 边集合：实存边（实线）∪ 未实证声明边（虚线）；节点 = 34 全类型 ∪ 边端点（防漏）
    edges = [(s, t, r, "solid") for s, t, r in registry]
    for link_type, (src, tgt) in sorted(LINK_DIRECTION.items()):
        if (src, tgt, link_type) not in registry_keys:
            edges.append((src, tgt, link_type, "dashed"))
    node_set = list(OBJECT_TYPES)
    for s, t, _r, _st in edges:
        for n in (s, t):
            if n not in node_set:
                node_set.append(n)

    lines = [
        "digraph ontology_map {",
        '  graph [bgcolor="transparent", rankdir="LR", splines="true", pad="0.2",'
        ' nodesep="0.3", ranksep="0.75", fontname="Helvetica"];',
        '  node [shape="box", style="rounded,filled", fontname="Helvetica", fontsize=11,'
        ' fontcolor="#e8f4f3", color="#5b7f84", penwidth=1];',
        '  edge [color="#7c9a9e", fontcolor="#a8c5c9", fontsize=8, fontname="Helvetica",'
        ' arrowsize=0.55];',
    ]
    # 业务域分簇：cluster 布局让"域"成为第一眼结构（新人先看域，再看域内对象与跨域连线）
    for dom in DOMAINS:
        members = [t for t in node_set if DOMAIN_OF.get(t) == dom]
        if not members:
            continue
        lines.append(f"  subgraph cluster_{dom} {{")
        lines.append(f'    label={_dot_quote(DOMAIN_LABELS[dom])}; fontcolor="#e8f4f3";'
                     ' fontsize=13; color="#5b7f84"; style="rounded";')
        for t in members:
            lines.append(f"    {_dot_quote(t)} [fillcolor={_dot_quote(DOMAIN_COLORS[dom])}];")
        lines.append("  }")
    for t in node_set:  # 未归域的类型（防御：DOMAIN_OF 漏配时仍可见）
        if DOMAIN_OF.get(t) is None:
            lines.append(f"  {_dot_quote(t)} [fillcolor=\"#37474f\"];")
    for s, t, r, style in edges:
        lines.append(f"  {_dot_quote(s)} -> {_dot_quote(t)}"
                     f" [label={_dot_quote(r)}, style={_dot_quote(style)}];")
    lines.append("}")
    return {
        "dot": "\n".join(lines),
        "nodes": len(node_set),
        "edges": len(edges),
        "registry_edges": len(registry),
        "declared_only_edges": len(edges) - len(registry),
    }


# ---------- 视图二：对象邻域（实例级 trace）----------
def _physical_fk_specs() -> list:
    """把 FK_SUPPLEMENT + RICH_FK_SUPPLEMENT 归一为物理外键边规格：
    (holder_type, holder_table, holder_pk, col, ref_type, rel)——holder 表的 col 列存 ref 的主键。
    fk_out(A 声明) → holder=A；fk_in(A 声明) → holder=目标类型。去重后双向可查（站在任一端都能枚举）。"""
    specs, seen = [], set()
    merged = {}
    for src in (sov.FK_SUPPLEMENT, RICH_FK_SUPPLEMENT):
        for t, entries in src.items():
            merged.setdefault(t, []).extend(entries)
    for a_type, entries in merged.items():
        for rel, b_type, mode, col in entries:
            holder, ref = (a_type, b_type) if mode == "fk_out" else (b_type, a_type)
            hmeta = sov.TYPE_META.get(holder)
            if not hmeta or ref not in sov.TYPE_META:
                continue
            key = (holder, col, ref, rel)
            if key in seen:
                continue
            seen.add(key)
            specs.append((holder, hmeta["table"], hmeta["pk"], col, ref, rel))
    return sorted(specs)


_FK_SPECS = _physical_fk_specs()


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _oriented(rel: str, a_type: str, a_id: str, b_type: str, b_id: str) -> tuple:
    """边方向对齐 ontology 声明方向（LINK_DIRECTION）；未声明（derived_* 等）保持 a→b 物理方向。"""
    if LINK_DIRECTION.get(rel) == (b_type, a_type):
        return (b_type, b_id, a_type, a_id, rel)
    return (a_type, a_id, b_type, b_id, rel)


def _neighbors(conn: sqlite3.Connection, node_type: str, node_id: str) -> list:
    """枚举一个实例节点的全部单跳邻边（无向）：registry 双向 + 物理 FK 规格双向。
    返回已定向的 (src_type, src_id, tgt_type, tgt_id, rel) 列表，排序保证 DOT 确定性。"""
    edges = set()
    if _table_exists(conn, "object_relationships"):
        for r in conn.execute(
                """SELECT relationship_type, target_type, target_id FROM object_relationships
                   WHERE source_type=? AND source_id=?""", (node_type, node_id)):
            edges.add((node_type, node_id, r[1], r[2], r[0]))
        for r in conn.execute(
                """SELECT relationship_type, source_type, source_id FROM object_relationships
                   WHERE target_type=? AND target_id=?""", (node_type, node_id)):
            edges.add((r[1], r[2], node_type, node_id, r[0]))
    row = None
    for holder, table, pk, col, ref, rel in _FK_SPECS:
        if holder == node_type:  # 本表 col 指向 ref：读本行取值
            if not _table_exists(conn, table):
                continue
            if row is None:
                cur = conn.execute(f"SELECT * FROM {table} WHERE {pk}=?", (node_id,))
                fetched = cur.fetchone()
                row = dict(zip([c[0] for c in cur.description], fetched)) if fetched else {}
            val = row.get(col)
            if val is not None and val != "":
                edges.add(_oriented(rel, node_type, node_id, ref, val))
        elif ref == node_type:  # 对方表 col 指向本对象：反查对方表
            if not _table_exists(conn, table) or col not in _columns(conn, table):
                continue
            for r in conn.execute(f"SELECT {pk} FROM {table} WHERE {col}=? ORDER BY {pk}",
                                  (node_id,)):
                edges.add(_oriented(rel, holder, r[0], node_type, node_id))
    return sorted(edges)


def _status_by_node(conn: sqlite3.Connection, nodes: list) -> dict:
    """批量取每个节点的状态（STATUS_FIELD_CANDIDATES 白名单第一个存在的列）。
    仅此白名单列可进节点标签——敏感字段（金额/成本/tier）结构上不可达。"""
    by_type: dict = {}
    for t, i in nodes:
        by_type.setdefault(t, []).append(i)
    out = {}
    for t, ids in by_type.items():
        meta = sov.TYPE_META.get(t)
        if not meta or not _table_exists(conn, meta["table"]):
            continue
        cols = _columns(conn, meta["table"])
        status_col = next((c for c in STATUS_FIELD_CANDIDATES if c in cols), None)
        if not status_col:
            continue
        ph = ",".join("?" * len(ids))
        for r in conn.execute(
                f"SELECT {meta['pk']}, {status_col} FROM {meta['table']} "
                f"WHERE {meta['pk']} IN ({ph})", ids):
            if r[1] is not None and r[1] != "":
                out[(t, r[0])] = str(r[1])
    return out


def build_neighborhood_dot(conn: sqlite3.Connection, obj_type: str, obj_id: str, role: str,
                           actor: str = None, max_hops: int = 2,
                           max_nodes: int = MAX_NEIGHBORHOOD_NODES) -> dict:
    """实例级对象邻域（"一件事的世界"）：以 (obj_type, obj_id) 为中心 ≤max_hops 跳 BFS。

    - 遍历参照 engine/graph.explain_path（deque + visited + 深度上限），双向化；边=registry ∪ FK 规格。
    - 数据范围：复用 audit_region_index/audit_in_scope（审计日志同一套 scope 口径）——中心越域直接
      拒绝（out_of_scope），邻域内越域节点被过滤；manager=all 全量。actor 仅备签名兼容/呈现，
      区域取自 DEMO_ROSTER（与风险队列 scope_for_role(role,'team') 同规）。
    - 节点标签只有 类型+ID+状态；中心节点高亮。返回 {dot, nodes, edges, truncated, out_of_scope, error}。
    """
    empty = {"dot": None, "nodes": 0, "edges": 0, "truncated": False,
             "out_of_scope": False, "error": None}
    if not obj_type or not obj_id:
        return {**empty, "error": "缺少对象类型或对象 ID"}
    meta = sov.TYPE_META.get(obj_type)
    if not meta:
        return {**empty, "error": f"未知对象类型 {obj_type}（不在 ontology 对象清单中）"}
    if not _table_exists(conn, meta["table"]) or conn.execute(
            f"SELECT 1 FROM {meta['table']} WHERE {meta['pk']}=?", (obj_id,)).fetchone() is None:
        return {**empty, "error": f"{obj_type} {obj_id} 不存在"}

    scope = scope_for_role(role, "team")  # manager 由 scope_predicate 强制 all（监督全量）
    region_index = audit_region_index(conn) if scope.mode != "all" else {}
    if not audit_in_scope(scope, obj_id, region_index):
        return {**empty, "out_of_scope": True,
                "error": f"{obj_type} {obj_id} 不在当前角色数据范围（{scope.label}）内"}

    center = (obj_type, obj_id)
    hops = {center: 0}
    order = [center]
    edge_set = set()
    truncated = False
    queue = deque([center])
    while queue:
        node = queue.popleft()
        if hops[node] >= max_hops:
            continue
        for src_t, src_i, tgt_t, tgt_i, rel in _neighbors(conn, node[0], node[1]):
            other = (tgt_t, tgt_i) if (src_t, src_i) == node else (src_t, src_i)
            # 数据范围过滤：越域节点不进图（audit_in_scope 未覆盖的对象兜底本区域，不过度隐藏）
            if not audit_in_scope(scope, other[1], region_index):
                continue
            if other not in hops:
                if len(hops) >= max_nodes:
                    truncated = True
                    continue
                hops[other] = hops[node] + 1
                order.append(other)
                queue.append(other)
            edge_set.add((src_t, src_i, tgt_t, tgt_i, rel))

    # 只保留两端都入图的边（截断/越域丢弃的端点，其边一并丢弃）
    edges = sorted(e for e in edge_set if (e[0], e[1]) in hops and (e[2], e[3]) in hops)
    status = _status_by_node(conn, order)

    lines = [
        "digraph neighborhood {",
        '  graph [bgcolor="transparent", rankdir="LR", splines="true", pad="0.2",'
        ' nodesep="0.3", ranksep="0.7", fontname="Helvetica"];',
        '  node [shape="box", style="rounded,filled", fontname="Helvetica", fontsize=10,'
        ' fontcolor="#e8f4f3", color="#5b7f84", penwidth=1];',
        '  edge [color="#7c9a9e", fontcolor="#a8c5c9", fontsize=8, fontname="Helvetica",'
        ' arrowsize=0.55];',
    ]
    for t, i in order:
        label = f"{t}\n{i}" + (f"\n{status[(t, i)]}" if (t, i) in status else "")
        fill = DOMAIN_COLORS.get(DOMAIN_OF.get(t, ""), "#37474f")
        attrs = f"label={_dot_quote(label)}, fillcolor={_dot_quote(fill)}"
        if (t, i) == center:  # 中心节点高亮：亮青描边加粗（配色同 app 主题 --cyan）
            attrs += ', color="#22d7e6", penwidth=3'
        lines.append(f"  {_dot_quote(t + '|' + str(i))} [{attrs}];")
    for src_t, src_i, tgt_t, tgt_i, rel in edges:
        lines.append(f"  {_dot_quote(src_t + '|' + str(src_i))} -> "
                     f"{_dot_quote(tgt_t + '|' + str(tgt_i))} [label={_dot_quote(rel)}];")
    lines.append("}")
    return {"dot": "\n".join(lines), "nodes": len(order), "edges": len(edges),
            "truncated": truncated, "out_of_scope": False, "error": None}


def open_risk_candidates(conn: sqlite3.Connection, role: str) -> list:
    """邻域起点默认候选：open 状态 RiskEvent（含无货运锚点的采购/仓储风险），按角色数据范围过滤
    （与风险队列同规：scope_for_role(role,'team') + 目的地 region；manager 全量）。纯读。"""
    if not _table_exists(conn, "risk_events"):
        return []
    scope = scope_for_role(role, "team")
    out = []
    for r in conn.execute(
            """SELECT r.risk_event_id, r.rule_id, r.type, r.severity, s.destination_port_locode
               FROM risk_events r LEFT JOIN shipments s ON s.shipment_id=r.shipment_id
               WHERE r.status='open' ORDER BY r.risk_event_id"""):
        if risk_in_region_scope(scope, r[4]):
            out.append({"risk_event_id": r[0], "rule_id": r[1], "type": r[2], "severity": r[3]})
    return out


# ---------- Streamlit 渲染（延迟 import st；纯逻辑测试不触及）----------
def render_knowledge_graph_tab(db, role: str) -> None:
    """知识图谱 tab（只读呈现层）：本体地图 / 对象邻域 两个视图。db 为连接工厂（streamlit_app.db）。"""
    import streamlit as st

    st.caption("知识图谱（只读理解/监督视图）：**本体地图**看\"系统的地图\"（34 个对象类型怎么连），"
               "**对象邻域**看\"一件事的世界\"（一个对象 2 跳内的实例关系 trace）。"
               "图上节点只含 类型 / ID / 状态，无金额、成本、客户等级等敏感字段。")
    mode = st.radio("视图", ["map", "nbr"], horizontal=True, key="kg_view_mode",
                    format_func=lambda m: {"map": "本体地图（类型级）",
                                           "nbr": "对象邻域（实例级）"}[m])

    if mode == "map":
        with db() as con:
            res = build_ontology_map_dot(con)
        st.graphviz_chart(res["dot"], width="stretch")
        st.caption(f"节点 {res['nodes']} 类对象 · 边 {res['edges']} 条"
                   f"（实线 {res['registry_edges']} 条=实例数据实存关系 object_relationships 登记表；"
                   f"虚线 {res['declared_only_edges']} 条=本体声明关系）")
        legend = " ".join(
            f"<span style='display:inline-block;width:0.72em;height:0.72em;border-radius:2px;"
            f"background:{DOMAIN_COLORS[d]};margin-right:0.25em'></span>{DOMAIN_LABELS[d]}"
            for d in DOMAINS)
        st.markdown(f"<div style='font-size:0.8rem'>图例：{legend}"
                    "（RiskEvent/Task 跨场景复用，着延误运营色）</div>", unsafe_allow_html=True)
        st.caption("本图只读，操作请回各工作台（风险队列 / 任务台 / 费用 / 采购 / 准入 / 协调）。")
        return

    # ---- 对象邻域（实例级）----
    types = sov.navigable_types()
    default_type = "RiskEvent" if "RiskEvent" in types else types[0]
    otype = st.selectbox("起点对象类型", types, index=types.index(default_type),
                         key="kg_obj_type")

    with db() as con:
        if otype == "RiskEvent":
            cands = open_risk_candidates(con, role)
            ids = [c["risk_event_id"] for c in cands]
            labels = {c["risk_event_id"]:
                      f"{c['risk_event_id']} · {c['rule_id']} {c['type']}（{c['severity']}）"
                      for c in cands}
            hint = "默认候选 = 当前 open 状态风险事件（按角色数据范围过滤）"
        else:
            meta = sov.TYPE_META[otype]
            all_ids = [r[0] for r in con.execute(
                f"SELECT {meta['pk']} FROM {meta['table']} ORDER BY {meta['pk']} LIMIT 300")] \
                if _table_exists(con, meta["table"]) else []
            scope = scope_for_role(role, "team")
            if scope.mode != "all" and all_ids:
                ridx = audit_region_index(con)
                all_ids = [i for i in all_ids if audit_in_scope(scope, i, ridx)]
            ids, labels = all_ids, {}
            hint = "（列前 300 个；按角色数据范围过滤）"
        if not ids:
            st.info(f"{otype} 当前无可选实例（或均在数据范围之外）。")
            return
        oid = st.selectbox(f"起点对象 ID {hint}", ids, key=f"kg_obj_id_{otype}",
                           format_func=lambda i: labels.get(i, i))
        hops = st.radio("跳数（邻域半径）", [1, 2], index=1, horizontal=True, key="kg_hops")
        res = build_neighborhood_dot(con, otype, oid, role, max_hops=hops)

    if res["error"]:
        (st.warning if res["out_of_scope"] else st.info)(res["error"])
        return
    st.graphviz_chart(res["dot"], width="stretch")
    trunc = f"（已达 {MAX_NEIGHBORHOOD_NODES} 节点上限，图有截断）" if res["truncated"] else ""
    st.caption(f"中心 {otype} {oid} · {hops} 跳内节点 {res['nodes']} 个 · 边 {res['edges']} 条{trunc}")
    st.caption("图例：节点＝类型+ID+状态（颜色=业务域，青色粗框=中心对象）；边＝对象关系"
               "（object_relationships 登记 + 本体声明外键）；不展示金额 / 成本 / 客户等级等敏感字段。")
    st.caption("本图只读，操作请回各工作台（风险队列 / 任务台 / 费用 / 采购 / 准入 / 协调）。")
