"""知识图谱呈现层（纯只读切片）：本体地图（类型级）+ 对象邻域（实例级 trace 视图）。

设计动机（≤5 行「为什么这样建」）：
- 决策纪要 §2：上帝视角是理解/监督工具不是操作台；理解的原子是一条 trace——本模块只画图不给动作，
  杀手价值是让"同一对象在多角色手里传递"肉眼可见（风险→任务→协调 一条线）。
- 零新依赖：产出 DOT 字符串交 st.graphviz_chart 前端渲染（Streamlit 1.53 自带，无需 graphviz 二进制）。
- 类型图数据源分层：object_relationships 登记表 DISTINCT 提炼的**实存关系**画实线，ontology links
  声明关系画虚线——34 个对象类型全为节点，按业务域着色分簇（"系统的地图"，服务新人上手）。
- 邻域图参照 engine/graph.explain_path 的 BFS/visited/深度上限，双向化 + 并入 standard_object_view
  的 FK_SUPPLEMENT 声明边（同一套"关联对象"口径）；节点只含 类型/ID/状态 三样，绝不放金额/成本/tier。
- 三刀改造（Daniel 反馈"难理解/不清爽"）：① 同关系同类型邻居 >4 收成「类型 ×N」聚合节点（主干
  对象永不聚合）② rankdir=LR 左→右像一条河 ③ 图上方大白话旁白（现查现算、按角色脱敏金额）。
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path

try:  # 包上下文（python3 -m app.*）
    from app import standard_object_view as sov
    from app.data_scope import audit_in_scope, audit_region_index, scope_for_role
except ImportError:  # streamlit run app/streamlit_app.py：脚本目录在 sys.path
    import standard_object_view as sov
    from data_scope import audit_in_scope, audit_region_index, scope_for_role

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

# 主干对象类型：业务故事的主角（风险→任务→协调、货→订单行→客户、采购单），永不被聚合收起——
# 聚合只收"同型配角"（里程碑/预期费用/发票行等），主角必须逐个可见才能讲一条 trace。
BACKBONE_TYPES = frozenset({"RiskEvent", "Task", "SalesOrderLine", "Customer",
                            "CoordinationThread", "Shipment", "PurchaseOrder"})

# 同一展开点上「同关系、同类型」的新邻居数超过此值 → 收成一个「类型 ×N」聚合节点
# （双边框+虚线区分单节点；聚合节点是终点，不再向外扩展下一跳）。
AGG_FANOUT_THRESHOLD = 4

# 旁白金额可见角色——与 streamlit_app.mask_cost 完全同口径（finance/manager）；
# 无权角色的金额片段整体不出现（不是打码是缺席，与图内"敏感字段结构上不可达"同一红线）。
AMOUNT_VISIBLE_ROLES = ("finance", "manager")


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
    - 收起重复扇出：同一展开点上「同关系、同类型」新邻居 > AGG_FANOUT_THRESHOLD 且非主干类型
      （BACKBONE_TYPES）→ 收成一个「类型 ×N」聚合节点（双边框虚线），不再向外扩展下一跳。
    - 节点标签只有 类型+ID+状态；中心节点高亮。返回 {dot, nodes, edges, truncated, out_of_scope,
      error, agg_nodes, collapsed_members}——nodes/edges 含聚合节点/边（与 DOT 同源）。
    """
    empty = {"dot": None, "nodes": 0, "edges": 0, "truncated": False,
             "out_of_scope": False, "error": None, "agg_nodes": 0, "collapsed_members": 0}
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
    edge_set = set()      # 实节点↔实节点的边（5 元组）
    agg_nodes = {}        # 聚合节点 key → {"type", "count"}（key 编码展开点+关系+类型+方向）
    agg_edges = set()     # 触及聚合节点的边：(src_key, tgt_key, rel)，key=DOT 节点 id
    absorbed = {}         # 被聚合吸收的成员 (type, id) → 聚合节点 key（后续发现的边改指聚合节点）
    collapsed = 0
    truncated = False
    queue = deque([center])

    def _key(t, i):
        return f"{t}|{i}"

    while queue:
        node = queue.popleft()
        if hops[node] >= max_hops:
            continue
        # 按 (关系, 邻居类型, 方向) 分组——聚合判定的粒度即"同关系、同类型的重复扇出"
        groups = {}
        for e in _neighbors(conn, node[0], node[1]):
            src_t, src_i, tgt_t, tgt_i, rel = e
            other = (tgt_t, tgt_i) if (src_t, src_i) == node else (src_t, src_i)
            if other == node:  # 防御自环
                continue
            # 数据范围过滤：越域节点不进图（audit_in_scope 未覆盖的对象兜底本区域，不过度隐藏）
            if not audit_in_scope(scope, other[1], region_index):
                continue
            outward = (src_t, src_i) == node
            groups.setdefault((rel, other[0], outward), []).append((other, e))
        for (rel, other_type, outward), items in sorted(groups.items()):
            fresh = sorted(x for x in items if x[0] not in hops and x[0] not in absorbed)
            for other, e in items:
                if other in hops:        # 已入图的实节点：边照画
                    edge_set.add(e)
                elif other in absorbed:  # 已被别处聚合吸收：边改指其聚合节点（不复活单节点）
                    a, b = ((_key(*node), absorbed[other]) if outward
                            else (absorbed[other], _key(*node)))
                    agg_edges.add((a, b, rel))
            if not fresh:
                continue
            if len(fresh) > AGG_FANOUT_THRESHOLD and other_type not in BACKBONE_TYPES:
                # 收起重复扇出：>4 个同关系同类型新邻居 → 单个「类型 ×N」聚合节点（终点，不扩展）
                agg_key = f"agg|{node[0]}|{node[1]}|{rel}|{other_type}|" \
                          f"{'out' if outward else 'in'}"
                if len(hops) + len(agg_nodes) >= max_nodes:
                    truncated = True
                    continue
                agg_nodes[agg_key] = {"type": other_type, "count": len(fresh)}
                a, b = ((_key(*node), agg_key) if outward else (agg_key, _key(*node)))
                agg_edges.add((a, b, rel))
                for other, _e in fresh:
                    absorbed[other] = agg_key
                collapsed += len(fresh)
            else:
                for other, e in fresh:
                    if other not in hops:
                        if len(hops) + len(agg_nodes) >= max_nodes:
                            truncated = True
                            continue
                        hops[other] = hops[node] + 1
                        order.append(other)
                        queue.append(other)
                    edge_set.add(e)

    # 只保留两端都入图的边（截断/越域丢弃的端点，其边一并丢弃）；聚合边构造时两端已保证存在
    edges = sorted(e for e in edge_set if (e[0], e[1]) in hops and (e[2], e[3]) in hops)
    status = _status_by_node(conn, order)

    lines = [
        "digraph neighborhood {",
        # rankdir=LR：左→右像一条河流过业务（上游采购/船 → 中游分配/订单 → 下游客户/风险/任务）；
        # ranksep/nodesep 较默认加大，让"跳"的层次一眼可辨
        '  graph [bgcolor="transparent", rankdir="LR", splines="true", pad="0.2",'
        ' nodesep="0.42", ranksep="1.0", fontname="Helvetica"];',
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
    for agg_key in sorted(agg_nodes):
        info = agg_nodes[agg_key]
        fill = DOMAIN_COLORS.get(DOMAIN_OF.get(info["type"], ""), "#37474f")
        # 聚合节点与单节点视觉区分：双边框（peripheries=2）+ 虚线描边；标签只有 类型 ×N，零敏感字段
        lines.append(f"  {_dot_quote(agg_key)} [label={_dot_quote(info['type'] + ' ×' + str(info['count']))},"
                     f" fillcolor={_dot_quote(fill)}, peripheries=2, style=\"rounded,filled,dashed\"];")
    for src_t, src_i, tgt_t, tgt_i, rel in edges:
        lines.append(f"  {_dot_quote(src_t + '|' + str(src_i))} -> "
                     f"{_dot_quote(tgt_t + '|' + str(tgt_i))} [label={_dot_quote(rel)}];")
    for a, b, rel in sorted(agg_edges):
        lines.append(f"  {_dot_quote(a)} -> {_dot_quote(b)}"
                     f" [label={_dot_quote(rel)}, style=\"dashed\"];")
    lines.append("}")
    return {"dot": "\n".join(lines), "nodes": len(order) + len(agg_nodes),
            "edges": len(edges) + len(agg_edges), "truncated": truncated,
            "out_of_scope": False, "error": None,
            "agg_nodes": len(agg_nodes), "collapsed_members": collapsed}


# ---------- 大白话旁白（图上方一行话，现查现算，禁编造）----------
def _fmt_usd(v) -> str:
    return f"${v:,.0f}"


def build_neighborhood_narration(conn: sqlite3.Connection, obj_type: str, obj_id: str,
                                 role: str) -> dict:
    """邻域图上方的大白话旁白：从中心对象**现查现算**，返回 {"text", "figures"}。

    - Shipment：延误天数/状态 + 装了几个客户的几个订单行 + 影响金额 + 未结风险数
    - RiskEvent：级别/类型/状态 + 影响几个订单行共多少钱 + 处理任务数 + 催办线程数
    - 其他类型：通用旁白（类型 + 状态 + 一跳直接关联对象数）
    - 金额仅 finance/manager 可见（与 streamlit_app.mask_cost 同口径）；无权角色的金额片段
      整体不出现，figures 同步不含金额键——旁白与图共用同一条敏感红线。
    """
    meta = sov.TYPE_META.get(obj_type)
    if not meta or not _table_exists(conn, meta["table"]):
        return {"text": None, "figures": {}}
    cur = conn.execute(f"SELECT * FROM {meta['table']} WHERE {meta['pk']}=?", (obj_id,))
    fetched = cur.fetchone()
    if not fetched:
        return {"text": None, "figures": {}}
    row = dict(zip([c[0] for c in cur.description], fetched))
    show_amount = role in AMOUNT_VISIBLE_ROLES

    if obj_type == "Shipment":
        delay = row.get("delay_days") or 0
        status = row.get("status") or "-"
        lines = [r[0] for r in conn.execute(
            """SELECT DISTINCT target_id FROM object_relationships
               WHERE source_type='Shipment' AND source_id=?
                 AND relationship_type='derived_shipment_allocates_line'""", (obj_id,))] \
            if _table_exists(conn, "object_relationships") else []
        n_cust = 0
        if lines:
            ph = ",".join("?" * len(lines))
            n_cust = conn.execute(
                f"""SELECT count(DISTINCT so.customer_id) FROM sales_order_lines l
                    JOIN sales_orders so ON so.so_id=l.so_id
                    WHERE l.so_line_id IN ({ph})""", lines).fetchone()[0]
        k_open, z = conn.execute(
            """SELECT count(*), COALESCE(sum(affected_value_usd), 0) FROM risk_events
               WHERE shipment_id=? AND status NOT IN ('resolved','escalated')""",
            (obj_id,)).fetchone()
        delay_part = f"**延误 {delay} 天**" if delay > 0 else "未延误"
        text = (f"这票货（{delay_part}，状态 {status}）装着 **{n_cust} 个客户** 的 "
                f"{len(lines)} 个订单行"
                + (f"，影响金额 **{_fmt_usd(z)}**" if show_amount else "")
                + f"，有 {k_open} 个未结风险。")
        figures = {"delay_days": delay, "status": status, "customers": n_cust,
                   "so_lines": len(lines), "open_risks": k_open}
        if show_amount:
            figures["affected_value_usd"] = z
        return {"text": text, "figures": figures}

    if obj_type == "RiskEvent":
        sev = row.get("severity") or "-"
        rtype = row.get("type") or "-"
        status = row.get("status") or "-"
        try:
            n_lines = len(json.loads(row.get("affected_so_line_ids") or "[]"))
        except (TypeError, ValueError):
            n_lines = 0
        z = row.get("affected_value_usd")
        m_tasks = conn.execute("SELECT count(*) FROM tasks WHERE risk_event_id=?",
                               (obj_id,)).fetchone()[0] if _table_exists(conn, "tasks") else 0
        k_thr = conn.execute("SELECT count(*) FROM coordination_threads WHERE risk_event_id=?",
                             (obj_id,)).fetchone()[0] \
            if _table_exists(conn, "coordination_threads") else 0
        text = (f"这个风险（级别 {sev} / 类型 {rtype}，状态 {status}）影响 {n_lines} 个订单行"
                + (f"（共 **{_fmt_usd(z)}**）" if show_amount and z is not None else "")
                + f"，当前有 {m_tasks} 个处理任务、{k_thr} 条催办线程。")
        figures = {"severity": sev, "type": rtype, "status": status, "so_lines": n_lines,
                   "tasks": m_tasks, "threads": k_thr}
        if show_amount and z is not None:
            figures["affected_value_usd"] = z
        return {"text": text, "figures": figures}

    # 通用旁白：类型 + 状态（白名单状态列，同节点标签口径）+ 一跳直接关联对象数
    status_col = next((c for c in STATUS_FIELD_CANDIDATES if c in row), None)
    status_part = (f"，状态 {row[status_col]}"
                   if status_col and row.get(status_col) not in (None, "") else "")
    others = set()
    for src_t, src_i, tgt_t, tgt_i, _rel in _neighbors(conn, obj_type, obj_id):
        other = (tgt_t, tgt_i) if (src_t, src_i) == (obj_type, obj_id) else (src_t, src_i)
        if other != (obj_type, obj_id):
            others.add(other)
    text = f"这个 **{obj_type}**（{obj_id}{status_part}）直接关联 **{len(others)}** 个对象。"
    return {"text": text, "figures": {"neighbors": len(others)}}


# ---------- Streamlit 渲染（延迟 import st；纯逻辑测试不触及）----------
def render_neighborhood_section(db, role: str, obj_type: str, obj_id: str) -> None:
    """「追查一件事」内嵌的邻域区块：大白话旁白 + 邻域图 + 怎么读。db 为连接工厂（streamlit_app.db）。

    嵌在所选对象只读视图下方（不再自带对象选择器——选谁看谁，跟随上方选择器）。纯只读。
    """
    import streamlit as st

    st.markdown("##### 来龙去脉图（它连着谁）")
    hops = st.radio("看多远", [1, 2], index=1, horizontal=True, key="kg_hops",
                    format_func=lambda h: {1: "只看直接相关（1 跳）",
                                           2: "再往外看一层（2 跳）"}[h])
    with db() as con:
        res = build_neighborhood_dot(con, obj_type, obj_id, role, max_hops=hops)
        nar = (build_neighborhood_narration(con, obj_type, obj_id, role)
               if not res["error"] else None)
    if res["error"]:
        (st.warning if res["out_of_scope"] else st.info)(res["error"])
        return
    if nar and nar["text"]:
        st.markdown(nar["text"])
    st.graphviz_chart(res["dot"], width="stretch")
    trunc = f"（已达 {MAX_NEIGHBORHOOD_NODES} 节点上限，图有截断）" if res["truncated"] else ""
    agg = (f" · 已把 {res['collapsed_members']} 个同类对象收进 {res['agg_nodes']} 个"
           "「类型 ×N」聚合框" if res["agg_nodes"] else "")
    st.caption(f"中心 {obj_type} {obj_id} · {hops} 跳内节点 {res['nodes']} 个 · "
               f"边 {res['edges']} 条{agg}{trunc}")
    st.caption("怎么读：**从左往右**顺着箭头就是这件事的业务流向；节点＝类型+ID+状态"
               "（颜色=业务域，青色粗框=当前追查的这件事）；同类配角超过 4 个会收成一个"
               "虚线双框「类型 ×N」，主角对象（货运/订单行/客户/风险/任务/采购单/协调线程）"
               "永不收起。不展示金额 / 成本 / 客户等级等敏感字段。")
    st.caption("本图只读，操作请回工作台（风险队列 / 任务台 / 费用 / 采购 / 准入 / 协调）。")


def render_ontology_map_expander(db) -> None:
    """「追查一件事」页尾折叠的系统全貌（本体地图，build_ontology_map_dot 原样复用）。"""
    import streamlit as st

    with st.expander("想看系统全貌（34 类对象怎么连）？展开"):
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
        st.caption("本图只读，操作请回工作台（风险队列 / 任务台 / 费用 / 采购 / 准入 / 协调）。")
