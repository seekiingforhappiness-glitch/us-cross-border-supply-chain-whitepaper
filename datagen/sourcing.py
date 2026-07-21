"""P3 采购富化2：RFQ 询价 + R14 单一来源 + R15 maverick（Build A：模型 + 数据 + ground truth）。

与 v0.3 admission / v0.4 cost / P1 procurement 同构的隔离设计：独立随机流
random.Random(seed + sourcing.seed_offset)，不消耗既有随机流——控制塔/准入/费用/采购/仓储数据
逐字节零扰动，可复现性从源头保证（§7 / X2 / V2 先例）。须在 procurement 之后运行（依赖其
po_lines + R7/R9 注入真值）。

建模选择（每条 ≤5 行"为什么这样建"）：
- approved 供应商 = {目录 incumbent(skus.supplier_id)} ∪ {对该 SKU 有 awarded Quote 的供应商}：
  awarded RFQ 把新供应商"认证"为该 SKU 的合规备源，天然承载 AVL（多对多）；单一来源 = 该并集只有 1 个。
  代价：目录里 sku→supplier 本是 1:N（单源），故成熟组织"应双源"的常态由 awarded 历史询价建立。
- R14 断供 = active SKU 单一来源 且该 incumbent 近 N 天有 R7/R9 事件：读既有 risk_events（禁读真值），
  与 detect 同判据（P/R=1.000 保证 R7/R9 检出集==注入集，故 datagen oracle 用 proc anomalies 等价）。
- R15 maverick = supplier_invoice.supplier ≠ 其 PO 的 supplier 且该 biller 非该 SKU 的 approved 备源：
  新增"绕流程"发票（引用真实 PO/po_line，价=行价/量=行量 → 不触 R10/R11，既有检出逐条不变）。
  既有 61 张合规发票 supplier==PO supplier → 永不触发（clean/FP 守卫）；approved 备源 biller → 豁免（灰区）。
- R14/R15 真值独立存 data/truth/expected_sourcing_risks.csv：R14 需 sku_id 锚点，不入既有
  expected_procurement_risks.csv schema → 既有 R7-R13 真值文件逐字节不变（守 §5 铁律）。

产出（写入 world["sourcing"]）：
  rfqs / rfq_lines / quotes                 询价对象（含 awarded 历史 + 在建）
  maverick_invoices / maverick_invoice_lines  R15 绕流程发票（并入 ap_supplier_invoices 输出）
  anomalies                                 R14/R15 ground truth（每键一行）
  roles                                     r14_targets/r14_gray/multi_source/... 供 verify 断言
"""
from collections import defaultdict
from datetime import timedelta

# R14 资质证严重度无关；R14/R15 severity 由敞口/发票额 vs config 阈值决定（gen 与 detect 同一映射）。


def _iso(d):
    return f"{d.isoformat()}T00:00:00Z"


