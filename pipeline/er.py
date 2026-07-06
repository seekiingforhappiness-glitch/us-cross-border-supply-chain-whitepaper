"""供应商名称 entity resolution（W3，限时实现：规范化 + 令牌匹配，不上模糊算法库）。

输入：TMS 侧出现的供应商名（含变体） + SRM 规范名
输出：raw_name → supplier_id 映射（None=未匹配），供 DQ 报告与 supplier_name_map 表。
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
