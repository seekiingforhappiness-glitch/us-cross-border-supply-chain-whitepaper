"""P1 采购三方对账数据生成（Build 1/3：仅模型 + 数据 + ground truth，不做检测/动作/UI）。

与 v0.3 admission / v0.4 cost 同构的隔离设计：独立随机流 random.Random(seed+4000)，
不消耗既有随机流——控制塔/准入/费用数据逐字节零扰动，可复现性从源头保证（§7 / X2 / V2 先例）。

建模选择（每条 ≤5 行"为什么这样建"）：
- 复用既有 PurchaseOrder 作为采购单头：PoLine 是 PO 的子对象（P1 明示"加 PoLine 覆盖 D2 单 SKU 约束"），
  故不新建采购 PO 头表；supplier_id 由既有 purchase_orders.supplier_id 恢复，po_id 直接引用既有 PO。
  代价：被选 PO 的头 sku_id/qty 是单 SKU 遗留视图，权威数量/价改到 PoLine（D2 被 PoLine 覆盖）。
- po_line.expected_ready_date 独立取样（不沿用 PO 头日期）：采购稽核视图需按行控交期，且要保证
  收货/开票落在数据窗口内、可精确注入 R7 延误——独立窗口给全时序控制权。
- 一单一供应商不变式：多 SKU 行只从同供应商 SKU 池取，符合真实 P2P（一 PO 对一 vendor）。
- 每个异常 PO 只注一类规则、落在具体行：ground truth 与注入行 1:1，Build 2 检测可精确对账。
- 灰区偏差 = 对应容差×0.5：严格容差内，测未来 Build 2 的误报；干净 PO 全维度合规。

产出（写入 world["procurement"]）：
  po_lines / goods_receipts / grn_lines / supplier_invoices / supplier_invoice_lines
  anomalies   ground truth（按 (po_id, po_line_id, rule_id) 每键一行）
  design_cases  PD-A..PD-F 确定性锚点（demo/verify 用）
"""
from datetime import date, timedelta

# PO 级注入桶顺序（决策日志 P2）：既有 6 桶在前、字节不变；富化 4 桶 append 在后。
# selected 排序前缀不变 + rng 在既有 52 PO 之后才为新桶抽样 → 既有 R7-R10 数据逐字节零扰动。
PO_BUCKETS = ("clean", "gray", "r7", "r8", "r9", "r10", "r11", "r11_gray", "r12", "r12_gray")

# R11 注入幅度（固定，非随机 → P/R 精确 1.000）：实收比订购少 2.5%（落 [over_tol,short_tol]=
# [2%,3%] 带内：>2% 触发 R11 超收，<3% 不触发 R8 短装、发票仍开足订购量 → 不触 R10 数量分支）。
R11_SHORT = 0.025      # r11：received = ordered×(1−0.025)，invoice = ordered
R11_GRAY_SHORT = 0.01  # r11_gray：received = ordered×(1−0.01)，超收 ~1% < 2% 容差 → 不触发
# R13 资质证严重度：合规/审计类过期 = high，管理体系/产品认证类 = medium（gen 与 detect 同一映射）
CERT_SEVERITY = {"factory_audit": "high", "uflpa_traceability": "high",
                 "iso9001": "medium", "product_safety_cert": "medium"}


def _num(po_id):
    return int(po_id.split("-")[-1])


def _iso(d):
    return f"{d.isoformat()}T00:00:00Z"


def _clamp(d, lo, hi):
    return max(lo, min(d, hi))