def build_sourcing_world(world, cfg, rng):
    """生成 RFQ/Quote + R14/R15 数据与真值。rng 必须是 random.Random(seed + sourcing.seed_offset)。"""
    sc = cfg["sourcing"]
    inj = sc["inject"]
    as_of = world["as_of"]
    single_high = sc["single_source_high_usd"]
    maverick_high = sc["maverick_high_usd"]

    skus = world["skus"]
    pos = world["pos"]
    suppliers = world["suppliers"]
    proc = world["procurement"]
    po_lines = proc["po_lines"]
    proc_anomalies = proc["anomalies"]
    profile = proc["profile"]

    all_suppliers = sorted(suppliers)

    # 1) 断供供应商 = 有 R7/R9 注入真值的供应商（== detect 的 R7/R9 检出，因 P/R=1.000）。
    disrupted = {a["supplier_id"] for a in proc_anomalies if a["rule_id"] in ("R7", "R9")}

    # 2) active SKU（目录单一 incumbent）；po_lines 按 sku 聚合（R14 敞口值 + R15 引用来源）。
    active = sorted(k for k in skus if skus[k].get("sku_status") == "active")
    incumbent = {k: skus[k]["supplier_id"] for k in active}
    pols_by_sku = defaultdict(list)
    for l in po_lines:
        pols_by_sku[l["sku_id"]].append(l)

    def sku_spend(sku):
        return round(sum(l["qty"] * l["unit_price_usd"] for l in pols_by_sku.get(sku, [])), 2)

    # 3) 画像分区（确定性排序）：
    disrupted_active = [k for k in active if incumbent[k] in disrupted]
    nondisrupted_active = [k for k in active if incumbent[k] not in disrupted]
    disrupted_with_pol = [k for k in disrupted_active if k in pols_by_sku]

    r14_targets = disrupted_with_pol[:inj["r14"]]             # 单源 + 断供 → R14 正例
    must_multi = [k for k in disrupted_active if k not in r14_targets]  # 其余断供 SKU 必须多源化
    r14_gray = nondisrupted_active[:inj["r14_gray"]]          # 单源但健康 → 不触发（灰区）
    clean_multi = [k for k in nondisrupted_active if k not in r14_gray][:inj["clean_multi_source"]]
    multi_source_skus = must_multi + clean_multi             # 获 awarded 第二来源（多源）

    # 4) RFQ / RFQLine / Quote 生成 --------------------------------------------------
    rfqs, rfq_lines, quotes = [], [], []
    seqs = {"rfq": 0, "rfql": 0, "quo": 0}

    def nid(kind, fmt):
        seqs[kind] += 1
        return fmt.format(seqs[kind])

    awarded_alt = {}  # sku_id -> awarded 备源 supplier_id（该 SKU 的 approved 备源，R14/R15 共用）
    created_base = as_of - timedelta(days=60)

    def _second_source(sku, idx):
        cands = [s for s in all_suppliers if s != incumbent.get(sku)]
        return cands[idx % len(cands)]

    def _emit_rfq(sku, status, created):
        rid = nid("rfq", "RFQ-2026-{:04d}")
        rfqs.append({"rfq_id": rid, "sku_id": sku, "status": status,
                     "created_date": created.isoformat(), "as_of_date": as_of.isoformat()})
        # 单 SKU RFQ：一条 RFQLine 承载询价量
        qty = rng.randint(500, 5000)
        rfq_lines.append({"rfq_line_id": nid("rfql", "RFQL-{:05d}"), "rfq_id": rid,
                          "sku_id": sku, "qty": qty})
        return rid

    def _emit_quote(rid, sku, supplier_id, status):
        base = skus[sku]["unit_price_usd"]
        unit_price = round(base * rng.uniform(0.92, 1.08), 2)
        quotes.append({"quote_id": nid("quo", "QUO-{:05d}"), "rfq_id": rid,
                       "supplier_id": supplier_id, "unit_price_usd": unit_price,
                       "currency": "USD", "status": status, "as_of_date": as_of.isoformat()})

    # 4a) awarded 历史询价（建立第二来源 → 多源）
    for i, sku in enumerate(multi_source_skus):
        created = created_base - timedelta(days=rng.randint(0, 30))
        rid = _emit_rfq(sku, "awarded", created)
        alt = _second_source(sku, i)
        awarded_alt[sku] = alt
        # 邀请：incumbent（落选）+ 备源（中标）+ 可能第三方（落选）
        _emit_quote(rid, sku, incumbent[sku], "rejected")
        _emit_quote(rid, sku, alt, "awarded")
        third = next((s for s in all_suppliers if s not in (incumbent[sku], alt)), None)
        if third and rng.random() < 0.5:
            _emit_quote(rid, sku, third, "rejected")

    # 4b) 在建 RFQ（第二来源在途，未 awarded → 目标仍单源）
    inflight_states = ["draft", "sent", "quoting", "evaluating"]
    quote_states = ["invited", "submitted", "shortlisted"]
    for i, sku in enumerate(r14_targets[:inj["r14_inflight"]]):
        created = as_of - timedelta(days=rng.randint(3, 20))
        rid = _emit_rfq(sku, inflight_states[i % len(inflight_states)], created)
        alt = _second_source(sku, i + 1)
        _emit_quote(rid, sku, alt, quote_states[i % len(quote_states)])

    # 5) R15 绕流程发票（引用真实 clean-profile PO/po_line，价量匹配 → 不触 R10/R11）--------
    clean_pos = [pid for pid in sorted(profile) if profile[pid] == "clean"]
    pol_of_po = defaultdict(list)
    for l in po_lines:
        pol_of_po[l["po_id"]].append(l)

    def _first_line(pid):
        ls = sorted(pol_of_po.get(pid, []), key=lambda x: x["po_line_id"])
        return ls[0] if ls else None

    maverick_invoices, maverick_invoice_lines = [], []
    mseq = {"inv": 0, "il": 0}
    r15_pos_ids, r15_gray_ids = [], []

    def _emit_maverick(pid, biller, is_gray):
        line = _first_line(pid)
        if line is None:
            return None
        mseq["inv"] += 1
        inv_id = "SINV-MVK-{:04d}".format(mseq["inv"])
        issue = as_of - timedelta(days=rng.randint(2, 15))
        qty = line["qty"]
        unit = line["unit_price_usd"]          # 价=PO 行价 → 不触 R10；量=PO 行量、行足量收货 → 不触 R11
        amount = round(qty * unit, 2)
        mseq["il"] += 1
        maverick_invoice_lines.append({
            "supplier_invoice_line_id": "SIL-MVK-{:05d}".format(mseq["il"]),
            "supplier_invoice_id": inv_id, "po_line_id": line["po_line_id"], "qty": qty,
            "unit_price_usd": unit, "amount_usd": amount,
            "as_of_date": as_of.isoformat(), "created_at": _iso(issue)})
        maverick_invoices.append({
            "supplier_invoice_id": inv_id, "supplier_id": biller, "po_id": pid,
            "vendor_invoice_no": f"MVK{rng.randint(100000, 999999)}",
            "issue_date": issue.isoformat(), "currency": "USD", "total_usd": amount,
            "status": "received", "as_of_date": as_of.isoformat(), "created_at": _iso(issue)})
        return inv_id, biller, pid, amount, line

    used_pos = set()
    # 5a) 灰区：biller = 该 SKU 的 approved 备源（awarded）→ 豁免。取 sku 已多源化的 clean PO。
    for pid in clean_pos:
        if len(r15_gray_ids) >= inj["r15_gray"]:
            break
        line = _first_line(pid)
        if line is None:
            continue
        sku = line["sku_id"]
        if sku in awarded_alt and awarded_alt[sku] != pos[pid]["supplier_id"]:
            r = _emit_maverick(pid, awarded_alt[sku], is_gray=True)
            if r:
                r15_gray_ids.append(r[0])
                used_pos.add(pid)

    # 5b) 正例：biller ≠ PO supplier 且非该 SKU 的 approved 备源 → maverick。
    for pid in clean_pos:
        if len(r15_pos_ids) >= inj["r15"]:
            break
        if pid in used_pos:
            continue
        line = _first_line(pid)
        if line is None:
            continue
        sku = line["sku_id"]
        a = pos[pid]["supplier_id"]
        not_approved = [s for s in all_suppliers
                        if s != a and s != awarded_alt.get(sku)]
        if not not_approved:
            continue
        biller = not_approved[0]
        r = _emit_maverick(pid, biller, is_gray=False)
        if r:
            r15_pos_ids.append(r[0])
            used_pos.add(pid)

    # 6) ground truth（独立 oracle，与 detect 同判据）--------------------------------
    anomalies = []
    aseq = [0]

    def add_anomaly(rule_id, atype, sku_id, supplier_id, po_id, severity, value_usd, note):
        aseq[0] += 1
        anomalies.append({
            "expected_sourcing_risk_id": f"ESR-{aseq[0]:05d}", "rule_id": rule_id,
            "type": atype, "sku_id": sku_id, "supplier_id": supplier_id, "po_id": po_id,
            "severity": severity, "anomaly_value_usd": round(value_usd, 2), "note": note})

    # R14 oracle：active SKU 单一来源(仅 incumbent) 且 incumbent ∈ disrupted → 断供风险
    approved_of = {}
    for k in active:
        approved_of[k] = {incumbent[k]} | ({awarded_alt[k]} if k in awarded_alt else set())
    for k in active:
        if len(approved_of[k]) == 1 and incumbent[k] in disrupted:
            spend = sku_spend(k)
            sev = "high" if spend > single_high else "medium"
            add_anomaly("R14", "single_source", k, incumbent[k], "", sev, spend,
                        f"单一来源断供：SKU {k} 仅 1 个 approved 供应商 {incumbent[k]}，"
                        f"该供应商近 {sc['single_source_recent_days']} 天有 R7/R9 事件，"
                        f"PO 采购敞口 ${spend}")

    # R15 oracle：supplier_invoice.supplier ≠ PO supplier 且 biller 非该 SKU approved 备源 → maverick
    for inv in maverick_invoices:
        line = next(l for l in maverick_invoice_lines
                    if l["supplier_invoice_id"] == inv["supplier_invoice_id"])
        pol = next(l for l in po_lines if l["po_line_id"] == line["po_line_id"])
        sku = pol["sku_id"]
        po_supplier = pos[inv["po_id"]]["supplier_id"]
        biller = inv["supplier_id"]
        if biller == po_supplier:
            continue
        if biller == awarded_alt.get(sku):
            continue  # approved 备源 → 豁免（灰区）
        amt = inv["total_usd"]
        sev = "high" if amt > maverick_high else "medium"
        add_anomaly("R15", "maverick_spend", sku, biller, inv["po_id"], sev, amt,
                    f"绕流程采购：发票 {inv['supplier_invoice_id']} 由 {biller} 开出、"
                    f"引用 {inv['po_id']}（PO 供应商 {po_supplier}），biller 非该 SKU 的 approved "
                    f"供应商 → 无匹配 approved PO，绕流程金额 ${amt}")

    world["sourcing"] = {
        "rfqs": rfqs,
        "rfq_lines": rfq_lines,
        "quotes": quotes,
        "maverick_invoices": maverick_invoices,
        "maverick_invoice_lines": maverick_invoice_lines,
        "anomalies": anomalies,
        "roles": {
            "r14_targets": r14_targets, "r14_gray": r14_gray, "must_multi": must_multi,
            "clean_multi": clean_multi, "multi_source_skus": multi_source_skus,
            "awarded_alt": awarded_alt, "disrupted": sorted(disrupted),
            "r15_pos_invoices": r15_pos_ids, "r15_gray_invoices": r15_gray_ids,
        },
    }
    return world["sourcing"]
