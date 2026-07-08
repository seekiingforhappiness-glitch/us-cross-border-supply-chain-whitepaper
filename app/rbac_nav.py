"""RBAC 导航层（真·角色导航）：角色 → 可见工作台 tab 的纯映射与查询。

设计动机（≤5 行「为什么这样建」）：
- 原 UI 七个 tab 对所有角色全渲染再脱敏，是「假 RBAC」；本模块让不同角色只渲染自己的工作台，
  demo 一试就像真实系统。
- 纯数据 + 纯函数、零 Streamlit 依赖，可被 streamlit_app 与 test_rbac_nav 直接 import 而无副作用。
- **只管导航呈现，不放宽任何动作**：动作层权限仍由 app/actions.py ROLE_PERMS + M1 maker-checker 硬 gate。
"""

# tab key → 显示标签
TAB_LABELS = {
    "kpi": "KPI 总览",
    "risk": "风险队列",
    "task": "任务处理台",
    "cost": "费用工作台",
    "obj": "对象详情",
    "dq": "DQ 处置",
    "adm": "准入工作台",
    "log": "审计日志",
}

# 角色 → 工作台可见 tab（列表顺序即 tab 呈现顺序；每角色主战场排第一，成为默认落地页）
ROLE_WORKSPACE = {
    "ops":        ["risk", "task", "dq", "obj"],
    "cs":         ["risk", "task", "obj"],
    "finance":    ["cost", "adm", "obj"],
    "sales":      ["adm", "obj"],
    "compliance": ["adm", "risk", "obj"],
    "manager":    ["kpi", "risk", "task", "cost", "obj", "dq", "adm", "log"],
}

# 每角色工作台名 + 数据域（命令栏呈现，让页面明显因角色而不同）
ROLE_WORKSPACE_META = {
    "ops":        {"name": "运营处置台", "domain": "物流风险 · 任务 · DQ"},
    "cs":         {"name": "客户成功台", "domain": "客户交付风险 · 任务"},
    "finance":    {"name": "费用成本台", "domain": "发票对账 · 成本情景"},
    "sales":      {"name": "准入受理台", "domain": "准入案件"},
    "compliance": {"name": "合规审查台", "domain": "准入合规 · 单证风险"},
    "manager":    {"name": "经理总览台", "domain": "全域"},
}


def visible_tabs(role):
    """返回该角色可见的 tab key 列表（按 ROLE_WORKSPACE 定义顺序）。未知角色回退经理全量。"""
    return list(ROLE_WORKSPACE.get(role, ROLE_WORKSPACE["manager"]))


def can_see_tab(role, tab_key):
    """该角色是否可见某 tab。未知角色回退经理全量。"""
    return tab_key in ROLE_WORKSPACE.get(role, ROLE_WORKSPACE["manager"])
