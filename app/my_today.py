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
