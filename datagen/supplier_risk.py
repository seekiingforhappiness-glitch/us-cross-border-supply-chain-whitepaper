"""V23① 供应商风险感知 R22/R23 ground truth 构建（独立 oracle，纯派生）。

与 warehouse/procurement 真值构建的根本差异：R22/R23 是**派生规则**——不注入任何新异常、
不加新噪声、不消耗任何随机流（本函数无 rng 参数，物理上不可能扰动既有数据）。真值由本模块
从既有采购世界（world["pos"] 头 + world["procurement"] 的 goods_receipts/po_lines/
qualifications）按规则语义独立推导（R16 先例："独立 oracle 从现有数据推导"），落**新增文件**
data/truth/expected_supplier_risks.csv——既有 9 个 truth 文件逐字节不变（V23① 红线）。

oracle 语义（converge 到 engine/supplier_risk_rules.py，两侧独立实现互为验证）：
- R22 绩效劣化（supplier_id 锚）：PO 级交期达成率 = 驾驶舱供应商区同口径——PO 首张 GRN
  收货日 min(received_date≤as_of) ≤ PO 头 expected_ready_date 记达成，分母=有收货的 PO；
  样本 ≥ perf_min_pos 且 rate < perf_threshold_rate → 一供应商一行。
  severity：rate < perf_critical_rate → critical，否则 high。
  anomaly_value = 迟交 PO 的行金额合计（Σ po_line.qty × unit_price_usd）。
- R23 资质预警（qualification_id 锚）：逐证判定——过期（valid_to < as_of）= high、
  临期（as_of ≤ valid_to ≤ as_of + qual_warning_days）= medium；同 (supplier, cert_type)
  存在 valid_to 更晚且 ≥ as_of 的续证 → 已补新证，不报。

真值行 schema：expected_supplier_risk_id / rule_id / type / supplier_id / qualification_id /
cert_type / severity / anomaly_value_usd / note。排序 (rule_id, supplier_id, qualification_id)。
"""
from datetime import date, timedelta

# 与 engine/supplier_risk_rules.CERT_CN 平行定义（datagen 不 import engine 保持隔离，
# 同 procurement.CERT_SEVERITY 先例；业务口径单一源 = ontology R23 severity/logic）。
CERT_CN = {"factory_audit": "工厂审计", "uflpa_traceability": "UFLPA 溯源",
           "iso9001": "ISO9001 质量体系", "product_safety_cert": "产品安全认证"}


