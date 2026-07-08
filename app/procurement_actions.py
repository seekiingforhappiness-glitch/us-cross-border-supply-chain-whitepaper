"""P1 采购摄入动作（Build 3/3）：RecordGoodsReceipt / MatchSupplierInvoice。

与 app/actions.py、app/admission_actions.py 同一套模式：权限→前置→成功→失败→审计，
统一返回 {ok, object_id, side_effects, error}，从不抛异常给调用方；审计时间戳用 as_of（D8）。

设计动机（≤5 行「为什么这样建」）：
- 摄入即事实源：收货/供票是采购三方对账的原始事实，动作只**记录事实**，不判风险——
  R7-R10 风险一律由 engine.detect_procurement_risks 从事实里检出（P1 铁律：动作不判风险）。
- 收货是仓储/运营职责（RecordGoodsReceipt: {ops}）；供票匹配是应付/财务职责
  （MatchSupplierInvoice: {finance}）——与既有 ROLE_PERMS 域划分一致，不复制权限。
- 不注册为 agent 工具：摄入是人执行的事实录入，agent 只在对象工作台做只读分析/起草提案。
"""
try:
    from .action_context import transaction
    from .actions import _log, _res
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from action_context import transaction
    from actions import _log, _res

# 采购摄入动作权限矩阵（system 供引擎/自动化用；收货=运营，供票匹配=财务）
PROC_PERMS = {
    "RecordGoodsReceipt": {"ops", "system"},
    "MatchSupplierInvoice": {"finance", "system"},
}
GRN_LINE_KEYS = {"po_line_id", "received_qty", "accepted_qty", "rejected_qty",
                 "qc_status", "defect_ppm"}
SINV_LINE_KEYS = {"po_line_id", "qty", "unit_price_usd"}
QC_STATUSES = {"passed", "failed"}


def _denied(con, action, target, actor, role, as_of):
    cur = con.cursor()
    _log(cur, actor, role, action, target, {}, as_of, "denied: role not permitted")
    con.commit()
    return _res(False, error=f"权限拒绝：角色 {role} 不允许执行 {action}（已记录审计）")


def _fail(con, action, target, actor, role, as_of, msg, params=None):
    cur = con.cursor()
    _log(cur, actor, role, action, target, params or {}, as_of, f"rejected: {msg}")
    con.commit()
    return _res(False, error=msg)


def _next_seq_id(cur, table, id_col, prefix, width):
    """下一个稳定序号 id（count+1 起，遇冲突向上找空位）——避让 datagen 种子已占用的号段。"""
    n = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0] + 1
    cand = f"{prefix}{n:0{width}d}"
    while cur.execute(f"SELECT 1 FROM {table} WHERE {id_col}=?", (cand,)).fetchone():
        n += 1
        cand = f"{prefix}{n:0{width}d}"
    return cand


