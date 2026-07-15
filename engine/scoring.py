"""v0.6 专题一 Y1：风险评分模型（H1 两段式 / H2 零新依赖手写逻辑回归）。

learn "ML 落地" 而非追指标（plan §专题一）：
  组 A 静态模型——仅用 etd 前可知特征，预测该票是否会发展出 R1 延误。
    本世界的延误随机注入，静态特征本无信号，预期 AUC≈0.5，学到"无信号"是正确结果。
  组 B 早警模型——用首个 eta_change 的幅度与时点等**严格早于风险判定**的早期事件特征，
    预测最终是否击穿。此处应有真实信号（AUC≥0.7）。

铁律（AGENTS §5 + H2）：零新依赖（禁 sklearn/numpy），无系统时间、无非确定随机。
本模块属分析/评分层（非重建层），plan 明确允许读 truth 作标签来源。
标签与特征只来自 data/ontology.sqlite + data/truth/expected_risk_events.csv。

为什么这样建（≤5 行）：
  1) 组 A 特征全部在 etd 前冻结，组 B 只加"首个 eta_change"这一严格早于 R1 判定的信号——
     R1 靠 eta_current 击穿 promise 判定，首个 eta 变更的幅度/时点是其早期弱先兆但不含判定本身。
  2) 手写逻辑回归权重初始化 0、无正则、全批量 GD——确定性可复现，无需第三方数值库。
  3) 切分按 shipment_id 的 sha256 排序取前 70%——纯确定性，禁 random，可任意模型接手复现。
"""
import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date

import yaml

DB = "data/ontology.sqlite"
TRUTH = "data/truth/expected_risk_events.csv"

# 静态类别特征（etd 前可知）；数值特征单独归一
CAT_FEATURES_A = ["origin_locode", "destination_locode", "carrier_scac",
                  "container_type", "mode", "incoterm", "etd_month"]
NUM_FEATURES_A = ["lead_time_days"]


# ---------- 数据准备 ----------

