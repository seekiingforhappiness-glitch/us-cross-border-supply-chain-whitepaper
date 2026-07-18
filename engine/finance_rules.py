"""资金流检测 R19-R21（F1 资金流域，V8-②）。

与 engine/rules.py、cost_rules.py、procurement_rules.py、warehouse_rules.py 同构：只读
data/ontology.sqlite 的 payments/supplier_invoices/invoices/sales_order_lines 表，**禁读
data/truth/**（§5 铁律）；as_of 必须显式传入（D8）——payments.as_of_date <= as_of 才可见，
未来快照不可见；overdue 为派生（as_of vs due_date 现算），绝不信 status 字段以外的派生标记。
阈值/容差/窗口一律从 config.finance 段读，不硬编码。

规则语义（converge 到 datagen 注入，绝不读真值）：
- R19 逾期应收（payment 锚）：direction='in' 且 status='scheduled'（未 paid）且 as_of > due_date +
  config.finance.overdue_receivable_days。severity：amount_usd > overdue_high_usd → critical，否则 high。
- R20 现金水位（合成窗口键锚）：窗口 (as_of, as_of+cash_watch_window_days] 内
  Σ(out.scheduled.amount, due 落窗口) − Σ(in.scheduled.amount, due 落窗口) > cash_watch_threshold_usd。
  单窗单事件，anchor=CASH14D-<as_of>，severity=critical。
- R21 付款异常（payment 锚）：paid 付款(paid_date<=as_of)中——(a) 同 (ref_type, ref_id) 有 >1 条 paid
  → 除首条外(按 payment_id 排序)均为重复；(b) |amount_usd − 单据金额| > amount_mismatch_tol_usd
  （单据金额按 ref_type 取 supplier_invoices/invoices.total_usd 或 sales_order 行合计）。severity=high。

候选结构（写库与 action_log 走 detect.apply_finance_candidates）：
  rule_id / type / anchor（payment_id 或 CASH14D-<as_of>）/ severity / affected_value_usd /
  root_cause / detected_at。资金流 RiskEvent 的 shipment_id/po_id/supplier_id/warehouse_id 留空，
  用 affected_so_line_ids（承载受影响业务对象 id=anchor）锚点（沿仓储 RiskEvent 通用列先例）。

匹配键（评估器 engine/evaluate_finance）：统一 payment_id（R20 用合成窗口键）。
"""
from datetime import date, timedelta


def _d(s):
    """ISO 日期串 → date；空/None → None。"""
    if not s:
        return None
    return date.fromisoformat(str(s)[:10])


