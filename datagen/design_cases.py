"""15 个确定性设计案例（plan §9 / demo-assertions DEMO-01）。

不靠随机：全部字段显式指定，占用 world.py 预留的槽位。
每个案例声明预期结果（expected），verify.py 据此硬校验。
"""
from datetime import date

D = date.fromisoformat


def _ms(sid, etype, day, hour=10, new_eta="", src="carrier_edi", ingested_at=None):
    m = {"shipment_id": sid, "event_type": etype,
         "event_time": f"{day}T{hour:02d}:00:00Z", "new_eta": new_eta, "source_system": src}
    if ingested_at:
        m["ingested_at"] = ingested_at
    return m


# 每案例: shipment / pos / sos(含行) / allocations / noise 指令 / 备注
CASES = [
    {
        "case_id": "DEMO-01",
        "note": "主演示：延误7天击穿A级客户承诺日6天→critical；同船C级行承诺日充裕不受影响",
        "shipment": {"shipment_id": "SHP-2026-0099", "origin_port": "yantian", "destination_port": "los_angeles",
                     "etd": "2026-08-01", "eta_initial": "2026-08-14", "eta_current": "2026-08-21",
                     "status": "in_transit", "po_ids": ["PO-2026-0101", "PO-2026-0102"]},
        "milestones": [
            _ms("SHP-2026-0099", "booking_confirmed", "2026-07-26"),
            _ms("SHP-2026-0099", "departed", "2026-08-01"),
            _ms("SHP-2026-0099", "eta_change", "2026-08-08", hour=9, new_eta="2026-08-21"),
        ],
        "pos": [{"po_id": "PO-2026-0101", "sku_id": "SKU-0001", "supplier_id": "SUP-0003"},
                {"po_id": "PO-2026-0102", "sku_id": "SKU-0002", "supplier_id": "SUP-0003"}],
        "sos": [
            {"so_id": "SO-2026-0188", "customer_id": "CUS-0007", "order_date": "2026-06-25",
             "lines": [{"n": 1, "sku_id": "SKU-0001", "qty": 500, "promise": "2026-08-20"}]},
            {"so_id": "SO-2026-0201", "customer_id": "CUS-0011", "order_date": "2026-06-28",
             "lines": [{"n": 1, "sku_id": "SKU-0002", "qty": 200, "promise": "2026-09-10", "alloc": False},
                       {"n": 2, "sku_id": "SKU-0002", "qty": 300, "promise": "2026-09-05"}]},
        ],
    },
    {
        "case_id": "DEMO-02",
        "note": "一票延误击穿 2 客户 3 行（A+B）→ critical",
        "shipment": {"shipment_id": "SHP-2026-0042", "etd": "2026-07-25", "eta_initial": "2026-08-10",
                     "eta_current": "2026-08-20", "status": "in_transit", "po_ids": ["PO-2026-0203"]},
        "milestones": [
            _ms("SHP-2026-0042", "booking_confirmed", "2026-07-19"),
            _ms("SHP-2026-0042", "departed", "2026-07-25"),
            _ms("SHP-2026-0042", "eta_change", "2026-08-03", new_eta="2026-08-20"),
            _ms("SHP-2026-0042", "transshipment", "2026-08-05"),
        ],
        "pos": [{"po_id": "PO-2026-0203", "sku_id": "SKU-0003"}],
        "sos": [
            {"so_id": "SO-2026-0210", "customer_id": "CUS-0002", "order_date": "2026-06-20",
             "lines": [{"n": 1, "sku_id": "SKU-0003", "qty": 400, "promise": "2026-08-18"}]},
            {"so_id": "SO-2026-0211", "customer_id": "CUS-0009", "order_date": "2026-06-21",
             "lines": [{"n": 1, "sku_id": "SKU-0003", "qty": 600, "promise": "2026-08-15"}]},
            {"so_id": "SO-2026-0212", "customer_id": "CUS-0009", "order_date": "2026-06-22",
             "lines": [{"n": 1, "sku_id": "SKU-0003", "qty": 350, "promise": "2026-08-14"}]},
        ],
    },
    {
        "case_id": "DEMO-03",
        "note": "C 级客户小幅击穿 2 天 → medium",
        "shipment": {"shipment_id": "SHP-2026-0055", "etd": "2026-07-20", "eta_initial": "2026-08-12",
                     "eta_current": "2026-08-16", "status": "in_transit", "po_ids": ["PO-2026-0204"]},
        "milestones": [
            _ms("SHP-2026-0055", "booking_confirmed", "2026-07-14"),
            _ms("SHP-2026-0055", "departed", "2026-07-20"),
            _ms("SHP-2026-0055", "eta_change", "2026-08-01", new_eta="2026-08-16"),
            _ms("SHP-2026-0055", "transshipment", "2026-08-06"),
        ],
        "pos": [{"po_id": "PO-2026-0204", "sku_id": "SKU-0004"}],
        "sos": [{"so_id": "SO-2026-0213", "customer_id": "CUS-0013", "order_date": "2026-06-18",
                 "lines": [{"n": 1, "sku_id": "SKU-0004", "qty": 800, "promise": "2026-08-19"}]}],
    },
    {
        "case_id": "DEMO-04",
        "note": "A 级客户小幅击穿 2 天 → medium 升 high（tier 升级规则）",
        "shipment": {"shipment_id": "SHP-2026-0060", "etd": "2026-07-22", "eta_initial": "2026-08-12",
                     "eta_current": "2026-08-15", "status": "in_transit", "po_ids": ["PO-2026-0205"]},
        "milestones": [
            _ms("SHP-2026-0060", "booking_confirmed", "2026-07-16"),
            _ms("SHP-2026-0060", "departed", "2026-07-22"),
            _ms("SHP-2026-0060", "eta_change", "2026-08-04", new_eta="2026-08-15"),
        ],
        "pos": [{"po_id": "PO-2026-0205", "sku_id": "SKU-0004"}],
        "sos": [{"so_id": "SO-2026-0214", "customer_id": "CUS-0001", "order_date": "2026-06-19",
                 "lines": [{"n": 1, "sku_id": "SKU-0004", "qty": 300, "promise": "2026-08-18"}]}],
    },
    {
        "case_id": "DEMO-05",
        "note": "文件缺失且 ETA 距 as_of 4 天 → R2 high（承诺日充裕，无 R1）",
        "shipment": {"shipment_id": "SHP-2026-0070", "etd": "2026-07-28", "eta_initial": "2026-08-12",
                     "eta_current": "2026-08-12", "status": "in_transit", "po_ids": ["PO-2026-0206"],
                     "missing_docs": ["commercial_invoice"]},
        "milestones": [
            _ms("SHP-2026-0070", "booking_confirmed", "2026-07-21"),
            _ms("SHP-2026-0070", "departed", "2026-07-28"),
            _ms("SHP-2026-0070", "transshipment", "2026-08-05"),
        ],
        "pos": [{"po_id": "PO-2026-0206", "sku_id": "SKU-0007"}],
        "sos": [{"so_id": "SO-2026-0215", "customer_id": "CUS-0011", "order_date": "2026-06-25",
                 "lines": [{"n": 1, "sku_id": "SKU-0007", "qty": 900, "promise": "2026-09-15"}]}],
    },
    {
        "case_id": "DEMO-06",
        "note": "反例：文件缺失但 ETA 还远（20 天）→ 无风险",
        "shipment": {"shipment_id": "SHP-2026-0075", "etd": "2026-08-12", "eta_initial": "2026-08-28",
                     "eta_current": "2026-08-28", "status": "planned", "po_ids": ["PO-2026-0207"],
                     "missing_docs": ["isf"]},
        "milestones": [_ms("SHP-2026-0075", "booking_confirmed", "2026-08-04")],
        "pos": [{"po_id": "PO-2026-0207", "sku_id": "SKU-0007"}],
        "sos": [{"so_id": "SO-2026-0216", "customer_id": "CUS-0013", "order_date": "2026-07-01",
                 "lines": [{"n": 1, "sku_id": "SKU-0007", "qty": 400, "promise": "2026-09-20"}]}],
    },
    {
        "case_id": "DEMO-07",
        "note": "在途静默 8 天 → R3 medium（承诺日充裕，无 R1）",
        "shipment": {"shipment_id": "SHP-2026-0080", "etd": "2026-07-31", "eta_initial": "2026-08-16",
                     "eta_current": "2026-08-16", "status": "in_transit", "po_ids": ["PO-2026-0208"]},
        "milestones": [
            _ms("SHP-2026-0080", "booking_confirmed", "2026-07-25"),
            _ms("SHP-2026-0080", "departed", "2026-07-31"),
        ],
        "pos": [{"po_id": "PO-2026-0208", "sku_id": "SKU-0008"}],
        "sos": [{"so_id": "SO-2026-0217", "customer_id": "CUS-0011", "order_date": "2026-06-27",
                 "lines": [{"n": 1, "sku_id": "SKU-0008", "qty": 700, "promise": "2026-09-10"}]}],
    },
    {
        "case_id": "DEMO-08",
        "note": "反例：已到港后静默 6 天，但状态非 in_transit → 无 R3",
        "shipment": {"shipment_id": "SHP-2026-0085", "etd": "2026-07-18", "eta_initial": "2026-08-02",
                     "eta_current": "2026-08-02", "status": "arrived", "ata": "2026-08-02",
                     "po_ids": ["PO-2026-0209"]},
        "milestones": [
            _ms("SHP-2026-0085", "booking_confirmed", "2026-07-12"),
            _ms("SHP-2026-0085", "departed", "2026-07-18"),
            _ms("SHP-2026-0085", "arrived", "2026-08-02"),
        ],
        "pos": [{"po_id": "PO-2026-0209", "sku_id": "SKU-0008"}],
        "sos": [{"so_id": "SO-2026-0218", "customer_id": "CUS-0013", "order_date": "2026-06-15",
                 "lines": [{"n": 1, "sku_id": "SKU-0008", "qty": 250, "promise": "2026-09-01"}]}],
    },
    {
        "case_id": "DEMO-09",
        "note": "噪声：eta_change 完全重复上报 → 只允许 1 个 R1 high",
        "shipment": {"shipment_id": "SHP-2026-0090", "etd": "2026-07-28", "eta_initial": "2026-08-10",
                     "eta_current": "2026-08-18", "status": "in_transit", "po_ids": ["PO-2026-0210"]},
        "milestones": [
            _ms("SHP-2026-0090", "booking_confirmed", "2026-07-22"),
            _ms("SHP-2026-0090", "departed", "2026-07-28"),
            _ms("SHP-2026-0090", "eta_change", "2026-08-05", new_eta="2026-08-18"),
        ],
        "pos": [{"po_id": "PO-2026-0210", "sku_id": "SKU-0009"}],
        "sos": [{"so_id": "SO-2026-0219", "customer_id": "CUS-0013", "order_date": "2026-06-24",
                 "lines": [{"n": 1, "sku_id": "SKU-0009", "qty": 550, "promise": "2026-08-16"}]}],
        "noise": [{"type": "milestone_duplicate", "match": {"shipment_id": "SHP-2026-0090", "event_type": "eta_change"}}],
    },
    {
        "case_id": "DEMO-10",
        "note": "噪声：乱序到达（旧 ETA 后到）→ 以 event_time 最新为准，无风险",
        "shipment": {"shipment_id": "SHP-2026-0095", "etd": "2026-07-18", "eta_initial": "2026-08-14",
                     "eta_current": "2026-08-15", "status": "in_transit", "po_ids": ["PO-2026-0211"]},
        "milestones": [
            _ms("SHP-2026-0095", "booking_confirmed", "2026-07-12"),
            _ms("SHP-2026-0095", "departed", "2026-07-18"),
            _ms("SHP-2026-0095", "eta_change", "2026-07-20", new_eta="2026-08-23",
                ingested_at="2026-07-29T10:00:00Z"),  # 旧事件晚到
            _ms("SHP-2026-0095", "eta_change", "2026-07-28", new_eta="2026-08-15",
                ingested_at="2026-07-28T18:00:00Z"),
            _ms("SHP-2026-0095", "transshipment", "2026-08-04"),
        ],
        "pos": [{"po_id": "PO-2026-0211", "sku_id": "SKU-0009"}],
        "sos": [{"so_id": "SO-2026-0220", "customer_id": "CUS-0011", "order_date": "2026-06-20",
                 "lines": [{"n": 1, "sku_id": "SKU-0009", "qty": 300, "promise": "2026-08-22"}]}],
        "noise": [{"type": "milestone_out_of_order", "match": {"shipment_id": "SHP-2026-0095", "event_time": "2026-07-20T10:00:00Z"}}],
    },
    {
        "case_id": "DEMO-11",
        "note": "噪声陷阱：TMS 状态字段说 in_transit，事件流显示已到港 → 以事件流为准，无 R3",
        "shipment": {"shipment_id": "SHP-2026-0100", "etd": "2026-07-12", "eta_initial": "2026-07-30",
                     "eta_current": "2026-07-30", "status": "arrived", "ata": "2026-07-30",
                     "po_ids": ["PO-2026-0212"]},
        "milestones": [
            _ms("SHP-2026-0100", "booking_confirmed", "2026-07-06"),
            _ms("SHP-2026-0100", "departed", "2026-07-12"),
            _ms("SHP-2026-0100", "arrived", "2026-07-30"),
        ],
        "pos": [{"po_id": "PO-2026-0212", "sku_id": "SKU-0010"}],
        "sos": [{"so_id": "SO-2026-0221", "customer_id": "CUS-0013", "order_date": "2026-06-10",
                 "lines": [{"n": 1, "sku_id": "SKU-0010", "qty": 450, "promise": "2026-09-05"}]}],
        "noise": [{"type": "status_conflict", "emit_status": "in_transit"}],
    },
    {
        "case_id": "DEMO-12",
        "note": "反例：延误后恢复（两次 eta_change 拉回）→ 无风险",
        "shipment": {"shipment_id": "SHP-2026-0105", "etd": "2026-07-15", "eta_initial": "2026-08-12",
                     "eta_current": "2026-08-13", "status": "in_transit", "po_ids": ["PO-2026-0213"]},
        "milestones": [
            _ms("SHP-2026-0105", "booking_confirmed", "2026-07-09"),
            _ms("SHP-2026-0105", "departed", "2026-07-15"),
            _ms("SHP-2026-0105", "eta_change", "2026-07-20", new_eta="2026-08-19"),
            _ms("SHP-2026-0105", "eta_change", "2026-07-25", new_eta="2026-08-13"),
            _ms("SHP-2026-0105", "transshipment", "2026-08-06"),
        ],
        "pos": [{"po_id": "PO-2026-0213", "sku_id": "SKU-0010"}],
        "sos": [{"so_id": "SO-2026-0222", "customer_id": "CUS-0011", "order_date": "2026-06-12",
                 "lines": [{"n": 1, "sku_id": "SKU-0010", "qty": 600, "promise": "2026-08-20"}]}],
    },
    {
        "case_id": "DEMO-13",
        "note": "噪声：供应商名不一致（SUP-0006 变体）不得打断影响链 → R1 high 照常",
        "shipment": {"shipment_id": "SHP-2026-0110", "etd": "2026-07-26", "eta_initial": "2026-08-11",
                     "eta_current": "2026-08-19", "status": "in_transit", "po_ids": ["PO-2026-0201"]},
        "milestones": [
            _ms("SHP-2026-0110", "booking_confirmed", "2026-07-20"),
            _ms("SHP-2026-0110", "departed", "2026-07-26"),
            _ms("SHP-2026-0110", "eta_change", "2026-08-02", new_eta="2026-08-19"),
            _ms("SHP-2026-0110", "transshipment", "2026-08-05"),
        ],
        "pos": [{"po_id": "PO-2026-0201", "sku_id": "SKU-0011", "supplier_id": "SUP-0006"}],
        "sos": [{"so_id": "SO-2026-0223", "customer_id": "CUS-0005", "order_date": "2026-06-22",
                 "lines": [{"n": 1, "sku_id": "SKU-0011", "qty": 500, "promise": "2026-08-20"}]}],
        "noise": [{"type": "supplier_name_variant", "supplier_id": "SUP-0006"}],
    },
    {
        "case_id": "DEMO-14",
        "note": "噪声：vessel/carrier 空值 + 延误 6 天击穿 → 检测不受空值影响，R1 high",
        "shipment": {"shipment_id": "SHP-2026-0115", "etd": "2026-07-27", "eta_initial": "2026-08-12",
                     "eta_current": "2026-08-18", "status": "in_transit", "po_ids": ["PO-2026-0214"],
                     "vessel_voyage": None, "carrier_name": None},
        "milestones": [
            _ms("SHP-2026-0115", "booking_confirmed", "2026-07-21"),
            _ms("SHP-2026-0115", "departed", "2026-07-27"),
            _ms("SHP-2026-0115", "eta_change", "2026-08-03", new_eta="2026-08-18"),
            _ms("SHP-2026-0115", "transshipment", "2026-08-06"),
        ],
        "pos": [{"po_id": "PO-2026-0214", "sku_id": "SKU-0012"}],
        "sos": [{"so_id": "SO-2026-0224", "customer_id": "CUS-0013", "order_date": "2026-06-23",
                 "lines": [{"n": 1, "sku_id": "SKU-0012", "qty": 350, "promise": "2026-08-17"}]}],
        "noise": [{"type": "null_vessel_carrier"}],
    },
    {
        "case_id": "DEMO-15",
        "note": "多 PO 多 SKU 同船：延误只击穿 SKU-0005 行，SKU-0006 行不受影响 → affected 精确到行",
        "shipment": {"shipment_id": "SHP-2026-0118", "etd": "2026-07-29", "eta_initial": "2026-08-13",
                     "eta_current": "2026-08-20", "status": "in_transit",
                     "po_ids": ["PO-2026-0230", "PO-2026-0231"]},
        "milestones": [
            _ms("SHP-2026-0118", "booking_confirmed", "2026-07-23"),
            _ms("SHP-2026-0118", "departed", "2026-07-29"),
            _ms("SHP-2026-0118", "eta_change", "2026-08-04", new_eta="2026-08-20"),
        ],
        "pos": [{"po_id": "PO-2026-0230", "sku_id": "SKU-0005"},
                {"po_id": "PO-2026-0231", "sku_id": "SKU-0006"}],
        "sos": [
            {"so_id": "SO-2026-0225", "customer_id": "CUS-0005", "order_date": "2026-06-26",
             "lines": [{"n": 1, "sku_id": "SKU-0005", "qty": 420, "promise": "2026-08-19"}]},
            {"so_id": "SO-2026-0226", "customer_id": "CUS-0011", "order_date": "2026-06-26",
             "lines": [{"n": 1, "sku_id": "SKU-0006", "qty": 380, "promise": "2026-09-10"}]},
        ],
    },
]

