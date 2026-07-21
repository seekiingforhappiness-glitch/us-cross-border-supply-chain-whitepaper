"""W4 KPI 评估：python3 -m engine.evaluate

检出的 risk_events 对照 data/truth/expected_risk_events.csv（本脚本属评估层，允许读 truth）。
KPI（plan §10，受 AGENTS §5 保护，不得为通过而修改判定逻辑或目标值）：
  Recall ≥ 95% 且高危（high/critical）漏判 = 0
  Precision ≥ 85%
  设计案例影响定位准确率 = 100%
"""
import csv
import json
import sqlite3
import sys

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    with open("data/truth/expected_risk_events.csv", encoding="utf-8") as f:
        expected = list(csv.DictReader(f))
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    detected = [dict(r) for r in con.execute("SELECT * FROM risk_events")]

    exp_map = {(r["shipment_id"], r["rule_id"]): r for r in expected}
    # R4-R6 由 evaluate_cost 评估（X3 主会话授权的范围澄清，非判定逻辑变更）
    det_map = {(r["shipment_id"], r["rule_id"]): r for r in detected
               if r["rule_id"] in ("R1", "R2", "R3")}
    matched = set(exp_map) & set(det_map)
    missed = set(exp_map) - set(det_map)
    false_pos = set(det_map) - set(exp_map)

    recall = len(matched) / len(exp_map) if exp_map else 1.0
    precision = len(matched) / len(det_map) if det_map else 1.0
    high_missed = [k for k in missed if exp_map[k]["severity"] in ("high", "critical")]

    print(f"expected={len(exp_map)} detected={len(det_map)} matched={len(matched)}")
    print(f"Recall={recall:.3f}  Precision={precision:.3f}")
    print("== KPI 判定 ==")
    check(f"Recall ≥ 0.95（实际 {recall:.3f}）", recall >= 0.95, f"漏判: {sorted(missed)}")
    check("高危漏判 = 0", not high_missed, str(high_missed))
    check(f"Precision ≥ 0.85（实际 {precision:.3f}）", precision >= 0.85,
          f"误报: {sorted(false_pos)}")

    print("== 匹配质量（对已匹配事件）==")
    sev_ok = aff_ok = val_ok = 0
    design_aff_bad = []
    for k in matched:
        e, d = exp_map[k], det_map[k]
        if e["severity"] == d["severity"]:
            sev_ok += 1
        same_aff = sorted(json.loads(e["affected_so_line_ids"])) == \
            sorted(json.loads(d["affected_so_line_ids"]))
        if same_aff:
            aff_ok += 1
        elif e["case_id"]:
            design_aff_bad.append(k)
        if abs(float(e["affected_value_usd"]) - float(d["affected_value_usd"])) < 0.01:
            val_ok += 1
    n = len(matched) or 1
    check(f"severity 一致（{sev_ok}/{n}）", sev_ok == len(matched))
    check(f"影响行集合一致（{aff_ok}/{n}）", aff_ok == len(matched))
    check("设计案例影响定位 100%", not design_aff_bad, str(design_aff_bad))
    check(f"影响金额一致（{val_ok}/{n}）", val_ok == len(matched))

    print("== 闭环前置检查 ==")
    at_risk = con.execute("SELECT count(*) c FROM sales_order_lines WHERE line_status='at_risk'").fetchone()["c"]
    logs = con.execute("SELECT count(*) c FROM action_log WHERE action='CreateRiskEvent'").fetchone()["c"]
    check(f"受影响行已置 at_risk（{at_risk} 条）", at_risk > 0)
    check(f"每个事件有审计记录（{logs} 条 log vs {len(detected)} 事件）", logs >= len(detected))

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
