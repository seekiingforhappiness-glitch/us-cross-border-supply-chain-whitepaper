"""供应商风险感知 R22-R23（V23①，L-UX 轮3 裁决：合规/采购域"零 AI 覆盖"补位）。

与 engine/rules.py、procurement_rules.py、warehouse_rules.py 同构：只读 data/ontology.sqlite
既有表（purchase_orders / goods_receipts / po_lines / supplier_qualifications / suppliers），
**禁读 data/truth/**（§5 铁律）；as_of 必须显式传入（D8）——只看 received_date ≤ as_of 的收货，
资质有效期与 as_of 比较。阈值一律从 config supplier_risk 段读，不硬编码。

规则语义（converge 到 datagen/supplier_risk.py 独立 oracle，绝不读真值）：
- R22 performance_degradation（supplier_id 锚）：供应商 PO 级交期达成率 < perf_threshold_rate
  且样本（有收货的 PO 数）≥ perf_min_pos → 慢性绩效劣化，每供应商至多一条。
  达成口径 = **驾驶舱供应商区同款**（apps/api/cockpit._zone_suppliers 的交期达成率 SQL）：
  PO 首张 GRN 收货日 min(goods_receipts.received_date) ≤ PO 头 expected_ready_date 记达成，
  分母 = 有收货的 PO——告警数字与画面数字同源，永不打架。与 R7 的关系：R7 是单行急性延误
  （po_line 级、一次超容差就报），R22 是供应商级慢性模式（多 PO 聚合、比例判定），正交互补。
  severity：rate < perf_critical_rate → critical，否则 high。
  affected_value = 该供应商全部迟交 PO 的行金额合计（Σ po_line.qty × unit_price_usd）。
  perf_window_days > 0 时只计首收货日落 [as_of−N, as_of] 的 PO（0 = 全历史，保守缺省）。
- R23 qualification_expiry_warning（qualification_id 锚，supplier_id 同置）：某资质证
  已过期（valid_to < as_of）或临期（as_of ≤ valid_to ≤ as_of + qual_warning_days），且未补新证
  ——同 (supplier, cert_type) 存在 valid_to 更晚且 ≥ as_of 的续证即视为已补，不报。
  与 R13 的关系：R13 是供应商级合规敞口事件（某类证**全部**过期且无有效覆盖 + 仍有 open PO），
  R23 是逐张证的生命周期**预警**（含临期、不设 open PO 门）——R23 先亮灯，R13 是灯没人理后的
  击穿，感知层与敞口层正交并存。severity：过期 = high，临期 = medium。affected_value = 0
  （合规敞口非金额量纲，同 R13 惯例）。

候选结构（写库与 action_log 走 detect.apply_supplier_risk_candidates）：
  rule_id / type / supplier_id / anchor（R22=supplier_id、R23=qualification_id）/ severity /
  affected_value_usd / root_cause / detected_at
R22/R23 RiskEvent 的 shipment_id/po_id 留空：R22 锚 supplier_id 列（同 R13/R14 先例）；
R23 锚 qualification_id 载于 affected_so_line_ids 通用列（同仓储/资金流"通用列承载受影响
业务对象 id"先例），supplier_id 列同置（供 panorama 供应商节点锚定与对象卡关系区）。

匹配键（评估器 engine/evaluate_supplier_risk.py）：R22→supplier_id / R23→qualification_id。
"""
from datetime import date, timedelta

# cert_type → 中文（root_cause 人话用；未知类型原样保留不猜——同 ux_copy"不编造"纪律）
CERT_CN = {"factory_audit": "工厂审计", "uflpa_traceability": "UFLPA 溯源",
           "iso9001": "ISO9001 质量体系", "product_safety_cert": "产品安全认证"}


