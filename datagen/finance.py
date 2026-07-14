"""F1 资金流域数据生成（V8-②：Payment 收付一本子 + R19-R21 真值注入）。

独立随机流 random.Random(seed + finance.seed_offset=7000)，在 sourcing(seed+6000) 之后、
不消耗既有随机流——控制塔/准入/费用/采购/仓储/询价数据逐字节零扰动（§7 / V2 / X2 / P1 先例）。
finance 是 build() 最后一个域，故本模块对 world 的任何写入（含 SKU declared_value 回填、
Supplier.payment_terms_days）都不影响既有 8 个真值文件（其生成早于本模块、随机流独立）。

建模选择（每条 ≤5 行"为什么这样建"）：
- Payment 收付一本子（问1）：direction=in/out 单对象，out 挂供应商发票/物流发票、in 挂销售订单；
  amount == 单据金额（正常付款足额结清），只有 R21 注入才偏离——保证 R21 金额分支真值可控。
- due 推算（问3）：out-supplier due=issue+Supplier.payment_terms_days；out-vendor due=issue+vendor 常量；
  in due=order_date+对客常量 30。账期档位 30/45/60 由 finance 流赋（不扰动既有 supplier 数据）。
- 三规则正交注入（真值 1:1，P/R=1.000）：
  · R19 逾期应收：预选老龄 SO(due<as_of-7) 中 8 个置 scheduled/未付 = 真值；其余老龄 SO 强制 paid
    （杜绝意外逾期→零 FP），近期 SO 按 in_paid_rate 分。
  · R20 现金水位：取最大的若干供应商发票构成"密集应付段"——scheduled 且 due 落 (as_of, as_of+14]，
    累计 > 阈值+余量必击穿；同时任何非 cluster 付款若 scheduled 且 due 落窗口一律强制 paid，
    使窗口内 scheduled 仅剩 cluster → 净流出 = cluster 累计（datagen 与引擎同式，真值=检出）。单窗单事件。
  · R21 付款异常：3 张发票各发两条 paid（第二条=duplicate 锚）；4 张发票 paid amount=单据+偏移(>容差)。
    两组与 cluster/彼此不相交，正常付款 amount 足额 → 仅注入触发、零 FP。
- 顺手修复（B1 歧义#7）：基础目录 20 个 active SKU 的 declared_value_usd 空串 → round(uniform(2,20),2)
  （与准入 SKU 同分布逻辑）。该字段无任何规则/真值消费，用独立 finance 流回填 → 8 真值 md5 不变。

产出（写入 world["finance"]）：payments（全部 Payment 行）+ anomalies（expected_finance_risks 真值）。
真值锚点统一 payment_id（R20 用合成窗口键 CASH14D-<as_of>）；评估器 _anchor 同口径。
"""
from datetime import date, timedelta


def _iso(d):
    return f"{d.isoformat()}T00:00:00Z"


