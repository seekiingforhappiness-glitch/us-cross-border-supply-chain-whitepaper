"""仓储异常 KPI 评估：python3 -m engine.evaluate_warehouse（R16-R18 库存准确主线，W1 Build 1/3）

检出的仓储 risk_events 对照 data/truth/expected_warehouse_risks.csv（本脚本属评估层，允许读 truth；
引擎检测绝不读 truth——§5 铁律）。

匹配锚点按规则：
  R16 → inventory_position_id；R17 → so_line_id；R18 → cycle_count_id。
  检出侧统一取 affected_so_line_ids[0]（仓储 RiskEvent 用该通用列承载受影响业务对象 id）。
  真值每锚点一行；灰区/干净样本不在真值，被检出即 false positive → 计入误报。

KPI（每规则）：precision ≥ 0.90 且 recall ≥ 0.90 → PASS。
逐规则打印 检出/真值/TP/FP/FN + precision/recall + PASS/FAIL；匹配质量 severity / value±0.02。
格式仿 engine/evaluate_procurement.py；exit code：全过 0，否则 1。
"""
import csv
import json
import sqlite3
import sys

FAILS = []
WH_RULES = ("R16", "R17", "R18")
GATE_P = 0.90
GATE_R = 0.90


def _anchor_exp(r):
    """真值行锚点：R16→inventory_position_id，R17→so_line_id，R18→cycle_count_id。"""
    if r["rule_id"] == "R16":
        return r["inventory_position_id"]
    if r["rule_id"] == "R17":
        return r["so_line_id"]
    return r["cycle_count_id"]


def _anchor_det(r):
    """检出事件锚点：统一取 affected_so_line_ids[0]（仓储 RiskEvent 承载受影响业务对象 id）。"""
    ids = json.loads(r["affected_so_line_ids"] or "[]")
    return ids[0] if ids else None


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    with open("data/truth/expected_warehouse_risks.csv", encoding="utf-8") as f:
        expected = list(csv.DictReader(f))
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    detected = [dict(r) for r in con.execute(
        "SELECT * FROM risk_events WHERE rule_id IN ('R16','R17','R18')")]

    exp_map = {(_anchor_exp(r), r["rule_id"]): r for r in expected}
    det_map = {(_anchor_det(r), r["rule_id"]): r for r in detected}
    matched = set(exp_map) & set(det_map)
    missed = set(exp_map) - set(det_map)
    false_pos = set(det_map) - set(exp_map)

    recall = len(matched) / len(exp_map) if exp_map else 1.0
    precision = len(matched) / len(det_map) if det_map else 1.0
    print(f"expected={len(exp_map)} detected={len(det_map)} matched={len(matched)}")
    print(f"整体 Recall={recall:.3f}  Precision={precision:.3f}  误报(FP)={len(false_pos)}")

    print("== 逐规则 检出/真值/TP/FP/FN + P/R + 判定（precision≥0.90 recall≥0.90）==")
    for rule in WH_RULES:
        exp_r = {k for k in exp_map if k[1] == rule}
        det_r = {k for k in det_map if k[1] == rule}
        tp = exp_r & det_r
        fp = det_r - exp_r
        fn = exp_r - det_r
        p = len(tp) / len(det_r) if det_r else 1.0
        r = len(tp) / len(exp_r) if exp_r else 1.0
        ok = p >= GATE_P and r >= GATE_R
        detail = ""
        if not ok:
            detail = f"FN={sorted(x[0] for x in fn)} FP={sorted(x[0] for x in fp)}"
        check(f"{rule}: 检出={len(det_r)} 真值={len(exp_r)} TP={len(tp)} FP={len(fp)} "
              f"FN={len(fn)} P={p:.3f} R={r:.3f}", ok, detail)

    # 灰区误报：灰区样本不在真值，被检出即计入 FP。整体 FP=0 ⟹ 灰区零误报。
    print("== 灰区误报核查 ==")
    check(f"灰区/容差内样本零误报（整体 FP={len(false_pos)}）", len(false_pos) == 0,
          f"误报键: {sorted(false_pos)}")

    print("== 匹配质量（对已匹配事件）==")
    sev_ok = val_ok = 0
    for k in matched:
        e, d = exp_map[k], det_map[k]
        if e["severity"] == d["severity"]:
            sev_ok += 1
        if abs(float(e["anomaly_value_usd"]) - float(d["affected_value_usd"])) < 0.02:
            val_ok += 1
    check(f"severity 一致（{sev_ok}/{len(matched)}）", sev_ok == len(matched))
    check(f"影响金额一致 ±0.02（{val_ok}/{len(matched)}）", val_ok == len(matched))

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
