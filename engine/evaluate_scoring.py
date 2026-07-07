"""Y1 风险评分模型评估：python3 -m engine.evaluate_scoring

H1 两段式验收（格式仿 engine/evaluate_cost）：
  组 B（早警）——硬门：测试集 AUC ≥ auc_gate_early（0.7，H1 唯一硬门）且 precision@10 ≥ 0.5。
  组 A（静态）——只验诚实，不设 AUC 门槛：
    AUC_A < 0.62 时断言输出含"静态特征无信号/接近随机"类结论；
    AUC_A ≥ 0.62 时如实打印"静态特征存在弱信号"并同样通过（两种结果都合法，禁伪造叙事）。
  校准表：早警模型按概率十分位分桶，打印每桶实际正例率（仅展示不设门）。
  可复现：连续跑两次 scoring，两列分数逐值一致（断言）。

本脚本属评估层，允许读 truth（标签来源）。exit code：全过 0，否则 1。
"""
import sqlite3
import sys

import yaml

from . import scoring

FAILS = []
CONCLUSION_NO_SIGNAL = "静态特征无信号（接近随机），与随机注入延误的世界一致"
CONCLUSION_WEAK_SIGNAL = "静态特征存在弱信号"


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def calibration_table(con):
    """早警模型按概率十分位分桶，打印每桶实际正例率（全量样本，仅展示）。"""
    pos = scoring.load_labels()
    rows = [(r["shipment_id"], r["risk_score_early"])
            for r in con.execute("SELECT shipment_id, risk_score_early FROM shipments "
                                 "WHERE status != 'planned' AND risk_score_early IS NOT NULL")]
    rows.sort(key=lambda x: x[1])
    n = len(rows)
    print("== 早警模型校准表（概率十分位，仅展示）==")
    print("  bucket  n   score_range          actual_pos_rate")
    for b in range(10):
        lo = b * n // 10
        hi = (b + 1) * n // 10 if b < 9 else n
        chunk = rows[lo:hi]
        if not chunk:
            continue
        npos = sum(1 for sid, _ in chunk if sid in pos)
        s_lo, s_hi = chunk[0][1], chunk[-1][1]
        print(f"  D{b + 1:<2}    {len(chunk):<3} [{s_lo:.3f}, {s_hi:.3f}]"
              f"   {npos}/{len(chunk)} = {npos / len(chunk):.2f}")


def main():
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    gate = cfg["scoring"]["auc_gate_early"]

    # 可复现：连续跑两次 scoring，读回两列分数逐值一致
    s1 = scoring.run(cfg)
    scores1 = _read_scores()
    s2 = scoring.run(cfg)
    scores2 = _read_scores()

    auc_a = s1["auc_static_A"]
    auc_b = s1["auc_early_B"]
    p10_a = s1["precision_at_10_static_A"]
    p10_b = s1["precision_at_10_early_B"]

    print(f"samples={s1['samples']} positives={s1['positives_total']} "
          f"train={s1['train_size']} test={s1['test_size']} "
          f"train_pos_rate={s1['train_pos_rate']}")
    print(f"组 A 维度={s1['group_a_dim']}  组 B 维度={s1['group_b_dim']}")
    print(f"AUC_A(静态)={auc_a}  AUC_B(早警)={auc_b}  "
          f"P@10_A={p10_a}  P@10_B={p10_b}")

    # 结论文本（H1）：先定，供组 A 诚实断言引用
    if auc_a is not None and auc_a < 0.62:
        conclusion_a = (f"结论(组A): {CONCLUSION_NO_SIGNAL}——AUC_A={auc_a} < 0.62。")
    else:
        conclusion_a = (f"结论(组A): {CONCLUSION_WEAK_SIGNAL}——AUC_A={auc_a} ≥ 0.62；"
                        "推断源于 as_of 快照对不同 etd 月份票的删失（早发票已交付脱离风险，"
                        "近发票仍在途更易被 R1 标记），属时点混杂而非延误注入本身的因果信号。")
    print(conclusion_a)

    print("== 组 B（早警）硬门（H1 唯一硬门）==")
    check(f"测试 AUC_B ≥ {gate}（实际 {auc_b}）", auc_b is not None and auc_b >= gate)
    check(f"测试 precision@10_B ≥ 0.5（实际 {p10_b}）", p10_b >= 0.5)

    print("== 组 A（静态）只验诚实（不设 AUC 门槛）==")
    if auc_a is not None and auc_a < 0.62:
        check("AUC_A<0.62 时结论含'无信号/接近随机'文本",
              (CONCLUSION_NO_SIGNAL in conclusion_a)
              and ("无信号" in conclusion_a or "随机" in conclusion_a),
              conclusion_a)
    else:
        check("AUC_A≥0.62 时如实打印'静态特征存在弱信号'并通过",
              CONCLUSION_WEAK_SIGNAL in conclusion_a, conclusion_a)

    print("== 可复现（连续两次 scoring 两列分数逐值一致）==")
    same = scores1 == scores2
    check("risk_score_static / risk_score_early 两次运行逐值一致", same,
          "分数不确定——违反 H2 确定性要求")

    con = sqlite3.connect(scoring.DB)
    con.row_factory = sqlite3.Row
    calibration_table(con)
    con.close()

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


def _read_scores():
    con = sqlite3.connect(scoring.DB)
    con.row_factory = sqlite3.Row
    rows = {r["shipment_id"]: (r["risk_score_static"], r["risk_score_early"])
            for r in con.execute("SELECT shipment_id, risk_score_static, risk_score_early "
                                 "FROM shipments WHERE risk_score_static IS NOT NULL")}
    con.close()
    return rows


if __name__ == "__main__":
    main()