def build_supplier_risk_truth(world, cfg):
    """从既有世界纯派生 R22/R23 真值（无 rng——零随机流消耗，既有产物字节不变）。"""
    sc = cfg["supplier_risk"]
    thr = sc["perf_threshold_rate"]
    crit = sc["perf_critical_rate"]
    min_pos = sc["perf_min_pos"]
    window_days = sc["perf_window_days"]
    warn_days = sc["qual_warning_days"]
    as_of = world["as_of"]
    as_of_str = as_of.isoformat()

    pos = world["pos"]
    proc = world["procurement"]
    suppliers = world["suppliers"]

    def sup_name(sid):
        return suppliers.get(sid, {}).get("supplier_name", sid)

    anomalies = []
    seq = [0]

    def add(rule_id, atype, supplier_id, qualification_id, cert_type, severity, value, note):
        seq[0] += 1
        anomalies.append({
            "expected_supplier_risk_id": f"ESR-{seq[0]:05d}",
            "rule_id": rule_id, "type": atype, "supplier_id": supplier_id,
            "qualification_id": qualification_id, "cert_type": cert_type,
            "severity": severity, "anomaly_value_usd": round(value, 2), "note": note})

    # === R22 oracle：PO 首收货日（≤as_of） vs PO 头 expected_ready_date ===
    first_recv = {}
    for g in proc["goods_receipts"]:
        if g["received_date"] > as_of_str:      # D8：未来收货不可见
            continue
        cur = first_recv.get(g["po_id"])
        if cur is None or g["received_date"] < cur:
            first_recv[g["po_id"]] = g["received_date"]
    if window_days and window_days > 0:
        window_start = (as_of - timedelta(days=window_days)).isoformat()
        first_recv = {p: d for p, d in first_recv.items() if d >= window_start}

    po_value = {}
    for l in proc["po_lines"]:
        po_value[l["po_id"]] = po_value.get(l["po_id"], 0.0) + l["qty"] * l["unit_price_usd"]

    perf = {}   # supplier_id -> {"measured", "on_time", "late": [(po_id, value)]}
    for pid in sorted(first_recv):
        sid = pos[pid]["supplier_id"]
        expected_ready = pos[pid]["expected_ready_date"].isoformat()
        p = perf.setdefault(sid, {"measured": 0, "on_time": 0, "late": []})
        p["measured"] += 1
        if first_recv[pid] <= expected_ready:
            p["on_time"] += 1
        else:
            p["late"].append((pid, po_value.get(pid, 0.0)))

    for sid in sorted(perf):
        p = perf[sid]
        if p["measured"] < min_pos:
            continue
        rate = p["on_time"] / p["measured"]
        if rate >= thr:
            continue
        sev = "critical" if rate < crit else "high"
        late_ids = sorted(pid for pid, _ in p["late"])
        value = sum(v for _, v in p["late"])
        add("R22", "performance_degradation", sid, "", "", sev, value,
            f"供应商绩效劣化：{sup_name(sid)} 交期达成率 {round(rate * 100, 1)}%"
            f"（{p['on_time']}/{p['measured']} 单准时）< 阈值 {round(thr * 100)}%；"
            f"迟交 {len(late_ids)} 单（{'、'.join(late_ids)}），涉及货值 ${round(value, 2)}")

    # === R23 oracle：逐证过期/临期且未补新证 ===
    warn_hi = (as_of + timedelta(days=warn_days)).isoformat()
    quals = sorted(proc["qualifications"], key=lambda q: q["qualification_id"])
    by_sup_cert = {}
    for q in quals:
        by_sup_cert.setdefault((q["supplier_id"], q["cert_type"]), []).append(q)

    def superseded(q):
        return any(x["qualification_id"] != q["qualification_id"]
                   and x["valid_to"] > q["valid_to"] and x["valid_to"] >= as_of_str
                   for x in by_sup_cert[(q["supplier_id"], q["cert_type"])])

    for q in quals:
        cert_cn = CERT_CN.get(q["cert_type"], q["cert_type"])
        if q["valid_to"] < as_of_str and not superseded(q):
            days_over = (date.fromisoformat(as_of_str)
                         - date.fromisoformat(q["valid_to"])).days
            add("R23", "qualification_expiry_warning", q["supplier_id"],
                q["qualification_id"], q["cert_type"], "high", 0.0,
                f"资质过期：{sup_name(q['supplier_id'])} 的 {cert_cn}"
                f"（{q['qualification_id']}）已于 {q['valid_to']} 过期 {days_over} 天，"
                f"未见同类续证")
        elif as_of_str <= q["valid_to"] <= warn_hi and not superseded(q):
            days_left = (date.fromisoformat(q["valid_to"])
                         - date.fromisoformat(as_of_str)).days
            add("R23", "qualification_expiry_warning", q["supplier_id"],
                q["qualification_id"], q["cert_type"], "medium", 0.0,
                f"资质临期：{sup_name(q['supplier_id'])} 的 {cert_cn}"
                f"（{q['qualification_id']}）将于 {q['valid_to']} 到期"
                f"（剩 {days_left} 天，预警窗 {warn_days} 天），未见同类续证")

    # 稳定排序 + 序号重排（§5 铁律同款：排序后 id 连续可复现）
    anomalies.sort(key=lambda a: (a["rule_id"], a["supplier_id"], a["qualification_id"]))
    for i, a in enumerate(anomalies, 1):
        a["expected_supplier_risk_id"] = f"ESR-{i:05d}"

    world["supplier_risk"] = {"anomalies": anomalies}
    return world["supplier_risk"]
