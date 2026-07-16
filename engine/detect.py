"""W4 检测入口：python3 -m engine.detect [--as-of 2026-08-08]

跑 R1-R3 → 按 A2 CreateRiskEvent 语义写入 risk_events：
同 (shipment, type) 非终态事件合并（severity 取高、受影响行取并集），
受影响行置 at_risk，全程写 action_log（审计时间戳用 as_of，不用系统时钟——D8）。

G-Ledger 规则执行台账（治理证据包规格 2026-07-16-governance-evidence-package.md「G-Ledger」节）：
每次 detect 跑完，本模块给 R1-R21 各 append 一行 rule_run_ledger（纯旁路表，不进 34 对象计数/
ontology_lint 断言域/真值 md5，不改变任何既有检测产出）。口径细节见下方
ensure_rule_run_ledger_table / _ontology_version / _fingerprint 及各 _fp_* 函数的 docstring。
"""
import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import yaml

from .rules import detect_risks, SEV_ORDER
from .cost_rules import detect_cost_anomalies
from .procurement_rules import detect_procurement_risks
from .warehouse_rules import detect_warehouse_risks
from .sourcing_rules import detect_sourcing_risks
from .finance_rules import detect_finance_risks
from .graph import upsert_relationship

DB = "data/ontology.sqlite"
ONTOLOGY_PATH = "ontology/control-tower-ontology.json"

# 规则 id 分组（与 detect_*_risks() 的函数边界一一对应，供台账按组写 fingerprint/status）——
# 六组共 3+3+7+3+2+3=21 条，对齐 manual R1-R21 全量。
TOWER_RULES = ("R1", "R2", "R3")
COST_RULES = ("R4", "R5", "R6")
PROCUREMENT_RULES = ("R7", "R8", "R9", "R10", "R11", "R12", "R13")
WAREHOUSE_RULES = ("R16", "R17", "R18")
SOURCING_RULES = ("R14", "R15")
FINANCE_RULES = ("R19", "R20", "R21")


# ═══════════════════════════════════════════════════════════════════════════
# G-Ledger：rule_run_ledger 旁路台账（建表 + 指纹 + 落账，规格 8 列逐字执行）
# ═══════════════════════════════════════════════════════════════════════════

RULE_RUN_LEDGER_DDL = """CREATE TABLE IF NOT EXISTS rule_run_ledger (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    rule_version TEXT,
    input_fingerprint TEXT,
    detected_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
)"""


def ensure_rule_run_ledger_table(conn):
    """建表（幂等，IF NOT EXISTS）：DDL 单一事实源在此（仿 engine.resolution_memory /
    agent.egress_gate 的 ensure_*_table 先例）。pipeline.build_ontology 建库时调用一次（正表，
    见该模块 main() 内对本函数的延迟 import——避免 engine.detect→engine.rules→
    pipeline.build_ontology 的循环导入，注释详见调用处）；engine.detect 入口也防御性调用一次
    （旧库副本兼容，同 llm_calls 模式）。旁路表：不进 34 对象计数、不进 ontology_lint 断言域、
    不进真值 md5 基线——它是运维台账，不是本体对象。"""
    conn.execute(RULE_RUN_LEDGER_DDL)


def _ontology_version():
    """rule_version 兜底口径（规格 G-Ledger 节）：现读 ontology/control-tower-ontology.json 的
    顶层 version 字段（现值 0.11.3），不硬编码——本体升版本时台账 rule_version 自动跟着变。
    R1-R21 所在的六个规则模块（rules.py/cost_rules.py/procurement_rules.py/warehouse_rules.py/
    sourcing_rules.py/finance_rules.py）当前均无独立语义版本号，故本轮 rule_version 全量取本体
    版本兜底（规格原文："若无则本体版本即可"）；未来任一模块补上语义版本后，可在此按模块覆盖，
    不用改表结构。读取失败（文件缺失/JSON 损坏）时返回 'unknown'，不让台账写入本身崩溃主流程。"""
    try:
        with open(ONTOLOGY_PATH, encoding="utf-8") as f:
            return json.load(f).get("version") or "unknown"
    except (OSError, ValueError):
        return "unknown"


