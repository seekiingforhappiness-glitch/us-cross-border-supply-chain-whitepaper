"""W1 仓储操作/连接动作（Build 2/3）+ 仓储处置动作审批回写。

与 app/actions.py、app/procurement_actions.py 同一套模式：权限→前置→成功→失败→审计，
统一返回 {ok, object_id, side_effects, error}，从不抛异常给调用方；审计时间戳用 as_of（D8）。

设计动机（≤5 行「为什么这样建」）：
- 操作/连接动作只**记录事实/搬桶**，不判风险——库存断货/不可履约/盘点差异一律由
  engine.detect_warehouse_risks 从事实重算（W1 铁律：动作不判风险，不信状态字段）。
- 三个连接点把跨域对象图接起来：①采购收货 accepted_qty →Putaway→ InventoryPosition.available
  （采购→库存）②Reservation.allocated + reserved+=qty 驱动 SalesOrderLine open→allocated（库存→履约）
  ③延误(R1-R3)/R17 →查目的仓 available 拆单先发 + 余量 backorder（履约救援，杀手锏）。
- 仓储操作是运营职责（WH_PERMS: {ops,system}），与既有 ROLE_PERMS 域划分一致，不复制权限。
- 处置动作（suggest_substitution/adjust_inventory/escalate_replenishment）走既有 A4/A5 闭环：
  ProposeMitigation:{ops,cs,finance} + ApproveMitigation 仅 manager（不改 ROLE_PERMS）；本文件只提供
  审批通过后的「可见状态回写」helper（apply_warehouse_disposition），由 actions.approve_mitigation 调用。
- 不注册为 agent 工具：操作是人执行的事实录入，approve/close 永不做 agent 工具（原则2）。
"""
try:
    from .action_context import transaction
    from .actions import _log, _res
except ImportError:  # streamlit run 场景：app/ 为脚本目录，无包上下文
    from action_context import transaction
    from actions import _log, _res

# 仓储操作动作权限矩阵（system 供引擎/自动化用；上架/预留/盘点均运营职责，与收货同域）
WH_PERMS = {
    "Putaway": {"ops", "system"},
    "ReserveInventory": {"ops", "system"},
    "ReleaseReservation": {"ops", "system"},
    "RecordCycleCount": {"ops", "system"},
}
RESERVATION_TERMINAL = ("released", "fulfilled")


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


def _position_for(cur, sku_id, warehouse_id):
    """取 (sku,仓库) 头寸（DQ：一对至多一条）；无则 None。"""
    return cur.execute(
        "SELECT * FROM inventory_positions WHERE sku_id=? AND warehouse_id=?",
        (sku_id, warehouse_id)).fetchone()


def _ensure_position(cur, sku_id, warehouse_id, as_of):
    """取 (sku,仓库) 头寸；无则建一条空头寸（available=0）——Putaway 上架新品到新仓时用。"""
    p = _position_for(cur, sku_id, warehouse_id)
    if p:
        return p["inventory_position_id"]
    pid = _next_seq_id(cur, "inventory_positions", "inventory_position_id", "INVP-", 5)
    cur.execute("""INSERT INTO inventory_positions (inventory_position_id, sku_id, warehouse_id,
                   available_qty, reserved_qty, in_transit_qty, quarantine_qty, safety_stock,
                   as_of_date) VALUES (?,?,?,?,?,?,?,?,?)""",
                (pid, sku_id, warehouse_id, 0, 0, 0, 0, 0, as_of))
    return pid


