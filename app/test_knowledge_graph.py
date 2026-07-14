"""知识图谱呈现层测试：python3 -m app.test_knowledge_graph

锁定八项保证（+一条 trace 附加验证）：
① 类型级本体地图：DOT 非空、含 digraph 声明；35 个 ontology 对象类型全为节点（F1 增 Payment）；
   object_relationships 登记表中出现的全部类型/DISTINCT 关系三元组都入图（实线），计数与 DOT 同源
② 实例级邻域：以设计案例船 SHP-2026-0099 为中心，1/2 跳可达性正确（已知链路在、3 跳外不在）
②b 三刀之一「收起重复扇出」：同关系同类型邻居 >4 收成「类型 ×N」聚合节点（计数与库一致、
   双边框区分、被吸收成员不再单独出现也不向外扩展）；主干类型（RiskEvent/Task/SalesOrderLine/
   Customer/CoordinationThread/Shipment/PurchaseOrder）永不聚合；≤4 的同型邻居不收
②c 三刀之二「左→右流向」：邻域 DOT rankdir=LR 存在
②d 三刀之三「大白话旁白」：Shipment/RiskEvent/通用三款旁白数字全部与库独立复算一致（现算禁编造）；
   金额仅 finance/manager 可见（同 mask_cost 口径）——无权角色旁白与 figures 中金额零出现
③ 敏感字段零泄漏：金额/成本/tier 等字段模式与真实金额值在 DOT 字符串中零出现（含 manager 全量视图）
④ 数据范围：ops 的邻域结果被 data_scope 区域过滤（CN 节点被滤/越域中心被拒），manager 全量
⑤ DOT 转义安全：对象 ID 含引号/反斜杠/换行/注入片段，不破坏 DOT 语法（引号平衡、只以转义形态出现）
⑥ AppTest：manager / ops 打开控制室「追查一件事」（对象详情+内嵌邻域图+页尾本体地图）0 未捕获
   异常、≥2 张 graphviz 图真实渲染、旁白行出现且 ops 旁白无金额
⑦ trace 附加：风险→任务→协调线程 一条链在邻域图中肉眼可见（决策纪要 §2 的"理解原子是一条 trace"）

红线自检：本模块只读（源码零写 SQL）、agent/ 目录零引用（不给 AI 任何新工具）。
只读断言 + 临时副本（绝不污染 data/ontology.sqlite；结束校验其 md5 不变）。
"""
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import knowledge_graph as kgm
from .knowledge_graph import (build_neighborhood_dot, build_neighborhood_narration,
                              build_ontology_map_dot)

DB = "data/ontology.sqlite"
FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _conn(path=DB):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _copy_db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    Path(path).write_bytes(Path(DB).read_bytes())
    import os
    os.close(fd)
    return path


def _node_ids(dot):
    """DOT 中的实例节点 id 集合（"Type|id" 形式的声明行）。"""
    return set(re.findall(r'^\s*"([^"|]+\|[^"]*)" \[', dot, re.M))