def detect_supplier_risks(con, as_of, cfg):
    """返回供应商风险候选列表（不写库）。cfg 需含 supplier_risk 段阈值。"""
    sc = cfg["supplier_risk"]
    thr = sc["perf_threshold_rate"]
    crit = sc["perf_critical_rate"]
    min_pos = sc["perf_min_pos"]
    window_days = sc["perf_window_days"]
    warn_days = sc["qual_warning_days"]
    as_of_str = as_of.isoformat()

    sup_name = {r["supplier_id"]: r["supplier_name"] for r in con.execute(
        "SELECT supplier_id, supplier_name FROM suppliers")}

    cands = []

    # --- R22 供应商绩效劣化：PO 级交期达成率（驾驶舱供应商区同口径）---
    # first_recv = PO 首张 GRN 收货日（received_date ≤ as_of，D8）；达成 = first_recv ≤ PO 头
    # expected_ready_date。窗口（可选）按 first_recv 过滤。
    rows = [dict(r) for r in con.execute(
        """SELECT po.po_id, po.supplier_id, po.expected_ready_date,
                  min(g.received_date) AS first_recv
           FROM purchase_orders po
           JOIN goods_receipts g ON g.po_id = po.po_id AND g.received_date <= ?
           GROUP BY po.po_id ORDER BY po.po_id""", (as_of_str,))]
    if window_days and window_days > 0:
        window_start = (as_of - timedelta(days=window_days)).isoformat()
        rows = [r for r in rows if r["first_recv"] >= window_start]

    po_value = {r["po_id"]: r["v"] for r in con.execute(
        "SELECT po_id, sum(qty * unit_price_usd) AS v FROM po_lines GROUP BY po_id")}

    perf = {}  # supplier_id -> {"measured": n, "on_time": n, "late_pos": [(po_id, value)]}
    for r in rows:
        p = perf.setdefault(r["supplier_id"], {"measured": 0, "on_time": 0, "late_pos": []})
        p["measured"] += 1
        if r["first_recv"] <= r["expected_ready_date"]:
            p["on_time"] += 1
        else:
            p["late_pos"].append((r["po_id"], float(po_value.get(r["po_id"]) or 0.0)))

    for sid in sorted(perf):
        p = perf[sid]
        if p["measured"] < min_pos:
            continue                     # 样本不足不评（防小样本误伤）
        rate = p["on_time"] / p["measured"]
        if rate >= thr:
            continue
        sev = "critical" if rate < crit else "high"
        late_ids = sorted(pid for pid, _ in p["late_pos"])
        value = sum(v for _, v in p["late_pos"])
        name = sup_name.get(sid, sid)
        cands.append({
            "rule_id": "R22", "type": "performance_degradation",
            "supplier_id": sid, "anchor": sid,
            "severity": sev, "affected_value_usd": round(value, 2),
            "root_cause": (f"供应商绩效劣化：{name} 交期达成率 "
                           f"{round(rate * 100, 1)}%（{p['on_time']}/{p['measured']} 单准时）"
                           f"< 阈值 {round(thr * 100)}%；迟交 {len(late_ids)} 单"
                           f"（{'、'.join(late_ids)}），涉及货值 ${round(value, 2)}"),
            "detected_at": as_of_str})

    # --- R23 资质过期预警：逐张证过期/临期且未补新证 ---
    warn_hi = (as_of + timedelta(days=warn_days)).isoformat()
    quals = [dict(r) for r in con.execute(
        """SELECT qualification_id, supplier_id, cert_type, valid_from, valid_to
           FROM supplier_qualifications ORDER BY qualification_id""")]
    by_sup_cert = {}
    for q in quals:
        by_sup_cert.setdefault((q["supplier_id"], q["cert_type"]), []).append(q)

    def superseded(q):
        """同 (supplier, cert_type) 存在更晚且覆盖/晚于 as_of 的续证 → 已补，不报。"""
        return any(x["qualification_id"] != q["qualification_id"]
                   and x["valid_to"] > q["valid_to"] and x["valid_to"] >= as_of_str
                   for x in by_sup_cert[(q["supplier_id"], q["cert_type"])])

    for q in quals:
        cert_cn = CERT_CN.get(q["cert_type"], q["cert_type"])
        name = sup_name.get(q["supplier_id"], q["supplier_id"])
        if q["valid_to"] < as_of_str and not superseded(q):
            days_over = (date.fromisoformat(as_of_str)
                         - date.fromisoformat(q["valid_to"])).days
            cands.append({
                "rule_id": "R23", "type": "qualification_expiry_warning",
                "supplier_id": q["supplier_id"], "anchor": q["qualification_id"],
                "severity": "high", "affected_value_usd": 0.0,
                "root_cause": (f"资质过期：{name} 的 {cert_cn}（{q['qualification_id']}）"
                               f"已于 {q['valid_to']} 过期 {days_over} 天，未见同类续证"),
                "detected_at": as_of_str})
        elif as_of_str <= q["valid_to"] <= warn_hi and not superseded(q):
            days_left = (date.fromisoformat(q["valid_to"])
                         - date.fromisoformat(as_of_str)).days
            cands.append({
                "rule_id": "R23", "type": "qualification_expiry_warning",
                "supplier_id": q["supplier_id"], "anchor": q["qualification_id"],
                "severity": "medium", "affected_value_usd": 0.0,
                "root_cause": (f"资质临期：{name} 的 {cert_cn}（{q['qualification_id']}）"
                               f"将于 {q['valid_to']} 到期（剩 {days_left} 天，"
                               f"预警窗 {warn_days} 天），未见同类续证"),
                "detected_at": as_of_str})

    return cands
