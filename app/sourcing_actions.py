"""P3 采购富化2 处置动作审批回写（Build B：R14 单一来源 / R15 maverick 处置 + 闭环）。

与 app/warehouse_actions.py、app/procurement_actions.py 同一套模式：审批通过后由
actions.approve_mitigation 在事务内调用（只用 cur），只回写「可见状态」（RFQ / supplier_invoice /
purchase_order），从不抛异常给调用方；审计时间戳用 as_of（D8）。

设计动机（≤5 行「为什么这样建」）：
- 绝不判风险——R14/R15 检测仍由 engine.detect_sourcing_risks 从事实重算（不信 rfq/invoice/po 状态字段）；
  这里只按已批准方案回写可见状态，把「风险」转成「已处置」的事实痕迹。
- initiate_second_source（R14）：对断供 SKU 建/标一条 RFQ(status=sent) 启动第二来源询价——RFQ 是既有
  对象（P3 Build A 已建），复用不新造；有 draft 询价则「标」为 sent，无则「建」一条 + 行。
- block_non_po_payment（R15）：把绕流程 supplier_invoice 标 on_hold 拦截付款（可见状态回写）。
- backfill_po（R15）：为 maverick 发票补一张追溯 PurchaseOrder（supplier=biller）并重指向关联——补单后
  biller==PO 供应商，绕流程缺口在事实层闭合（合规补单）。approve/close 永不做 agent 工具（原则2）。
"""
import json

try:
    from .actions import _log  # noqa: F401  (供未来审计扩展；主审计仍在 approve_mitigation)
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from actions import _log  # noqa: F401


def _next_seq_id(cur, table, id_col, prefix, width):
    """下一个稳定序号 id（count+1 起，遇冲突向上找空位）——避让 datagen 种子已占用的号段。"""
    n = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0] + 1
    cand = f"{prefix}{n:0{width}d}"
    while cur.execute(f"SELECT 1 FROM {table} WHERE {id_col}=?", (cand,)).fetchone():
        n += 1
        cand = f"{prefix}{n:0{width}d}"
    return cand


def _maverick_invoice_ids(cur, risk):
    """R15 maverick 供票 id 集：优先 affected_invoice_line_ids(SIL)→发票；回退 po_id + biller(supplier_id)。"""
    sil_ids = json.loads(risk["affected_invoice_line_ids"] or "[]")
    inv_ids = []
    if sil_ids:
        ph = ",".join("?" * len(sil_ids))
        inv_ids = [r["supplier_invoice_id"] for r in cur.execute(
            f"""SELECT DISTINCT supplier_invoice_id FROM supplier_invoice_lines
                WHERE supplier_invoice_line_id IN ({ph})""", sil_ids)]
    if not inv_ids and risk["po_id"] and risk["supplier_id"]:
        inv_ids = [r["supplier_invoice_id"] for r in cur.execute(
            "SELECT supplier_invoice_id FROM supplier_invoices WHERE po_id=? AND supplier_id=?",
            (risk["po_id"], risk["supplier_id"]))]
    return sorted(inv_ids)