SHIP_DEFAULTS = {"mode": "ocean_fcl", "origin_port": "yantian", "destination_port": "los_angeles",
                 "destination_warehouse": "LAX-DC1", "ata": None, "customs_status": "not_filed",
                 "missing_docs": [], "expedite_flag": False,
                 "vessel_voyage": "MV Pacific/101E", "carrier_name": "COSCO"}


def apply_design_cases(world, rng):
    """把 15 个设计案例写入世界（占用预留槽位）。返回 design_noise 指令列表。"""
    design_noise = []
    world["design_ship_case"] = {}
    alloc_seq = 900000
    for case in CASES:
        cid = case["case_id"]
        # shipment
        sp = dict(SHIP_DEFAULTS)
        sp.update(case["shipment"])
        if sp.get("container_no") is None or "container_no" not in sp:
            sp["container_no"] = f"CONT{9000000 + int(sp['shipment_id'][-4:])}"
        for f in ("etd", "eta_initial", "eta_current"):
            sp[f] = D(sp[f])
        if sp.get("ata"):
            sp["ata"] = D(sp["ata"])
        world["shipments"][sp["shipment_id"]] = sp
        world["design_ship_case"][sp["shipment_id"]] = cid
        world["milestones"].extend(case["milestones"])
        # POs（补齐通用字段；供应商默认取 SKU 目录归属）
        for po in case["pos"]:
            sup = po.get("supplier_id") or world["skus"][po["sku_id"]]["supplier_id"]
            if po.get("supplier_id"):
                world["skus"][po["sku_id"]]["supplier_id"] = sup  # 保持目录一致
            world["pos"][po["po_id"]] = {
                "po_id": po["po_id"], "supplier_id": sup, "sku_id": po["sku_id"],
                "qty": 5000, "po_date": sp["etd"].replace(day=1),
                "expected_ready_date": sp["etd"], "status": "shipped"}
        # SOs / 行 / 分配
        for so in case["sos"]:
            world["sos"][so["so_id"]] = {"so_id": so["so_id"], "customer_id": so["customer_id"],
                                         "order_date": D(so["order_date"])}
            so_num = so["so_id"].split("-")[-1]
            for ln in so["lines"]:
                lid = f"SOL-{so_num}-{ln['n']}"
                world["lines"][lid] = {"so_line_id": lid, "so_id": so["so_id"], "sku_id": ln["sku_id"],
                                       "qty": ln["qty"], "promised_delivery_date": D(ln["promise"]),
                                       "line_status": "open" if ln.get("alloc") is False else "allocated"}
                if ln.get("alloc") is False:
                    continue
                alloc_seq += 1
                world["allocations"].append({"allocation_id": f"ALC-{alloc_seq:06d}",
                                             "shipment_id": sp["shipment_id"], "so_line_id": lid,
                                             "allocated_qty": ln["qty"]})
        for nz in case.get("noise", []):
            design_noise.append({**nz, "case_id": cid, "shipment_id": nz.get("shipment_id", sp["shipment_id"])})
    return design_noise