# ========== 操作/连接动作（只记事实，不判风险）==========
def putaway(con, grn_id, warehouse_id, actor, role, as_of):
    """连接点①（采购→库存）：把一张 GoodsReceipt 各行 accepted_qty 上架到目的仓对应
    InventoryPosition.available（sku 经 po_line 解析；无头寸则建）。只搬桶，不判风险。"""
    if role not in WH_PERMS["Putaway"]:
        return _denied(con, "Putaway", grn_id, actor, role, as_of)
    cur = con.cursor()
    grn = cur.execute("SELECT * FROM goods_receipts WHERE grn_id=?", (grn_id,)).fetchone()
    if not grn:
        return _fail(con, "Putaway", grn_id, actor, role, as_of, "收货单不存在")
    if not cur.execute("SELECT 1 FROM warehouses WHERE warehouse_id=?", (warehouse_id,)).fetchone():
        return _fail(con, "Putaway", grn_id, actor, role, as_of, f"仓库 {warehouse_id} 不存在")
    lines = cur.execute(
        """SELECT gl.accepted_qty, pl.sku_id FROM goods_receipt_lines gl
           JOIN po_lines pl ON pl.po_line_id=gl.po_line_id
           WHERE gl.grn_id=? ORDER BY gl.grn_line_id""", (grn_id,)).fetchall()
    if not lines:
        return _fail(con, "Putaway", grn_id, actor, role, as_of, "收货单无行可上架")
    effects, moved = [], {}
    with transaction(con):
        for ln in lines:
            if ln["accepted_qty"] <= 0:
                continue
            pid = _ensure_position(cur, ln["sku_id"], warehouse_id, as_of)
            cur.execute("UPDATE inventory_positions SET available_qty=available_qty+? "
                        "WHERE inventory_position_id=?", (ln["accepted_qty"], pid))
            moved[pid] = moved.get(pid, 0) + ln["accepted_qty"]
        for pid, q in sorted(moved.items()):
            effects.append(f"Putaway {q} 件 → InventoryPosition {pid}.available（+{q}）")
        _log(cur, actor, role, "Putaway", grn_id,
             {"warehouse_id": warehouse_id, "moved": moved}, as_of, "ok")
    return _res(True, grn_id, effects or [f"GoodsReceipt {grn_id} 无 accepted_qty 可上架"])


def reserve_inventory(con, so_line_id, warehouse_id, qty, actor, role, as_of):
    """连接点②（库存→履约）：为一条 SO 行在目的仓建 Reservation(allocated) +
    InventoryPosition.reserved+=qty，并驱动 SalesOrderLine open→allocated。只记事实，不判风险。"""
    if role not in WH_PERMS["ReserveInventory"]:
        return _denied(con, "ReserveInventory", so_line_id, actor, role, as_of)
    cur = con.cursor()
    sol = cur.execute("SELECT * FROM sales_order_lines WHERE so_line_id=?", (so_line_id,)).fetchone()
    if not sol:
        return _fail(con, "ReserveInventory", so_line_id, actor, role, as_of, "订单行不存在")
    if qty <= 0:
        return _fail(con, "ReserveInventory", so_line_id, actor, role, as_of, "预留数量须为正")
    p = _position_for(cur, sol["sku_id"], warehouse_id)
    if not p:
        return _fail(con, "ReserveInventory", so_line_id, actor, role, as_of,
                     f"仓库 {warehouse_id} 无 {sol['sku_id']} 头寸，无法预留")
    pid = p["inventory_position_id"]
    rsv_id = _next_seq_id(cur, "inventory_reservations", "reservation_id", "RSV-", 6)
    with transaction(con):
        cur.execute("""INSERT INTO inventory_reservations (reservation_id, so_line_id,
                       inventory_position_id, qty, status, as_of_date) VALUES (?,?,?,?,?,?)""",
                    (rsv_id, so_line_id, pid, qty, "allocated", as_of))
        cur.execute("UPDATE inventory_positions SET reserved_qty=reserved_qty+? "
                    "WHERE inventory_position_id=?", (qty, pid))
        cur.execute("UPDATE sales_order_lines SET line_status='allocated' "
                    "WHERE so_line_id=? AND line_status='open'", (so_line_id,))
        _log(cur, actor, role, "ReserveInventory", rsv_id,
             {"so_line_id": so_line_id, "inventory_position_id": pid, "qty": qty}, as_of, "ok")
    return _res(True, rsv_id, [
        f"Reservation {rsv_id} allocated（{so_line_id} @ {pid}，{qty} 件）",
        f"InventoryPosition {pid}.reserved +{qty}",
        f"SalesOrderLine {so_line_id}: open→allocated"])


