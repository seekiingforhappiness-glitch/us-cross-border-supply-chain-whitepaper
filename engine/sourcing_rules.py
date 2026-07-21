"""采购富化2 检测 R14/R15（P3 Build A：单一来源 + maverick）。

与 engine/procurement_rules.py、warehouse_rules.py 同构：只读 data/ontology.sqlite，
禁读 data/truth/（§5 铁律）；as_of 必须显式传入（D8）。容差/窗口一律从 config sourcing 段读。

依赖顺序：R14 读既有 risk_events 的 R7/R9 事件 → detect 必须在 apply_procurement_candidates
（R7-R13 写库）之后调用本模块。

规则语义（converge 到 datagen 注入，绝不读真值）：
- 近期断供供应商：risk_events.rule_id in (R7,R9) 且 detected_at 落 [as_of-N, as_of]（N=
  single_source_recent_days）。本数据 detected_at 恒 = as_of，故窗口对全部 R7/R9 事件成立
  （窗口 knob 为将来带真实事件日期而设；此处如实标注恒满足）。
- approved 备源集：{目录 incumbent(skus.supplier_id)} ∪ {对该 SKU 有 status='awarded' Quote
  且 rfq.created_date<=as_of 的供应商}。单一来源 = 该并集恰 1 个成员。
- R14 single_source：active SKU 单一来源 且 incumbent ∈ 近期断供 → 断供风险。锚 supplier_id；
  sku_id 装进 affected_po_line_ids=[sku_id]（复用通用受影响对象载体，同 warehouse 先例）。
  affected_value = 该 SKU 的 PO 采购额 sum(po_line.qty*unit_price)。
- R15 maverick_spend：supplier_invoice.supplier ≠ 其 PO 的 supplier 且该 biller 非该发票行 SKU 的
  approved 备源（无 awarded Quote）→ 绕流程。既有合规发票(supplier==PO supplier)永不触发；
  approved 备源 biller 豁免。锚 po_id（supplier_id + affected_invoice_line_ids 承载 maverick 行）。
"""
import json
from datetime import date, timedelta


def _awarded_alt(con, as_of_str):
    """approved 备源：sku_id -> {有 awarded Quote 且 rfq.created_date<=as_of 的供应商}。"""
    alt = {}
    rows = con.execute(
        """SELECT r.sku_id AS sku_id, q.supplier_id AS supplier_id
           FROM quotes q JOIN rfqs r ON r.rfq_id = q.rfq_id
           WHERE q.status = 'awarded' AND r.created_date <= ?""", (as_of_str,))
    for row in rows:
        alt.setdefault(row["sku_id"], set()).add(row["supplier_id"])
    return alt


