"""RBAC 导航层脚本测试：python3 -m app.test_rbac_nav

验证真·角色导航（app/rbac_nav）：
① 每个角色的 ROLE_WORKSPACE 只含允许的 tab（映射正确、manager 含 KPI 且最全、sales 不含费用工作台等）
② can_see_tab / visible_tabs 对每角色返回正确集合
③ 动作层权限（ROLE_PERMS）未被改动——本次只动导航层，硬 gate 不变
④（前后台分离）SURFACE_TABS 是 11 tab 的完全划分；visible_tabs(role, surface) 是原列表的
   保序子列表、两面并集 == 分面前全集（可见性零变化）——细粒度分组契约见 test_layout_split
④c（控制室三问句）CONTROL_GROUPS 是控制室 5 成员 tab 的完全划分；visible_control_groups
   的组可见性 == 成员可见性并集、组内成员 = 该角色有权成员（合并纯呈现层，权限语义零变化）

不改任何对象/规则/KPI；纯读断言。
"""
import sys

from .rbac_nav import (CONTROL_GROUP_LABELS, CONTROL_GROUP_OF, CONTROL_GROUP_ORDER,
                       CONTROL_GROUPS, ROLE_WORKSPACE, ROLE_WORKSPACE_META,
                       SURFACE_LABELS, SURFACE_TABS, SURFACES, TAB_LABELS,
                       can_see_tab, visible_control_groups, visible_tabs)
from .actions import ROLE_PERMS
from .coordination_actions import COORD_PERMS

FAILS = []
ROLES = ["ops", "cs", "finance", "sales", "compliance", "manager", "procurement"]
ALL_TABS = {"kpi", "risk", "task", "coord", "cost", "po", "obj", "kg", "dq", "adm", "log"}
# 协调收件箱(coord)是 COORD_PERMS 作用域内的 tab，manager 不含它（与权限组同集），故 manager
# 覆盖 ALL_TABS - {coord}；下方 EXPECTED_WORKSPACE 与断言据此冻结。
MANAGER_TABS = ALL_TABS - {"coord"}

# 需求指定的角色→工作台映射（真源，测试即冻结此契约）
# 审计日志(log)口径收敛：给有审阅需要的 ops(运营)/compliance(治理)/manager(监督)——按 data_scope 过滤。
# 采购工作台(po)：P4 起以专职「采购 procurement」为主场（po 落地页）；财务仍可见做供票/价量；经理全域。
# P4：ops 去掉 po「回归纯物流」。
# 协调收件箱(coord)：CL1 呈现层切片——仅给 COORD_PERMS 的 ops/cs/finance/procurement（放各自主战场后），
# 与 coordination_actions.COORD_PERMS 严格同集；sales/compliance/manager 不加（本切片不放宽协调写权限）。
# 知识图谱(kg)：纯只读呈现层（本体地图+对象邻域 trace，无任何写动作/agent 工具）——挂 manager（监督者）
# 与 ops（理解者），紧跟对象详情(obj)；实例级查询过 data_scope（ops 区域过滤、manager 全量）。
# P0-1：finance 补挂 task（陌生人测试王姐/财务断头路——有提案权却无任务台入口）；cs/procurement 本就有。
EXPECTED_WORKSPACE = {
    "ops":         ["risk", "task", "coord", "dq", "obj", "kg", "log"],
    "cs":          ["risk", "task", "coord", "obj"],
    "finance":     ["cost", "task", "po", "coord", "adm", "obj"],
    "procurement": ["po", "task", "coord", "obj"],
    "sales":       ["adm", "obj"],
    "compliance":  ["adm", "risk", "obj", "log"],
    "manager":     ["kpi", "risk", "task", "cost", "po", "obj", "kg", "dq", "adm", "log"],
}