def apply_sourcing_disposition(cur, act, risk, params, as_of):
    """审批通过后的采购富化2 处置「可见状态」回写（在 approve_mitigation 的事务内执行，只用 cur）。
    绝不判风险——检测仍由 engine.detect_sourcing_risks 从事实重算；这里只按已批准方案回写可见状态。

    - initiate_second_source（R14 单一来源）：sku 载体在 affected_po_line_ids[0]。对该 SKU 现有 draft/sent
      询价则标 status=sent；无则新建一条 RFQ(status=sent) + RFQLine，启动第二来源询价。
    - block_non_po_payment（R15 maverick）：把该风险的 maverick supplier_invoice 标 status=on_hold（拦截付款）。
    - backfill_po（R15 maverick）：为每张 maverick 供票补一张追溯 PurchaseOrder(supplier=biller，sku/qty 取自
      票行)，并把该发票重指向新 PO + status=matched（合规补单，补后 biller==PO 供应商，绕流程缺口闭合）。
    """
    if act == "initiate_second_source":
        skus = json.loads(risk["affected_po_line_ids"] or "[]")
        sku = skus[0] if skus else None
        if not sku:
            return ["initiate_second_source 批准：R14 事件无 SKU 载体，无可询价对象"]
        existing = cur.execute(
            """SELECT rfq_id FROM rfqs WHERE sku_id=? AND status IN ('draft', 'sent')
               ORDER BY rfq_id LIMIT 1""", (sku,)).fetchone()
        if existing:
            rfq_id = existing["rfq_id"]
            cur.execute("UPDATE rfqs SET status='sent' WHERE rfq_id=?", (rfq_id,))
            return [f"initiate_second_source 批准：SKU {sku} 现有询价 RFQ {rfq_id} → status=sent"
                    f"（启动第二来源询价）"]
        rfq_id = _next_seq_id(cur, "rfqs", "rfq_id", "RFQ-2026-", 4)
        qty = cur.execute("SELECT COALESCE(SUM(qty), 0) q FROM po_lines WHERE sku_id=?",
                          (sku,)).fetchone()["q"] or 1
        cur.execute("""INSERT INTO rfqs (rfq_id, sku_id, status, created_date, as_of_date)
                       VALUES (?,?,?,?,?)""", (rfq_id, sku, "sent", as_of, as_of))
        rl_id = _next_seq_id(cur, "rfq_lines", "rfq_line_id", "RFQL-", 5)
        cur.execute("""INSERT INTO rfq_lines (rfq_line_id, rfq_id, sku_id, qty)
                       VALUES (?,?,?,?)""", (rl_id, rfq_id, sku, qty))
        return [f"initiate_second_source 批准：SKU {sku} 新建询价 RFQ {rfq_id}(status=sent) "
                f"+ 行 {rl_id}(qty={qty})，启动第二来源"]

    if act == "block_non_po_payment":
        inv_ids = _maverick_invoice_ids(cur, risk)
        for sid in inv_ids:
            cur.execute("UPDATE supplier_invoices SET status='on_hold' "
                        "WHERE supplier_invoice_id=?", (sid,))
        if not inv_ids:
            return ["block_non_po_payment 批准：未定位到 maverick 供票（无可拦截付款）"]
        return [f"block_non_po_payment 批准：maverick 供票 {inv_ids} → status=on_hold（拦截绕流程付款）"]

    if act == "backfill_po":
        inv_ids = _maverick_invoice_ids(cur, risk)
        effects = []
        for sid in inv_ids:
            inv = cur.execute("SELECT * FROM supplier_invoices WHERE supplier_invoice_id=?",
                              (sid,)).fetchone()
            line = cur.execute(
                """SELECT pl.sku_id sku FROM supplier_invoice_lines sil
                   JOIN po_lines pl ON pl.po_line_id=sil.po_line_id
                   WHERE sil.supplier_invoice_id=? ORDER BY sil.supplier_invoice_line_id
                   LIMIT 1""", (sid,)).fetchone()
            tot_qty = cur.execute(
                "SELECT COALESCE(SUM(qty), 0) q FROM supplier_invoice_lines WHERE supplier_invoice_id=?",
                (sid,)).fetchone()["q"]
            sku = line["sku"] if line else None
            new_po = _next_seq_id(cur, "purchase_orders", "po_id", "PO-2026-", 4)
            cur.execute("""INSERT INTO purchase_orders (po_id, supplier_id, sku_id, qty, po_date,
                           expected_ready_date, status) VALUES (?,?,?,?,?,?,?)""",
                        (new_po, inv["supplier_id"], sku, tot_qty, as_of, as_of, "closed"))
            cur.execute("UPDATE supplier_invoices SET po_id=?, status='matched' "
                        "WHERE supplier_invoice_id=?", (new_po, sid))
            effects.append(
                f"backfill_po 批准：供票 {sid} 补追溯 PO {new_po}（supplier={inv['supplier_id']}=biller，"
                f"sku={sku} qty={tot_qty}），发票重指向新 PO + status=matched（合规补单，biller==PO 供应商）")
        if not effects:
            return ["backfill_po 批准：未定位到 maverick 供票（无可补单）"]
        return effects

    return [f"未知采购富化2 处置 {act}（无回写）"]