def _fingerprint(con, queries):
    """input_fingerprint 口径：给定一组 (sql, params)，按顺序逐条执行；每个结果集内部把行
    转 tuple 后排序（消除 SQL 返回顺序不稳定造成的假阳性"变了"），再并入同一 sha256，表与表
    之间以 NUL 字节分隔（防拼接歧义，如 "ab"+"c" 与 "a"+"bc" 不应撞出同哈希）。截断取前 16 位
    十六进制——够用于"两次扫描的输入切片是否相同"这一比对场景，非密码学用途、不强求防碰撞。
    同 as_of 同数据两次调用必得同一指纹（幂等友好）；源表任何行级差异（含新增/删除/字段值变化）
    指纹必变。"""
    h = hashlib.sha256()
    for sql, params in queries:
        rows = con.execute(sql, params).fetchall()
        normalized = sorted(tuple(row) for row in rows)
        h.update(repr(normalized).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


# --- 各规则组的 input_fingerprint：口径 = 该组对应 detect_*_risks() 实际读取的源表切片
# （as_of 过滤后）。同一次 detect_*_risks() 调用内共享派生变量的规则（如 R1-R3 共享
# state/by_ship，R7-R9 共享 recv_by_line）共享同一指纹——在"不拆函数、不碰既有检测逻辑"的
# 硬约束下，逐规则精确隔离读取边界做不到；这是规格允许的"不求完美，求稳定可复现"的合理粒度，
# 足以回答"这批规则的输入切片较上次扫描是否变化"。

def _fp_tower(con, as_of_str):
    """R1/R2/R3：shipments 全量 + shipment_milestones(is_duplicate=0, event_time≤as_of) +
    shipment_allocations⋈sales_order_lines⋈sales_orders⋈customers——对齐
    engine.rules.detect_risks() 实际读取的四表口径（三规则共享同一批派生 state/by_ship）。

    刻意排除 l.line_status（真实查询里有选它，但 detect_risks() 函数体从未消费这一列——已用
    grep 核实，rules.py 全文件只在这一处 SELECT 出现 line_status，随后无任何引用）：apply_
    candidates() 的 A2 副作用会把命中行的 line_status 从 allocated 改成 at_risk，若把它纳入
    指纹，同一批数据在"本轮 detect 尚未跑 apply"和"上一轮 detect 已跑过 apply"两个时点会算出
    不同指纹——这是检测**逻辑**根本不关心的字段抖动，纳入会污染"指纹变了=数据真变了"这条诊断
    价值链（G-Ledger 存在的意义就是让这个信号干净）。故排除，只留检测逻辑实际读取判定的字段。"""
    return _fingerprint(con, [
        ("SELECT * FROM shipments ORDER BY shipment_id", ()),
        ("""SELECT * FROM shipment_milestones WHERE is_duplicate=0
            AND substr(event_time,1,10) <= ? ORDER BY event_time""", (as_of_str,)),
        ("""SELECT a.shipment_id, a.so_line_id, a.allocated_qty, l.promised_delivery_date,
                   l.unit_price_usd, c.tier
            FROM shipment_allocations a JOIN sales_order_lines l ON l.so_line_id=a.so_line_id
            JOIN sales_orders so ON so.so_id=l.so_id JOIN customers c ON c.customer_id=so.customer_id
            ORDER BY a.shipment_id, a.so_line_id""", ()),
    ])


def _fp_cost(con, as_of_str):
    """R4/R5/R6：invoice_lines⋈invoices(issue_date≤as_of) + expected_costs 全量 +
    shipments(delay_days)——对齐 cost_rules.detect_cost_anomalies() 口径（三规则共享同一批
    rows/baseline/delay_of）。"""
    return _fingerprint(con, [
        ("""SELECT il.invoice_line_id, il.invoice_id, il.charge_code, il.container_no,
                   il.amount_usd, iv.shipment_id, iv.issue_date
            FROM invoice_lines il JOIN invoices iv ON iv.invoice_id = il.invoice_id
            WHERE iv.issue_date <= ? ORDER BY iv.shipment_id, il.invoice_line_id""", (as_of_str,)),
        ("SELECT shipment_id, charge_code, container_no, baseline_usd FROM expected_costs", ()),
        ("SELECT shipment_id, delay_days FROM shipments", ()),
    ])


def _fp_receiving(con, as_of_str):
    """R7/R8/R9：po_lines 全量 + goods_receipt_lines⋈goods_receipts(received_date≤as_of)——
    对齐 procurement_rules 的收货聚合口径（三规则共享同一批 recv_by_line）。"""
    return _fingerprint(con, [
        ("SELECT * FROM po_lines ORDER BY po_line_id", ()),
        ("""SELECT gl.po_line_id, gl.received_qty, gl.accepted_qty, gl.rejected_qty,
                   gl.qc_status, gl.defect_ppm, gl.received_date
            FROM goods_receipt_lines gl JOIN goods_receipts g ON g.grn_id = gl.grn_id
            WHERE g.received_date <= ? ORDER BY gl.po_line_id, gl.grn_line_id""", (as_of_str,)),
    ])


def _fp_invoice_match(con, as_of_str):
    """R10/R11：po_lines 全量 + goods_receipt_lines⋈goods_receipts(收货聚合，R11 需要，R10
    严格不需要但同组处理，见下) + supplier_invoice_lines⋈supplier_invoices(issue_date≤as_of)。
    R10/R11 同组：R10 只需后两者、R11 额外需收货聚合，细分到逐规则会拆散
    detect_procurement_risks() 单次查询的复用结构，故取并集同组处理。"""
    return _fingerprint(con, [
        ("SELECT * FROM po_lines ORDER BY po_line_id", ()),
        ("""SELECT gl.po_line_id, gl.received_qty, gl.accepted_qty, gl.rejected_qty,
                   gl.qc_status, gl.defect_ppm, gl.received_date
            FROM goods_receipt_lines gl JOIN goods_receipts g ON g.grn_id = gl.grn_id
            WHERE g.received_date <= ? ORDER BY gl.po_line_id, gl.grn_line_id""", (as_of_str,)),
        ("""SELECT sil.supplier_invoice_line_id, sil.po_line_id, sil.qty, sil.unit_price_usd,
                   si.issue_date
            FROM supplier_invoice_lines sil JOIN supplier_invoices si
                 ON si.supplier_invoice_id = sil.supplier_invoice_id
            WHERE si.issue_date <= ? ORDER BY sil.po_line_id, sil.supplier_invoice_line_id""",
         (as_of_str,)),
    ])


def _fp_deposit(con, as_of_str):
    """R12：po_lines(po_id 分组用) + purchase_orders(supplier_of_po) +
    goods_receipts(received_date≤as_of，判定"是否已收货") + purchase_payments(deposit,
    paid_date≤as_of)——对齐预付款敞口重算口径。"""
    return _fingerprint(con, [
        ("SELECT po_line_id, po_id FROM po_lines ORDER BY po_line_id", ()),
        ("SELECT po_id, supplier_id FROM purchase_orders ORDER BY po_id", ()),
        ("SELECT DISTINCT po_id FROM goods_receipts WHERE received_date <= ? ORDER BY po_id",
         (as_of_str,)),
        ("""SELECT payment_id, po_id, amount_usd, paid_date FROM purchase_payments
            WHERE payment_type='deposit' AND paid_date <= ? ORDER BY payment_id""",
         (as_of_str,)),
    ])


def _fp_qualification(con, as_of_str):
    """R13：purchase_orders(status≠closed 的供应商集合) + supplier_qualifications 全量——
    供应商资质过期由 valid_from/valid_to vs as_of 现算，不信 status 字段（as_of_str 未直接
    使用，保留同签名便于统一调用）。"""
    return _fingerprint(con, [
        ("""SELECT DISTINCT supplier_id FROM purchase_orders WHERE status != 'closed'
            ORDER BY supplier_id""", ()),
        ("SELECT supplier_id, cert_type, valid_from, valid_to FROM supplier_qualifications", ()),
    ])


def _fp_stockout(con, as_of_str):
    """R16：skus(unit_price_usd) + inventory_positions(as_of_date≤as_of)。"""
    return _fingerprint(con, [
        ("SELECT sku_id, unit_price_usd FROM skus ORDER BY sku_id", ()),
        ("SELECT * FROM inventory_positions WHERE as_of_date <= ? ORDER BY inventory_position_id",
         (as_of_str,)),
    ])


def _fp_fulfill(con, as_of_str):
    """R17：sales_order_lines(line_status='open') + inventory_reservations(as_of_date≤as_of,
    status open/backordered) + inventory_positions(as_of_date≤as_of) + skus。"""
    return _fingerprint(con, [
        ("SELECT so_line_id FROM sales_order_lines WHERE line_status='open' ORDER BY so_line_id",
         ()),
        ("""SELECT reservation_id, so_line_id, inventory_position_id, qty, status
            FROM inventory_reservations WHERE as_of_date <= ? AND status IN ('open','backordered')
            ORDER BY reservation_id""", (as_of_str,)),
        ("SELECT * FROM inventory_positions WHERE as_of_date <= ? ORDER BY inventory_position_id",
         (as_of_str,)),
        ("SELECT sku_id, unit_price_usd FROM skus ORDER BY sku_id", ()),
    ])


def _fp_cycle(con, as_of_str):
    """R18：cycle_counts(as_of_date≤as_of) + inventory_positions(as_of_date≤as_of，供 sku 反查) +
    skus。"""
    return _fingerprint(con, [
        ("""SELECT cycle_count_id, inventory_position_id, warehouse_id, system_qty, counted_qty
            FROM cycle_counts WHERE as_of_date <= ? ORDER BY cycle_count_id""", (as_of_str,)),
        ("SELECT * FROM inventory_positions WHERE as_of_date <= ? ORDER BY inventory_position_id",
         (as_of_str,)),
        ("SELECT sku_id, unit_price_usd FROM skus ORDER BY sku_id", ()),
    ])


def _fp_single_source(con, as_of_str, window_start):
    """R14：risk_events(rule_id in R7/R9, detected_at 落 [window_start, as_of]) +
    skus(sku_status='active') + po_lines(spend 分组) + quotes⋈rfqs(status='awarded',
    created_date≤as_of)。注意：risk_events 切片读的是本次 detect 运行中 apply_procurement_
    candidates() 已写入的 R7/R9 事件（R14 检测在采购写库之后调用，见 main() 顺序），故此指纹
    反映"本次 detect 的采购阶段产出"而非纯上游原始表——如实标注，非缺陷（R14 本身的检出逻辑
    就依赖这条因果链，manual 原文如此设计）。"""
    return _fingerprint(con, [
        ("""SELECT risk_event_id, supplier_id, detected_at FROM risk_events
            WHERE rule_id IN ('R7','R9') AND supplier_id IS NOT NULL
              AND detected_at <= ? AND detected_at >= ? ORDER BY risk_event_id""",
         (as_of_str, window_start)),
        ("SELECT sku_id, supplier_id FROM skus WHERE sku_status='active' ORDER BY sku_id", ()),
        ("SELECT sku_id, qty, unit_price_usd FROM po_lines ORDER BY po_line_id", ()),
        ("""SELECT r.sku_id AS sku_id, q.supplier_id AS supplier_id
            FROM quotes q JOIN rfqs r ON r.rfq_id = q.rfq_id
            WHERE q.status = 'awarded' AND r.created_date <= ?
            ORDER BY sku_id, supplier_id, q.quote_id""", (as_of_str,)),
    ])


def _fp_maverick(con, as_of_str):
    """R15：quotes⋈rfqs(awarded 备源) + purchase_orders(supplier_of_po) +
    po_lines(sku_of_pol) + supplier_invoice_lines⋈supplier_invoices(issue_date≤as_of)。"""
    return _fingerprint(con, [
        ("""SELECT r.sku_id AS sku_id, q.supplier_id AS supplier_id
            FROM quotes q JOIN rfqs r ON r.rfq_id = q.rfq_id
            WHERE q.status = 'awarded' AND r.created_date <= ?
            ORDER BY sku_id, supplier_id, q.quote_id""", (as_of_str,)),
        ("SELECT po_id, supplier_id FROM purchase_orders ORDER BY po_id", ()),
        ("SELECT po_line_id, sku_id FROM po_lines ORDER BY po_line_id", ()),
        ("""SELECT sil.supplier_invoice_line_id, sil.supplier_invoice_id, sil.po_line_id
            FROM supplier_invoice_lines sil JOIN supplier_invoices si
                 ON si.supplier_invoice_id = sil.supplier_invoice_id
            WHERE si.issue_date <= ? ORDER BY sil.supplier_invoice_line_id""", (as_of_str,)),
        ("""SELECT supplier_invoice_id, supplier_id, po_id, total_usd FROM supplier_invoices
            WHERE issue_date <= ? ORDER BY supplier_invoice_id""", (as_of_str,)),
    ])


def _fp_payments_scheduled(con, as_of_str):
    """R19/R20：payments(as_of_date≤as_of)——两规则共享同一批付款快照
    （finance_rules.detect_finance_risks() 顶部单次查询）。"""
    return _fingerprint(con, [
        ("SELECT * FROM payments WHERE as_of_date <= ? ORDER BY payment_id", (as_of_str,)),
    ])


def _fp_payments_paid(con, as_of_str):
    """R21：payments(as_of_date≤as_of) + supplier_invoices/invoices/sales_order_lines 全量——
    R21 的 doc_amount() 按 (ref_type, ref_id) 动态查三表任一，逐笔精确隔离读取边界成本过高，
    取三张单据表全量兜底（规格允许的"不求完美，求稳定可复现"）。"""
    return _fingerprint(con, [
        ("SELECT * FROM payments WHERE as_of_date <= ? ORDER BY payment_id", (as_of_str,)),
        ("SELECT supplier_invoice_id, total_usd FROM supplier_invoices ORDER BY supplier_invoice_id",
         ()),
        ("SELECT invoice_id, total_usd FROM invoices ORDER BY invoice_id", ()),
        ("SELECT so_line_id, so_id, qty, unit_price_usd FROM sales_order_lines ORDER BY so_line_id",
         ()),
    ])


def _write_ledger(con, as_of, run_ts, rule_version, cands, rule_fingerprints):
    """G-Ledger 落账成功路径：cands 是本次 detect 产出的规则候选列表（函数内部按 rule_id
    分组计数），rule_fingerprints 是 {rule_id: input_fingerprint} 映射，须覆盖本次要写的
    全部 rule_id（含候选数为 0 的规则——一次 detect 对每个 rule_id 固定写一行，不因未命中而
    漏行，凑够 R1..R21 共 21 行）。status 恒 'ok'：detect_*_risks() 若抛异常，由调用方 except
    分支改走 _write_ledger_error，不会进入本函数。纯 INSERT + 独立 commit，不touch risk_events/
    action_log/sales_order_lines 等既有表——红线：台账是纯 append 的旁路。"""
    counts = {}
    for c in cands:
        counts[c["rule_id"]] = counts.get(c["rule_id"], 0) + 1
    cur = con.cursor()
    for rid, fp in rule_fingerprints.items():
        cur.execute("""INSERT INTO rule_run_ledger (as_of, rule_id, rule_version,
                       input_fingerprint, detected_count, status, error, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (as_of.isoformat(), rid, rule_version, fp, counts.get(rid, 0),
                     "ok", None, run_ts))
    con.commit()


def _write_ledger_error(con, as_of, run_ts, rule_version, rule_ids, exc):
    """G-Ledger 落账异常路径：detect_*_risks() 本身抛异常时，给该 block 覆盖的 rule_id 各写
    一行 status='error'/detected_count=0/error=异常摘要，调用方随后仍需 re-raise——台账只旁路
    记录，不吞异常、不改变脚本原有的崩溃退出行为（红线：不得改变任何既有检测结果/流程；已提交
    的 detect 阶段之前的表写入维持原样，未提交的后续阶段因异常传播而不执行，与"没有台账"时的
    行为完全一致，只是多了一行可追溯的失败记录）。"""
    msg = f"{type(exc).__name__}: {exc}"[:500]
    cur = con.cursor()
    for rid in rule_ids:
        cur.execute("""INSERT INTO rule_run_ledger (as_of, rule_id, rule_version,
                       input_fingerprint, detected_count, status, error, created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (as_of.isoformat(), rid, rule_version, None, 0, "error", msg, run_ts))
    con.commit()


def _relationship_id(relationship_type, *parts):
    return "REL-" + relationship_type + "-" + "-".join(str(p) for p in parts)


def _upsert_risk_relationships(con, risk_event_id, shipment_id, so_line_ids, invoice_line_ids):
    upsert_relationship(
        con,
        _relationship_id("risk_on_shipment", risk_event_id, shipment_id),
        "RiskEvent",
        risk_event_id,
        "Shipment",
        shipment_id,
        "risk_on_shipment",
        1.0,
        "engine.detect",
    )
    for so_line_id in sorted(so_line_ids):
        upsert_relationship(
            con,
            _relationship_id("risk_affects_line", risk_event_id, so_line_id),
            "RiskEvent",
            risk_event_id,
            "SalesOrderLine",
            so_line_id,
            "risk_affects_line",
            1.0,
            "engine.detect",
        )
    for invoice_line_id in sorted(invoice_line_ids):
        upsert_relationship(
            con,
            _relationship_id("risk_affects_invoice_line", risk_event_id, invoice_line_id),
            "RiskEvent",
            risk_event_id,
            "InvoiceLine",
            invoice_line_id,
            "risk_affects_invoice_line",
            1.0,
            "engine.detect",
        )


def apply_candidates(con, cands, as_of):
    """A2 语义写库。返回 (created, merged)。"""
    cur = con.cursor()
    ts = f"{as_of.isoformat()}T00:00:00Z"
    seq = cur.execute("SELECT count(*) FROM risk_events").fetchone()[0]
    created = merged = 0
    for c in sorted(cands, key=lambda x: (x["shipment_id"], x["rule_id"])):
        ex = cur.execute("""SELECT * FROM risk_events WHERE shipment_id=? AND type=?
                            AND status NOT IN ('resolved','escalated')""",
                         (c["shipment_id"], c["type"])).fetchone()
        # affected_invoice_line_ids（P1 新列）：R1-R3 无此键留 None；R4-R6 由 cost_rules 填充。
        c_inv = c.get("affected_invoice_line_ids")
        if ex:
            aff = sorted(set(json.loads(ex["affected_so_line_ids"]))
                         | set(json.loads(c["affected_so_line_ids"])))
            sev = ex["severity"] if SEV_ORDER[ex["severity"]] >= SEV_ORDER[c["severity"]] \
                else c["severity"]
            # A2 合并：invoice 行取并集（既有与本候选皆可能非空）
            ex_inv = json.loads(ex["affected_invoice_line_ids"]) if ex["affected_invoice_line_ids"] else []
            new_inv = json.loads(c_inv) if c_inv else []
            inv_merged = sorted(set(ex_inv) | set(new_inv))
            inv_val = json.dumps(inv_merged) if inv_merged else None
            cur.execute("""UPDATE risk_events SET affected_so_line_ids=?, severity=?,
                           affected_value_usd=?, root_cause=?, affected_invoice_line_ids=?
                           WHERE risk_event_id=?""",
                        (json.dumps(aff), sev, c["affected_value_usd"], c["root_cause"],
                         inv_val, ex["risk_event_id"]))
            rid, result = ex["risk_event_id"], "merged"
            invoice_line_ids = inv_merged
            merged += 1
        else:
            seq += 1
            rid = f"RSK-{seq:04d}"
            # 显式列名（不用位置 VALUES）：P1 采购锚点新增 po_id/supplier_id/affected_po_line_ids
            # 列后，R1-R6 写入不受列数变化影响；三个采购列此处留空（Build 2 才写）。
            cur.execute("""INSERT INTO risk_events (risk_event_id, type, rule_id, severity,
                           shipment_id, affected_so_line_ids, affected_value_usd, detected_at,
                           root_cause, status, resolved_at, outcome, resolution_summary,
                           affected_invoice_line_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, c["type"], c["rule_id"], c["severity"], c["shipment_id"],
                         c["affected_so_line_ids"], c["affected_value_usd"], c["detected_at"],
                         c["root_cause"], "open", None, None, None, c_inv))
            result = "created"
            aff = sorted(json.loads(c["affected_so_line_ids"]))
            invoice_line_ids = sorted(json.loads(c_inv)) if c_inv else []
            created += 1
        _upsert_risk_relationships(con, rid, c["shipment_id"], aff, invoice_line_ids)
        # 受影响行 → at_risk（A2 副作用）
        for lid in json.loads(c["affected_so_line_ids"]):
            cur.execute("""UPDATE sales_order_lines SET line_status='at_risk'
                           WHERE so_line_id=? AND line_status='allocated'""", (lid,))
        cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id,
                       params_json, as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                    ("engine", "system", "CreateRiskEvent", rid,
                     json.dumps({"rule_id": c["rule_id"], "shipment_id": c["shipment_id"],
                                 "severity": c["severity"]}),
                     as_of.isoformat(), ts, result))
    con.commit()
    return created, merged


def match_invoices(con, cost_cands, as_of):
    """MatchInvoice（系统，manual §2 状态机 + §4）：对每张 issue_date ≤ as_of 且
    status='received' 的发票执行 match。行涉及任一费用异常 → under_review，否则 → approved。
    每张写一条 action_log（actor=engine, role=system, action=MatchInvoice, target=invoice_id）。
    issue_date > as_of 的发票保持 received 不动（XB6 as_of 纪律）。返回状态分布 dict。"""
    cur = con.cursor()
    ts = f"{as_of.isoformat()}T00:00:00Z"
    # 异常涉及的全部账单行（来自本轮费用候选，as_of 已由 cost_rules 过滤）
    anomaly_ils = set()
    for c in cost_cands:
        anomaly_ils |= set(json.loads(c.get("affected_invoice_line_ids") or "[]"))

    invs = cur.execute("""SELECT invoice_id, issue_date FROM invoices
                          WHERE status='received' AND issue_date <= ?
                          ORDER BY invoice_id""", (as_of.isoformat(),)).fetchall()
    dist = {"approved": 0, "under_review": 0}
    for inv in invs:
        line_ids = [r["invoice_line_id"] for r in cur.execute(
            "SELECT invoice_line_id FROM invoice_lines WHERE invoice_id=?", (inv["invoice_id"],))]
        has_anom = any(lid in anomaly_ils for lid in line_ids)
        new_status = "under_review" if has_anom else "approved"
        cur.execute("UPDATE invoices SET status=? WHERE invoice_id=?",
                    (new_status, inv["invoice_id"]))
        dist[new_status] += 1
        cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id,
                       params_json, as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                    ("engine", "system", "MatchInvoice", inv["invoice_id"],
                     json.dumps({"result": new_status, "has_anomaly": has_anom}, ensure_ascii=False),
                     as_of.isoformat(), ts, "ok"))
    con.commit()
    return dist


def apply_procurement_candidates(con, cands, as_of):
    """P1 采购 RiskEvent 写库（Build 2）。与 apply_candidates 分离：采购事件用
    po_id/supplier_id/affected_po_line_ids 锚点，shipment_id 留空，不牵动 sales_order_lines，
    不做 (shipment,type) 合并（每 (po_line, rule) 唯一 → 恒 create）。审计时间戳用 as_of（D8）。
    RSK 序号续既有事件之后。返回 created。"""
    cur = con.cursor()
    ts = f"{as_of.isoformat()}T00:00:00Z"
    seq = cur.execute("SELECT count(*) FROM risk_events").fetchone()[0]
    created = 0

    def _order(x):
        # R7-R10 保持既有排序（False 段 + po_line_id + rule_id → RSK 序号逐字节不变）；
        # R11-R13 富化候选一律排在既有之后（True 段），空 affected_po_line_ids（R13 supplier 锚）
        # 用 po_id/supplier_id 兜底 anchor，避免 [0] 越界。
        plids = json.loads(x["affected_po_line_ids"] or "[]")
        anchor = plids[0] if plids else (x.get("po_id") or x.get("supplier_id") or "")
        is_rich = x["rule_id"] in ("R11", "R12", "R13")
        return (is_rich, anchor, x["rule_id"])

    for c in sorted(cands, key=_order):
        # 洞1.3 幂等：自然键 = (rule_id, po_id, supplier_id, affected_po_line_ids,
        # affected_invoice_line_ids)——R7-R11 锚 po_line（±invoice 行区分 R10/R11），R12 锚 po_id、
        # R13 锚 supplier_id。查非终态既有事件命中则 UPDATE（复用 id、不新增行、不动 seq），未命中
        # 才 INSERT 续号。重跑同数据全部命中 → risk_events 不翻倍（对齐 apply_candidates 合并语义）。
        ex = cur.execute("""SELECT risk_event_id FROM risk_events
                            WHERE rule_id=? AND po_id IS ? AND supplier_id IS ?
                              AND affected_po_line_ids=? AND affected_invoice_line_ids IS ?
                              AND status NOT IN ('resolved','escalated')""",
                         (c["rule_id"], c.get("po_id"), c.get("supplier_id"),
                          c["affected_po_line_ids"], c.get("affected_invoice_line_ids"))).fetchone()
        if ex:
            rid = ex["risk_event_id"]
            cur.execute("""UPDATE risk_events SET severity=?, affected_value_usd=?, root_cause=?
                           WHERE risk_event_id=?""",
                        (c["severity"], c["affected_value_usd"], c["root_cause"], rid))
            result = "merged"
        else:
            seq += 1
            rid = f"RSK-{seq:04d}"
            cur.execute("""INSERT INTO risk_events (risk_event_id, type, rule_id, severity,
                           shipment_id, affected_so_line_ids, affected_value_usd, detected_at,
                           root_cause, status, resolved_at, outcome, resolution_summary,
                           affected_invoice_line_ids, po_id, supplier_id, affected_po_line_ids)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, c["type"], c["rule_id"], c["severity"], None, "[]",
                         c["affected_value_usd"], c["detected_at"], c["root_cause"], "open",
                         None, None, None, c.get("affected_invoice_line_ids"),
                         c["po_id"], c["supplier_id"], c["affected_po_line_ids"]))
            result = "created"
            created += 1
        cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id,
                       params_json, as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                    ("engine", "system", "CreateRiskEvent", rid,
                     json.dumps({"rule_id": c["rule_id"], "po_id": c["po_id"],
                                 "po_line_ids": json.loads(c["affected_po_line_ids"]),
                                 "severity": c["severity"]}, ensure_ascii=False),
                     as_of.isoformat(), ts, result))
    con.commit()
    return created


