"""采购检测 R7-R10（三方对账，Build 2）+ R11-R13（富化，决策日志 P2）。

与 engine/rules.py、engine/cost_rules.py 同构：只读 data/ontology.sqlite 采购表，
禁读 data/truth/（§5 铁律）；as_of 必须显式传入（D8）——只看 received_date/issue_date/
paid_date ≤ as_of 的收货/发票/付款，未来事件不可见。容差/阈值一律从 config procurement 段读。

P2 富化（不改 R7-R10 判定；R7-R10 检出与既有逐条不变）：
- R11 开票超收货量（po_line 锚）：发票行 qty > 该行累计实收×(1+invoice_over_qty_tol)，且该行足量
  收货（received ≥ ordered×(1−short_qty_tol)，非短装 R8 域）。对比 received 而非 accepted → 与 R9 正交。
- R12 预付款敞口（po_id 锚）：deposit 已付(≤as_of)、PO 无收货、账龄 > deposit_grace_days。不信
  exposure_status 字段，由收货存在性 + 付款账龄重算。
- R13 供应商资质过期（supplier_id 锚）：某 cert_type 全过期(valid_to<as_of)且无同类有效证覆盖
  as_of，且供应商仍有 open PO(status≠closed)。聚合为供应商级一事件；不信 status 字段。

候选结构（写库与 action_log 走 detect.apply_procurement_candidates）：
  rule_id / type / po_id / supplier_id / severity / affected_value_usd / root_cause /
  detected_at / affected_po_line_ids（排序 JSON）/ affected_invoice_line_ids（R10 填发票行，其余 None）
采购 RiskEvent 的 shipment_id 留空，用 po_id/supplier_id 锚点（P1）。

匹配键 (po_id, po_line_id, rule_id)——ground truth 每键一行，评估器精确对账。

规则语义（converge 到 datagen 注入，绝不读真值）：
- R7 supplier_delay：某 po_line 的最早行级到货日（该行所有 GRN 行的 min gl.received_date）
  晚于 expected_ready_date 超 receipt_delay_tol_days → 延误。用行级 received_date 而非 GRN
  头日：GRN 头 received_date 是同单各行到货日的 min，多行 PO 里被注入延误的行会被同单准时兄弟行
  拉低的头日期掩盖（R7 漏检根因）；行级到货日是真实分批 GRN 本就有的字段，改读后延误行不再被掩盖。
  仍取该行各批次的 min（最早批次）：分批收货第二批天然更晚，用最早批次避免把正常分批误判成延误。
- R8 short_receipt：某 po_line 累计收货量 sum(received_qty) < qty×(1−short_qty_tol) → 短装。
  用 received_qty（非 accepted_qty）：QC 拒收（R9）会压低 accepted 但 received 仍足量，
  用 received 才能把"供应商少发"（R8）与"质量拒收"（R9）两个正交维度分开，避免 R9→R8 误报。
- R9 qc_failure：某 po_line 任一 GRN 行 qc_status='failed' 或 defect_ppm > defect_ppm_threshold。
- R10 price_qty_mismatch：某发票行单价与对应 po_line 单价偏离 > price_tol（或数量不符）。
"""
import json

# R13 资质证 → 严重度（与 ontology riskRules R13 + datagen.procurement.CERT_SEVERITY 同一映射；
# 引擎不 import datagen 以保持独立，故此处平行定义，业务口径单一源=ontology R13 severity）。
CERT_SEVERITY = {"factory_audit": "high", "uflpa_traceability": "high",
                 "iso9001": "medium", "product_safety_cert": "medium"}


