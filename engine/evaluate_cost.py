"""X3 费用异常 KPI 评估：python3 -m engine.evaluate_cost

检出的 R4-R6 risk_events 对照 data/truth/expected_cost_anomalies.csv（本脚本属评估层，
允许读 truth）。KPI（cost-manual §XB，受 AGENTS §5 保护，不得为通过而改判定逻辑或目标值）：
  Recall ≥ 95%（gt 中 severity=high 的漏判 = 0）
  Precision ≥ 85%
  逐项设计案例 XB2-XB6 + 匹配质量（severity / value±0.02 / invoice 行集合）
格式仿 engine/evaluate.py；exit code：全过 0，否则 1。
"""
import csv
import json
import sqlite3
import sys

FAILS = []
COST_RULES = ("R4", "R5", "R6")


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    with open("data/truth/expected_cost_anomalies.csv", encoding="utf-8") as f:
        expected = list(csv.DictReader(f))
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    detected = [dict(r) for r in con.execute(
        "SELECT * FROM risk_events WHERE rule_id IN ('R4','R5','R6')")]

    exp_map = {(r["shipment_id"], r["rule_id"]): r for r in expected}
    det_map = {(r["shipment_id"], r["rule_id"]): r for r in detected}
    matched = set(exp_map) & set(det_map)
    missed = set(exp_map) - set(det_map)
    false_pos = set(det_map) - set(exp_map)

    recall = len(matched) / len(exp_map) if exp_map else 1.0
    precision = len(matched) / len(det_map) if det_map else 1.0
    high_missed = [k for k in missed if exp_map[k]["severity"] == "high"]

    print(f"expected={len(exp_map)} detected={len(det_map)} matched={len(matched)}")
    print(f"Recall={recall:.3f}  Precision={precision:.3f}")
    by_rule = {r: sum(1 for k in det_map if k[1] == r) for r in COST_RULES}
    print(f"by_rule(detected)={by_rule}")

    print("== KPI 判定（XB1）==")
    check(f"Recall ≥ 0.95（实际 {recall:.3f}）", recall >= 0.95, f"漏判: {sorted(missed)}")
    check("高危(high)漏判 = 0", not high_missed, str(high_missed))
    check(f"Precision ≥ 0.85（实际 {precision:.3f}）", precision >= 0.85,
          f"误报: {sorted(false_pos)}")

    print("== 匹配质量（对已匹配事件）==")
    sev_ok = val_ok = inv_ok = 0
    design_bad = []
    for k in matched:
        e, d = exp_map[k], det_map[k]
        if e["severity"] == d["severity"]:
            sev_ok += 1
        if abs(float(e["anomaly_value_usd"]) - float(d["affected_value_usd"])) < 0.02:
            val_ok += 1
        exp_inv = sorted(json.loads(e["affected_invoice_line_ids"]))
        det_inv = sorted(json.loads(d["affected_invoice_line_ids"] or "[]"))
        if exp_inv == det_inv:
            inv_ok += 1
        elif e["case_id"]:
            design_bad.append(k)
    n = len(matched) or 1
    check(f"severity 一致（{sev_ok}/{n}）", sev_ok == len(matched))
    check(f"影响金额一致 ±0.02（{val_ok}/{n}）", val_ok == len(matched))
    check(f"invoice 行集合一致（{inv_ok}/{n}）", inv_ok == len(matched))
    check("设计案例 invoice 行定位 100%", not design_bad, str(design_bad))

    def det_by_case(cid):
        for e in expected:
            if e["case_id"] == cid:
                return det_map.get((e["shipment_id"], e["rule_id"])), e
        return None, None

    print("== 逐项设计案例 ==")
    # XB2 CD-A：R4 检出，value 与 gt ±0.02（超收部分非全额）
    d_a, e_a = det_by_case("CD-A")
    check("XB2 CD-A R4 检出且 value±0.02（超收部分）",
          d_a is not None and e_a["rule_id"] == "R4"
          and abs(float(d_a["affected_value_usd"]) - float(e_a["anomaly_value_usd"])) < 0.02,
          f"det={d_a['affected_value_usd'] if d_a else None} gt={e_a['anomaly_value_usd'] if e_a else None}")

    # XB3 CD-B 跨票 + CD-F 同票 R5 检出
    d_b, e_b = det_by_case("CD-B")
    d_f, e_f = det_by_case("CD-F")
    check("XB3 CD-B（跨票）与 CD-F（同票）R5 均检出",
          d_b is not None and d_b["rule_id"] == "R5"
          and d_f is not None and d_f["rule_id"] == "R5")

    # XB4 CD-C：R6 检出且 root_cause 含"延误"与"8"（F4 归因）
    d_c, e_c = det_by_case("CD-C")
    rc = (d_c["root_cause"] if d_c else "") or ""
    check("XB4 CD-C R6 检出且 root_cause 含'延误'与'8'（F4）",
          d_c is not None and d_c["rule_id"] == "R6" and "延误" in rc and "8" in rc, rc)

    # XB5 CD-E（SHP-2026-0005）零费用异常且其全部发票 status='approved'
    cde_ship = "SHP-2026-0005"
    cde_anoms = [k for k in det_map if k[0] == cde_ship]
    cde_inv = con.execute("SELECT status FROM invoices WHERE shipment_id=?", (cde_ship,)).fetchall()
    all_approved = bool(cde_inv) and all(r["status"] == "approved" for r in cde_inv)
    check("XB5 CD-E 零费用异常且全部发票 approved",
          not cde_anoms and all_approved,
          f"anoms={cde_anoms} statuses={[r['status'] for r in cde_inv]}")

    # XB6 as_of 安全：issue_date > as_of 的发票仍 received，其行未进任何异常
    as_of = con.execute("SELECT as_of_date FROM action_log WHERE action='MatchInvoice' LIMIT 1").fetchone()
    as_of = as_of["as_of_date"] if as_of else "2026-08-08"
    future = con.execute("SELECT invoice_id, status FROM invoices WHERE issue_date > ?",
                         (as_of,)).fetchall()
    if not future:
        print("  [SKIP] XB6：数据中无 issue_date>as_of 的未来发票——视为通过（注：无未来发票）")
    else:
        still_received = all(r["status"] == "received" for r in future)
        # 未来发票的行不得进入任何 R4-R6 异常的 affected_invoice_line_ids
        anom_ils = set()
        for d in detected:
            anom_ils |= set(json.loads(d["affected_invoice_line_ids"] or "[]"))
        fut_inv_ids = [r["invoice_id"] for r in future]
        ph = ",".join("?" * len(fut_inv_ids))
        fut_ils = {r["invoice_line_id"] for r in con.execute(
            f"SELECT invoice_line_id FROM invoice_lines WHERE invoice_id IN ({ph})", fut_inv_ids)}
        no_leak = not (fut_ils & anom_ils)
        check(f"XB6 未来发票（{len(future)} 张）仍 received 且其行未进异常",
              still_received and no_leak,
              f"still_received={still_received} leaked={sorted(fut_ils & anom_ils)}")

    print("== 发票状态分布 ==")
    for r in con.execute("SELECT status, count(*) c FROM invoices GROUP BY status ORDER BY status"):
        print(f"  {r['status']}: {r['c']}")

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