def apply_warehouse_candidates(con, cands, as_of):
    """W1 仓储 RiskEvent 写库（Build 1/3 引擎接线）。与采购写库同构：用 warehouse_id +
    affected_so_line_ids（承载受影响业务对象 id：R16=InventoryPosition/R17=SalesOrderLine/
    R18=CycleCount）锚点，shipment_id/po_id/supplier_id 留空，不合并、不牵动 sales_order_lines
    （每锚点唯一 → 恒 create）。审计时间戳用 as_of（D8）。RSK 序号续既有事件之后（append，
    不扰动 R1-R13 序号）。返回 created。"""
    cur = con.cursor()
    ts = f"{as_of.isoformat()}T00:00:00Z"
    seq = cur.execute("SELECT count(*) FROM risk_events").fetchone()[0]
    created = 0
    for c in sorted(cands, key=lambda x: (x["rule_id"],
                                          json.loads(x["affected_object_ids"])[0])):
        # 洞1.3 幂等：自然键 = (rule_id, affected_object_ids)——R16 锚 inventory_position_id、
        # R17 锚 so_line_id、R18 锚 cycle_count_id（affected_object_ids 存入 affected_so_line_ids 列）。
        # 查非终态既有事件命中则 UPDATE（复用 id、不新增行、不动 seq），未命中才 INSERT 续号。
        ex = cur.execute("""SELECT risk_event_id FROM risk_events
                            WHERE rule_id=? AND affected_so_line_ids=?
                              AND status NOT IN ('resolved','escalated')""",
                         (c["rule_id"], c["affected_object_ids"])).fetchone()
        if ex:
            rid = ex["risk_event_id"]
            cur.execute("""UPDATE risk_events SET severity=?, affected_value_usd=?, root_cause=?
                           WHERE risk_event_id=?""",
                        (c["severity"], c["affected_value_usd"], c["root_cause"], rid))
            result = "merged"
        else:
            seq += 1
            rid = f"RSK-{seq:04d}"
            cur.execute("""INSERT INTO risk_events (risk_event_id, type, rule_id, severity,
                           shipment_id, affected_so_line_ids, affected_value_usd, detected_at,
                           root_cause, status, resolved_at, outcome, resolution_summary,
                           affected_invoice_line_ids, po_id, supplier_id, affected_po_line_ids,
                           warehouse_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, c["type"], c["rule_id"], c["severity"], None,
                         c["affected_object_ids"], c["affected_value_usd"], c["detected_at"],
                         c["root_cause"], "open", None, None, None, None, None, None, None,
                         c["warehouse_id"]))
            result = "created"
            created += 1
        cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id,
                       params_json, as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                    ("engine", "system", "CreateRiskEvent", rid,
                     json.dumps({"rule_id": c["rule_id"], "warehouse_id": c["warehouse_id"],
                                 "affected": json.loads(c["affected_object_ids"]),
                                 "severity": c["severity"]}, ensure_ascii=False),
                     as_of.isoformat(), ts, result))
    con.commit()
    return created


def apply_sourcing_candidates(con, cands, as_of):
    """P3 采购富化2 RiskEvent 写库（R14/R15）。与采购/仓储写库同构：不合并、不牵动订单行
    （每锚点唯一 → 恒 create）。R14 锚 supplier_id + affected_po_line_ids=[sku_id]；R15 锚 po_id +
    supplier_id + affected_invoice_line_ids。RSK 序号续既有事件之后（append，不扰动 R1-R18 序号）。
    审计时间戳用 as_of（D8）。返回 created。

    V6-裁1（补）：R14 是唯一锚定 SKU 的规则，检测时把受影响 sku_id 并行写入正式承载列
    affected_sku_ids（risk_affects_sku 的正式 N:M 载体）。核实到的证据链：既有采购评估器
    engine/evaluate_procurement.py 的 _anchor_det() 对 R14 消费 affected_po_line_ids[0]=sku_id
    与真值 sku_id 列匹配——退役旧 hack 需改评估器读法，违 V6『评估器全绿+真值不动』唯一约束，
    故 hack 保留、新列并行写入（两列并存，语义：affected_po_line_ids 为兼容载体、affected_sku_ids
    为正式承载）。R15 非 SKU 锚 → affected_sku_ids=None。"""
    cur = con.cursor()
    ts = f"{as_of.isoformat()}T00:00:00Z"
    seq = cur.execute("SELECT count(*) FROM risk_events").fetchone()[0]
    created = 0

    def _order(x):
        plids = json.loads(x["affected_po_line_ids"] or "[]")
        anchor = plids[0] if plids else (x.get("po_id") or x.get("supplier_id") or "")
        return (x["rule_id"], anchor)

    for c in sorted(cands, key=_order):
        # V6-裁1：affected_sku_ids 正式承载 risk_affects_sku。R14 锚定 SKU（sku_id 载于
        # affected_po_line_ids），并行写入正式列；R15 非 SKU 锚 → None。见函数 docstring 证据链。
        sku_ids = c["affected_po_line_ids"] if c["rule_id"] == "R14" else None
        # 洞1.3 幂等：自然键 = (rule_id, po_id, supplier_id, affected_po_line_ids,
        # affected_invoice_line_ids)——R14 锚 supplier_id + sku（载于 affected_po_line_ids）、
        # R15 锚 po_id + maverick 发票行（affected_invoice_line_ids）。命中则 UPDATE 复用 id。
        ex = cur.execute("""SELECT risk_event_id FROM risk_events
                            WHERE rule_id=? AND po_id IS ? AND supplier_id IS ?
                              AND affected_po_line_ids=? AND affected_invoice_line_ids IS ?
                              AND status NOT IN ('resolved','escalated')""",
                         (c["rule_id"], c.get("po_id"), c.get("supplier_id"),
                          c["affected_po_line_ids"], c.get("affected_invoice_line_ids"))).fetchone()
        if ex:
            rid = ex["risk_event_id"]
            cur.execute("""UPDATE risk_events SET severity=?, affected_value_usd=?, root_cause=?
                           WHERE risk_event_id=?""",
                        (c["severity"], c["affected_value_usd"], c["root_cause"], rid))
            result = "merged"
        else:
            seq += 1
            rid = f"RSK-{seq:04d}"
            cur.execute("""INSERT INTO risk_events (risk_event_id, type, rule_id, severity,
                           shipment_id, affected_so_line_ids, affected_value_usd, detected_at,
                           root_cause, status, resolved_at, outcome, resolution_summary,
                           affected_invoice_line_ids, po_id, supplier_id, affected_po_line_ids,
                           affected_sku_ids)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, c["type"], c["rule_id"], c["severity"], None, "[]",
                         c["affected_value_usd"], c["detected_at"], c["root_cause"], "open",
                         None, None, None, c.get("affected_invoice_line_ids"),
                         c["po_id"], c["supplier_id"], c["affected_po_line_ids"],
                         sku_ids))
            result = "created"
            created += 1
        cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id,
                       params_json, as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                    ("engine", "system", "CreateRiskEvent", rid,
                     json.dumps({"rule_id": c["rule_id"], "po_id": c["po_id"],
                                 "supplier_id": c["supplier_id"],
                                 "affected": json.loads(c["affected_po_line_ids"] or "[]"),
                                 "severity": c["severity"]}, ensure_ascii=False),
                     as_of.isoformat(), ts, result))
    con.commit()
    return created