# 动作层权限矩阵（manual §6 + cost-manual §5 + P4：ProposeMitigation +procurement）
# 铁律不变：ApproveMitigation 仍仅 manager、CloseRiskEvent 仍仅 ops（maker-checker）。
EXPECTED_ROLE_PERMS = {
    "AssignTask": {"ops", "system"},
    "ProposeMitigation": {"ops", "cs", "finance", "procurement"},
    "ApproveMitigation": {"manager"},
    "CloseRiskEvent": {"ops"},
}


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    print("== ① 角色→工作台映射（真·角色导航）==")
    check("6 个角色全部有工作台定义", set(ROLE_WORKSPACE) == set(ROLES),
          str(set(ROLE_WORKSPACE)))
    for r in ROLES:
        check(f"{r} 映射与需求一致", ROLE_WORKSPACE.get(r) == EXPECTED_WORKSPACE[r],
              f"{ROLE_WORKSPACE.get(r)} != {EXPECTED_WORKSPACE[r]}")
    for r in ROLES:
        check(f"{r} 的 tab 全部合法（∈ 11 个已知 tab）",
              set(ROLE_WORKSPACE[r]) <= ALL_TABS,
              str(set(ROLE_WORKSPACE[r]) - ALL_TABS))
    for r in ROLES:
        check(f"{r} 的 tab 均有显示标签", all(k in TAB_LABELS for k in ROLE_WORKSPACE[r]))

    print("== ② KPI / 最全 / 关键排除项 ==")
    check("KPI 总览仅 manager 可见",
          [r for r in ROLES if "kpi" in ROLE_WORKSPACE[r]] == ["manager"],
          str([r for r in ROLES if "kpi" in ROLE_WORKSPACE[r]]))
    check("manager 含 KPI", "kpi" in ROLE_WORKSPACE["manager"])
    check("manager 工作台最全（tab 数严格最多）",
          all(len(ROLE_WORKSPACE["manager"]) > len(ROLE_WORKSPACE[r])
              for r in ROLES if r != "manager"))
    check("manager 覆盖除 coord 外全部 10 个运营 tab（coord 属 COORD_PERMS 作用域，manager 不含）",
          set(ROLE_WORKSPACE["manager"]) == MANAGER_TABS,
          str(set(ROLE_WORKSPACE["manager"]) ^ MANAGER_TABS))
    check("sales 不含费用工作台(cost)", "cost" not in ROLE_WORKSPACE["sales"])
    check("sales 不含准入外的运营 tab（无 task/risk/dq）",
          not ({"task", "risk", "dq"} & set(ROLE_WORKSPACE["sales"])))
    check("finance 有任务处理台(task, 提案权对齐 P0-1) 但不含物流风险队列(risk)",
          "task" in ROLE_WORKSPACE["finance"] and "risk" not in ROLE_WORKSPACE["finance"])
    # P0-1 推导式断言（不硬编码角色名，与 ROLE_PERMS 真源对齐）：凡有 ProposeMitigation 提案权的角色，
    # 工作台必须含任务处理台(task)——否则「有权提案却无入口」= 断头路（陌生人测试王姐/财务撞到）。
    for _r in sorted(ROLE_PERMS["ProposeMitigation"]):
        if _r not in ROLE_WORKSPACE:
            continue
        check(f"{_r} 有提案权 → 工作台含任务处理台(task)（P0-1 对齐 ROLE_PERMS）",
              "task" in ROLE_WORKSPACE[_r], str(ROLE_WORKSPACE[_r]))
    check("cs 不含费用/准入/DQ",
          not ({"cost", "adm", "dq"} & set(ROLE_WORKSPACE["cs"])))
    check("审计日志(log)给 ops/compliance/manager（收敛：运营/治理/监督需审阅，data_scope 过滤）",
          sorted(r for r in ROLES if "log" in ROLE_WORKSPACE[r]) == ["compliance", "manager", "ops"],
          str(sorted(r for r in ROLES if "log" in ROLE_WORKSPACE[r])))
    check("审计日志(log)对 cs/finance/sales 不可见（无审阅需要）",
          not any("log" in ROLE_WORKSPACE[r] for r in ("cs", "finance", "sales")))
    check("协调收件箱(coord)仅 COORD_PERMS 4 角色可见（ops/cs/finance/procurement）",
          sorted(r for r in ROLES if "coord" in ROLE_WORKSPACE[r])
          == ["cs", "finance", "ops", "procurement"],
          str(sorted(r for r in ROLES if "coord" in ROLE_WORKSPACE[r])))
    check("协调收件箱(coord)对 sales/compliance/manager 不可见（与 COORD_PERMS 严格同集，不放宽写权限）",
          not any("coord" in ROLE_WORKSPACE[r] for r in ("sales", "compliance", "manager")))
    check("知识图谱(kg)仅 manager（监督者）+ ops（理解者）可见——纯只读呈现层切片",
          sorted(r for r in ROLES if "kg" in ROLE_WORKSPACE[r]) == ["manager", "ops"],
          str(sorted(r for r in ROLES if "kg" in ROLE_WORKSPACE[r])))
    check("知识图谱(kg)对 cs/finance/sales/compliance/procurement 不可见（本切片不扩散）",
          not any("kg" in ROLE_WORKSPACE[r]
                  for r in ("cs", "finance", "sales", "compliance", "procurement")))
    check("coord 导航集 == COORD_PERMS.ManageCoordination（导航与权限组同集，谁能看即谁能写，不多不少）",
          {r for r in ROLES if "coord" in ROLE_WORKSPACE[r]} == COORD_PERMS["ManageCoordination"],
          f"{ {r for r in ROLES if 'coord' in ROLE_WORKSPACE[r]} } vs {COORD_PERMS['ManageCoordination']}")

    print("== ③ visible_tabs / can_see_tab 一致性 ==")
    for r in ROLES:
        check(f"visible_tabs({r}) 顺序与映射一致",
              visible_tabs(r) == EXPECTED_WORKSPACE[r], str(visible_tabs(r)))
    for r in ROLES:
        good = all(can_see_tab(r, t) == (t in ROLE_WORKSPACE[r]) for t in ALL_TABS)
        check(f"can_see_tab({r}, *) 对 11 个 tab 全部正确", good)
    check("未知角色回退经理全量（visible_tabs）",
          visible_tabs("intern") == ROLE_WORKSPACE["manager"])
    check("未知角色回退经理全量（can_see_tab kpi）", can_see_tab("intern", "kpi"))

    print("== ④ 命令栏元数据（工作台名 + 数据域）==")
    check("每个角色都有工作台名 + 数据域",
          all(r in ROLE_WORKSPACE_META and {"name", "domain"} <= set(ROLE_WORKSPACE_META[r])
              for r in ROLES))

    print("== ④b 前后台分离：SURFACE 划分契约（细粒度分组断言见 test_layout_split）==")
    check("SURFACES=(work,control) 且工作台默认在前", SURFACES == ("work", "control"))
    check("导航面标签 工作台/控制室",
          SURFACE_LABELS == {"work": "工作台", "control": "控制室"}, str(SURFACE_LABELS))
    _w, _c = set(SURFACE_TABS["work"]), set(SURFACE_TABS["control"])
    check("两面是 11 tab 的完全划分（不重不漏）", not (_w & _c) and (_w | _c) == ALL_TABS,
          f"交={_w & _c} 并异={( _w | _c) ^ ALL_TABS}")
    check("控制室=理解与监督 5 tab（kpi/obj/kg/dq/log）", _c == {"kpi", "obj", "kg", "dq", "log"},
          str(_c))
    for r in ROLES:
        _sub = visible_tabs(r, "work") + visible_tabs(r, "control")
        check(f"{r} 两面并集 == 分面前全集（RBAC 可见性零变化）",
              sorted(_sub) == sorted(visible_tabs(r)), str(_sub))
        check(f"{r} 各面为保序子列表",
              all(visible_tabs(r, s) == [k for k in visible_tabs(r) if k in set(SURFACE_TABS[s])]
                  for s in SURFACES))
    check("visible_tabs 不带 surface 参数语义不变（向后兼容全集）",
          all(visible_tabs(r) == EXPECTED_WORKSPACE[r] for r in ROLES))

    print("== ④c 控制室三问句合并组：完全划分 + 组可见=成员并集（细粒度见 test_layout_split）==")
    _members = [t for g in CONTROL_GROUP_ORDER for t in CONTROL_GROUPS[g]]
    check("合并组是控制室 5 成员 tab 的完全划分（不重不漏）",
          sorted(_members) == sorted(SURFACE_TABS["control"])
          and len(_members) == len(set(_members)), str(_members))
    check("三问句组标签（全局概览/追查一件事/操作与异常记录）",
          CONTROL_GROUP_LABELS == {"overview": "全局概览", "trace": "追查一件事",
                                   "records": "操作与异常记录"}, str(CONTROL_GROUP_LABELS))
    check("CONTROL_GROUP_OF 反查与 CONTROL_GROUPS 一致",
          all(t in CONTROL_GROUPS[g] for t, g in CONTROL_GROUP_OF.items())
          and set(CONTROL_GROUP_OF) == set(_members))
    for r in ROLES:
        got = visible_control_groups(r)
        seen = set(visible_tabs(r, "control"))
        derived = [(g, [t for t in CONTROL_GROUPS[g] if t in seen])
                   for g in CONTROL_GROUP_ORDER]
        derived = [(g, m) for g, m in derived if m]
        check(f"{r} 组可见=成员可见性并集且组内只含有权成员（零放宽零收紧）",
              got == derived, f"{got} != {derived}")
        check(f"{r} 组成员并集 == 控制室面可见 tab 全集（合并不增不减内容）",
              sorted(t for _g, m in got for t in m) == sorted(seen))
    check("kpi 组（全局概览）仅 manager 可见（原规则维持）",
          [r for r in ROLES if any(g == "overview" for g, _m in visible_control_groups(r))]
          == ["manager"])
    check("追查一件事人人可见（obj 全角色可见 → 组并集可见性）",
          all(any(g == "trace" for g, _m in visible_control_groups(r)) for r in ROLES))
    check("cs/finance/procurement/sales 的追查一件事无图部分（kg 成员不在组内）",
          all(dict(visible_control_groups(r)).get("trace") == ["obj"]
              for r in ("cs", "finance", "procurement", "sales")))

    print("== ⑤ 动作层权限（ROLE_PERMS）未被改动 ==")
    # 快照锈蚀修复（同 test_coordination_loop⑥/test_data_scope⑤先例）：整字典相等在 F1 合法
    # 新增键后必假失败；改为基线键逐值精确核对（未放松任何一键），下三行具名断言原样保留。
    check("ROLE_PERMS 基线键未被削弱（硬 gate：逐键精确）",
          all(ROLE_PERMS.get(k) == v for k, v in EXPECTED_ROLE_PERMS.items()),
          str({k: ROLE_PERMS.get(k) for k in EXPECTED_ROLE_PERMS}))
    check("AssignTask 仍限 ops/system", ROLE_PERMS["AssignTask"] == {"ops", "system"})
    check("ApproveMitigation 仍仅 manager", ROLE_PERMS["ApproveMitigation"] == {"manager"})
    check("CloseRiskEvent 仍仅 ops", ROLE_PERMS["CloseRiskEvent"] == {"ops"})

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
