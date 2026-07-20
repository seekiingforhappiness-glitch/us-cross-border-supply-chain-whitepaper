"""供应商风险 KPI 评估：python3 -m engine.evaluate_supplier_risk（R22-R23，V23① 感知层）。

检出的 R22/R23 risk_events 对照 data/truth/expected_supplier_risks.csv（本脚本属评估层，
允许读 truth；引擎检测绝不读 truth——§5 铁律）。

匹配锚点按规则：
  R22 → supplier_id（检出侧直接读 supplier_id 列——R13/R14 同款供应商锚）；
  R23 → qualification_id（检出侧取 affected_so_line_ids[0]——通用列承载受影响业务对象 id，
        仓储/资金流先例）。
  真值每锚点一行；不达阈值/已续证样本不在真值，被检出即 false positive → 计入误报。

KPI（每规则）：precision = recall = 1.000 → PASS（门=1.000 而非家族惯例 0.90：R22/R23 真值
是纯派生 oracle 与引擎双侧独立实现同一确定性判定，任何偏差都是实现分歧，不存在"接近即可"）。
逐规则打印 检出/真值/TP/FP/FN + precision/recall + PASS/FAIL；匹配质量 severity / value±0.02。
格式仿 engine/evaluate_warehouse.py；exit code：全过 0，否则 1。
"""
import csv
import json
import sqlite3
import sys

FAILS = []
SUP_RULES = ("R22", "R23")
GATE_P = 1.0
GATE_R = 1.0


def _anchor_exp(r):
    """真值行锚点：R22→supplier_id，R23→qualification_id。"""
    return r["supplier_id"] if r["rule_id"] == "R22" else r["qualification_id"]


def _anchor_det(r):
    """检出事件锚点：R22→supplier_id 列，R23→affected_so_line_ids[0]（通用列承载）。"""
    if r["rule_id"] == "R22":
        return r["supplier_id"]
    ids = json.loads(r["affected_so_line_ids"] or "[]")
    return ids[0] if ids else None


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    with open("data/truth/expected_supplier_risks.csv", encoding="utf-8") as f:
        expected = list(csv.DictReader(f))
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    detected = [dict(r) for r in con.execute(
        "SELECT * FROM risk_events WHERE rule_id IN ('R22','R23')")]

    exp_map = {(_anchor_exp(r), r["rule_id"]): r for r in expected}
    det_map = {(_anchor_det(r), r["rule_id"]): r for r in detected}
    matched = set(exp_map) & set(det_map)
    false_pos = set(det_map) - set(exp_map)

    recall = len(matched) / len(exp_map) if exp_map else 1.0
    precision = len(matched) / len(det_map) if det_map else 1.0
    print(f"expected={len(exp_map)} detected={len(det_map)} matched={len(matched)}")
    print(f"整体 Recall={recall:.3f}  Precision={precision:.3f}  误报(FP)={len(false_pos)}")

    print("== 逐规则 检出/真值/TP/FP/FN + P/R + 判定（precision=recall=1.000）==")
    for rule in SUP_RULES:
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

    # 灰区误报：达成率 ≥ 阈值 / 样本不足 / 已续证 / 有效期充裕的样本不在真值，被检出即 FP。
    print("== 灰区误报核查 ==")
    check(f"阈内/已续证/样本不足样本零误报（整体 FP={len(false_pos)}）", len(false_pos) == 0,
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