def apply_finance_candidates(con, cands, as_of):
    """F1 资金流 RiskEvent 写库（R19-R21，V8-②）。与仓储/采购写库同构：不合并、不牵动订单行
    （每 anchor 唯一 → 恒 create）。资金流 RiskEvent 无 shipment/po/supplier/warehouse 锚，用
    affected_so_line_ids（承载受影响业务对象 id=payment_id 或合成窗口键 CASH14D-<as_of>）锚点
    （沿仓储 RiskEvent 通用列先例）。RSK 序号续既有事件之后（append，不扰动 R1-R18 序号）。
    审计时间戳用 as_of（D8）。返回 created。"""
    cur = con.cursor()
    ts = f"{as_of.isoformat()}T00:00:00Z"
    seq = cur.execute("SELECT count(*) FROM risk_events").fetchone()[0]
    created = 0
    for c in sorted(cands, key=lambda x: (x["rule_id"], x["anchor"])):
        # 洞1.3 幂等：自然键 = (rule_id, affected_so_line_ids=json([anchor]))——R19/R21 锚
        # payment_id、R20 锚合成窗口键 CASH14D-<as_of>（anchor 存入 affected_so_line_ids 列）。
        # 命中非终态既有事件则 UPDATE（复用 id、不新增行、不动 seq），未命中才 INSERT 续号。
        anchor_json = json.dumps([c["anchor"]])
        ex = cur.execute("""SELECT risk_event_id FROM risk_events
                            WHERE rule_id=? AND affected_so_line_ids=?
                              AND status NOT IN ('resolved','escalated')""",
                         (c["rule_id"], anchor_json)).fetchone()
        if ex:
            rid = ex["risk_event_id"]
            cur.execute("""UPDATE risk_events SET severity=?, affected_value_usd=?, root_cause=?
                           WHERE risk_event_id=?""",
                        (c["severity"], c["affected_value_usd"], c["root_cause"], rid))
            result = "merged"
        else:
            seq += 1
            rid = f"RSK-{seq:04d}"
            cur.execute("""INSERT INTO risk_events (risk_event_id, type, rule_id, severity,
                           shipment_id, affected_so_line_ids, affected_value_usd, detected_at,
                           root_cause, status, resolved_at, outcome, resolution_summary,
                           affected_invoice_line_ids, po_id, supplier_id, affected_po_line_ids,
                           warehouse_id, affected_sku_ids)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, c["type"], c["rule_id"], c["severity"], None,
                         anchor_json, c["affected_value_usd"], c["detected_at"],
                         c["root_cause"], "open", None, None, None, None, None, None, None,
                         None, None))
            result = "created"
            created += 1
        cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id,
                       params_json, as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                    ("engine", "system", "CreateRiskEvent", rid,
                     json.dumps({"rule_id": c["rule_id"], "anchor": c["anchor"],
                                 "severity": c["severity"]}, ensure_ascii=False),
                     as_of.isoformat(), ts, result))
    con.commit()
    return created


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/datagen.yaml")
    ap.add_argument("--as-of", default=None, help="默认取配置 window.as_of（D8：必须显式，禁系统时钟）")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    as_of = date.fromisoformat(args.as_of or cfg["window"]["as_of"])
    as_of_str = as_of.isoformat()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_rule_run_ledger_table(con)  # 幂等兜底：正表已由 pipeline.build_ontology 建
    rule_version = _ontology_version()
    # G-Ledger created_at：本表是运维台账（记录"本次 detect 实际执行的真实时刻"），非业务对象，
    # 不受 D8「审计时间戳用 as_of 不用系统时钟」约束——D8 管的是模拟世界内的业务事件时间，这里
    # 要回答的是"这批 21 行是哪一次真实运行产出的"，故用真实系统时钟（区别于 as_of 列，as_of 列
    # 仍是模拟世界日期）。
    run_ts = datetime.now(timezone.utc).isoformat()

    # R1-R3 控制塔风险，随后 R4-R6 费用异常，合并走同一 apply_candidates（A2 合并语义）
    try:
        cands = detect_risks(con, as_of, cfg)
    except Exception as e:
        _write_ledger_error(con, as_of, run_ts, rule_version, TOWER_RULES, e)
        raise
    _write_ledger(con, as_of, run_ts, rule_version, cands,
                  {rid: _fp_tower(con, as_of_str) for rid in TOWER_RULES})

    try:
        cost_cands = detect_cost_anomalies(con, as_of, cfg)
    except Exception as e:
        _write_ledger_error(con, as_of, run_ts, rule_version, COST_RULES, e)
        raise
    _write_ledger(con, as_of, run_ts, rule_version, cost_cands,
                  {rid: _fp_cost(con, as_of_str) for rid in COST_RULES})

    # MatchInvoice 先于 CreateRiskEvent：对账匹配暴露异常，异常再生成风险事件（§2 状态机因果）。
    inv_dist = match_invoices(con, cost_cands, as_of)
    created, merged = apply_candidates(con, cands + cost_cands, as_of)

    # P1 采购 R7-R10（Build 2）+ R11-R13（富化）：独立检测与写库路径（po 锚点，不合并、不牵动订单行）
    try:
        proc_cands = detect_procurement_risks(con, as_of, cfg)
    except Exception as e:
        _write_ledger_error(con, as_of, run_ts, rule_version, PROCUREMENT_RULES, e)
        raise
    proc_fp = {rid: _fp_receiving(con, as_of_str) for rid in ("R7", "R8", "R9")}
    proc_fp.update({rid: _fp_invoice_match(con, as_of_str) for rid in ("R10", "R11")})
    proc_fp["R12"] = _fp_deposit(con, as_of_str)
    proc_fp["R13"] = _fp_qualification(con, as_of_str)
    _write_ledger(con, as_of, run_ts, rule_version, proc_cands, proc_fp)
    proc_created = apply_procurement_candidates(con, proc_cands, as_of)

    # W1 仓储 R16-R18（Build 1/3）：独立检测与写库路径（warehouse 锚点，不合并、不牵动订单行）
    try:
        wh_cands = detect_warehouse_risks(con, as_of, cfg)
    except Exception as e:
        _write_ledger_error(con, as_of, run_ts, rule_version, WAREHOUSE_RULES, e)
        raise
    wh_fp = {"R16": _fp_stockout(con, as_of_str), "R17": _fp_fulfill(con, as_of_str),
             "R18": _fp_cycle(con, as_of_str)}
    _write_ledger(con, as_of, run_ts, rule_version, wh_cands, wh_fp)
    wh_created = apply_warehouse_candidates(con, wh_cands, as_of)

    # P3 采购富化2 R14/R15（Build A）：须在 proc R7-R13 之后（R14 依赖 R7/R9 事件）。RSK 序号 append 末尾。
    try:
        src_cands = detect_sourcing_risks(con, as_of, cfg)
    except Exception as e:
        _write_ledger_error(con, as_of, run_ts, rule_version, SOURCING_RULES, e)
        raise
    window_start = (as_of - timedelta(days=cfg["sourcing"]["single_source_recent_days"])).isoformat()
    src_fp = {"R14": _fp_single_source(con, as_of_str, window_start),
              "R15": _fp_maverick(con, as_of_str)}
    _write_ledger(con, as_of, run_ts, rule_version, src_cands, src_fp)
    src_created = apply_sourcing_candidates(con, src_cands, as_of)

    # F1 资金流 R19-R21（V8-②）：独立检测与写库路径（payment/合成窗口键锚点，不合并、不牵动订单行）。
    # RSK 序号 append 末尾（不扰动 R1-R18 序号）。
    try:
        fin_cands = detect_finance_risks(con, as_of, cfg)
    except Exception as e:
        _write_ledger_error(con, as_of, run_ts, rule_version, FINANCE_RULES, e)
        raise
    fin_fp = {"R19": _fp_payments_scheduled(con, as_of_str),
              "R20": _fp_payments_scheduled(con, as_of_str),
              "R21": _fp_payments_paid(con, as_of_str)}
    _write_ledger(con, as_of, run_ts, rule_version, fin_cands, fin_fp)
    fin_created = apply_finance_candidates(con, fin_cands, as_of)
    by_rule = {}
    for c in cands + cost_cands + proc_cands + wh_cands + src_cands + fin_cands:
        by_rule[c["rule_id"]] = by_rule.get(c["rule_id"], 0) + 1
    print(json.dumps({"as_of": as_of.isoformat(),
                      "candidates": len(cands) + len(cost_cands) + len(proc_cands)
                      + len(wh_cands) + len(src_cands) + len(fin_cands),
                      "created": created + proc_created + wh_created + src_created + fin_created,
                      "merged": merged,
                      "by_rule": by_rule, "invoice_status": inv_dist},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