def record_goods_receipt(con, po_id, received_date, lines, actor, role, as_of):
    """A7 摄入：记录一张收货单（GoodsReceipt + 行）。整体校验通过才落库（不落半截）。

    lines 每行需含 {po_line_id, received_qty, accepted_qty, rejected_qty, qc_status, defect_ppm}
    （可选 received_date 覆盖头日期，供分批到货行级日期）。只记录事实，不判风险（R7-R10 由 detect 检出）。
    """
    if role not in PROC_PERMS["RecordGoodsReceipt"]:
        return _denied(con, "RecordGoodsReceipt", po_id, actor, role, as_of)
    cur = con.cursor()
    po = cur.execute("SELECT * FROM purchase_orders WHERE po_id=?", (po_id,)).fetchone()
    if not po:
        return _fail(con, "RecordGoodsReceipt", po_id, actor, role, as_of, "采购单不存在")
    if not lines:
        return _fail(con, "RecordGoodsReceipt", po_id, actor, role, as_of, "收货必须至少一行")
    for ln in lines:  # 先整体校验（不落半截）
        if not GRN_LINE_KEYS <= set(ln):
            return _fail(con, "RecordGoodsReceipt", po_id, actor, role, as_of,
                         f"收货行缺字段: {sorted(GRN_LINE_KEYS - set(ln))}", {"bad": str(ln)[:200]})
        if ln["qc_status"] not in QC_STATUSES:
            return _fail(con, "RecordGoodsReceipt", po_id, actor, role, as_of,
                         f"qc_status 非法: {ln['qc_status']}（需 passed/failed）")
        pol = cur.execute("SELECT po_id FROM po_lines WHERE po_line_id=?",
                          (ln["po_line_id"],)).fetchone()
        if not pol or pol["po_id"] != po_id:
            return _fail(con, "RecordGoodsReceipt", po_id, actor, role, as_of,
                         f"采购行 {ln['po_line_id']} 不属于 {po_id}")
    grn_id = _next_seq_id(cur, "goods_receipts", "grn_id", "GRN-2026-", 5)
    ts = f"{as_of}T00:00:00Z"
    line_ids = []
    with transaction(con):
        cur.execute("""INSERT INTO goods_receipts (grn_id, po_id, received_date, status,
                       as_of_date, created_at) VALUES (?,?,?,?,?,?)""",
                    (grn_id, po_id, received_date, "received", as_of, ts))
        for ln in lines:
            grl_id = _next_seq_id(cur, "goods_receipt_lines", "grn_line_id", "GRL-", 6)
            cur.execute("""INSERT INTO goods_receipt_lines (grn_line_id, grn_id, po_line_id,
                           received_qty, accepted_qty, rejected_qty, qc_status, defect_ppm,
                           received_date, as_of_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (grl_id, grn_id, ln["po_line_id"], ln["received_qty"], ln["accepted_qty"],
                         ln["rejected_qty"], ln["qc_status"], ln["defect_ppm"],
                         ln.get("received_date", received_date), as_of, ts))
            line_ids.append(grl_id)
        _log(cur, actor, role, "RecordGoodsReceipt", grn_id,
             {"po_id": po_id, "received_date": received_date, "grn_line_ids": line_ids},
             as_of, "ok")
    return _res(True, grn_id, [f"GoodsReceipt {grn_id} recorded ({len(line_ids)} lines) for {po_id}"])


def match_supplier_invoice(con, po_id, supplier_id, vendor_invoice_no, issue_date, lines,
                           actor, role, as_of):
    """A8 摄入：记录/匹配一张供应商发票（SupplierInvoice + 行）到采购单，status→matched。

    lines 每行需含 {po_line_id, qty, unit_price_usd}；amount 由动作现算。触发状态（received→matched）
    表示"已匹配到 PO 事实"，**不判价量风险**——R10 价量不符由 engine.detect 独立检出（摄入不判风险）。
    """
    if role not in PROC_PERMS["MatchSupplierInvoice"]:
        return _denied(con, "MatchSupplierInvoice", po_id, actor, role, as_of)
    cur = con.cursor()
    po = cur.execute("SELECT * FROM purchase_orders WHERE po_id=?", (po_id,)).fetchone()
    if not po:
        return _fail(con, "MatchSupplierInvoice", po_id, actor, role, as_of, "采购单不存在")
    if not lines:
        return _fail(con, "MatchSupplierInvoice", po_id, actor, role, as_of, "发票必须至少一行")
    for ln in lines:  # 先整体校验
        if not SINV_LINE_KEYS <= set(ln):
            return _fail(con, "MatchSupplierInvoice", po_id, actor, role, as_of,
                         f"发票行缺字段: {sorted(SINV_LINE_KEYS - set(ln))}", {"bad": str(ln)[:200]})
        pol = cur.execute("SELECT po_id FROM po_lines WHERE po_line_id=?",
                          (ln["po_line_id"],)).fetchone()
        if not pol or pol["po_id"] != po_id:
            return _fail(con, "MatchSupplierInvoice", po_id, actor, role, as_of,
                         f"采购行 {ln['po_line_id']} 不属于 {po_id}")
    sinv_id = _next_seq_id(cur, "supplier_invoices", "supplier_invoice_id", "SINV-2026-", 5)
    ts = f"{as_of}T00:00:00Z"
    total = round(sum(float(ln["qty"]) * float(ln["unit_price_usd"]) for ln in lines), 2)
    line_ids = []
    with transaction(con):
        cur.execute("""INSERT INTO supplier_invoices (supplier_invoice_id, supplier_id, po_id,
                       vendor_invoice_no, issue_date, currency, total_usd, status,
                       as_of_date, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (sinv_id, supplier_id, po_id, vendor_invoice_no, issue_date, "USD",
                     total, "matched", as_of, ts))
        for ln in lines:
            sil_id = _next_seq_id(cur, "supplier_invoice_lines", "supplier_invoice_line_id",
                                  "SIL-", 6)
            amount = round(float(ln["qty"]) * float(ln["unit_price_usd"]), 2)
            cur.execute("""INSERT INTO supplier_invoice_lines (supplier_invoice_line_id,
                           supplier_invoice_id, po_line_id, qty, unit_price_usd, amount_usd,
                           as_of_date, created_at) VALUES (?,?,?,?,?,?,?,?)""",
                        (sil_id, sinv_id, ln["po_line_id"], ln["qty"], ln["unit_price_usd"],
                         amount, as_of, ts))
            line_ids.append(sil_id)
        _log(cur, actor, role, "MatchSupplierInvoice", sinv_id,
             {"po_id": po_id, "vendor_invoice_no": vendor_invoice_no, "total_usd": total,
              "supplier_invoice_line_ids": line_ids}, as_of, "ok")
    return _res(True, sinv_id,
                [f"SupplierInvoice {sinv_id} matched to {po_id} (total ${total}, status=matched)"])