def release_reservation(con, reservation_id, actor, role, as_of):
    """ReserveInventory 的反向：Reservation→released + InventoryPosition.reserved−=qty，
    若该 SOL 无其它非终态预留则 allocated→open。只记事实，不判风险。"""
    if role not in WH_PERMS["ReleaseReservation"]:
        return _denied(con, "ReleaseReservation", reservation_id, actor, role, as_of)
    cur = con.cursor()
    rsv = cur.execute("SELECT * FROM inventory_reservations WHERE reservation_id=?",
                      (reservation_id,)).fetchone()
    if not rsv:
        return _fail(con, "ReleaseReservation", reservation_id, actor, role, as_of, "预留不存在")
    if rsv["status"] in RESERVATION_TERMINAL:
        return _fail(con, "ReleaseReservation", reservation_id, actor, role, as_of,
                     f"预留状态为 {rsv['status']}，已终态不可释放")
    effects = []
    with transaction(con):
        cur.execute("UPDATE inventory_reservations SET status='released' WHERE reservation_id=?",
                    (reservation_id,))
        cur.execute("UPDATE inventory_positions SET reserved_qty=MAX(reserved_qty-?,0) "
                    "WHERE inventory_position_id=?", (rsv["qty"], rsv["inventory_position_id"]))
        effects.append(f"Reservation {reservation_id}: {rsv['status']}→released")
        effects.append(f"InventoryPosition {rsv['inventory_position_id']}.reserved −{rsv['qty']}")
        left = cur.execute("""SELECT count(*) c FROM inventory_reservations WHERE so_line_id=?
                              AND status NOT IN ('released','fulfilled')""",
                           (rsv["so_line_id"],)).fetchone()["c"]
        if left == 0:
            cur.execute("UPDATE sales_order_lines SET line_status='open' "
                        "WHERE so_line_id=? AND line_status='allocated'", (rsv["so_line_id"],))
            effects.append(f"SalesOrderLine {rsv['so_line_id']}: allocated→open（无其它活跃预留）")
        _log(cur, actor, role, "ReleaseReservation", reservation_id,
             {"so_line_id": rsv["so_line_id"], "qty": rsv["qty"]}, as_of, "ok")
    return _res(True, reservation_id, effects)