def detect_sourcing_risks(con, as_of, cfg):
    """返回 R14/R15 候选列表（不写库）。cfg 需含 sourcing 段。"""
    sc = cfg["sourcing"]
    recent_days = sc["single_source_recent_days"]
    single_high = sc["single_source_high_usd"]
    maverick_high = sc["maverick_high_usd"]
    as_of_str = as_of.isoformat()
    window_start = (as_of - timedelta(days=recent_days)).isoformat()

    cands = []
    awarded_alt = _awarded_alt(con, as_of_str)

    # === R14 单一来源断供 ===
    # 近期断供供应商：既有 risk_events 的 R7/R9（detected_at 落窗口内、≤ as_of）。
    recent_disrupted = {row["supplier_id"] for row in con.execute(
        """SELECT DISTINCT supplier_id FROM risk_events
           WHERE rule_id IN ('R7','R9') AND supplier_id IS NOT NULL
             AND detected_at <= ? AND detected_at >= ?""", (as_of_str, window_start))}

    # active SKU + 目录 incumbent
    active = [dict(r) for r in con.execute(
        "SELECT sku_id, supplier_id FROM skus WHERE sku_status='active' ORDER BY sku_id")]
    # 该 SKU 的 PO 采购额（敞口）
    spend_of = {}
    for row in con.execute(
            "SELECT sku_id, SUM(qty*unit_price_usd) AS s FROM po_lines GROUP BY sku_id"):
        spend_of[row["sku_id"]] = round(row["s"], 2)

    for r in active:
        sku, incumbent = r["sku_id"], r["supplier_id"]
        approved = {incumbent} | awarded_alt.get(sku, set())
        if len(approved) == 1 and incumbent in recent_disrupted:
            spend = spend_of.get(sku, 0.0)
            sev = "high" if spend > single_high else "medium"
            cands.append({
                "rule_id": "R14", "type": "single_source", "po_id": None,
                "supplier_id": incumbent,
                "affected_po_line_ids": json.dumps([sku]),   # sku_id 载体（同 warehouse 先例）
                "affected_invoice_line_ids": None,
                "severity": sev, "affected_value_usd": round(spend, 2),
                "root_cause": f"单一来源断供：SKU {sku} 仅 1 个 approved 供应商 {incumbent}，"
                              f"该供应商近 {recent_days} 天有 R7/R9 事件，PO 采购敞口 ${round(spend, 2)}",
                "detected_at": as_of_str})

    # === R15 maverick 绕流程采购 ===
    supplier_of_po = {row["po_id"]: row["supplier_id"] for row in con.execute(
        "SELECT po_id, supplier_id FROM purchase_orders")}
    sku_of_pol = {row["po_line_id"]: row["sku_id"] for row in con.execute(
        "SELECT po_line_id, sku_id FROM po_lines")}
    # 发票行（issue_date ≤ as_of）：按发票聚合行 id + 行 SKU
    inv_lines = {}
    for row in con.execute(
            """SELECT sil.supplier_invoice_id AS inv, sil.supplier_invoice_line_id AS sil_id,
                      sil.po_line_id AS pol
               FROM supplier_invoice_lines sil
               JOIN supplier_invoices si ON si.supplier_invoice_id = sil.supplier_invoice_id
               WHERE si.issue_date <= ?
               ORDER BY sil.supplier_invoice_line_id""", (as_of_str,)):
        d = inv_lines.setdefault(row["inv"], {"sil_ids": [], "skus": set()})
        d["sil_ids"].append(row["sil_id"])
        if row["pol"] in sku_of_pol:
            d["skus"].add(sku_of_pol[row["pol"]])

    invoices = [dict(r) for r in con.execute(
        """SELECT supplier_invoice_id, supplier_id, po_id, total_usd
           FROM supplier_invoices WHERE issue_date <= ? ORDER BY supplier_invoice_id""",
        (as_of_str,))]
    for inv in invoices:
        po_sup = supplier_of_po.get(inv["po_id"])
        biller = inv["supplier_id"]
        if po_sup is None or biller == po_sup:
            continue  # 合规发票（supplier==PO supplier）→ 永不触发
        meta = inv_lines.get(inv["supplier_invoice_id"], {"sil_ids": [], "skus": set()})
        approved_alts = set()
        for sku in meta["skus"]:
            approved_alts |= awarded_alt.get(sku, set())
        if biller in approved_alts:
            continue  # approved 备源 → 豁免（灰区）
        amt = round(inv["total_usd"], 2)
        sev = "high" if amt > maverick_high else "medium"
        cands.append({
            "rule_id": "R15", "type": "maverick_spend", "po_id": inv["po_id"],
            "supplier_id": biller, "affected_po_line_ids": json.dumps([]),
            "affected_invoice_line_ids": json.dumps(sorted(meta["sil_ids"])) or None,
            "severity": sev, "affected_value_usd": amt,
            "root_cause": f"绕流程采购：发票 {inv['supplier_invoice_id']} 由 {biller} 开出、"
                          f"引用 {inv['po_id']}（PO 供应商 {po_sup}），biller 非该 SKU 的 approved "
                          f"供应商 → 无匹配 approved PO，绕流程金额 ${amt}",
            "detected_at": as_of_str})

    return cands
