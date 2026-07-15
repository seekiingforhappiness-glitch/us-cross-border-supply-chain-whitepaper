"""供应商名称 entity resolution（W3，限时实现：规范化 + 令牌匹配，不上模糊算法库）。

输入：TMS 侧出现的供应商名（含变体） + SRM 规范名
输出：raw_name → supplier_id 映射（None=未匹配），供 DQ 报告与 supplier_name_map 表。
M4 的 simulated MDM crosswalk resolver 独立放在 pipeline.mdm；本模块继续只负责既有 ER。
"""

ABBREV = {"sz": "shenzhen", "nb": "ningbo", "dg": "dongguan"}
STOP = {"co", "ltd", "inc", "elec", "electronic", "electronics", "(hk)", ""}


def normalize(name):
    s = name.lower().replace(",", " ").replace(".", " ")
    toks = [ABBREV.get(t, t) for t in s.split()]
    return " ".join(t for t in toks if t not in STOP)


def resolve(raw_names, canonical):
    """canonical: {supplier_id: supplier_name}。返回 (mapping, ambiguous_norms)。"""
    norm_canon = {}
    ambiguous = set()
    for sid, n in canonical.items():
        key = normalize(n)
        if key in norm_canon:
            ambiguous.add(key)  # 两家规范名归一后相同——真实世界的同名坑，记录之
        norm_canon[key] = sid
    mapping = {}
    for raw in sorted(set(raw_names)):
        n = normalize(raw)
        sid = norm_canon.get(n)
        if sid is None:  # 子集/前缀兜底
            for cn, csid in sorted(norm_canon.items()):
                if n and (cn.startswith(n) or n.startswith(cn)
                          or set(n.split()) <= set(cn.split())):
                    sid = csid
                    break
        mapping[raw] = sid
    return mapping, ambiguous


def resolve_milestones(ms_rows, ship_by_booking, ship_by_container):
    """单证号级 ER（v0.6-H3）：milestone 无内部 shipment_id，按单证号反查所属 shipment。

    真实承运商 EDI 事件带 booking_no / container_no，需映射回客户内部 shipment_id：
      1. booking_no 非空 → 查 tms_shipments.booking_no 唯一映射；
      2. 否则 container_no 非空 → 经 tms_containers 反查 shipment；
      3. 都失败 → unresolved（不丢弃、不猜），记 reason。

    返回 (resolved, unresolved)：
      resolved   = [dict(原行) + shipment_id + resolved_by]
      unresolved = [dict(原行) + reason]（原行全列保留）
    reason 取值：no_doc_ref / booking_not_found / container_not_found
    """
    resolved, unresolved = [], []
    for r in ms_rows:
        booking = r.get("booking_no", "")
        container = r.get("container_no", "")
        sid, by = None, None
        if booking:
            sid = ship_by_booking.get(booking)
            if sid:
                by = "booking_no"
        if sid is None and container:
            sid = ship_by_container.get(container)
            if sid:
                by = "container_no"
        if sid is not None:
            resolved.append({**r, "shipment_id": sid, "resolved_by": by})
        else:
            if not booking and not container:
                reason = "no_doc_ref"
            elif booking:
                # booking 非空但未命中（typo 场景）；若还带柜号也未命中一并归此因
                reason = "booking_not_found"
            else:
                reason = "container_not_found"
            unresolved.append({**r, "reason": reason})
    return resolved, unresolved
