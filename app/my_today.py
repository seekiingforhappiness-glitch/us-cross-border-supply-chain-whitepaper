"""「我的今天」轻聚合（工作台首屏）：按当前角色 + data_scope 现算的待办计数卡。

设计动机（≤5 行「为什么这样建」）：
- 前后台分离后，工作台首屏要一眼看清「今天什么落到我桌上」——四类待办计数 + 去哪个标签处理。
- 计数口径**逐条复用对应 tab 的同一查询/过滤**（风险=风险队列 JOIN+region 口径、DQ=DQ 台
  open 口径、提案=任务台 approval_status=pending、协调=收件箱 active+overdue）——不一致即 bug。
- 纯函数、零 Streamlit 依赖：可被 streamlit_app 与 test_layout_split 直接 import 并断言数字相等。
- 只读聚合：不新增对象/动作/权限；卡只在角色能见对应 tab 时出现（「去 XX 处理」的引导必须可达）。
"""

try:  # 包上下文（python3 -m app.*）
    from .coordination_actions import is_overdue
    from .data_scope import risk_in_region_scope, scope_for_role
    from .rbac_nav import visible_tabs
except ImportError:  # streamlit run app/streamlit_app.py：脚本目录在 sys.path
    from coordination_actions import is_overdue
    from data_scope import risk_in_region_scope, scope_for_role
    from rbac_nav import visible_tabs


def count_my_risks(con, role, mode=None):
    """与 render_risk_tab 同口径：open 风险 JOIN shipments（po/仓储锚定的无货运风险不进队列）；
    mode 缺省用该 tab 的角色默认（manager→all，其余→region 本区域），region 过滤复用
    risk_in_region_scope（scope_for_role(role, "team")，manager 恒 all 不受限）。"""
    rows_ = con.execute(
        """SELECT s.destination_port_locode FROM risk_events r
           JOIN shipments s ON s.shipment_id=r.shipment_id
           WHERE r.status NOT IN ('resolved','escalated')""").fetchall()
    eff = mode if mode in ("region", "all") else ("all" if role == "manager" else "region")
    if eff == "all":
        return len(rows_)
    scope = scope_for_role(role, "team")  # team=同 region；与 render_risk_tab 逐字同参
    return sum(1 for x in rows_ if risk_in_region_scope(scope, x[0]))


def count_open_dq(con):
    """与 render_dq_tab 默认视图同口径：未关闭的 DQ issue（含解析失败的 unresolved milestone 源）。"""
    return con.execute("SELECT count(*) c FROM dq_issues WHERE status!='closed'").fetchone()[0]


def count_pending_approvals(con):
    """与任务处理台「审批=pending」行同口径（manager 数据域恒 all，故无行级过滤）。"""
    return con.execute("SELECT count(*) c FROM tasks WHERE approval_status='pending'").fetchone()[0]


def count_overdue_coordinations(con, as_of):
    """与 render_coord_tab 同口径：活跃线程（非 resolved/dead_ended）中 overdue 的条数。"""
    threads = con.execute("SELECT state, next_action_due FROM coordination_threads").fetchall()
    active = [t for t in threads if t[0] not in ("resolved", "dead_ended")]
    return sum(1 for t in active if is_overdue(t[0], t[1], as_of))


def build_my_today(con, role, as_of, risk_mode=None):
    """四个计数卡（按角色 tab 可见性裁剪，卡序=规格给定序）：[{key,label,count,go}...]，纯读。

    裁剪规则（最保守读法）：卡上数字必须与「对应 tab 内看到的数」一致，角色看不见该 tab 就没有
    可对照的数、引导也无处可去 → 该卡不出现。审批卡按规格仅 manager。
    """
    tabs = set(visible_tabs(role))
    cards = []
    if "risk" in tabs:
        cards.append({"key": "risk", "label": "待我处理的风险",
                      "count": count_my_risks(con, role, risk_mode),
                      "go": "去「风险队列」处理"})
    if "dq" in tabs:
        cards.append({"key": "dq", "label": "待我核对的数据",
                      "count": count_open_dq(con),
                      "go": "去 控制室→「操作与异常记录」核对"})
    if role == "manager" and "task" in tabs:
        cards.append({"key": "approve", "label": "待我批准的提案",
                      "count": count_pending_approvals(con),
                      "go": "去「任务处理台」审批"})
    if "coord" in tabs:
        cards.append({"key": "coord", "label": "超期催办的协调线程",
                      "count": count_overdue_coordinations(con, as_of),
                      "go": "去「协调收件箱」催办"})
    return cards


# ---------- P1-3「去 XX」引导可点：卡 key → 目标 tab（st.tabs 不可编程选中，方案 B——
# 见 docs 报告：按钮设 session_state 目标 + 提示用户点哪个标签 + 目标标签名高亮加粗）----------
# 与 test_layout_split.py 里独立复算的 card_tab 字典同值（该测试按本仓「双路对账」哲学故意不从
# 本模块导入、自己重写一份核对）——两处若不一致，测试会先炸。
CARD_TARGET_TAB = {"risk": "risk", "dq": "dq", "approve": "task", "coord": "coord"}

# 卡 key → 紧急度权重（数字越小越急）：业务风险/待批提案 > 协调超期 > 数据核对——不是「数字最大的
# 卡最急」，而是按业务重要性人工排的固定序（数据质量问题通常不如一个未处理的延误风险紧急）。
_CARD_URGENCY_RANK = {"risk": 0, "approve": 0, "coord": 1, "dq": 2}


def _most_urgent_card(cards):
    candidates = [c for c in cards if c["count"] > 0]
    if not candidates:
        return None
    return min(candidates, key=lambda c: (_CARD_URGENCY_RANK.get(c["key"], 9), -c["count"]))


def build_health_line(con, role, as_of, cards):
    """P1-5 首屏一句话人话结论：数字与 build_my_today 的卡逐字同源（同一次调用产出的 cards
    直接传入，不重新查询——保证「卡上数字」与「这句话里的数字」不会因两次查询时间差而对不上）。

    manager 版：生意整体健康度（未解决 critical 风险数 + 合计敞口 + 待批提案数）；
    其余角色版：个人待办总数 + 最急的一项（按 _CARD_URGENCY_RANK，不是「数字最大」）。
    """
    if role == "manager":
        crit = con.execute(
            """SELECT count(*) c, COALESCE(sum(affected_value_usd), 0) v FROM risk_events
               WHERE severity='critical' AND status NOT IN ('resolved','escalated')""").fetchone()
        n_crit, exposure = crit[0], crit[1]
        approvals = next((c["count"] for c in cards if c["key"] == "approve"), 0)
        if n_crit == 0 and approvals == 0:
            return "今天：无 critical 风险未解决，暂无待批提案——保持关注即可。"
        wan = round(exposure / 10000, 1)
        return (f"今天：{n_crit} 笔 critical 风险未解决，合计敞口 ${wan} 万；"
                f"{approvals} 件提案等你批")
    total = sum(c["count"] for c in cards)
    if not cards or total == 0:
        return "今天：暂无待办——可主动看看当前工作台有无新情况。"
    top = _most_urgent_card(cards)
    urgent = f"{top['label']}（{top['count']} 项）" if top else "-"
    return f"你有 {total} 件待办，最急的是 {urgent}"
