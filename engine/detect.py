"""W4 检测入口：python3 -m engine.detect [--as-of 2026-08-08]

跑 R1-R3 → 按 A2 CreateRiskEvent 语义写入 risk_events：
同 (shipment, type) 非终态事件合并（severity 取高、受影响行取并集），
受影响行置 at_risk，全程写 action_log（审计时间戳用 as_of，不用系统时钟——D8）。
"""
import argparse
import json
import sqlite3
from datetime import date

import yaml

from .rules import detect_risks, SEV_ORDER
from .cost_rules import detect_cost_anomalies
from .procurement_rules import detect_procurement_risks
from .warehouse_rules import detect_warehouse_risks
from .sourcing_rules import detect_sourcing_risks
from .finance_rules import detect_finance_risks
from .graph import upsert_relationship

DB = "data/ontology.sqlite"


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

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # R1-R3 控制塔风险，随后 R4-R6 费用异常，合并走同一 apply_candidates（A2 合并语义）
    cands = detect_risks(con, as_of, cfg)
    cost_cands = detect_cost_anomalies(con, as_of, cfg)
    # MatchInvoice 先于 CreateRiskEvent：对账匹配暴露异常，异常再生成风险事件（§2 状态机因果）。
    inv_dist = match_invoices(con, cost_cands, as_of)
    created, merged = apply_candidates(con, cands + cost_cands, as_of)
    # P1 采购 R7-R10（Build 2）：独立检测与写库路径（po 锚点，不合并、不牵动订单行）
    proc_cands = detect_procurement_risks(con, as_of, cfg)
    proc_created = apply_procurement_candidates(con, proc_cands, as_of)
    # W1 仓储 R16-R18（Build 1/3）：独立检测与写库路径（warehouse 锚点，不合并、不牵动订单行）
    wh_cands = detect_warehouse_risks(con, as_of, cfg)
    wh_created = apply_warehouse_candidates(con, wh_cands, as_of)
    # P3 采购富化2 R14/R15（Build A）：须在 proc R7-R13 之后（R14 依赖 R7/R9 事件）。RSK 序号 append 末尾。
    src_cands = detect_sourcing_risks(con, as_of, cfg)
    src_created = apply_sourcing_candidates(con, src_cands, as_of)
    # F1 资金流 R19-R21（V8-②）：独立检测与写库路径（payment/合成窗口键锚点，不合并、不牵动订单行）。
    # RSK 序号 append 末尾（不扰动 R1-R18 序号）。
    fin_cands = detect_finance_risks(con, as_of, cfg)
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