def build_procurement_world(world, cfg, rng):
    """生成采购三方对账全套数据。rng 必须是 random.Random(seed + procurement.seed_offset)。"""
    pc = cfg["procurement"]
    as_of = world["as_of"]
    win_end = date.fromisoformat(cfg["window"]["end"])
    ready_lo = date.fromisoformat(pc["ready_window"][0])
    ready_hi = date.fromisoformat(pc["ready_window"][1])
    tol_days = pc["receipt_delay_tol_days"]
    short_tol = pc["short_qty_tol"]
    ppm_thr = pc["defect_ppm_threshold"]
    price_tol = pc["price_tol"]
    deposit_ratio = pc["deposit_ratio"]
    grace_days = pc["deposit_grace_days"]
    exposure_high = pc["exposure_high_usd"]
    expiring_days = pc["qual_expiring_days"]
    inj = pc["inject"]
    qinj = pc["qual_inject"]

    pos = world["pos"]
    skus = world["skus"]
    from .world import DESIGN_PO_NUMS

    # 同供应商 SKU 池（一单一供应商不变式的取行来源）
    skus_of_supplier = {}
    for kid in sorted(skus):
        skus_of_supplier.setdefault(skus[kid]["supplier_id"], []).append(kid)

    # 确定性选 PO：排除设计槽位，按 po_id 排序取前 n_pos（含富化 4 桶；前缀=既有 52 不变）
    n_pos = sum(inj[k] for k in PO_BUCKETS)
    selected = [pid for pid in sorted(pos) if _num(pid) not in DESIGN_PO_NUMS][:n_pos]

    # 画像切片（PO_BUCKETS 顺序：既有 6 桶在前 → 主循环先处理、rng 抽样与产物字节不变）
    profile = {}
    i = 0
    for name in PO_BUCKETS:
        for pid in selected[i:i + inj[name]]:
            profile[pid] = name
        i += inj[name]

    # 设计锚点：每桶首个 PO
    def first_of(name):
        for pid in selected:
            if profile[pid] == name:
                return pid
        return None
    design_cases = {"PD-E": first_of("clean"), "PD-F": first_of("gray"),
                    "PD-A": first_of("r7"), "PD-B": first_of("r8"),
                    "PD-C": first_of("r9"), "PD-D": first_of("r10")}
    case_of_po = {v: k for k, v in design_cases.items() if v}

    po_lines = []
    goods_receipts = []
    grn_lines = []
    supplier_invoices = []
    supplier_invoice_lines = []
    payments = []
    qualifications = []
    anomalies = []
    seqs = {"pol": 0, "grn": 0, "grl": 0, "sinv": 0, "sil": 0, "epr": 0, "pay": 0, "qual": 0}

    def nid(kind, fmt):
        seqs[kind] += 1
        return fmt.format(seqs[kind])

    def add_anomaly(rule_id, atype, po_id, po_line_id, supplier_id, severity,
                    value_usd, note, case_id):
        anomalies.append({
            "expected_procurement_risk_id": nid("epr", "EPR-{:05d}"),
            "rule_id": rule_id, "type": atype, "po_id": po_id,
            "po_line_id": po_line_id, "supplier_id": supplier_id,
            "severity": severity, "anomaly_value_usd": round(value_usd, 2),
            "note": note, "case_id": case_id})

    # 灰区维度轮转（每个 gray PO 只碰一个近容差维度）
    gray_dims = ["late", "short", "defect", "price"]
    gray_seen = [0]

    for pid in selected:
        prof = profile[pid]
        supplier_id = pos[pid]["supplier_id"]
        header_sku = pos[pid]["sku_id"]
        po_date = pos[pid]["po_date"]
        case_id = case_of_po.get(pid, "")

        # --- 行数与 SKU（一单一供应商；line1 = 头 sku，多行取同供应商其它 SKU）---
        pool = [k for k in skus_of_supplier.get(supplier_id, []) if k != header_sku]
        want = _weighted(rng, pc["lines_per_po_weights"])
        extra = min(want - 1, len(pool))
        line_skus = [header_sku] + (rng.sample(sorted(pool), extra) if extra > 0 else [])

        # 该 PO 的异常落在哪条行（primary line）
        gray_dim = None
        if prof == "gray":
            gray_dim = gray_dims[gray_seen[0] % len(gray_dims)]
            gray_seen[0] += 1

        # r12/r12_gray = 预付款敞口画像：PO 已下、有 po_line，但尚未收货 → line_status=open
        line_status_val = "open" if prof in ("r12", "r12_gray") else "received"
        lines_meta = []  # (po_line_id, sku_id, qty, unit_price, expected_ready)
        for j, sku_id in enumerate(line_skus):
            pol_id = nid("pol", "POL-{:06d}")
            qty = rng.randint(*pc["qty_range"])
            unit_price = round(skus[sku_id]["unit_price_usd"], 2)  # PO 议定价=目录价（干净，R10 显式注入）
            span = (ready_hi - ready_lo).days
            expected_ready = ready_lo + timedelta(days=rng.randint(0, span))
            po_lines.append({
                "po_line_id": pol_id, "po_id": pid, "sku_id": sku_id, "qty": qty,
                "unit_price_usd": unit_price, "currency": "USD",
                "expected_ready_date": expected_ready.isoformat(),
                "line_status": line_status_val, "as_of_date": as_of.isoformat(),
                "created_at": _iso(po_date)})
            lines_meta.append([pol_id, sku_id, qty, unit_price, expected_ready])

        primary = lines_meta[0]  # 异常默认落在 primary 行

        # r12/r12_gray：不生成收货/GRN/开票（open PO，尚未到货、未开票）。故 recv_by_line 不含这些
        # po_line → R7-R11 天然不触发；敞口由 deposit 付款判定（主循环后 payment pass）。R12 真值同处生成。
        if prof in ("r12", "r12_gray"):
            continue

        # --- 收货（GoodsReceipt + 行）：默认单张 GRN；clean/gray 部分分批(2 张)---
        do_partial = (prof in ("clean", "gray")
                      and rng.random() < pc["partial_receipt_rate"] and len(lines_meta) >= 1)

        # 各行的收货结果（received/accepted/rejected/qc/ppm/received_date）
        recv = {}
        for k, (pol_id, sku_id, qty, unit_price, expected_ready) in enumerate(lines_meta):
            received_qty, accepted_qty, rejected_qty = qty, qty, 0
            qc_status, defect_ppm = "passed", rng.randint(50, 800)
            recv_date = _clamp(expected_ready + timedelta(days=rng.randint(0, tol_days)),
                               ready_lo, win_end)

            if k == 0 and prof == "r7":                          # R7 供应商交期延误
                excess = rng.randint(2, 11)
                recv_date = _clamp(expected_ready + timedelta(days=tol_days + excess),
                                   ready_lo, win_end)
            elif k == 0 and prof == "r8":                        # R8 短装（收货少于订购）
                short_ratio = rng.uniform(short_tol + 0.05, 0.20)
                received_qty = max(1, int(round(qty * (1 - short_ratio))))
                accepted_qty, rejected_qty = received_qty, 0
            elif k == 0 and prof == "r9":                        # R9 QC 不合格
                if rng.random() < 0.5:
                    qc_status = "failed"                          # 整批判废 → high
                    defect_ppm = rng.randint(ppm_thr + 2000, ppm_thr + 12000)
                    rejected_qty = max(1, int(round(qty * rng.uniform(0.15, 0.4))))
                    accepted_qty = qty - rejected_qty
                else:
                    qc_status = "passed"                         # 缺陷率超阈但未整批判废 → medium
                    defect_ppm = rng.randint(ppm_thr + 200, ppm_thr + 1500)
                    rejected_qty = max(1, int(round(qty * rng.uniform(0.02, 0.08))))
                    accepted_qty = qty - rejected_qty
            elif k == 0 and gray_dim == "late":                  # 灰区：恰在容差（不越界）
                recv_date = _clamp(expected_ready + timedelta(days=tol_days), ready_lo, win_end)
            elif k == 0 and gray_dim == "short":
                short_amt = int(qty * short_tol * 0.5)            # 短量 = 0.5×容差 < 容差
                received_qty = qty - short_amt
                accepted_qty, rejected_qty = received_qty, 0
            elif k == 0 and gray_dim == "defect":
                defect_ppm = int(ppm_thr * 0.8)                  # ppm = 0.8×阈值 < 阈值
            elif k == 0 and prof == "r11":                       # R11 开票超收货量
                # 实收比订购少 2.5%（发票仍开足订购量，见下）→ 发票 > 实收 → 超收；
                # 2.5% < short_tol(3%) → 非短装 R8；发票=订购 → 不触 R10 数量分支。
                received_qty = qty - round(qty * R11_SHORT)
                accepted_qty, rejected_qty = received_qty, 0
            elif k == 0 and prof == "r11_gray":                  # 灰区：超收 ~1% < 2% 容差
                received_qty = qty - round(qty * R11_GRAY_SHORT)
                accepted_qty, rejected_qty = received_qty, 0

            recv[pol_id] = dict(received_qty=received_qty, accepted_qty=accepted_qty,
                                rejected_qty=rejected_qty, qc_status=qc_status,
                                defect_ppm=defect_ppm, recv_date=recv_date)

        # 写 GRN（分批：primary 行拆两张；其余行落第一张）
        grn_a = nid("grn", "GRN-2026-{:05d}")
        date_a = min(recv[m[0]]["recv_date"] for m in lines_meta)
        goods_receipts.append({
            "grn_id": grn_a, "po_id": pid, "received_date": date_a.isoformat(),
            "status": "received", "as_of_date": as_of.isoformat(), "created_at": _iso(date_a)})
        grn_b = None
        if do_partial:
            date_b = _clamp(date_a + timedelta(days=rng.randint(3, 8)), ready_lo, win_end)
            grn_b = nid("grn", "GRN-2026-{:05d}")
            goods_receipts.append({
                "grn_id": grn_b, "po_id": pid, "received_date": date_b.isoformat(),
                "status": "received", "as_of_date": as_of.isoformat(), "created_at": _iso(date_b)})

        for k, (pol_id, sku_id, qty, unit_price, expected_ready) in enumerate(lines_meta):
            r = recv[pol_id]
            if grn_b and k == 0:
                # primary 行拆两张：足量部分分两批到货，属性一致（干净/灰区分批演示）。
                # 行级 received_date 按批次真实到货日：首批=本行 recv_date，次批=date_b（更晚）。
                half = r["received_qty"] // 2
                _emit_grn_line(grn_lines, nid, grn_a, pol_id, half, half, 0,
                               r["qc_status"], r["defect_ppm"], as_of, date_a, r["recv_date"])
                rem = r["received_qty"] - half
                _emit_grn_line(grn_lines, nid, grn_b, pol_id, rem, rem, 0,
                               r["qc_status"], r["defect_ppm"], as_of, date_a, date_b)
            else:
                # 单批：行级 received_date = 本行真实（含注入延误）到货日 recv_date，
                # 不再被同单准时兄弟行拉低的 GRN 头 min 掩盖（R7 多行 PO 缺口修复）。
                _emit_grn_line(grn_lines, nid, grn_a, pol_id, r["received_qty"],
                               r["accepted_qty"], r["rejected_qty"], r["qc_status"],
                               r["defect_ppm"], as_of, date_a, r["recv_date"])

        # --- 供应商发票（+行）：逐行对应 po_line；R10 单价超差 ---
        sinv_id = nid("sinv", "SINV-2026-{:05d}")
        issue_date = _clamp(date_a + timedelta(days=rng.randint(2, 7)), ready_lo, win_end)
        total = 0.0
        for k, (pol_id, sku_id, qty, unit_price, expected_ready) in enumerate(lines_meta):
            inv_unit = unit_price
            inv_qty = qty
            if k == 0 and prof == "r10":
                inv_unit = round(unit_price * (1 + rng.uniform(price_tol + 0.03, 0.25)), 2)
            elif k == 0 and gray_dim == "price":
                inv_unit = round(unit_price * (1 + price_tol * 0.5), 2)  # 0.5×容差 < 容差
            amount = round(inv_qty * inv_unit, 2)
            total += amount
            supplier_invoice_lines.append({
                "supplier_invoice_line_id": nid("sil", "SIL-{:06d}"),
                "supplier_invoice_id": sinv_id, "po_line_id": pol_id, "qty": inv_qty,
                "unit_price_usd": inv_unit, "amount_usd": amount,
                "as_of_date": as_of.isoformat(), "created_at": _iso(issue_date)})
        supplier_invoices.append({
            "supplier_invoice_id": sinv_id, "supplier_id": supplier_id, "po_id": pid,
            "vendor_invoice_no": f"SUP{rng.randint(100000, 999999)}",
            "issue_date": issue_date.isoformat(), "currency": "USD",
            "total_usd": round(total, 2), "status": "received",
            "as_of_date": as_of.isoformat(), "created_at": _iso(issue_date)})

        # --- ground truth（异常 PO 各一行，落 primary 行）---
        pol_id, sku_id, qty, unit_price, expected_ready = primary
        r = recv[pol_id]
        if prof == "r7":
            delay = (r["recv_date"] - expected_ready).days
            excess = delay - tol_days
            sev = "high" if excess > 7 else "medium"
            add_anomaly("R7", "supplier_delay", pid, pol_id, supplier_id, sev,
                        qty * unit_price,
                        f"供应商交期延误 {delay} 天（预期 {expected_ready.isoformat()}，"
                        f"实收 {r['recv_date'].isoformat()}，超容差 {excess} 天）", case_id)
        elif prof == "r8":
            short_qty = qty - r["received_qty"]
            sev = "high" if short_qty > qty * 0.10 else "medium"
            add_anomaly("R8", "short_receipt", pid, pol_id, supplier_id, sev,
                        short_qty * unit_price,
                        f"短装 {short_qty} 件（订 {qty} 收 {r['received_qty']}）", case_id)
        elif prof == "r9":
            sev = "high" if r["qc_status"] == "failed" else "medium"
            add_anomaly("R9", "qc_failure", pid, pol_id, supplier_id, sev,
                        r["rejected_qty"] * unit_price,
                        f"QC 不合格（qc_status={r['qc_status']}, defect_ppm={r['defect_ppm']}, "
                        f"拒收 {r['rejected_qty']} 件）", case_id)
        elif prof == "r10":
            inv_unit = next(l["unit_price_usd"] for l in supplier_invoice_lines
                            if l["po_line_id"] == pol_id)
            over = (inv_unit - unit_price) / unit_price
            sev = "high" if over > 0.10 else "medium"
            add_anomaly("R10", "price_qty_mismatch", pid, pol_id, supplier_id, sev,
                        (inv_unit - unit_price) * qty,
                        f"价量不符：PO 单价 ${unit_price} vs 发票单价 ${inv_unit}"
                        f"（超 {round(over * 100, 1)}%）", case_id)
        elif prof == "r11":
            inv_qty = qty  # 发票开足订购量（默认 inv_qty=qty），实收短 2.5% → 超收
            over_units = inv_qty - r["received_qty"]
            over_ratio = over_units / r["received_qty"] if r["received_qty"] else 0
            sev = "high" if over_ratio > 0.10 else "medium"
            add_anomaly("R11", "invoice_over_receipt", pid, pol_id, supplier_id, sev,
                        over_units * unit_price,
                        f"开票超收货量：实收 {r['received_qty']} 件 / 开票 {inv_qty} 件"
                        f"（超收 {over_units} 件未到货，占实收 {round(over_ratio * 100, 1)}%）", case_id)

    # === PurchasePayment（挂 PO）+ R12 预付款敞口真值（决策日志 P2）===
    # 每 selected PO 一笔预付定金。有收货的 PO 定金 released/covered；r12/r12_gray（无收货）为敞口。
    # 检测不信 exposure_status 字段（denormalized hint），由收货 + 付款日重算（同 build_ontology
    # "不信状态字段"哲学）。付款在主循环后统一抽 rng → 既有 52 PO 的 po_line/GRN/发票逐字节零扰动。
    po_total = {}
    for l in po_lines:
        po_total[l["po_id"]] = po_total.get(l["po_id"], 0.0) + l["qty"] * l["unit_price_usd"]
    pos_with_grn = {g["po_id"] for g in goods_receipts}
    for pid in selected:
        prof = profile[pid]
        supplier_id = pos[pid]["supplier_id"]
        po_date = pos[pid]["po_date"]
        case_id = case_of_po.get(pid, "")
        deposit_amt = round(deposit_ratio * po_total.get(pid, 0.0), 2)
        if prof == "r12":                       # 敞口：超宽限、无收货
            paid = _clamp(as_of - timedelta(days=grace_days + rng.randint(5, 20)), ready_lo, as_of)
            exposure = "at_risk"
        elif prof == "r12_gray":                # 灰区：仍在宽限期内
            paid = _clamp(as_of - timedelta(days=rng.randint(1, grace_days - 2)), ready_lo, as_of)
            exposure = "covered"
        else:                                   # 有收货 → 定金已被履约覆盖
            paid = _clamp(po_date + timedelta(days=rng.randint(3, 10)), ready_lo, as_of)
            exposure = "released" if pid in pos_with_grn else "covered"
        payments.append({
            "payment_id": nid("pay", "PAY-2026-{:05d}"), "po_id": pid, "payment_type": "deposit",
            "amount_usd": deposit_amt, "paid_date": paid.isoformat(),
            "exposure_status": exposure, "as_of_date": as_of.isoformat(),
            "created_at": _iso(paid)})
        if prof == "r12":  # 注入即真值（detect 同判定：deposit≤as_of + 无 GRN + (as_of−paid)>grace）
            sev = "high" if deposit_amt > exposure_high else "medium"
            add_anomaly("R12", "prepayment_exposure", pid, "", supplier_id, sev, deposit_amt,
                        f"预付款敞口：定金 ${deposit_amt} 已付（{paid.isoformat()}）、PO 至今无收货、"
                        f"超 {grace_days} 天宽限（敞口 {'>' if deposit_amt > exposure_high else '≤'} "
                        f"${exposure_high} → {sev}）", case_id)

    # === SupplierQualification（挂 Supplier）+ R13 资质过期真值（决策日志 P2）===
    # 每供应商基础发一张有效 iso9001；R13 target 另发一张过期证（无同类续证）。R13 锚 supplier_id：
    # 某 cert_type 全部过期且无同类有效证覆盖 as_of + 供应商仍有 open PO(status≠closed) → 敞口。
    # 检测不信 status 字段，由 valid_from/valid_to vs as_of 重算（as-of 安全）。
    suppliers_all = sorted({pos[pid]["supplier_id"] for pid in pos})
    open_suppliers = sorted({pos[pid]["supplier_id"] for pid in pos
                             if pos[pid].get("status") != "closed"})
    r13_targets = open_suppliers[:qinj["r13"]]
    gray_pool = [s for s in open_suppliers if s not in r13_targets]
    r13_gray = gray_pool[:qinj["r13_gray"]]
    r13_expiring = gray_pool[qinj["r13_gray"]:qinj["r13_gray"] + qinj["r13_expiring"]]

    def _emit_qual(supplier_id, cert_type, vfrom, vto, status, evidence="verified"):
        qualifications.append({
            "qualification_id": nid("qual", "QUAL-{:05d}"), "supplier_id": supplier_id,
            "cert_type": cert_type, "evidence_status": evidence,
            "valid_from": vfrom.isoformat(), "valid_to": vto.isoformat(),
            "status": status, "as_of_date": as_of.isoformat(), "created_at": _iso(vfrom)})

    for supplier_id in suppliers_all:
        # 基础有效证（管理体系）：保证非 target 供应商全绿、且不与 target 过期证同 cert_type
        _emit_qual(supplier_id, "iso9001", as_of - timedelta(days=200),
                   as_of + timedelta(days=300), "valid")
        if supplier_id in r13_targets:
            ti = r13_targets.index(supplier_id)   # 前 3 = factory_audit(high)，其余 = product_safety_cert(medium)
            cert = "factory_audit" if ti < 3 else "product_safety_cert"
            vto = as_of - timedelta(days=30)
            _emit_qual(supplier_id, cert, as_of - timedelta(days=400), vto, "expired")
            sev = CERT_SEVERITY[cert]
            add_anomaly("R13", "qualification_expired", "", "", supplier_id, sev, 0.0,
                        f"供应商资质过期：{cert} 有效期至 {vto.isoformat()}（已过期、无同类续证），"
                        f"且供应商仍有 open PO（status≠closed）", "")
        elif supplier_id in r13_gray:            # 灰区：过期证 + 同类有效续证覆盖 as_of → 不触发
            _emit_qual(supplier_id, "factory_audit", as_of - timedelta(days=400),
                       as_of - timedelta(days=20), "expired")
            _emit_qual(supplier_id, "factory_audit", as_of - timedelta(days=10),
                       as_of + timedelta(days=355), "valid")
        elif supplier_id in r13_expiring:        # 灰区：临期（valid_to≥as_of，未过期）→ 不触发
            _emit_qual(supplier_id, "factory_audit", as_of - timedelta(days=200),
                       as_of + timedelta(days=max(1, expiring_days // 2)), "expiring")
        else:                                    # 全绿：有效 factory_audit
            _emit_qual(supplier_id, "factory_audit", as_of - timedelta(days=100),
                       as_of + timedelta(days=265), "valid")

    # 真值输出（§5 铁律）：R7-R10 原序原字节在前（只按既有键排序，逐字节不变），R11-R13 append 在后。
    r710 = sorted([a for a in anomalies if a["rule_id"] in ("R7", "R8", "R9", "R10")],
                  key=lambda a: (a["po_id"], a["po_line_id"], a["rule_id"]))
    rich = sorted([a for a in anomalies if a["rule_id"] in ("R11", "R12", "R13")],
                  key=lambda a: (a["rule_id"], a["po_id"], a["po_line_id"], a["supplier_id"]))

    world["procurement"] = {
        "pos": selected,
        "po_lines": po_lines,
        "goods_receipts": goods_receipts,
        "grn_lines": grn_lines,
        "supplier_invoices": supplier_invoices,
        "supplier_invoice_lines": supplier_invoice_lines,
        "payments": payments,
        "qualifications": qualifications,
        "anomalies": r710 + rich,
        "design_cases": design_cases,
        "profile": profile,
        "qual_roles": {"r13": r13_targets, "r13_gray": r13_gray, "r13_expiring": r13_expiring},
    }
    return world["procurement"]


def _emit_grn_line(grn_lines, nid, grn_id, po_line_id, received_qty, accepted_qty,
                   rejected_qty, qc_status, defect_ppm, as_of, created_date, line_recv_date):
    # created_date 沿用 GRN 头日（既有 created_at 语义不变）；line_recv_date 为新增行级到货日
    # （真实分批 GRN 本就有行级到货日）——R7 改读行级 received_date 而非 GRN 头 min。
    grn_lines.append({
        "grn_line_id": nid("grl", "GRL-{:06d}"), "grn_id": grn_id,
        "po_line_id": po_line_id, "received_qty": received_qty,
        "accepted_qty": accepted_qty, "rejected_qty": rejected_qty,
        "qc_status": qc_status, "defect_ppm": defect_ppm,
        "received_date": line_recv_date.isoformat(),
        "as_of_date": as_of.isoformat(), "created_at": _iso(created_date)})


def _weighted(rng, weights):
    r = rng.random()
    acc = 0.0
    for k, w in sorted(weights.items(), key=lambda kv: int(kv[0])):
        acc += w
        if r <= acc:
            return int(k)
    return int(sorted(weights, key=lambda k: int(k))[-1])