def load_labels():
    """标签 y：该 shipment 是否在 gt 中有 R1 行。"""
    pos = set()
    with open(TRUTH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["rule_id"] == "R1":
                pos.add(r["shipment_id"])
    return pos


def build_samples(con):
    """全部 status != planned 的 shipment，抽取组 A 静态特征 + 组 B 早警特征原始值。"""
    sup_lead = {r["supplier_id"]: r["lead_time_days"]
                for r in con.execute("SELECT supplier_id, lead_time_days FROM suppliers")}
    po_sup = {r["po_id"]: r["supplier_id"]
              for r in con.execute("SELECT po_id, supplier_id FROM purchase_orders")}

    # 首个 eta_change（按 event_time）+ eta_change 次数 + 是否 transshipment（判重后）
    first_eta = {}          # sid -> (event_time, new_eta)
    eta_count = defaultdict(int)
    has_transship = set()
    for m in con.execute("""SELECT shipment_id, event_type, event_time, new_eta
                            FROM shipment_milestones WHERE is_duplicate = 0
                            ORDER BY shipment_id, event_time, milestone_id"""):
        sid = m["shipment_id"]
        if m["event_type"] == "eta_change" and m["new_eta"]:
            eta_count[sid] += 1
            if sid not in first_eta:
                first_eta[sid] = (m["event_time"], m["new_eta"])
        elif m["event_type"] == "transshipment":
            has_transship.add(sid)

    samples = []
    for sp in con.execute("SELECT * FROM shipments WHERE status != 'planned' "
                          "ORDER BY shipment_id"):
        sid = sp["shipment_id"]
        # 供应商 lead_time：po_ids 首个 PO 反查 supplier → lead_time_days（无则 0）
        pos = [p for p in (sp["po_ids"] or "").split("|") if p]
        lead = sup_lead.get(po_sup.get(pos[0])) if pos else None
        lead = lead if lead is not None else 0

        # 组 A 静态特征（etd 前可知）
        feat_a = {
            "origin_locode": sp["origin_port_locode"] or "NA",
            "destination_locode": sp["destination_port_locode"] or "NA",
            "carrier_scac": sp["carrier_scac"] or "NA",
            "container_type": sp["container_type"] or "NA",
            "mode": sp["mode"] or "NA",
            "incoterm": sp["incoterm"] or "NA",
            "etd_month": sp["etd"][5:7] if sp["etd"] else "NA",
            "lead_time_days": float(lead),
        }

        # 组 B 早警特征（严格早于风险判定：首个 eta_change 的幅度/时点/次数 + 中转）
        # 无任何 eta_change 的票这些特征为 0
        if sid in first_eta:
            ev_time, new_eta = first_eta[sid]
            first_delay = (date.fromisoformat(new_eta)
                           - date.fromisoformat(sp["eta_initial"])).days
            days_from_etd = (date.fromisoformat(ev_time[:10])
                             - date.fromisoformat(sp["etd"])).days
        else:
            first_delay = 0
            days_from_etd = 0
        feat_b_num = {
            "first_eta_delay_days": float(first_delay),
            "first_eta_days_from_etd": float(days_from_etd),
            "eta_change_count": float(eta_count.get(sid, 0)),
        }
        feat_b_cat = {"has_transshipment": "1" if sid in has_transship else "0"}

        samples.append({"shipment_id": sid, "feat_a": feat_a,
                        "feat_b_num": feat_b_num, "feat_b_cat": feat_b_cat})
    return samples


# ---------- 特征编码（手写 one-hot + min-max）----------

def _cat_levels(samples, getters):
    """收集每个类别特征的取值集合（排序保证确定性）。"""
    levels = {}
    for name, get in getters.items():
        levels[name] = sorted({get(s) for s in samples})
    return levels


def _num_bounds(samples, getters):
    bounds = {}
    for name, get in getters.items():
        vals = [get(s) for s in samples]
        bounds[name] = (min(vals), max(vals))
    return bounds


def encode_group_a(samples):
    """组 A：类别 one-hot + 数值 min-max。返回 (X, feat_names)。"""
    cat_get = {n: (lambda s, n=n: s["feat_a"][n]) for n in CAT_FEATURES_A}
    num_get = {n: (lambda s, n=n: s["feat_a"][n]) for n in NUM_FEATURES_A}
    levels = _cat_levels(samples, cat_get)
    bounds = _num_bounds(samples, num_get)
    names, X = [], []
    for n in CAT_FEATURES_A:
        names += [f"{n}={lv}" for lv in levels[n]]
    names += list(NUM_FEATURES_A)
    for s in samples:
        row = []
        for n in CAT_FEATURES_A:
            v = s["feat_a"][n]
            row += [1.0 if v == lv else 0.0 for lv in levels[n]]
        for n in NUM_FEATURES_A:
            lo, hi = bounds[n]
            row.append((s["feat_a"][n] - lo) / (hi - lo) if hi > lo else 0.0)
        X.append(row)
    return X, names


def encode_group_b(samples):
    """组 B：组 A 全部特征 + 早警数值（min-max）+ 中转 one-hot。"""
    Xa, names_a = encode_group_a(samples)
    num_names = ["first_eta_delay_days", "first_eta_days_from_etd", "eta_change_count"]
    num_get = {n: (lambda s, n=n: s["feat_b_num"][n]) for n in num_names}
    bounds = _num_bounds(samples, num_get)
    cat_get = {"has_transshipment": (lambda s: s["feat_b_cat"]["has_transshipment"])}
    levels = _cat_levels(samples, cat_get)
    names = list(names_a) + list(num_names)
    for lv in levels["has_transshipment"]:
        names.append(f"has_transshipment={lv}")
    X = []
    for i, s in enumerate(samples):
        row = list(Xa[i])
        for n in num_names:
            lo, hi = bounds[n]
            row.append((s["feat_b_num"][n] - lo) / (hi - lo) if hi > lo else 0.0)
        v = s["feat_b_cat"]["has_transshipment"]
        row += [1.0 if v == lv else 0.0 for lv in levels["has_transshipment"]]
        X.append(row)
    return X, names


# ---------- 确定性切分 ----------

def split_indices(samples, ratio):
    """按 shipment_id 的 sha256 十六进制值排序，取前 ratio 为训练集（禁 random）。"""
    keyed = sorted(range(len(samples)),
                   key=lambda i: hashlib.sha256(
                       samples[i]["shipment_id"].encode()).hexdigest())
    n_train = int(len(samples) * ratio)
    train = set(keyed[:n_train])
    return train


# ---------- 手写逻辑回归（sigmoid + 交叉熵 + 全批量 GD，权重初始化 0）----------

def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def train_logreg(X, y, lr, epochs):
    """返回权重 w（含偏置在末位）。X 每行不含偏置，内部追加常数 1。"""
    n = len(X)
    d = len(X[0]) if n else 0
    w = [0.0] * (d + 1)             # 权重初始化 0，末位为偏置
    for _ in range(epochs):
        grad = [0.0] * (d + 1)
        for i in range(n):
            z = w[d]
            xi = X[i]
            for j in range(d):
                z += w[j] * xi[j]
            err = _sigmoid(z) - y[i]
            for j in range(d):
                grad[j] += err * xi[j]
            grad[d] += err
        for j in range(d + 1):
            w[j] -= lr * grad[j] / n
    return w


def predict_proba(X, w):
    d = len(w) - 1
    out = []
    for xi in X:
        z = w[d]
        for j in range(d):
            z += w[j] * xi[j]
        out.append(_sigmoid(z))
    return out


# ---------- 手写指标 ----------

def auc(scores, labels):
    """AUC = 正负样本对中正样本得分更高的比例（并列计 0.5）。"""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def precision_at_k(scores, labels, k=10):
    order = sorted(range(len(scores)),
                   key=lambda i: (-scores[i], labels[i]))  # 得分降序，并列时负例优先（保守）
    top = order[:k]
    return sum(labels[i] for i in top) / len(top) if top else 0.0


# ---------- 评分写回 ----------

def ensure_columns(con):
    cols = {c[1] for c in con.execute("PRAGMA table_info(shipments)")}
    if "risk_score_static" not in cols:
        con.execute("ALTER TABLE shipments ADD COLUMN risk_score_static REAL")
    if "risk_score_early" not in cols:
        con.execute("ALTER TABLE shipments ADD COLUMN risk_score_early REAL")


def write_scores(con, samples, proba_a, proba_b):
    for s, pa, pb in zip(samples, proba_a, proba_b):
        con.execute("UPDATE shipments SET risk_score_static=?, risk_score_early=? "
                    "WHERE shipment_id=?", (round(pa, 6), round(pb, 6), s["shipment_id"]))


# ---------- 主流程 ----------

def run(cfg):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    sc = cfg["scoring"]

    pos = load_labels()
    samples = build_samples(con)
    y = [1 if s["shipment_id"] in pos else 0 for s in samples]

    Xa, names_a = encode_group_a(samples)
    Xb, names_b = encode_group_b(samples)

    train = split_indices(samples, sc["split_ratio"])
    tr = [i for i in range(len(samples)) if i in train]
    te = [i for i in range(len(samples)) if i not in train]

    def subset(X, idx):
        return [X[i] for i in idx]

    y_tr = [y[i] for i in tr]
    wa = train_logreg(subset(Xa, tr), y_tr, sc["lr"], sc["epochs"])
    wb = train_logreg(subset(Xb, tr), y_tr, sc["lr"], sc["epochs"])

    # 测试集概率与指标
    pa_te = predict_proba(subset(Xa, te), wa)
    pb_te = predict_proba(subset(Xb, te), wb)
    y_te = [y[i] for i in te]
    auc_a = auc(pa_te, y_te)
    auc_b = auc(pb_te, y_te)
    p10_a = precision_at_k(pa_te, y_te, 10)
    p10_b = precision_at_k(pb_te, y_te, 10)

    # 全量样本写回（含训练集，用途仅展示）
    ensure_columns(con)
    proba_a_all = predict_proba(Xa, wa)
    proba_b_all = predict_proba(Xb, wb)
    write_scores(con, samples, proba_a_all, proba_b_all)
    con.commit()
    con.close()

    train_pos_rate = sum(y_tr) / len(y_tr) if y_tr else 0.0
    return {
        "samples": len(samples),
        "positives_total": sum(y),
        "group_a_dim": len(names_a),
        "group_b_dim": len(names_b),
        "train_size": len(tr),
        "test_size": len(te),
        "train_pos_rate": round(train_pos_rate, 4),
        "auc_static_A": None if auc_a != auc_a else round(auc_a, 4),
        "auc_early_B": None if auc_b != auc_b else round(auc_b, 4),
        "precision_at_10_static_A": round(p10_a, 4),
        "precision_at_10_early_B": round(p10_b, 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/datagen.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    summary = run(cfg)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