def detect_finance_risks(con, as_of, cfg):
    """返回资金流异常候选列表（不写库）。cfg 需含 finance 段阈值/窗口/容差。"""
    fc = cfg["finance"]
    as_of_str = as_of.isoformat()
    overdue_days = fc["overdue_receivable_days"]
    window_days = fc["cash_watch_window_days"]
    win_hi = as_of + timedelta(days=window_days)
    overdue_high = fc["overdue_high_usd"]
    cash_threshold = fc["cash_watch_threshold_usd"]
    mismatch_tol = fc["amount_mismatch_tol_usd"]

    # as_of 安全（D8）：仅 as_of_date <= as_of 的 payment 可见
    payments = [dict(r) for r in con.execute(
        "SELECT * FROM payments WHERE as_of_date <= ? ORDER BY payment_id", (as_of_str,))]

    cands = []

    def emit(rule, rtype, anchor, severity, value, reason):
        cands.append({
            "rule_id": rule, "type": rtype, "anchor": anchor,
            "severity": severity, "affected_value_usd": round(value, 2),
            "root_cause": reason, "detected_at": as_of_str})

    # --- R19 逾期应收：in 向 scheduled 未付、逾期 > overdue_days ---
    for p in payments:
        if p["direction"] != "in" or p["status"] != "scheduled" or p["paid_date"]:
            continue
        due = _d(p["due_date"])
        if due is None:
            continue
        if (as_of - due).days > overdue_days:
            amt = float(p["amount_usd"])
            sev = "critical" if amt > overdue_high else "high"
            emit("R19", "overdue_receivable", p["payment_id"], sev, amt,
                 f"逾期应收：{p['payment_id']} 应收日 {p['due_date']} 已逾期 {(as_of - due).days} 天"
                 f"(> {overdue_days})、未回款（额 {'>' if amt > overdue_high else '<='} ${overdue_high} → {sev}）")

    # --- R20 现金水位：窗口内 scheduled 净流出 > 阈值（单窗单事件）---
    out_win = in_win = 0.0
    for p in payments:
        if p["status"] != "scheduled":
            continue
        due = _d(p["due_date"])
        if due is None or not (as_of < due <= win_hi):
            continue
        if p["direction"] == "out":
            out_win += float(p["amount_usd"])
        elif p["direction"] == "in":
            in_win += float(p["amount_usd"])
    net = round(out_win - in_win, 2)
    if net > cash_threshold:
        emit("R20", "cash_watch", f"CASH14D-{as_of_str}", "critical", net,
             f"现金水位预警：未来 {window_days} 天净流出 ${net} > 阈值 ${cash_threshold}"
             f"（scheduled 应付 ${round(out_win, 2)} − 应收 ${round(in_win, 2)}）")

    # --- R21 付款异常：重复付款 + 金额不符（仅 paid，paid_date<=as_of）---
    paid = [p for p in payments if p["status"] == "paid" and _d(p["paid_date"])
            and _d(p["paid_date"]) <= as_of]

    # 单据金额查询（按 ref_type）：supplier_invoice/invoice→total_usd；sales_order→行合计
    def doc_amount(ref_type, ref_id):
        if ref_type == "supplier_invoice":
            r = con.execute("SELECT total_usd FROM supplier_invoices WHERE supplier_invoice_id=?",
                            (ref_id,)).fetchone()
            return float(r[0]) if r and r[0] not in (None, "") else None
        if ref_type == "invoice":
            r = con.execute("SELECT total_usd FROM invoices WHERE invoice_id=?", (ref_id,)).fetchone()
            return float(r[0]) if r and r[0] not in (None, "") else None
        if ref_type == "sales_order":
            r = con.execute("SELECT COALESCE(SUM(qty*unit_price_usd),0) FROM sales_order_lines "
                            "WHERE so_id=?", (ref_id,)).fetchone()
            return float(r[0]) if r else None
        return None

    flagged = {}   # payment_id -> reason（去重：一笔付款至多一条 R21）

    # (a) 重复付款：同 (ref_type, ref_id) 有 >1 paid → 除首条(payment_id 最小)外均重复
    groups = {}
    for p in paid:
        groups.setdefault((p["ref_type"], p["ref_id"]), []).append(p)
    for key, grp in groups.items():
        if len(grp) > 1:
            ordered = sorted(grp, key=lambda x: x["payment_id"])
            for dup in ordered[1:]:
                flagged[dup["payment_id"]] = (
                    float(dup["amount_usd"]),
                    f"重复付款：单据 {dup['ref_id']} 已有一条 paid，{dup['payment_id']} 为重复全额结清 "
                    f"${round(float(dup['amount_usd']), 2)}")

    # (b) 金额不符：|amount − 单据金额| > 容差
    for p in paid:
        if p["payment_id"] in flagged:
            continue
        doc = doc_amount(p["ref_type"], p["ref_id"])
        if doc is None:
            continue
        amt = float(p["amount_usd"])
        if abs(round(amt - doc, 2)) > mismatch_tol:
            flagged[p["payment_id"]] = (
                amt, f"金额不符：付款额 ${round(amt, 2)} ≠ 单据金额 ${round(doc, 2)}"
                     f"（差 ${round(amt - doc, 2)} > 容差 ${mismatch_tol}）")

    for pid in sorted(flagged):
        value, reason = flagged[pid]
        emit("R21", "payment_anomaly", pid, "high", value, reason)

    return cands