def build_finance_world(world, cfg, rng):
    """生成资金流全套数据。rng 必须是 random.Random(seed + finance.seed_offset)。"""
    fc = cfg["finance"]
    inj = fc["inject"]
    as_of = world["as_of"]
    window_days = fc["cash_watch_window_days"]
    overdue_days = fc["overdue_receivable_days"]
    win_hi = as_of + timedelta(days=window_days)

    # --- 0) 顺手修复（B1 歧义#7，item 4）：基础目录 active SKU 的 declared_value_usd 空串回填 ---
    # 独立 finance 流、finance 为最后域 → 不扰动任何既有真值（该字段无规则/真值消费）。
    for kid in sorted(world["skus"]):
        k = world["skus"][kid]
        if k.get("sku_status") == "active" and not k.get("declared_value_usd"):
            k["declared_value_usd"] = round(rng.uniform(2, 20), 2)

    # --- 1) Supplier.payment_terms_days 赋档（问3 账期）---
    for sid in sorted(world["suppliers"]):
        world["suppliers"][sid]["payment_terms_days"] = rng.choice(fc["supplier_terms_days"])
    supplier_terms = {sid: world["suppliers"][sid]["payment_terms_days"]
                      for sid in world["suppliers"]}

    payments = []
    anomalies = []
    _seq = [0]

    def new_pid():
        _seq[0] += 1
        return f"PAY-{_seq[0]:06d}"

    def emit(direction, ctype, cid, ref_type, ref_id, amount, due, paid_date, status, start):
        row = {
            "payment_id": new_pid(), "direction": direction,
            "counterparty_type": ctype, "counterparty_id": cid,
            "ref_type": ref_type, "ref_id": ref_id,
            "amount_usd": round(amount, 2),
            "due_date": due.isoformat(),
            "paid_date": paid_date.isoformat() if paid_date else "",
            "status": status,
            "as_of_date": as_of.isoformat(),
            "created_at": _iso(start),
        }
        payments.append(row)
        return row

    def add_truth(rule_id, rtype, payment_id, ref_type, ref_id, direction, cid, severity, value, note):
        anomalies.append({
            "expected_finance_risk_id": f"EFR-{len(anomalies) + 1:05d}",
            "rule_id": rule_id, "type": rtype, "payment_id": payment_id,
            "ref_type": ref_type, "ref_id": ref_id, "direction": direction,
            "counterparty_id": cid, "severity": severity,
            "anomaly_value_usd": round(value, 2), "note": note})

    def pick_paid(start, due):
        """付款日：[lo, hi] 内随机，hi=min(due, as_of)、lo=min(start, hi)；恒 <= as_of（D8）。
        lo 夹到 hi 防单据未来签发(start>as_of)时 span 下溢返回 > as_of（否则违 D8）。"""
        hi = min(due, as_of)
        lo = min(start, hi)
        span = max(0, (hi - lo).days)
        return lo + timedelta(days=rng.randint(0, span))

    def decide_out_status(due, start):
        """out 向 paid/scheduled：① 窗口内(scheduled 会污染 R20)一律 paid；② 单据未来签发
        (start>as_of，不能预先支付)→ scheduled；③ 其余按 out_paid_rate。窗口检查在前保 R20 洁净
        （账期>=30>14 天，未来单据的 due 恒在窗口外，两分支不冲突）。"""
        if as_of < due <= win_hi:
            return "paid"
        if start > as_of:
            return "scheduled"
        return "paid" if rng.random() < fc["out_paid_rate"] else "scheduled"

    # =========================================================================
    # OUT 向：供应商发票（应付货款）——含 maverick 发票，与 ap_supplier_invoices 一致口径
    # =========================================================================
    proc = world["procurement"]
    src = world.get("sourcing", {})
    supplier_invoices = (sorted(proc["supplier_invoices"], key=lambda x: x["supplier_invoice_id"])
                         + sorted(src.get("maverick_invoices", []),
                                  key=lambda x: x["supplier_invoice_id"]))

    # R20 cluster：按发票额降序累计到 > 阈值 + 余量（密集应付段，scheduled 落窗口 → 击穿现金水位）
    by_total = sorted(supplier_invoices, key=lambda x: (-float(x["total_usd"]), x["supplier_invoice_id"]))
    cluster, cum = [], 0.0
    need = fc["cash_watch_threshold_usd"] + inj["r20_cluster_margin_usd"]
    for si in by_total:
        if cum > need:
            break
        cluster.append(si["supplier_invoice_id"])
        cum += float(si["total_usd"])
    cluster_set = set(cluster)

    # R21 目标（从非 cluster 供应商发票按 id 顺序取，互不相交）
    non_cluster = [si for si in supplier_invoices if si["supplier_invoice_id"] not in cluster_set]
    r21_mismatch_ids = {si["supplier_invoice_id"] for si in non_cluster[:inj["r21_mismatch"]]}
    r21_dup_ids = {si["supplier_invoice_id"]
                   for si in non_cluster[inj["r21_mismatch"]:inj["r21_mismatch"] + inj["r21_dup"]]}

    for i, si in enumerate(supplier_invoices):
        sid = si["supplier_invoice_id"]
        total = float(si["total_usd"])
        supplier_id = si["supplier_id"]
        issue = date.fromisoformat(si["issue_date"])
        terms = supplier_terms.get(supplier_id, fc["supplier_terms_days"][0])
        due = issue + timedelta(days=terms)

        if sid in cluster_set:
            # 密集应付段：scheduled，due 落 (as_of, as_of+window] 内错开
            cdue = as_of + timedelta(days=1 + (cluster.index(sid) % (window_days - 1)))
            emit("out", "supplier", supplier_id, "supplier_invoice", sid, total, cdue, None,
                 "scheduled", issue)
            continue
        if sid in r21_mismatch_ids:
            # 金额不符：paid，amount = 单据 + 偏移(>容差)
            offset = round(total * 0.08 + 100.0, 2)
            paid = pick_paid(issue, due)
            row = emit("out", "supplier", supplier_id, "supplier_invoice", sid, total + offset,
                       due, paid, "paid", issue)
            add_truth("R21", "payment_anomaly", row["payment_id"], "supplier_invoice", sid, "out",
                      supplier_id, "high", total + offset,
                      f"金额不符：付款额 ${round(total + offset, 2)} ≠ 单据金额 ${round(total, 2)}"
                      f"（差 ${offset} > 容差 ${fc['amount_mismatch_tol_usd']}）")
            continue
        if sid in r21_dup_ids:
            # 重复付款：两条 paid（第二条=duplicate 锚），amount 均足额（仅触发 dup 分支）
            p1 = pick_paid(issue, due)
            emit("out", "supplier", supplier_id, "supplier_invoice", sid, total, due, p1, "paid", issue)
            p2 = pick_paid(issue, due)
            dup = emit("out", "supplier", supplier_id, "supplier_invoice", sid, total, due, p2,
                       "paid", issue)
            add_truth("R21", "payment_anomaly", dup["payment_id"], "supplier_invoice", sid, "out",
                      supplier_id, "high", total,
                      f"重复付款：单据 {sid} 已有一条 paid，本条为重复全额结清 ${round(total, 2)}")
            continue
        # 正常：amount 足额，按 out_paid_rate 决定 paid/scheduled
        status = decide_out_status(due, issue)
        paid = pick_paid(issue, due) if status == "paid" else None
        emit("out", "supplier", supplier_id, "supplier_invoice", sid, total, due, paid, status, issue)

    # =========================================================================
    # OUT 向：物流/费用发票（应付 vendor 费用）——正常付款，不注入异常
    # =========================================================================
    vendor_terms = fc["vendor_terms_days"]
    for iv in sorted(world["cost"]["invoices"], key=lambda x: x["invoice_id"]):
        total = float(iv["total_usd"])
        issue = date.fromisoformat(iv["issue_date"])
        due = issue + timedelta(days=vendor_terms)
        status = decide_out_status(due, issue)
        paid = pick_paid(issue, due) if status == "paid" else None
        emit("out", "vendor", iv["vendor_name"], "invoice", iv["invoice_id"], total, due, paid,
             status, issue)

    # =========================================================================
    # IN 向：销售订单回款（应收）——due = order_date + 对客常量；R19 逾期应收注入
    # =========================================================================
    cust_terms = fc["customer_terms_days"]
    so_amount = {}
    for ln in world["lines"].values():
        so_amount[ln["so_id"]] = so_amount.get(ln["so_id"], 0.0) \
            + float(ln["qty"]) * float(ln["unit_price_usd"])

    sos = sorted(world["sos"].values(), key=lambda x: x["so_id"])
    overdue_cut = as_of - timedelta(days=overdue_days)   # due < 此 → 逾期 > overdue_days
    # R19 候选 = 老龄 SO（due < overdue_cut），取前 r19 个置逾期；其余老龄 SO 强制 paid
    old_sos = [s for s in sos if (s["order_date"] + timedelta(days=cust_terms)) < overdue_cut]
    r19_ids = {s["so_id"] for s in old_sos[:inj["r19"]]}

    for s in sos:
        so_id = s["so_id"]
        cid = s["customer_id"]
        order_date = s["order_date"]
        amount = so_amount.get(so_id, 0.0)
        due = order_date + timedelta(days=cust_terms)
        is_old = due < overdue_cut
        if so_id in r19_ids:
            row = emit("in", "customer", cid, "sales_order", so_id, amount, due, None,
                       "scheduled", order_date)
            sev = "critical" if amount > fc["overdue_high_usd"] else "high"
            add_truth("R19", "overdue_receivable", row["payment_id"], "sales_order", so_id, "in",
                      cid, sev, amount,
                      f"逾期应收：回款 ${round(amount, 2)} 应收日 {due.isoformat()} 已逾期 "
                      f"{(as_of - due).days} 天(> {overdue_days})、未回款"
                      f"（额 {'>' if amount > fc['overdue_high_usd'] else '<='} "
                      f"${fc['overdue_high_usd']} → {sev}）")
            continue
        if is_old:
            status, paid = "paid", pick_paid(order_date, due)   # 老龄非注入 → 强制已回（零 FP）
        else:
            # 近期 SO：窗口内 scheduled 会污染 R20 → 强制 paid；其余按 in_paid_rate
            if as_of < due <= win_hi:
                status, paid = "paid", pick_paid(order_date, due)
            elif rng.random() < fc["in_paid_rate"]:
                status, paid = "paid", pick_paid(order_date, due)
            else:
                status, paid = "scheduled", None
        emit("in", "customer", cid, "sales_order", so_id, amount, due, paid, status, order_date)

    # =========================================================================
    # R20：现金水位单窗单事件真值（净流出 = 窗口内 scheduled out − scheduled in；in 已清零）
    # =========================================================================
    out_win = sum(row["amount_usd"] for row in payments
                  if row["direction"] == "out" and row["status"] == "scheduled"
                  and as_of < date.fromisoformat(row["due_date"]) <= win_hi)
    in_win = sum(row["amount_usd"] for row in payments
                 if row["direction"] == "in" and row["status"] == "scheduled"
                 and as_of < date.fromisoformat(row["due_date"]) <= win_hi)
    net = round(out_win - in_win, 2)
    if net > fc["cash_watch_threshold_usd"]:
        add_truth("R20", "cash_watch", f"CASH14D-{as_of.isoformat()}", "", "", "", "", "critical",
                  net, f"现金水位预警：未来 {window_days} 天净流出 ${net} > 阈值 "
                       f"${fc['cash_watch_threshold_usd']}（scheduled 应付 ${round(out_win, 2)} − "
                       f"应收 ${round(in_win, 2)}）")

    # 真值排序：规则 → 锚点（稳定、可复现）
    anomalies.sort(key=lambda a: (a["rule_id"], a["payment_id"]))
    for i, a in enumerate(anomalies, 1):
        a["expected_finance_risk_id"] = f"EFR-{i:05d}"

    world["finance"] = {"payments": payments, "anomalies": anomalies}
    return world["finance"]