def detect_procurement_risks(con, as_of, cfg):
    """返回采购异常候选列表（不写库）。cfg 需含 procurement 段容差/阈值。"""
    pc = cfg["procurement"]
    tol_days = pc["receipt_delay_tol_days"]
    short_tol = pc["short_qty_tol"]
    ppm_thr = pc["defect_ppm_threshold"]
    price_tol = pc["price_tol"]
    over_tol = pc["invoice_over_qty_tol"]
    grace_days = pc["deposit_grace_days"]
    exposure_high = pc["exposure_high_usd"]
    as_of_str = as_of.isoformat()

    # po_lines：权威 qty / unit_price / expected_ready（采购稽核行级视图，D2 被 PoLine 覆盖）
    po_lines = {r["po_line_id"]: dict(r) for r in con.execute(
        "SELECT * FROM po_lines ORDER BY po_line_id")}
    # 供应商恢复：po_id → supplier_id（既有 purchase_orders 头，一单一供应商不变式）
    supplier_of_po = {r["po_id"]: r["supplier_id"] for r in con.execute(
        "SELECT po_id, supplier_id FROM purchase_orders")}

    cands = []

    def emit(rule, rtype, pol_id, severity, value, reason, inv_lines=None):
        po_id = po_lines[pol_id]["po_id"]
        cands.append({
            "rule_id": rule, "type": rtype, "po_id": po_id,
            "supplier_id": supplier_of_po.get(po_id),
            "affected_po_line_ids": json.dumps([pol_id]),
            "severity": severity, "affected_value_usd": round(value, 2),
            "root_cause": reason, "detected_at": as_of_str,
            "affected_invoice_line_ids": json.dumps(sorted(inv_lines)) if inv_lines else None})

    # --- 收货聚合：每 po_line 的最早收货日 + 累计收货量 + QC 汇总（只算 GRN 头 received_date ≤ as_of）---
    # first_recv 用行级 gl.received_date（真实分批到货日），不用 GRN 头 g.received_date（该头是
    # 同单各行到货日的 min）：多行 PO 里被注入延误的行，其真实延误到货日会被同单准时兄弟行拉低的头
    # 日期掩盖，导致 R7 漏检。改读行级后延误行不再被掩盖。as_of 可见性仍按 GRN 头把关（D8，不变），
    # 故 R8/R9 的收货聚合样本与既有逐字节一致。
    grl = [dict(r) for r in con.execute(
        """SELECT gl.po_line_id, gl.received_qty, gl.accepted_qty, gl.rejected_qty,
                  gl.qc_status, gl.defect_ppm, gl.received_date AS line_received_date
           FROM goods_receipt_lines gl JOIN goods_receipts g ON g.grn_id = gl.grn_id
           WHERE g.received_date <= ?
           ORDER BY gl.po_line_id, gl.grn_line_id""", (as_of_str,))]
    recv_by_line = {}
    for r in grl:
        acc = recv_by_line.setdefault(r["po_line_id"], {
            "first_recv": None, "received": 0, "rejected": 0,
            "qc_failed": False, "max_ppm": 0})
        acc["received"] += r["received_qty"]
        acc["rejected"] += r["rejected_qty"]
        if r["qc_status"] == "failed":
            acc["qc_failed"] = True
        acc["max_ppm"] = max(acc["max_ppm"], r["defect_ppm"])
        if acc["first_recv"] is None or r["line_received_date"] < acc["first_recv"]:
            acc["first_recv"] = r["line_received_date"]

    from datetime import date
    D = date.fromisoformat

    for pol_id in sorted(recv_by_line):
        pl = po_lines[pol_id]
        acc = recv_by_line[pol_id]
        qty = pl["qty"]
        unit_price = pl["unit_price_usd"]
        exp = pl["expected_ready_date"]

        # --- R7 供应商交期延误：最早批次到货日超 expected_ready + 容差 ---
        if acc["first_recv"] is not None:
            delay = (D(acc["first_recv"]) - D(exp)).days
            excess = delay - tol_days
            if excess > 0:
                sev = "high" if excess > 7 else "medium"
                emit("R7", "supplier_delay", pol_id, sev, qty * unit_price,
                     f"供应商交期延误 {delay} 天（预期 {exp}，最早收货 {acc['first_recv']}，"
                     f"超容差 {excess} 天）")

        # --- R8 短装：累计收货量低于订购量超容差 ---
        if acc["received"] < qty * (1 - short_tol):
            short_qty = qty - acc["received"]
            sev = "high" if short_qty > qty * 0.10 else "medium"
            emit("R8", "short_receipt", pol_id, sev, short_qty * unit_price,
                 f"短装 {short_qty} 件（订 {qty} 收 {acc['received']}）")

        # --- R9 QC 不合格：整批判废（failed）或缺陷率超阈 ---
        if acc["qc_failed"] or acc["max_ppm"] > ppm_thr:
            sev = "high" if acc["qc_failed"] else "medium"
            qc = "failed" if acc["qc_failed"] else "passed"
            emit("R9", "qc_failure", pol_id, sev, acc["rejected"] * unit_price,
                 f"QC 不合格（qc_status={qc}, defect_ppm={acc['max_ppm']}, "
                 f"拒收 {acc['rejected']} 件）")

    # --- R10 价量不符：发票行单价/数量与 po_line 偏离超容差（issue_date ≤ as_of）---
    sil = [dict(r) for r in con.execute(
        """SELECT sil.supplier_invoice_line_id, sil.po_line_id, sil.qty, sil.unit_price_usd,
                  si.issue_date
           FROM supplier_invoice_lines sil
           JOIN supplier_invoices si ON si.supplier_invoice_id = sil.supplier_invoice_id
           WHERE si.issue_date <= ?
           ORDER BY sil.po_line_id, sil.supplier_invoice_line_id""", (as_of_str,))]
    for r in sil:
        pl = po_lines.get(r["po_line_id"])
        if pl is None or pl["unit_price_usd"] <= 0:
            continue
        po_price = pl["unit_price_usd"]
        po_qty = pl["qty"]
        price_over = (r["unit_price_usd"] - po_price) / po_price
        qty_dev = abs(r["qty"] - po_qty) / po_qty if po_qty else 0.0
        if abs(price_over) > price_tol:
            sev = "high" if price_over > 0.10 else "medium"
            emit("R10", "price_qty_mismatch", r["po_line_id"], sev,
                 (r["unit_price_usd"] - po_price) * po_qty,
                 f"价量不符：PO 单价 ${po_price} vs 发票单价 ${r['unit_price_usd']}"
                 f"（超 {round(price_over * 100, 1)}%）",
                 inv_lines=[r["supplier_invoice_line_id"]])
        elif qty_dev > short_tol:
            # 数量不符（本数据未注入；备语义完整）
            emit("R10", "price_qty_mismatch", r["po_line_id"], "medium",
                 abs(r["qty"] - po_qty) * po_price,
                 f"价量不符：数量 PO {po_qty} vs 发票 {r['qty']}",
                 inv_lines=[r["supplier_invoice_line_id"]])

        # --- R11 开票超收货量：发票行数量 > 该行累计实收×(1+容差) 且该行足量收货 ---
        # 独立 if（非 R10 的 elif）：R11 与 R10 正交（价 vs 收货量）。对比 received（非 accepted）：
        # QC 拒收(R9)压低 accepted 但 received 仍足量，用 received 才与 R9 正交（同 R8 既有口径）。
        # 足量收货门槛 received≥ordered×(1−short_tol) 把"短装(R8)"排除在 R11 之外（短装归 R8）。
        recv = recv_by_line.get(r["po_line_id"])
        if recv is not None:
            received = recv["received"]
            if r["qty"] > received * (1 + over_tol) and received >= po_qty * (1 - short_tol):
                over_units = r["qty"] - received
                over_ratio = over_units / received if received else 0.0
                sev = "high" if over_ratio > 0.10 else "medium"
                emit("R11", "invoice_over_receipt", r["po_line_id"], sev, over_units * po_price,
                     f"开票超收货量：实收 {received} 件 / 开票 {r['qty']} 件"
                     f"（超收 {over_units} 件未到货，占实收 {round(over_ratio * 100, 1)}%）",
                     inv_lines=[r["supplier_invoice_line_id"]])

    # --- R12 预付款敞口：deposit 已付(≤as_of)、PO 无收货(≤as_of)、超宽限天数 ---
    # 不信 exposure_status 字段：由收货存在性 + 付款账龄重算（同 build_ontology "不信状态字段"）。
    grn_pos = {row["po_id"] for row in con.execute(
        "SELECT DISTINCT po_id FROM goods_receipts WHERE received_date <= ?", (as_of_str,))}
    deposits = [dict(row) for row in con.execute(
        """SELECT payment_id, po_id, amount_usd, paid_date FROM purchase_payments
           WHERE payment_type='deposit' AND paid_date <= ? ORDER BY payment_id""", (as_of_str,))]
    pol_of_po = {}
    for pol_id, pl in po_lines.items():
        pol_of_po.setdefault(pl["po_id"], []).append(pol_id)
    for p in deposits:
        po_id = p["po_id"]
        if po_id in grn_pos:
            continue  # 已收货 → 定金被履约覆盖，无敞口
        if (D(as_of_str) - D(p["paid_date"])).days > grace_days:
            amt = p["amount_usd"]
            sev = "high" if amt > exposure_high else "medium"
            cands.append({
                "rule_id": "R12", "type": "prepayment_exposure", "po_id": po_id,
                "supplier_id": supplier_of_po.get(po_id),
                "affected_po_line_ids": json.dumps(sorted(pol_of_po.get(po_id, []))),
                "severity": sev, "affected_value_usd": round(amt, 2),
                "root_cause": f"预付款敞口：定金 ${amt} 已付（{p['paid_date']}）、PO 至今无收货、"
                              f"超 {grace_days} 天宽限",
                "detected_at": as_of_str, "affected_invoice_line_ids": None})

    # --- R13 供应商资质过期：某 cert_type 全过期且无同类有效证覆盖 as_of + 供应商仍有 open PO ---
    # supplier 级锚点（一供应商一事件，聚合多张过期证）；不信 status 字段，由 valid_from/valid_to vs as_of 重算。
    open_suppliers = {row["supplier_id"] for row in con.execute(
        "SELECT DISTINCT supplier_id FROM purchase_orders WHERE status != 'closed'")}
    quals = [dict(row) for row in con.execute(
        "SELECT supplier_id, cert_type, valid_from, valid_to FROM supplier_qualifications")]
    by_sup_cert = {}
    for q in quals:
        by_sup_cert.setdefault((q["supplier_id"], q["cert_type"]), []).append(q)
    flagged = {}  # supplier_id -> [severity, [cert_types]]
    for (sup, cert), qs in sorted(by_sup_cert.items()):
        if sup not in open_suppliers:
            continue
        has_valid = any(q["valid_from"] <= as_of_str <= q["valid_to"] for q in qs)
        has_expired = any(q["valid_to"] < as_of_str for q in qs)
        if has_expired and not has_valid:
            sev = CERT_SEVERITY.get(cert, "medium")
            cur = flagged.setdefault(sup, ["medium", []])
            cur[0] = "high" if "high" in (cur[0], sev) else "medium"
            cur[1].append(cert)
    for sup in sorted(flagged):
        sev, certs = flagged[sup]
        cands.append({
            "rule_id": "R13", "type": "qualification_expired", "po_id": None,
            "supplier_id": sup, "affected_po_line_ids": json.dumps([]),
            "severity": sev, "affected_value_usd": 0.0,
            "root_cause": f"供应商资质过期：{'/'.join(sorted(certs))} 已过期、无同类续证，"
                          f"且供应商仍有 open PO（status≠closed）",
            "detected_at": as_of_str, "affected_invoice_line_ids": None})

    return cands