def record_cycle_count(con, inventory_position_id, counted_qty, actor, role, as_of):
    """记录一次盘点（CycleCount）：system_qty 取头寸账面 available，variance=counted−system。
    只记事实、不调整头寸、不判风险——R18 盘点差异由 engine.detect 从 counted/system 比率重算，
    调整走 adjust_inventory 处置（审批后）。"""
    if role not in WH_PERMS["RecordCycleCount"]:
        return _denied(con, "RecordCycleCount", inventory_position_id, actor, role, as_of)
    cur = con.cursor()
    p = cur.execute("SELECT * FROM inventory_positions WHERE inventory_position_id=?",
                    (inventory_position_id,)).fetchone()
    if not p:
        return _fail(con, "RecordCycleCount", inventory_position_id, actor, role, as_of, "头寸不存在")
    if counted_qty < 0:
        return _fail(con, "RecordCycleCount", inventory_position_id, actor, role, as_of,
                     "实盘数量不能为负")
    system_qty = p["available_qty"]
    cc_id = _next_seq_id(cur, "cycle_counts", "cycle_count_id", "CCNT-", 5)
    with transaction(con):
        cur.execute("""INSERT INTO cycle_counts (cycle_count_id, inventory_position_id, warehouse_id,
                       system_qty, counted_qty, variance, status, as_of_date)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (cc_id, inventory_position_id, p["warehouse_id"], system_qty, counted_qty,
                     counted_qty - system_qty, "counted", as_of))
        _log(cur, actor, role, "RecordCycleCount", cc_id,
             {"inventory_position_id": inventory_position_id, "system_qty": system_qty,
              "counted_qty": counted_qty, "variance": counted_qty - system_qty}, as_of, "ok")
    return _res(True, cc_id, [
        f"CycleCount {cc_id} recorded（{inventory_position_id}：账面 {system_qty} 实盘 {counted_qty}，"
        f"差 {counted_qty - system_qty}）"])


# ========== 仓储处置审批回写（审批后由 actions.approve_mitigation 调用，只回写「可见状态」）==========
def _warehouse_of_risk(cur, risk):
    """处置目标仓：仓储风险(R16-R18)用 warehouse_id；延误风险(R1-R3)用 shipment 目的仓。"""
    if risk["warehouse_id"]:
        return risk["warehouse_id"]
    if risk["shipment_id"]:
        row = cur.execute("SELECT destination_warehouse FROM shipments WHERE shipment_id=?",
                          (risk["shipment_id"],)).fetchone()
        return row["destination_warehouse"] if row else None
    return None


def _spot_position(cur, sku_id, target_wh):
    """查目的仓现货头寸（连接点③）：优先目的仓 (sku,仓库)，回退任意有货头寸（available 最大）。"""
    if target_wh:
        row = _position_for(cur, sku_id, target_wh)
        if row:
            return row
    return cur.execute("""SELECT * FROM inventory_positions WHERE sku_id=? AND available_qty>0
                          ORDER BY available_qty DESC, inventory_position_id LIMIT 1""",
                       (sku_id,)).fetchone()


def apply_warehouse_disposition(cur, act, risk, affected, params, as_of):
    """审批通过后的仓储处置「可见状态」回写（在 approve_mitigation 的事务内执行，只用 cur）。
    绝不判风险——检测仍由 engine.detect 从事实重算；这里只按已批准方案回写可见状态 + 审计。

    - suggest_substitution（延误 R1-R3 / R17 连接点杀手锏）：对受影响 SOL，用目的仓 available 拆单
      先发（reserve 现货部分，Reservation=allocated + reserved+=）、余量标 backorder（Reservation=
      backordered）、SOL open/at_risk→allocated。
    - adjust_inventory（R18）：按 CycleCount.counted 调整 InventoryPosition.available + status=reconciled。
    - escalate_replenishment（R16）：升级补货——把补足安全库存的缺口计入 in_transit_qty（在途补货）；
      available 不动（货未到，R16 到货前仍成立），事实由审计留痕。
    """
    effects = []
    if act == "suggest_substitution":
        target_wh = _warehouse_of_risk(cur, risk)
        for sol_id in affected:
            sol = cur.execute("SELECT sku_id, qty FROM sales_order_lines WHERE so_line_id=?",
                              (sol_id,)).fetchone()
            if not sol:
                continue
            demand = sol["qty"]
            p = _spot_position(cur, sol["sku_id"], target_wh)
            if not p:
                effects.append(f"{sol_id}：目的仓 {target_wh} 及全网无 {sol['sku_id']} 现货，"
                               f"{demand} 件全部 backorder（无头寸可挂预留）")
                continue
            pid = p["inventory_position_id"]
            ship_now = min(p["available_qty"], demand)
            backorder = demand - ship_now
            if ship_now > 0:
                rsv_id = _next_seq_id(cur, "inventory_reservations", "reservation_id", "RSV-", 6)
                cur.execute("""INSERT INTO inventory_reservations (reservation_id, so_line_id,
                               inventory_position_id, qty, status, as_of_date)
                               VALUES (?,?,?,?,?,?)""",
                            (rsv_id, sol_id, pid, ship_now, "allocated", as_of))
                cur.execute("UPDATE inventory_positions SET reserved_qty=reserved_qty+? "
                            "WHERE inventory_position_id=?", (ship_now, pid))
                cur.execute("""UPDATE sales_order_lines SET line_status='allocated'
                               WHERE so_line_id=? AND line_status IN ('open','at_risk')""", (sol_id,))
                effects.append(f"{sol_id}：现货拆单先发 {ship_now} 件（Reservation {rsv_id} allocated "
                               f"@ {pid}，reserved +{ship_now}，SOL→allocated）")
            if backorder > 0:
                bo_id = _next_seq_id(cur, "inventory_reservations", "reservation_id", "RSV-", 6)
                cur.execute("""INSERT INTO inventory_reservations (reservation_id, so_line_id,
                               inventory_position_id, qty, status, as_of_date)
                               VALUES (?,?,?,?,?,?)""",
                            (bo_id, sol_id, pid, backorder, "backordered", as_of))
                effects.append(f"{sol_id}：余量 {backorder} 件转 backorder（Reservation {bo_id} "
                               f"backordered @ {pid}）")
        if not effects:
            effects.append("suggest_substitution 批准：无受影响 SOL 可拆单")
        return effects

    if act == "adjust_inventory":
        cc_id = affected[0] if affected else None
        cc = cur.execute("SELECT * FROM cycle_counts WHERE cycle_count_id=?", (cc_id,)).fetchone()
        if not cc:
            return [f"adjust_inventory 批准：盘点单 {cc_id} 不存在，无可调整头寸"]
        pid = cc["inventory_position_id"]
        cur.execute("UPDATE inventory_positions SET available_qty=? WHERE inventory_position_id=?",
                    (cc["counted_qty"], pid))
        cur.execute("UPDATE cycle_counts SET status='reconciled' WHERE cycle_count_id=?", (cc_id,))
        return [f"adjust_inventory 批准：InventoryPosition {pid}.available "
                f"{cc['system_qty']}→{cc['counted_qty']}（按实盘调整），CycleCount {cc_id}→reconciled"]

    if act == "escalate_replenishment":
        pid = affected[0] if affected else None
        p = cur.execute("SELECT * FROM inventory_positions WHERE inventory_position_id=?",
                        (pid,)).fetchone()
        if not p:
            return [f"escalate_replenishment 批准：头寸 {pid} 不存在"]
        gap = max(p["safety_stock"] - p["available_qty"], 0)
        cur.execute("UPDATE inventory_positions SET in_transit_qty=in_transit_qty+? "
                    "WHERE inventory_position_id=?", (gap, pid))
        return [f"escalate_replenishment 批准：InventoryPosition {pid} 升级补货，"
                f"in_transit +{gap}（补足安全库存 {p['safety_stock']} 缺口；available {p['available_qty']} "
                f"不动，货未到 R16 仍成立至到货）"]

    return [f"未知仓储处置 {act}（无回写）"]
