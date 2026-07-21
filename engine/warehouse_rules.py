"""仓储检测 R16-R18（库存准确主线，W1 Build 1/3 引擎）。

与 engine/rules.py、cost_rules.py、procurement_rules.py 同构：只读 data/ontology.sqlite 仓储表，
**禁读 data/truth/**（§5 铁律）；as_of 必须显式传入（D8）——快照表的 as_of_date ≤ as_of 才可见，
未来快照不可见。阈值/容差一律从 config warehouse 段读，不硬编码。

规则语义（converge 到 datagen 注入，绝不读真值）：
- R16 stockout（inventory_position 锚）：available_qty ≤ safety_stock → 断货。
  severity：available ≤ safety×stockout_high_frac → high，否则 medium。
- R17 unfulfillable（so_line 锚）：某 open reservation（status∈{open,backordered}）其 SOL line_status=open，
  且目的仓 position 的 ATP < 预留需求量。ATP = available_qty + in_transit_qty − reserved_qty（由 position
  桶字段直算，不从 reservation 反推——避免循环依赖）。severity：ATP ≤ 0 → high，否则 medium。
- R18 shrinkage（cycle_count 锚）：|counted_qty − system_qty| / system_qty > cycle_count_tol → 盘点差异
  （等价 IRA<ira_target）。severity：比率 > shrink_high_ratio → high，否则 medium。

候选结构（写库与 action_log 走 detect.apply_warehouse_candidates）：
  rule_id / type / warehouse_id / severity / affected_value_usd / root_cause / detected_at /
  affected_object_ids（排序 JSON：R16=[inventory_position_id]、R17=[so_line_id]、R18=[cycle_count_id]）
仓储 RiskEvent 的 shipment_id/po_id 留空，用 warehouse_id + affected_so_line_ids（承载受影响业务对象 id）锚点。

匹配键（评估器）：R16→inventory_position_id / R17→so_line_id / R18→cycle_count_id。
"""
import json


def detect_warehouse_risks(con, as_of, cfg):
    """返回仓储异常候选列表（不写库）。cfg 需含 warehouse 段阈值/容差。"""
    wc = cfg["warehouse"]
    tol = wc["cycle_count_tol"]
    stockout_high_frac = wc["stockout_high_frac"]
    shrink_high_ratio = wc["shrink_high_ratio"]
    as_of_str = as_of.isoformat()

    sku_price = {r["sku_id"]: r["unit_price_usd"] for r in con.execute(
        "SELECT sku_id, unit_price_usd FROM skus")}

    def price(sku_id):
        return float(sku_price.get(sku_id) or 0.0)

    cands = []

    def emit(rule, rtype, warehouse_id, anchor_id, severity, value, reason):
        cands.append({
            "rule_id": rule, "type": rtype, "warehouse_id": warehouse_id,
            "affected_object_ids": json.dumps([anchor_id]),
            "severity": severity, "affected_value_usd": round(value, 2),
            "root_cause": reason, "detected_at": as_of_str})

    # position 索引（ATP 直算所需的桶字段）
    positions = {r["inventory_position_id"]: dict(r) for r in con.execute(
        "SELECT * FROM inventory_positions WHERE as_of_date <= ? ORDER BY inventory_position_id",
        (as_of_str,))}

    # --- R16 断货：available_qty ≤ safety_stock ---
    for pid in sorted(positions):
        p = positions[pid]
        safety = p["safety_stock"]
        available = p["available_qty"]
        if available <= safety:
            sev = "high" if available <= safety * stockout_high_frac else "medium"
            emit("R16", "stockout", p["warehouse_id"], pid, sev,
                 (safety - available) * price(p["sku_id"]),
                 f"断货：可用 {available} ≤ 安全库存 {safety}（缺口 {safety - available} 件）")

    # --- R17 不可履约：open reservation 的 SOL open 且目的仓 ATP < 需求量 ---
    open_sol = {r["so_line_id"] for r in con.execute(
        "SELECT so_line_id FROM sales_order_lines WHERE line_status='open'")}
    reservations = [dict(r) for r in con.execute(
        """SELECT reservation_id, so_line_id, inventory_position_id, qty, status
           FROM inventory_reservations WHERE as_of_date <= ? AND status IN ('open','backordered')
           ORDER BY reservation_id""", (as_of_str,))]
    for r in reservations:
        if r["so_line_id"] not in open_sol:
            continue
        p = positions.get(r["inventory_position_id"])
        if p is None:
            continue
        atp = p["available_qty"] + p["in_transit_qty"] - p["reserved_qty"]
        demand = r["qty"]
        if atp < demand:
            sev = "high" if atp <= 0 else "medium"
            emit("R17", "unfulfillable", p["warehouse_id"], r["so_line_id"], sev,
                 (demand - max(atp, 0)) * price(p["sku_id"]),
                 f"不可履约：{r['so_line_id']} 需 {demand} 件，目的仓 {p['warehouse_id']} "
                 f"ATP={atp}（可用 {p['available_qty']}+在途 {p['in_transit_qty']}−预留 "
                 f"{p['reserved_qty']}）< 需求")

    # --- R18 盘点差异：|counted − system| / system > cycle_count_tol ---
    cycle_counts = [dict(r) for r in con.execute(
        """SELECT cycle_count_id, inventory_position_id, warehouse_id, system_qty, counted_qty
           FROM cycle_counts WHERE as_of_date <= ? ORDER BY cycle_count_id""", (as_of_str,))]
    for c in cycle_counts:
        system_qty = c["system_qty"]
        if system_qty <= 0:
            continue
        variance = c["counted_qty"] - system_qty
        ratio = abs(variance) / system_qty
        if ratio > tol:
            sev = "high" if ratio > shrink_high_ratio else "medium"
            sku_id = positions.get(c["inventory_position_id"], {}).get("sku_id")
            ira = round(1 - ratio, 4)
            emit("R18", "shrinkage", c["warehouse_id"], c["cycle_count_id"], sev,
                 abs(variance) * price(sku_id),
                 f"盘点差异：账面 {system_qty} 实盘 {c['counted_qty']}（差 {variance}，"
                 f"|差异|/账面={round(ratio * 100, 1)}% > 容差 {round(tol * 100, 1)}%，IRA={ira})")

    return cands