def main():
    md5_before = hashlib.md5(open(DB, "rb").read()).hexdigest()
    onto = json.load(open("ontology/control-tower-ontology.json", encoding="utf-8"))
    onto_types = [o["type"] for o in onto["objects"]]

    # ===== ① 类型级本体地图 =====
    print("== ① 本体地图（类型级）：35 类型全节点 + 登记表关系全入图 ==")
    con = _conn()
    reg = con.execute("""SELECT DISTINCT source_type, target_type, relationship_type
                         FROM object_relationships""").fetchall()
    reg_types = sorted({t for r in reg for t in (r[0], r[1])})
    m = build_ontology_map_dot(con)
    dot = m["dot"]
    check("① DOT 非空且含 digraph 声明", bool(dot) and "digraph" in dot)
    check(f"① 节点数 = 35 = ontology 对象类型数（登记表类型 {len(reg_types)} 类为其子集，F1 增 Payment）",
          m["nodes"] == len(onto_types) == 35, f"nodes={m['nodes']}")
    check("① 35 个对象类型全部出现在 DOT 节点中",
          all(f'"{t}"' in dot for t in onto_types),
          str([t for t in onto_types if f'"{t}"' not in dot]))
    check("① object_relationships 中出现的类型全部为节点",
          all(f'"{t}"' in dot for t in reg_types))
    check("① 登记表 DISTINCT 三元组全部为实线边",
          all(f'"{s}" -> "{t}" [label="{r}", style="solid"]' in dot for s, t, r in reg),
          str([(s, t, r) for s, t, r in reg
               if f'"{s}" -> "{t}" [label="{r}", style="solid"]' not in dot][:3]))
    check("① 边计数与 DOT 同源（returned edges == DOT 中 '->' 行数）",
          m["edges"] == dot.count(" -> "), f"{m['edges']} != {dot.count(' -> ')}")
    check("① 实线数 = 登记表三元组数", m["registry_edges"] == len(reg),
          f"{m['registry_edges']} != {len(reg)}")
    check("① 七个业务域全部成簇", all(f"subgraph cluster_{d}" in dot for d in kgm.DOMAINS))
    print(f"  规模：节点 {m['nodes']} · 边 {m['edges']}"
          f"（实线 {m['registry_edges']} + 虚线 {m['declared_only_edges']}）")

    # ===== ② 邻域可达性（设计案例船 SHP-2026-0099）=====
    print("\n== ② 对象邻域：设计案例船 SHP-2026-0099 的 1/2 跳可达性 ==")
    n2 = build_neighborhood_dot(con, "Shipment", "SHP-2026-0099", "manager", max_hops=2)
    n1 = build_neighborhood_dot(con, "Shipment", "SHP-2026-0099", "manager", max_hops=1)
    ids2, ids1 = _node_ids(n2["dot"]), _node_ids(n1["dot"])
    check("② 中心节点在图中且高亮（penwidth=3）",
          'Shipment|SHP-2026-0099" [' in n2["dot"]
          and re.search(r'"Shipment\|SHP-2026-0099" \[[^\]]*penwidth=3', n2["dot"]) is not None)
    # 已知链路：risk_on_shipment（1 跳）→ 受影响订单行（1 跳）→ 行的 Sku/SalesOrder/Customer（2 跳）
    line = con.execute("""SELECT target_id FROM object_relationships
                          WHERE source_id='SHP-2026-0099'
                            AND relationship_type='derived_shipment_allocates_line'
                          ORDER BY target_id LIMIT 1""").fetchone()[0]
    lrow = con.execute("SELECT so_id, sku_id FROM sales_order_lines WHERE so_line_id=?",
                       (line,)).fetchone()
    cust = con.execute("SELECT customer_id FROM sales_orders WHERE so_id=?",
                       (lrow["so_id"],)).fetchone()[0]
    check("② 1 跳：风险事件 RSK-0068 可达", "RiskEvent|RSK-0068" in ids1)
    check(f"② 1 跳：受影响订单行 {line} 可达", f"SalesOrderLine|{line}" in ids1)
    check(f"② 2 跳：行的 Sku {lrow['sku_id']} 可达", f"Sku|{lrow['sku_id']}" in ids2)
    check(f"② 2 跳：行的 SalesOrder {lrow['so_id']} 可达", f"SalesOrder|{lrow['so_id']}" in ids2)
    check(f"② 2 跳：行的 Customer {cust} 可达", f"Customer|{cust}" in ids2)
    check("② 1 跳图不含 2 跳节点（Sku 不在 1 跳内）", f"Sku|{lrow['sku_id']}" not in ids1)
    sibling = con.execute("""SELECT so_line_id FROM sales_order_lines
                             WHERE so_id=? AND so_line_id NOT IN (
                               SELECT target_id FROM object_relationships
                               WHERE source_id='SHP-2026-0099'
                                 AND relationship_type='derived_shipment_allocates_line')
                             ORDER BY so_line_id LIMIT 1""", (lrow["so_id"],)).fetchone()
    if sibling:
        check(f"② 3 跳外不可达：同单其他行 {sibling[0]}（经 SalesOrder 需 3 跳）不在 2 跳图",
              f"SalesOrderLine|{sibling[0]}" not in ids2)
    check("② 节点/边计数与 DOT 同源",
          n2["nodes"] == len(ids2) and n2["edges"] == n2["dot"].count(" -> "),
          f"nodes {n2['nodes']}/{len(ids2)} edges {n2['edges']}/{n2['dot'].count(' -> ')}")
    check("② 不存在的对象 → 友好 error 不抛",
          build_neighborhood_dot(con, "Shipment", "SHP-NOPE", "manager")["error"] is not None)
    check("② 未知类型 → 友好 error 不抛",
          build_neighborhood_dot(con, "NotAType", "x", "manager")["error"] is not None)
    print(f"  规模：2 跳 {n2['nodes']} 节点/{n2['edges']} 边；1 跳 {n1['nodes']} 节点/{n1['edges']} 边"
          f"（聚合节点 {n2['agg_nodes']} 个，收起成员 {n2['collapsed_members']} 个）")

    # ===== ②b 三刀之一：收起重复扇出（聚合节点）=====
    print("\n== ②b 聚合：>4 同关系同类型收成「类型 ×N」；主干永不聚合；≤4 不收 ==")
    n_exp = con.execute(
        """SELECT count(DISTINCT target_id) FROM object_relationships
           WHERE source_type='Shipment' AND source_id='SHP-2026-0099'
             AND relationship_type='derived_shipment_has_expected_cost'""").fetchone()[0]
    n_ms = con.execute(
        """SELECT count(DISTINCT target_id) FROM object_relationships
           WHERE source_type='Shipment' AND source_id='SHP-2026-0099'
             AND relationship_type='shipment_has_milestone'""").fetchone()[0]
    check(f"②b 前置：设计案例船 ExpectedCost 扇出 {n_exp} 个（>4）、里程碑 {n_ms} 个（≤4）",
          n_exp > 4 and n_ms <= 4)
    check(f"②b ExpectedCost ×{n_exp} 聚合节点出现且计数与库一致",
          f'label="ExpectedCost ×{n_exp}"' in n2["dot"])
    check("②b 被吸收的 ExpectedCost 不再逐个出现（个体节点零声明）",
          '"ExpectedCost|' not in n2["dot"])
    check("②b 聚合节点用双边框+虚线与单节点区分（peripheries=2, dashed）",
          re.search(r'label="ExpectedCost ×\d+"[^\]]*peripheries=2[^\]]*dashed', n2["dot"])
          is not None)
    check("②b 返回计数与 DOT 同源（agg_nodes/collapsed_members）",
          n2["agg_nodes"] == n2["dot"].count("peripheries=2")
          and n2["collapsed_members"] >= n_exp,
          f"agg={n2['agg_nodes']} collapsed={n2['collapsed_members']}")
    check(f"②b ≤4 的同型邻居不收：{n_ms} 个里程碑仍逐个出现且无聚合",
          len(re.findall(r'^\s*"ShipmentMilestone\|[^"]*" \[', n2["dot"], re.M)) == n_ms
          and "ShipmentMilestone ×" not in n2["dot"])
    # 主干类型永不聚合：找一个 >4 行的销售订单为中心——SalesOrderLine 是主角，必须逐个可见
    so5 = con.execute("""SELECT so_id, count(*) c FROM sales_order_lines
                         GROUP BY so_id HAVING c>4 ORDER BY so_id LIMIT 1""").fetchone()
    check("②b 前置：存在 >4 行的销售订单", so5 is not None)
    if so5:
        nso = build_neighborhood_dot(con, "SalesOrder", so5["so_id"], "manager", max_hops=1)
        n_line_nodes = len(re.findall(r'^\s*"SalesOrderLine\|[^"]*" \[', nso["dot"], re.M))
        check(f"②b 主干 SalesOrderLine 永不聚合（{so5['c']} 行逐个可见、无 ×N）",
              n_line_nodes == so5["c"] and "SalesOrderLine ×" not in nso["dot"],
              f"nodes={n_line_nodes}")
    for bt in sorted(kgm.BACKBONE_TYPES):
        check(f"②b 主干 {bt} 在全部样例 DOT 中零聚合标签", f"{bt} ×" not in n2["dot"])
    # 阈值边界 + 聚合节点不向外扩展：临时副本把里程碑扇出补到 5（>4）→ 聚合出现、成员消失
    tmpb = _copy_db()
    tb = _conn(tmpb)
    need = 5 - n_ms
    for k in range(need):
        tb.execute("""INSERT INTO object_relationships VALUES
                      (?, 'Shipment','SHP-2026-0099','ShipmentMilestone', ?,
                       'shipment_has_milestone', 1.0, 'test')""",
                   (f"REL-aggtest-{k}", f"MS-AGGTEST-{k}"))
    tb.commit()
    nb = build_neighborhood_dot(tb, "Shipment", "SHP-2026-0099", "manager", max_hops=2)
    check("②b 阈值边界：补到 5 个（>4）后 ShipmentMilestone 收成 ×5 且个体消失",
          'label="ShipmentMilestone ×5"' in nb["dot"] and '"ShipmentMilestone|' not in nb["dot"])
    ms1 = con.execute("""SELECT target_id FROM object_relationships
                         WHERE source_type='Shipment' AND source_id='SHP-2026-0099'
                           AND relationship_type='shipment_has_milestone'
                         ORDER BY target_id LIMIT 1""").fetchone()[0]
    ms_nbr = {(r[0], r[1]) for r in tb.execute(
        """SELECT target_type, target_id FROM object_relationships
           WHERE source_type='ShipmentMilestone' AND source_id=?
           UNION SELECT source_type, source_id FROM object_relationships
           WHERE target_type='ShipmentMilestone' AND target_id=?""", (ms1, ms1))}
    only_via_ms = {f"{t}|{i}" for t, i in ms_nbr} - {"Shipment|SHP-2026-0099"} \
        - _node_ids(build_neighborhood_dot(tb, "Shipment", "SHP-2026-0099", "manager",
                                           max_hops=1)["dot"])
    check("②b 聚合节点是终点：被收起的里程碑不再向外扩展下一跳",
          all(k not in _node_ids(nb["dot"]) for k in only_via_ms), str(list(only_via_ms)[:3]))
    tb.close()
    Path(tmpb).unlink()

    # ===== ②c 三刀之二：左→右流向 =====
    print("\n== ②c 流向：rankdir=LR（左→右像一条河）==")
    check("②c 邻域 DOT 含 rankdir=LR", 'rankdir="LR"' in n2["dot"])
    check("②c 本体地图 DOT 含 rankdir=LR", 'rankdir="LR"' in dot)

    # ===== ②d 三刀之三：大白话旁白（现查现算 + 按角色脱敏）=====
    print("\n== ②d 旁白：数字与库独立复算一致；金额仅 finance/manager（mask_cost 口径）==")
    srow = con.execute("SELECT delay_days, status FROM shipments WHERE shipment_id='SHP-2026-0099'"
                       ).fetchone()
    s_lines = [r[0] for r in con.execute(
        """SELECT DISTINCT target_id FROM object_relationships
           WHERE source_type='Shipment' AND source_id='SHP-2026-0099'
             AND relationship_type='derived_shipment_allocates_line'""")]
    ph = ",".join("?" * len(s_lines))
    s_cust = con.execute(
        f"""SELECT count(DISTINCT so.customer_id) FROM sales_order_lines l
            JOIN sales_orders so ON so.so_id=l.so_id WHERE l.so_line_id IN ({ph})""",
        s_lines).fetchone()[0]
    s_open, s_val = con.execute(
        """SELECT count(*), COALESCE(sum(affected_value_usd),0) FROM risk_events
           WHERE shipment_id='SHP-2026-0099' AND status NOT IN ('resolved','escalated')"""
    ).fetchone()
    nar_m = build_neighborhood_narration(con, "Shipment", "SHP-2026-0099", "manager")
    nar_o = build_neighborhood_narration(con, "Shipment", "SHP-2026-0099", "ops")
    check("②d Shipment 旁白（manager）数字与库一致：延误/客户数/订单行数/未结风险数",
          f"延误 {srow['delay_days']} 天" in nar_m["text"]
          and f"{s_cust} 个客户" in nar_m["text"]
          and f"{len(s_lines)} 个订单行" in nar_m["text"]
          and f"{s_open} 个未结风险" in nar_m["text"], nar_m["text"])
    check("②d Shipment 旁白（manager）金额=库中未结风险影响额合计（千分位）",
          f"${s_val:,.0f}" in nar_m["text"], nar_m["text"])
    check("②d figures 与文本同源（双路对账）",
          nar_m["figures"] == {"delay_days": srow["delay_days"], "status": srow["status"],
                               "customers": s_cust, "so_lines": len(s_lines),
                               "open_risks": s_open, "affected_value_usd": s_val},
          str(nar_m["figures"]))
    check("②d Shipment 旁白（ops）金额零出现（$ 与数值都不在，figures 无金额键）",
          "$" not in nar_o["text"] and f"{s_val:,.0f}" not in nar_o["text"]
          and "affected_value_usd" not in nar_o["figures"], nar_o["text"])
    check("②d 旁白与图一致：订单行数 == 图中 1 跳 SalesOrderLine 节点数",
          len(re.findall(r'^\s*"SalesOrderLine\|[^"]*" \[', n1["dot"], re.M)) == len(s_lines))
    r68 = con.execute("""SELECT severity, type, status, affected_so_line_ids, affected_value_usd
                         FROM risk_events WHERE risk_event_id='RSK-0068'""").fetchone()
    r_lines = len(json.loads(r68["affected_so_line_ids"]))
    r_tasks = con.execute("SELECT count(*) FROM tasks WHERE risk_event_id='RSK-0068'").fetchone()[0]
    r_thr = con.execute("SELECT count(*) FROM coordination_threads WHERE risk_event_id='RSK-0068'"
                        ).fetchone()[0]
    nr_m = build_neighborhood_narration(con, "RiskEvent", "RSK-0068", "manager")
    nr_c = build_neighborhood_narration(con, "RiskEvent", "RSK-0068", "cs")
    check("②d RiskEvent 旁白（manager）级别/类型/行数/任务数/线程数与库一致",
          f"级别 {r68['severity']}" in nr_m["text"] and f"类型 {r68['type']}" in nr_m["text"]
          and f"{r_lines} 个订单行" in nr_m["text"] and f"{r_tasks} 个处理任务" in nr_m["text"]
          and f"{r_thr} 条催办线程" in nr_m["text"], nr_m["text"])
    check("②d RiskEvent 旁白（manager）金额与库一致",
          f"${r68['affected_value_usd']:,.0f}" in nr_m["text"], nr_m["text"])
    check("②d RiskEvent 旁白（cs 无金额权）金额零出现", "$" not in nr_c["text"], nr_c["text"])
    # 通用旁白：Customer——独立复算邻居数（registry 双向 ∪ sales_orders FK ∪ admission FK）
    cust_id = con.execute("SELECT customer_id FROM customers ORDER BY customer_id LIMIT 1"
                          ).fetchone()[0]
    others = {(r[0], r[1]) for r in con.execute(
        """SELECT target_type, target_id FROM object_relationships
           WHERE source_type='Customer' AND source_id=?
           UNION SELECT source_type, source_id FROM object_relationships
           WHERE target_type='Customer' AND target_id=?""", (cust_id, cust_id))}
    others |= {("SalesOrder", r[0]) for r in con.execute(
        "SELECT so_id FROM sales_orders WHERE customer_id=?", (cust_id,))}
    others |= {("AdmissionCase", r[0]) for r in con.execute(
        "SELECT admission_case_id FROM admission_cases WHERE customer_id=?", (cust_id,))}
    ng = build_neighborhood_narration(con, "Customer", cust_id, "ops")
    check(f"②d 通用旁白（Customer {cust_id}）邻居数与库独立复算一致（{len(others)}）",
          f"直接关联 **{len(others)}** 个对象" in ng["text"]
          and ng["figures"]["neighbors"] == len(others), ng["text"])
    check("②d 不存在的对象 → 旁白 text=None 不抛",
          build_neighborhood_narration(con, "Shipment", "SHP-NOPE", "manager")["text"] is None)

    # ===== ③ 敏感字段零泄漏 =====
    print("\n== ③ 敏感字段（amount/cost/tier/usd 等模式 + 真实金额值）DOT 零出现 ==")
    inv = con.execute("""SELECT iv.invoice_id FROM invoices iv
                         JOIN invoice_lines il ON il.invoice_id=iv.invoice_id
                         ORDER BY iv.invoice_id LIMIT 1""").fetchone()[0]
    dots = {
        "map": dot,
        "nbr_ship": n2["dot"],
        "nbr_risk": build_neighborhood_dot(con, "RiskEvent", "RSK-0068", "manager")["dot"],
        "nbr_invoice": build_neighborhood_dot(con, "Invoice", inv, "manager")["dot"],
        "nbr_po_risk": build_neighborhood_dot(con, "RiskEvent", "RSK-0077", "manager")["dot"],
    }
    # 字段模式（大小写不敏感）。注：类型名 ExpectedCost/CostScenario 与关系名 *_expected_cost 属对象/
    # 关系命名非字段值，故不检 bare "cost"；金额泄漏由 usd/amount/value 模式 + 真实值断言双重覆盖。
    patterns = ("usd", "amount", "tier", "margin", "unit_price", "quote_price",
                "gross_margin", "affected_value", "baseline_", "credit_terms")
    for name, d in dots.items():
        low = d.lower()
        bad = [p for p in patterns if p in low]
        check(f"③ {name}：敏感字段模式零出现", not bad, str(bad))
    rv = con.execute("SELECT affected_value_usd FROM risk_events WHERE risk_event_id='RSK-0068'"
                     ).fetchone()[0]
    amounts = [r[0] for r in con.execute(
        "SELECT amount_usd FROM invoice_lines WHERE invoice_id=?", (inv,))]
    leak_vals = [v for v in [rv] + amounts if v is not None]
    for name, d in dots.items():
        hits = [v for v in leak_vals
                if f"{v:.1f}" in d or (len(str(int(v))) >= 5 and str(int(v)) in d)]
        check(f"③ {name}：真实金额值零出现（风险影响额/账单行金额）", not hits, str(hits[:2]))
    check("③ 节点标签只含 类型/ID/状态 三样（状态列白名单外的列名不进 DOT）",
          all(w not in dots["nbr_invoice"].lower() for w in ("vendor_name", "carrier", "incoterm")))

    # ===== ④ data_scope：ops 区域过滤 vs manager 全量 =====
    print("\n== ④ 数据范围：CN 目的地（临时副本）对 ops 被滤、对 manager 可见 ==")
    tmp = _copy_db()
    tc = _conn(tmp)
    combo = None  # (line, flip_shipment, keep_shipment, open_risk_on_flip)
    for r in tc.execute("""SELECT target_id line, group_concat(DISTINCT source_id) ships
                           FROM object_relationships
                           WHERE relationship_type='derived_shipment_allocates_line'
                           GROUP BY target_id HAVING count(DISTINCT source_id)=2"""):
        s1, s2 = sorted(r["ships"].split(","))
        for flip, keep in ((s1, s2), (s2, s1)):
            risk = tc.execute("""SELECT risk_event_id FROM risk_events
                                 WHERE shipment_id=? AND status='open' LIMIT 1""",
                              (flip,)).fetchone()
            if risk:
                combo = (r["line"], flip, keep, risk[0])
                break
        if combo:
            break
    check("④ 前置：找到跨两船的订单行且一船带 open 风险", combo is not None, "数据中无此组合")
    if combo:
        line, flip, keep, flip_risk = combo
        tc.execute("UPDATE shipments SET destination_port_locode='CNYTN' WHERE shipment_id=?",
                   (flip,))
        tc.commit()
        mgr = build_neighborhood_dot(tc, "SalesOrderLine", line, "manager", max_hops=1)
        ops = build_neighborhood_dot(tc, "SalesOrderLine", line, "ops", max_hops=1)
        mgr_ids, ops_ids = _node_ids(mgr["dot"]), _node_ids(ops["dot"])
        check(f"④ manager 邻域含 CN 船 {flip}（全量）", f"Shipment|{flip}" in mgr_ids)
        check(f"④ ops 邻域不含 CN 船 {flip}（US 区域过滤）", f"Shipment|{flip}" not in ops_ids)
        check(f"④ ops 邻域仍含 US 船 {keep}（不过度隐藏）", f"Shipment|{keep}" in ops_ids)
        check("④ ops 节点数 < manager 节点数（过滤真实发生）",
              ops["nodes"] < mgr["nodes"], f"{ops['nodes']} vs {mgr['nodes']}")
        blocked = build_neighborhood_dot(tc, "Shipment", flip, "ops")
        check("④ ops 以 CN 船为中心 → out_of_scope 拒绝（dot 为空）",
              blocked["out_of_scope"] is True and blocked["dot"] is None)
        check("④ manager 以 CN 船为中心 → 正常出图",
              build_neighborhood_dot(tc, "Shipment", flip, "manager")["dot"] is not None)
        _ = flip_risk  # CN 船上的 open 风险（组合前置用；起点候选下拉已随合并撤除）
    tc.close()
    Path(tmp).unlink()

    # ===== ⑤ DOT 转义安全 =====
    print("\n== ⑤ DOT 转义：对象 ID 含引号/反斜杠/换行/注入片段不破坏语法 ==")
    tmp = _copy_db()
    tc = _conn(tmp)
    evil = 'EVIL"x\\y\nz]; graph [label="pwn'
    tc.execute("""INSERT INTO object_relationships VALUES
                  ('REL-evil-1','Shipment','SHP-2026-0099','Container',?,
                   'shipment_has_container',1.0,'test')""", (evil,))
    tc.commit()
    try:
        ev = build_neighborhood_dot(tc, "Shipment", "SHP-2026-0099", "manager", max_hops=1)
        check("⑤ 含恶意 ID 的邻域构建不抛异常", ev["dot"] is not None)
        d = ev["dot"]
        cleaned = d.replace("\\\\", "#").replace('\\"', "#").replace("\\n", "#")
        check("⑤ 引号严格平衡（去转义后偶数）", cleaned.count('"') % 2 == 0,
              str(cleaned.count('"')))
        check("⑤ ID 中的引号只以转义形态出现（EVIL\\\" 在、裸 EVIL\" 不在）",
              'EVIL\\"' in d and 'EVIL"' not in cleaned)
        check("⑤ ID 中的反斜杠已转义（x\\\\y）", "x\\\\y" in d)
        check("⑤ ID 中的换行已转义为 \\n（每条含 EVIL 的声明单行完整、以 ]; 收尾）",
              all(line_.rstrip().endswith("];")
                  for line_ in d.splitlines() if "EVIL" in line_)
              and any("EVIL" in line_ for line_ in d.splitlines()))
        check("⑤ 注入片段未逃出引号（裸 [label=\"pwn 不存在）", '[label="pwn' not in cleaned)
    except Exception as e:  # noqa: BLE001
        check("⑤ 含恶意 ID 的邻域构建不抛异常", False, f"raised {type(e).__name__}: {e}")
    tc.close()
    Path(tmp).unlink()

    # ===== ⑦ trace 附加：风险→任务→协调 一条链可见（临时副本 + 运营种子）=====
    print("\n== ⑦ trace：风险→任务→协调线程 在邻域图中一条链可见 ==")
    tmp = _copy_db()
    try:
        from datagen.seed_demo_ops import run as seed_demo_ops
        seed_demo_ops(db_path=tmp)
        tc = _conn(tmp)
        th = tc.execute("""SELECT coordination_id, task_id, risk_event_id
                           FROM coordination_threads
                           WHERE task_id IS NOT NULL AND risk_event_id IS NOT NULL
                           LIMIT 1""").fetchone()
        check("⑦ 前置：种子含锚定 Task+RiskEvent 的协调线程", th is not None)
        if th:
            tr = build_neighborhood_dot(tc, "RiskEvent", th["risk_event_id"], "manager")
            tids = _node_ids(tr["dot"])
            check(f"⑦ 以 {th['risk_event_id']} 为中心：Task {th['task_id']} 可达",
                  f"Task|{th['task_id']}" in tids)
            check(f"⑦ 协调线程 {th['coordination_id']} 可达（同一件事在多角色手里传递）",
                  f"CoordinationThread|{th['coordination_id']}" in tids)
        tc.close()
    except Exception as e:  # noqa: BLE001
        check("⑦ trace 验证执行", False, f"raised {type(e).__name__}: {e}")
    Path(tmp).unlink()

    # ===== 红线自检：模块只读、不给 agent 任何新工具 =====
    print("\n== 红线：knowledge_graph 只读 + agent/ 零引用 ==")
    src = Path("app/knowledge_graph.py").read_text(encoding="utf-8")
    sql_writes = [w for w in ("INSERT", "UPDATE", "DELETE", "CREATE TABLE", "DROP", "ALTER")
                  if re.search(rf"execute\([^)]*{w}", src, re.I)]
    check("红线：模块源码零写 SQL（只读呈现层）", not sql_writes, str(sql_writes))
    agent_refs = [p.name for p in Path("agent").glob("*.py")
                  if "knowledge_graph" in p.read_text(encoding="utf-8")]
    check("红线：agent/ 目录零引用（不给 AI 任何新工具）", not agent_refs, str(agent_refs))
    con.close()

    # ===== ⑥ AppTest：manager / ops 打开控制室「追查一件事」0 异常 =====
    print("\n== ⑥ AppTest：控制室「追查一件事」（对象详情+内嵌邻域图+页尾本体地图）==")
    from streamlit.testing.v1 import AppTest
    for role in ("manager", "ops"):
        at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
        at.session_state["role"] = role
        at.session_state["nav_surface"] = "control"  # 三问句合并后 obj+kg 同在「追查一件事」
        at.run()
        excs = list(at.exception)
        check(f"⑥ role={role} 控制室 0 未捕获异常", not excs, str(excs[:1]))
        charts = at.get("graphviz_chart")
        check(f"⑥ role={role} graphviz 图 ≥2 张（默认船的邻域图 + 页尾本体地图）",
              len(charts) >= 2, f"charts={len(charts)}")
        md = " ||| ".join((m.value or "") for m in at.markdown)
        check(f"⑥ role={role} 旁白行出现（默认对象 SHP-2026-0099 的大白话一句）",
              "这票货" in md and "个未结风险" in md)
        nar_line = next((m.value for m in at.markdown if "这票货" in (m.value or "")), "")
        if role == "ops":
            check("⑥ ops 旁白无金额（$ 零出现——UI 渲染层同 mask_cost 口径）",
                  "$" not in nar_line, nar_line)
        else:
            check("⑥ manager 旁白含金额（$ 千分位）", "$" in nar_line, nar_line)
        # 不再有独立「知识图谱」标签与视图切换 radio（合并进「追查一件事」）
        tabs = [t.label for t in at.tabs]
        check(f"⑥ role={role} 控制室无独立「知识图谱」tab（已并入追查一件事）",
              "知识图谱" not in tabs and "追查一件事" in tabs, str(tabs))

    md5_after = hashlib.md5(open(DB, "rb").read()).hexdigest()
    check("真实 DB 未被污染（md5 逐字节一致）", md5_before == md5_after,
          f"{md5_before} != {md5_after}")

    print(f"\n{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本承载 CN 翻转/恶意 ID/运营种子；data/ontology.sqlite 只读未动）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
