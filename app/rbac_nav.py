"""RBAC 导航层（真·角色导航 + 前后台二分）：角色 → 可见工作台 tab 的纯映射与查询。

设计动机（≤5 行「为什么这样建」）：
- 原 UI 七个 tab 对所有角色全渲染再脱敏，是「假 RBAC」；本模块让不同角色只渲染自己的工作台，
  demo 一试就像真实系统。
- 前后台分离（Daniel 裁决）：tab 再分两个导航面——「工作台」（操作型，干活的人）与「控制室」
  （理解与监督，只读为主）。分面是 ROLE_WORKSPACE 的**划分**：不增不减任何角色的可见 tab。
- 纯数据 + 纯函数、零 Streamlit 依赖，可被 streamlit_app 与 test_rbac_nav 直接 import 而无副作用。
- **只管导航呈现，不放宽任何动作**：动作层权限仍由 app/actions.py ROLE_PERMS + M1 maker-checker 硬 gate。
"""

# tab key → 显示标签（kpi/dq 按 Daniel「命名去黑话」改名：原「KPI 总览」「DQ 处置」；
# obj/kg/log 在控制室内不再作为独立标签呈现——见下方 CONTROL_GROUPS 三问句合并组）
TAB_LABELS = {
    "kpi": "全局概览",
    "risk": "风险队列",
    "task": "任务处理台",
    "coord": "协调收件箱",
    "cost": "费用工作台",
    "po": "采购工作台",
    "obj": "对象详情",
    "kg": "知识图谱",
    "dq": "待核对的数据",
    "adm": "准入工作台",
    "log": "审计日志",
}

# 角色 → 工作台可见 tab（列表顺序即 tab 呈现顺序；每角色主战场排第一，成为默认落地页）
# 审计日志(log)给「有审阅需要」的角色——运营(ops)/治理(compliance)/监督(manager)；且按 data_scope
# 过滤（render_log_tab）：非 manager 只看本区域数据范围内对象的审计，manager 看全量（口径收敛）。
# 采购工作台(po)：P4 起以专职「采购 procurement」为主场（po 落地页）；财务仍可见 po 做供票/价量对账；
# 经理全域可见。P4：ops 去掉 po「回归纯物流」——采购三方对账归采购角色，采购提案→经理审批→运营关闭三方分离。
# 协调收件箱(coord)：CL1 协调回路的呈现层入口——放在各角色主战场之后。仅给 COORD_PERMS 的
# 4 角色（ops/cs/finance/procurement），与 coordination_actions.COORD_PERMS 一致；manager/sales/
# compliance 不加（本切片不放宽协调写权限，导航与权限组严格同集）。写动作仍由动作层独立 gate。
# 知识图谱(kg)：纯只读呈现层（本体地图 + 对象邻域 trace），零写动作零 agent 工具——挂 manager
# （监督者）与 ops（理解者）两个工作台，紧跟对象详情(obj)；实例级查询过 data_scope（ops 区域过滤）。
ROLE_WORKSPACE = {
    "ops":         ["risk", "task", "coord", "dq", "obj", "kg", "log"],
    "cs":          ["risk", "task", "coord", "obj"],
    "finance":     ["cost", "po", "coord", "adm", "obj"],
    "procurement": ["po", "task", "coord", "obj"],
    "sales":       ["adm", "obj"],
    "compliance":  ["adm", "risk", "obj", "log"],
    "manager":     ["kpi", "risk", "task", "cost", "po", "obj", "kg", "dq", "adm", "log"],
}

# 每角色工作台名 + 数据域（命令栏呈现，让页面明显因角色而不同）
ROLE_WORKSPACE_META = {
    "ops":         {"name": "运营处置台", "domain": "物流风险 · 任务 · DQ"},
    "cs":          {"name": "客户成功台", "domain": "客户交付风险 · 任务"},
    "finance":     {"name": "费用成本台", "domain": "发票对账 · 成本情景"},
    "procurement": {"name": "采购处置台", "domain": "采购三方对账 · 供应商 · 寻源"},
    "sales":       {"name": "准入受理台", "domain": "准入案件"},
    "compliance":  {"name": "合规审查台", "domain": "准入合规 · 单证风险"},
    "manager":     {"name": "经理总览台", "domain": "全域"},
}


