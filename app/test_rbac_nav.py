"""RBAC 导航层脚本测试：python3 -m app.test_rbac_nav

验证真·角色导航（app/rbac_nav）：
① 每个角色的 ROLE_WORKSPACE 只含允许的 tab（映射正确、manager 含 KPI 且最全、sales 不含费用工作台等）
② can_see_tab / visible_tabs 对每角色返回正确集合
③ 动作层权限（ROLE_PERMS）未被改动——本次只动导航层，硬 gate 不变

不改任何对象/规则/KPI；纯读断言。
"""
import sys

from .rbac_nav import (ROLE_WORKSPACE, ROLE_WORKSPACE_META, TAB_LABELS,
                       can_see_tab, visible_tabs)
from .actions import ROLE_PERMS

FAILS = []
ROLES = ["ops", "cs", "finance", "sales", "compliance", "manager"]
ALL_TABS = {"kpi", "risk", "task", "cost", "po", "obj", "dq", "adm", "log"}

# 需求指定的角色→工作台映射（真源，测试即冻结此契约）
# 审计日志(log)口径收敛：给有审阅需要的 ops(运营)/compliance(治理)/manager(监督)——按 data_scope 过滤。
# 采购工作台(po)：采购切片 Build 3 新增，给运营(收货/交期)/财务(供票/价量)/经理(全域)。
EXPECTED_WORKSPACE = {
    "ops":        ["risk", "task", "dq", "po", "obj", "log"],
    "cs":         ["risk", "task", "obj"],
    "finance":    ["cost", "po", "adm", "obj"],
    "sales":      ["adm", "obj"],
    "compliance": ["adm", "risk", "obj", "log"],
    "manager":    ["kpi", "risk", "task", "cost", "po", "obj", "dq", "adm", "log"],
}

# 动作层权限矩阵（manual §6 + cost-manual §5）——本次不得改动
EXPECTED_ROLE_PERMS = {
    "AssignTask": {"ops", "system"},
    "ProposeMitigation": {"ops", "cs", "finance"},
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
        check(f"{r} 的 tab 全部合法（∈ 9 个已知 tab）",
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
    check("manager 覆盖全部 9 个 tab", set(ROLE_WORKSPACE["manager"]) == ALL_TABS)
    check("sales 不含费用工作台(cost)", "cost" not in ROLE_WORKSPACE["sales"])
    check("sales 不含准入外的运营 tab（无 task/risk/dq）",
          not ({"task", "risk", "dq"} & set(ROLE_WORKSPACE["sales"])))
    check("finance 不含风险队列/任务台（导航层隔离）",
          not ({"risk", "task"} & set(ROLE_WORKSPACE["finance"])))
    check("cs 不含费用/准入/DQ",
          not ({"cost", "adm", "dq"} & set(ROLE_WORKSPACE["cs"])))
    check("审计日志(log)给 ops/compliance/manager（收敛：运营/治理/监督需审阅，data_scope 过滤）",
          sorted(r for r in ROLES if "log" in ROLE_WORKSPACE[r]) == ["compliance", "manager", "ops"],
          str(sorted(r for r in ROLES if "log" in ROLE_WORKSPACE[r])))
    check("审计日志(log)对 cs/finance/sales 不可见（无审阅需要）",
          not any("log" in ROLE_WORKSPACE[r] for r in ("cs", "finance", "sales")))

    print("== ③ visible_tabs / can_see_tab 一致性 ==")
    for r in ROLES:
        check(f"visible_tabs({r}) 顺序与映射一致",
              visible_tabs(r) == EXPECTED_WORKSPACE[r], str(visible_tabs(r)))
    for r in ROLES:
        good = all(can_see_tab(r, t) == (t in ROLE_WORKSPACE[r]) for t in ALL_TABS)
        check(f"can_see_tab({r}, *) 对 8 个 tab 全部正确", good)
    check("未知角色回退经理全量（visible_tabs）",
          visible_tabs("intern") == ROLE_WORKSPACE["manager"])
    check("未知角色回退经理全量（can_see_tab kpi）", can_see_tab("intern", "kpi"))

    print("== ④ 命令栏元数据（工作台名 + 数据域）==")
    check("每个角色都有工作台名 + 数据域",
          all(r in ROLE_WORKSPACE_META and {"name", "domain"} <= set(ROLE_WORKSPACE_META[r])
              for r in ROLES))

    print("== ⑤ 动作层权限（ROLE_PERMS）未被改动 ==")
    check("ROLE_PERMS 与基线完全一致（硬 gate 未削弱）",
          ROLE_PERMS == EXPECTED_ROLE_PERMS, str(ROLE_PERMS))
    check("AssignTask 仍限 ops/system", ROLE_PERMS["AssignTask"] == {"ops", "system"})
    check("ApproveMitigation 仍仅 manager", ROLE_PERMS["ApproveMitigation"] == {"manager"})
    check("CloseRiskEvent 仍仅 ops", ROLE_PERMS["CloseRiskEvent"] == {"ops"})

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
