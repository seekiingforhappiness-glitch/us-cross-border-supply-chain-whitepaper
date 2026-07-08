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
    inj = pc["inject"]

    pos = world["pos"]
    skus = world["skus"]
    from .world import DESIGN_PO_NUMS

    # 同供应商 SKU 池（一单一供应商不变式的取行来源）
    skus_of_supplier = {}
    for kid in sorted(skus):
        skus_of_supplier.setdefault(skus[kid]["supplier_id"], []).append(kid)

    # 确定性选 PO：排除设计槽位，按 po_id 排序取前 n_pos
    n_pos = sum(inj[k] for k in ("clean", "gray", "r7", "r8", "r9", "r10"))
    selected = [pid for pid in sorted(pos) if _num(pid) not in DESIGN_PO_NUMS][:n_pos]

    # 画像切片（顺序：clean / gray / r7 / r8 / r9 / r10）
    profile = {}
    i = 0
    for name in ("clean", "gray", "r7", "r8", "r9", "r10"):
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
    anomalies = []
    seqs = {"pol": 0, "grn": 0, "grl": 0, "sinv": 0, "sil": 0, "epr": 0}

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
                "line_status": "received", "as_of_date": as_of.isoformat(),
                "created_at": _iso(po_date)})
            lines_meta.append([pol_id, sku_id, qty, unit_price, expected_ready])

        primary = lines_meta[0]  # 异常默认落在 primary 行

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
                # primary 行拆两张：足量部分分两批到货，属性一致（干净/灰区分批演示）
                half = r["received_qty"] // 2
                _emit_grn_line(grn_lines, nid, grn_a, pol_id, half, half, 0,
                               r["qc_status"], r["defect_ppm"], as_of, date_a)
                rem = r["received_qty"] - half
                _emit_grn_line(grn_lines, nid, grn_b, pol_id, rem, rem, 0,
                               r["qc_status"], r["defect_ppm"], as_of, date_a)
            else:
                _emit_grn_line(grn_lines, nid, grn_a, pol_id, r["received_qty"],
                               r["accepted_qty"], r["rejected_qty"], r["qc_status"],
                               r["defect_ppm"], as_of, date_a)

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

    world["procurement"] = {
        "pos": selected,
        "po_lines": po_lines,
        "goods_receipts": goods_receipts,
        "grn_lines": grn_lines,
        "supplier_invoices": supplier_invoices,
        "supplier_invoice_lines": supplier_invoice_lines,
        "anomalies": sorted(anomalies, key=lambda a: (a["po_id"], a["po_line_id"], a["rule_id"])),
        "design_cases": design_cases,
        "profile": profile,
    }
    return world["procurement"]


def _emit_grn_line(grn_lines, nid, grn_id, po_line_id, received_qty, accepted_qty,
                   rejected_qty, qc_status, defect_ppm, as_of, recv_date):
    grn_lines.append({
        "grn_line_id": nid("grl", "GRL-{:06d}"), "grn_id": grn_id,
        "po_line_id": po_line_id, "received_qty": received_qty,
        "accepted_qty": accepted_qty, "rejected_qty": rejected_qty,
        "qc_status": qc_status, "defect_ppm": defect_ppm,
        "as_of_date": as_of.isoformat(), "created_at": _iso(recv_date)})


def _weighted(rng, weights):
    r = rng.random()
    acc = 0.0
    for k, w in sorted(weights.items(), key=lambda kv: int(kv[0])):
        acc += w
        if r <= acc:
            return int(k)
    return int(sorted(weights, key=lambda k: int(k))[-1])