# ---------- 前后台二分（顶层导航面）----------
# 工作台(work)=操作型标签（干活：处置/提案/审批/催办/建案）；控制室(control)=理解与监督
# （对象详情/知识图谱/审计日志/DQ 处置/经理 KPI 总览，只读为主）。两组是 11 个 tab 的完全划分
# （不重不漏）；每角色在某导航面可见的 tab = 该角色 ROLE_WORKSPACE 列表按组过滤（保持原相对顺序），
# 故任何角色的可见 tab 全集与分面前完全一致——RBAC 语义零变化，只是归组。
SURFACES = ("work", "control")           # 顺序即 sidebar 呈现顺序；work 是默认落地面
SURFACE_LABELS = {"work": "工作台", "control": "控制室"}
SURFACE_TABS = {
    "work":    ("risk", "task", "coord", "cost", "po", "adm"),
    "control": ("kpi", "obj", "kg", "dq", "log"),
}


# ---------- 控制室三问句合并组（Daniel 批准：5 个工程师标签 → 3 个大白话标签）----------
# 为什么这样建（≤5 行）：控制室原 5 tab 命名全是黑话且逻辑割裂（Daniel 反馈）；改为三个问句——
# 「全局概览」=生意整体怎么样（原 KPI）；「追查一件事」=一件事的来龙去脉（对象详情+知识图谱合一）；
# 「操作与异常记录」=谁做了什么+哪些数据待核对（审计日志+DQ 合一）。纯呈现层归组红线：
# 组可见 = 至少能看一个成员 tab；组内只渲染该角色原本有权看的成员——ROLE_WORKSPACE 的
# 成员级可见性仍是唯一权限真源，不增不减任何角色能看到的内容。
CONTROL_GROUPS = {
    "overview": ("kpi",),          # 全局概览：生意整体怎么样（仅 manager 可见，维持原规则）
    "trace":    ("obj", "kg"),     # 追查一件事：对象只读视图 + （有 kg 权限时）邻域图/本体地图
    "records":  ("log", "dq"),     # 操作与异常记录：审计流水分节 + 待核对数据分节（成员序=分节序）
}
CONTROL_GROUP_ORDER = ("overview", "trace", "records")
CONTROL_GROUP_LABELS = {"overview": "全局概览", "trace": "追查一件事",
                        "records": "操作与异常记录"}
# 成员 tab → 所属组（my_today 引导文案与测试用：控制室 tab 的入口是它的组标签）
CONTROL_GROUP_OF = {t: g for g, members in CONTROL_GROUPS.items() for t in members}


def visible_control_groups(role):
    """控制室导航面的合并标签：[(组 key, [该角色可见成员 tab]), ...]。

    组序 = CONTROL_GROUP_ORDER，成员序 = CONTROL_GROUPS 定义序（即分节呈现序）。
    组内可见成员为空 → 整组不出现（组可见性 = 成员可见性的并集，零放宽零收紧）。
    """
    seen = set(visible_tabs(role, "control"))
    out = []
    for g in CONTROL_GROUP_ORDER:
        members = [t for t in CONTROL_GROUPS[g] if t in seen]
        if members:
            out.append((g, members))
    return out


def visible_tabs(role, surface=None):
    """返回该角色可见的 tab key 列表（按 ROLE_WORKSPACE 定义顺序）。未知角色回退经理全量。

    surface=None → 全集（分面前语义，向后兼容）；surface∈SURFACES → 该导航面内的子列表
    （原相对顺序）。未知 surface 回退全集（同未知角色的「不误藏数据」哲学）。
    """
    tabs = list(ROLE_WORKSPACE.get(role, ROLE_WORKSPACE["manager"]))
    if surface not in SURFACES:
        return tabs
    group = set(SURFACE_TABS[surface])
    return [k for k in tabs if k in group]


def can_see_tab(role, tab_key):
    """该角色是否可见某 tab。未知角色回退经理全量。"""
    return tab_key in ROLE_WORKSPACE.get(role, ROLE_WORKSPACE["manager"])
