"""前后台界面分离测试：python3 -m app.test_layout_split

锁定七项保证（Daniel 裁决「前端使用界面与后端信息界面分开设计」+ 控制室三问句结构的呈现层契约）：
① 分组契约：SURFACE_TABS 是 11 个 tab 的完全划分（不重不漏）；每角色的工作台/控制室子列表
   与冻结契约一致、保持 ROLE_WORKSPACE 原相对顺序；两面并集 == 分面前全集（RBAC 语义零变化）
①b 控制室三问句合并组：CONTROL_GROUPS 是控制室 5 成员 tab 的完全划分；每角色可见组 = 冻结契约；
   组可见 == 成员可见性并集（能看≥1 个成员才见组，组内成员列表=该角色有权成员，零放宽零收紧）
② 「我的今天」计数与对应 tab 内的数一致：build_my_today 的每个数字 =（用各 tab 的**原样查询/
   过滤**独立复算的数字）= seed 数据冻结的具体数字（风险 76 / 待核对 11 / 待批 3 / 协调逾期 2）；
   区域过滤真实生效（临时副本把一票翻成 CN → ops 区域计数下降而 manager 全量不变）；
   卡只在角色能见对应 tab 时出现、引导文字指向真实入口（控制室成员 tab 指向其合并组标签）
③ 控制室标识行存在（只读为主，处置请回工作台）；工作台无标识行、有「我的今天」；
   manager 工作台首屏卡上数字与函数级计数一致（UI 渲染值即真值）；控制室分节标题存在、
   用户侧无「DQ 处置 / DQ issue / KPI 总览」等旧黑话字样
④ AppTest 7 角色 × 两导航面 0 未捕获异常；工作台 tab 标签=TAB_LABELS 契约，
   控制室 tab 标签=三问句组标签契约
⑤ 空库 × 控制室 7 角色 0 异常（分面后 test_smoke 默认只走工作台面，此处补控制室面的空库覆盖）
⑥ 红线：动作层 ROLE_PERMS 与基线一致（分面/合并不碰权限）；真实 DB md5 逐字节不变（纯读）

不改任何对象/规则/KPI/schema/权限；临时副本承载 CN 翻转与空库，绝不污染 data/ontology.sqlite。
"""
import hashlib
import os
import re
import shutil
import sqlite3
import sys
import tempfile

import yaml

from .actions import ROLE_PERMS
from .coordination_actions import is_overdue
from .data_scope import risk_in_region_scope, scope_for_role
from .my_today import (build_my_today, count_my_risks, count_open_dq,
                       count_overdue_coordinations, count_pending_approvals)
from .rbac_nav import (CONTROL_GROUP_LABELS, CONTROL_GROUP_OF, CONTROL_GROUP_ORDER,
                       CONTROL_GROUPS, ROLE_WORKSPACE, SURFACE_LABELS, SURFACE_TABS,
                       SURFACES, TAB_LABELS, visible_control_groups, visible_tabs)

DB = "data/ontology.sqlite"
ROLES = ("ops", "cs", "finance", "procurement", "sales", "compliance", "manager")
ALL_TABS = {"kpi", "risk", "task", "coord", "cost", "po", "obj", "kg", "dq", "adm", "log"}
AS_OF = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))["window"]["as_of"]
FAILS = []

# ---- 冻结契约：每角色两导航面的 tab 子列表（= ROLE_WORKSPACE 按 SURFACE_TABS 分组、保序）----
# 工作台=操作型（风险队列/任务台/协调收件箱/费用/采购/准入）；控制室=理解与监督
# （经理 KPI 总览/对象详情/知识图谱/DQ 处置/审计日志）。规格未列「仓储工作台」独立 tab
# （仓储对象经 对象详情/富工作台 浏览），故不存在也不新造。
# P0-1：finance 工作台面补挂 task（提案权对齐；cost 仍是主战场落地页，task 排其后）。
EXPECTED_WORK = {
    "ops":         ["risk", "task", "coord"],
    "cs":          ["risk", "task", "coord"],
    "finance":     ["cost", "task", "po", "coord", "adm"],
    "procurement": ["po", "task", "coord"],
    "sales":       ["adm"],
    "compliance":  ["adm", "risk"],
    "manager":     ["risk", "task", "cost", "po", "adm"],
}
EXPECTED_CONTROL = {
    "ops":         ["dq", "obj", "kg", "log"],
    "cs":          ["obj"],
    "finance":     ["obj"],
    "procurement": ["obj"],
    "sales":       ["obj"],
    "compliance":  ["obj", "log"],
    "manager":     ["kpi", "obj", "kg", "dq", "log"],
}

# ---- 冻结契约：控制室三问句合并组（Daniel 批准）——组可见 = 成员可见性并集，组内只含有权成员 ----
# 全局概览(kpi 仅 manager)；追查一件事(obj+kg：人人有 obj，kg 仅 ops/manager 带图)；
# 操作与异常记录(log+dq：ops/manager 双分节，compliance 仅审计分节)。
EXPECTED_CONTROL_GROUPS = {
    "ops":         [("trace", ["obj", "kg"]), ("records", ["log", "dq"])],
    "cs":          [("trace", ["obj"])],
    "finance":     [("trace", ["obj"])],
    "procurement": [("trace", ["obj"])],
    "sales":       [("trace", ["obj"])],
    "compliance":  [("trace", ["obj"]), ("records", ["log"])],
    "manager":     [("overview", ["kpi"]), ("trace", ["obj", "kg"]),
                    ("records", ["log", "dq"])],
}
EXPECTED_GROUP_LABELS = {"overview": "全局概览", "trace": "追查一件事",
                         "records": "操作与异常记录"}

# ---- 冻结契约：seed 数据下每角色「我的今天」卡（key, 计数）----
# 风险 76：风险队列口径 = open 风险 JOIN shipments（po/仓储锚定无货运的风险不进队列）；
# seed 全部目的港为 US → 非 manager 的「本区域 US」== 全集 76。DQ 11 / 待批提案 3 / 协调逾期 2。
EXPECTED_CARDS = {
    "ops":         [("risk", 76), ("dq", 11), ("coord", 2)],
    "cs":          [("risk", 76), ("coord", 2)],
    "finance":     [("coord", 2)],
    "procurement": [("coord", 2)],
    "sales":       [],
    "compliance":  [("risk", 76)],
    "manager":     [("risk", 76), ("dq", 11), ("approve", 3)],
}

# 动作层权限基线（与 test_rbac_nav 同源）：分面是呈现层重排，硬 gate 一字不动
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


def _conn(path=DB):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


# ---------- ② 的独立复算：逐字复刻各 tab 的查询与过滤（与 my_today 双路对账）----------
def tab_risk_count(con, role):
    """复刻 render_risk_tab 默认视图：open 风险 JOIN shipments；manager 默认「全部」，
    其余默认「本区域」（scope_for_role(role,'team') + risk_in_region_scope）。"""
    risks = con.execute(
        """SELECT r.*, s.destination_port_locode FROM risk_events r
           JOIN shipments s ON s.shipment_id=r.shipment_id
           WHERE r.status NOT IN ('resolved','escalated')""").fetchall()
    if role == "manager":
        return len(risks)
    scope = scope_for_role(role, "team")
    return sum(1 for r in risks if risk_in_region_scope(scope, r["destination_port_locode"]))


def tab_dq_count(con):
    """复刻 render_dq_tab 默认视图（show_closed=False）：含 LEFT JOIN 的整条查询后数行。"""
    return len(con.execute(
        """SELECT d.* FROM dq_issues d
           LEFT JOIN unresolved_milestones u
             ON d.source_table='unresolved_milestones' AND d.source_record_id=u.milestone_id
           WHERE d.status!='closed'""").fetchall())


def tab_pending_approval_count(con):
    """复刻任务处理台 manager 视图（数据域恒 all）：任务表 JOIN risk_events 后审批=pending 的行数。"""
    tasks = con.execute(
        """SELECT t.approval_status FROM tasks t
           JOIN risk_events r ON r.risk_event_id=t.risk_event_id""").fetchall()
    return sum(1 for t in tasks if t["approval_status"] == "pending")


def tab_coord_overdue_count(con):
    """复刻 render_coord_tab：活跃线程（非 resolved/dead_ended）里 is_overdue 的条数。"""
    th = con.execute("SELECT state, next_action_due FROM coordination_threads").fetchall()
    active = [t for t in th if t["state"] not in ("resolved", "dead_ended")]
    return sum(1 for t in active if is_overdue(t["state"], t["next_action_due"], AS_OF))


def _empty_copy_of(src):
    """src → 临时副本并清空所有表（schema 保留），返回路径（复用 test_smoke 模式）。"""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    shutil.copy(src, path)
    c = sqlite3.connect(path)
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        try:
            c.execute(f"DELETE FROM {t}")
        except sqlite3.Error:
            pass
    c.commit()
    c.close()
    return path


def main():
    md5_before = hashlib.md5(open(DB, "rb").read()).hexdigest()

    # ===== ① 分组契约（纯函数层）=====
    print("== ① 两导航面 · 各角色 tab 归组契约 ==")
    check("SURFACES 为 (work, control) 且 work 在前（默认落地面）", SURFACES == ("work", "control"))
    check("导航面标签为 工作台/控制室",
          SURFACE_LABELS == {"work": "工作台", "control": "控制室"}, str(SURFACE_LABELS))
    w, c = set(SURFACE_TABS["work"]), set(SURFACE_TABS["control"])
    check("两组不相交（一个 tab 只属一面）", not (w & c), str(w & c))
    check("两组并集 == 全部 11 个 tab（不漏）", (w | c) == ALL_TABS, str((w | c) ^ ALL_TABS))
    check("工作台组=操作型 6 tab（risk/task/coord/cost/po/adm）",
          w == {"risk", "task", "coord", "cost", "po", "adm"}, str(w))
    check("控制室组=理解监督 5 tab（kpi/obj/kg/dq/log）",
          c == {"kpi", "obj", "kg", "dq", "log"}, str(c))
    for r in ROLES:
        check(f"{r} 工作台面与契约一致", visible_tabs(r, "work") == EXPECTED_WORK[r],
              f"{visible_tabs(r, 'work')} != {EXPECTED_WORK[r]}")
        check(f"{r} 控制室面与契约一致", visible_tabs(r, "control") == EXPECTED_CONTROL[r],
              f"{visible_tabs(r, 'control')} != {EXPECTED_CONTROL[r]}")
    for r in ROLES:
        full = visible_tabs(r)
        merged = sorted(visible_tabs(r, "work") + visible_tabs(r, "control"))
        check(f"{r} 两面并集 == 分面前全集（可见性零变化）", merged == sorted(full),
              f"{merged} != {sorted(full)}")
        for surf in SURFACES:
            sub = visible_tabs(r, surf)
            order = [k for k in full if k in set(sub)]
            check(f"{r}/{surf} 保持 ROLE_WORKSPACE 原相对顺序", sub == order, f"{sub} != {order}")
    check("未知 surface 回退全集（不误藏 tab）", visible_tabs("ops", "nope") == visible_tabs("ops"))
    check("未知角色回退经理全量（surface 维度同样成立）",
          visible_tabs("intern", "control") == visible_tabs("manager", "control"))

    # ===== ①b 控制室三问句合并组（纯函数层）=====
    print("\n== ①b 控制室三问句合并组 · 组契约 + 可见性=成员并集 ==")
    grp_members = [t for g in CONTROL_GROUP_ORDER for t in CONTROL_GROUPS[g]]
    check("组序为 全局概览→追查一件事→操作与异常记录",
          CONTROL_GROUP_ORDER == ("overview", "trace", "records"))
    check("组标签为三问句大白话", CONTROL_GROUP_LABELS == EXPECTED_GROUP_LABELS,
          str(CONTROL_GROUP_LABELS))
    check("合并组是控制室 5 成员 tab 的完全划分（不重不漏）",
          sorted(grp_members) == sorted(SURFACE_TABS["control"])
          and len(grp_members) == len(set(grp_members)), str(grp_members))
    check("追查一件事 = obj+kg（对象详情在前，图在后）", CONTROL_GROUPS["trace"] == ("obj", "kg"))
    check("操作与异常记录 = log+dq（审计分节在前，待核对分节在后）",
          CONTROL_GROUPS["records"] == ("log", "dq"))
    check("全局概览 = 原 kpi 改名（TAB_LABELS 同步）",
          CONTROL_GROUPS["overview"] == ("kpi",) and TAB_LABELS["kpi"] == "全局概览")
    check("dq 的 TAB_LABELS 已去黑话（待核对的数据）", TAB_LABELS["dq"] == "待核对的数据")
    for r in ROLES:
        got = visible_control_groups(r)
        check(f"{r} 可见组与冻结契约一致", got == EXPECTED_CONTROL_GROUPS[r],
              f"{got} != {EXPECTED_CONTROL_GROUPS[r]}")
        # 组可见性 == 成员可见性并集（性质级双算：不依赖冻结表）
        derived = [(g, [t for t in CONTROL_GROUPS[g] if t in set(visible_tabs(r, "control"))])
                   for g in CONTROL_GROUP_ORDER]
        derived = [(g, m) for g, m in derived if m]
        check(f"{r} 组可见=成员并集（零放宽零收紧）", got == derived, f"{got} != {derived}")

    # ===== ② 「我的今天」计数 == 对应 tab 内的数（双路对账 + seed 冻结数字）=====
    print("\n== ② 我的今天 · 计数与各 tab 双路对账 + seed 冻结数字 ==")
    con = _conn()
    for r in ROLES:
        cards = build_my_today(con, r, AS_OF)
        got = [(x["key"], x["count"]) for x in cards]
        check(f"{r} 卡集合与 seed 契约一致 {EXPECTED_CARDS[r]}", got == EXPECTED_CARDS[r], str(got))
        for x in cards:
            tab_val = {"risk": lambda: tab_risk_count(con, r),
                       "dq": lambda: tab_dq_count(con),
                       "approve": lambda: tab_pending_approval_count(con),
                       "coord": lambda: tab_coord_overdue_count(con)}[x["key"]]()
            check(f"{r} 卡「{x['label']}」计数 == 对应 tab 独立复算（{x['count']}）",
                  x["count"] == tab_val, f"card={x['count']} tab={tab_val}")
    # 卡出现条件与引导可达性：卡的 tab 角色可见；引导文字指向真实入口——工作台 tab 用其
    # TAB_LABELS 标签，控制室成员 tab 用其三问句合并组标签（成员不再独立成 tab）+ 注明控制室
    card_tab = {"risk": "risk", "dq": "dq", "approve": "task", "coord": "coord"}
    for r in ROLES:
        for x in build_my_today(con, r, AS_OF):
            tkey = card_tab[x["key"]]
            check(f"{r} 卡「{x['label']}」对应 tab 该角色可见（引导可达）", tkey in visible_tabs(r))
            if tkey in SURFACE_TABS["control"]:
                glabel = CONTROL_GROUP_LABELS[CONTROL_GROUP_OF[tkey]]
                check(f"{r} 卡「{x['label']}」引导含合并组标签「{glabel}」",
                      glabel in x["go"], x["go"])
                check(f"{r} 卡「{x['label']}」引导注明控制室（跨面跳转说清楚）", "控制室" in x["go"], x["go"])
            else:
                check(f"{r} 卡「{x['label']}」引导含 tab 标签「{TAB_LABELS[tkey]}」",
                      TAB_LABELS[tkey] in x["go"], x["go"])
    check("sales 无聚合卡（risk/dq/coord/task 皆不可见——最保守读法）",
          build_my_today(con, "sales", AS_OF) == [])
    check("审批卡仅 manager（cs/ops/procurement 有 task tab 也不出现）",
          all("approve" not in [x["key"] for x in build_my_today(con, r, AS_OF)]
              for r in ROLES if r != "manager"))
    # 显式 risk_mode 覆盖（跟随风险队列radio）：ops 切「全部」== manager 全量口径
    check("ops risk_mode='all' == 全量 76（radio 切「全部」后卡随 tab 变）",
          count_my_risks(con, "ops", "all") == 76, str(count_my_risks(con, "ops", "all")))
    # 四个底层计数函数与 seed 冻结数字（独立于卡组装）
    check("count_my_risks(manager)=76", count_my_risks(con, "manager") == 76)
    check("count_open_dq=11", count_open_dq(con) == 11)
    check("count_pending_approvals=3", count_pending_approvals(con) == 3)
    check("count_overdue_coordinations=2", count_overdue_coordinations(con, AS_OF) == 2)
    con.close()

    # 区域过滤真实生效：临时副本把一票 open 风险的目的港翻成 CN → ops(US 区域) 计数下降，manager 不变
    tmp = tempfile.mkstemp(suffix=".sqlite")[1]
    shutil.copy(DB, tmp)
    tc = _conn(tmp)
    row = tc.execute("""SELECT s.shipment_id, count(*) k FROM risk_events r
                        JOIN shipments s ON s.shipment_id=r.shipment_id
                        WHERE r.status NOT IN ('resolved','escalated')
                        GROUP BY s.shipment_id LIMIT 1""").fetchone()
    k = row["k"]
    tc.execute("UPDATE shipments SET destination_port_locode='CNSHA' WHERE shipment_id=?",
               (row["shipment_id"],))
    tc.commit()
    check(f"CN 翻转后 ops 区域计数 76-{k}（region 过滤真实生效）",
          count_my_risks(tc, "ops") == 76 - k, str(count_my_risks(tc, "ops")))
    check("CN 翻转后 manager 全量仍 76（all 不受区域影响）", count_my_risks(tc, "manager") == 76)
    tc.close()
    os.remove(tmp)

    # ===== ③/④ AppTest：7 角色 × 两面（真实库）=====
    print("\n== ③/④ AppTest · 7 角色 × 两导航面（0 异常 + 标识行/我的今天/tab 标签分组）==")
    from streamlit.testing.v1 import AppTest
    for r in ROLES:
        for surf in SURFACES:
            at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
            at.session_state["role"] = r
            at.session_state["nav_surface"] = surf
            at.run()
            excs = list(at.exception)
            check(f"④ {r} × {SURFACE_LABELS[surf]} 0 未捕获异常", not excs, str(excs[:1]))
            labels = [t.label for t in at.tabs]
            if surf == "work":
                exp = [TAB_LABELS[k] for k in EXPECTED_WORK[r]]
            else:  # 控制室：三问句合并组标签（成员 tab 不再独立呈现）
                exp = [CONTROL_GROUP_LABELS[g] for g, _m in EXPECTED_CONTROL_GROUPS[r]]
            check(f"④ {r} × {SURFACE_LABELS[surf]} tab 标签与契约一致", labels == exp,
                  f"{labels} != {exp}")
            md = " ||| ".join((m.value or "") for m in at.markdown)
            md_cap = md + " ||| " + " ||| ".join((c.value or "") for c in at.caption)
            if surf == "control":
                check(f"③ {r} 控制室标识行存在（理解与监督/处置请回工作台）",
                      "理解与监督视图" in md and "处置请回工作台" in md)
                check(f"③ {r} 控制室无「我的今天」（聚合卡属于工作台首屏）", "我的今天" not in md)
                check(f"③ {r} 追查一件事有「选一件事…来龙去脉」引导行",
                      "选一件事" in md_cap and "来龙去脉" in md_cap)
                check(f"③ {r} 控制室用户侧无旧黑话（DQ 处置/DQ issue/KPI 总览）",
                      all(w not in md_cap for w in ("DQ 处置", "DQ issue", "KPI 总览")))
                if "log" in set(visible_tabs(r, "control")):
                    check(f"③ {r} 操作与异常记录含「谁在什么时候做了什么」分节标题",
                          "谁在什么时候做了什么" in md)
                if "dq" in set(visible_tabs(r, "control")):
                    check(f"③ {r} 操作与异常记录含「待核对的数据」分节标题", "待核对的数据" in md)
                else:
                    check(f"③ {r} 无 dq 权限则无「待核对的数据」分节（组内只渲染有权成员）",
                          "待核对的数据" not in md)
            else:
                check(f"③ {r} 工作台有「我的今天」首屏区", "我的今天" in md)
                check(f"③ {r} 工作台无控制室标识行", "理解与监督视图" not in md)
            if r == "manager" and surf == "work":
                strip = next((m.value for m in at.markdown if "待我处理的风险" in (m.value or "")), "")
                nums = dict(re.findall(r"<span>([^<]+)</span><strong>(\d+)</strong>", strip))
                check("③ manager 首屏卡 UI 数字 = 76/11/3（与 tab 一致的渲染真值）",
                      nums.get("待我处理的风险") == "76" and nums.get("待我核对的数据") == "11"
                      and nums.get("待我批准的提案") == "3", str(nums))

    # ===== ⑤ 空库 × 控制室 7 角色 0 异常（补 test_smoke 分面后的空库覆盖缺口）=====
    print("\n== ⑤ 空库（0 行）× 控制室 · 7 角色 AppTest（优雅降级）==")
    bak = tempfile.mkstemp(suffix=".sqlite")[1]
    shutil.copy(DB, bak)
    try:
        empty = _empty_copy_of(DB)
        shutil.copy(empty, DB)
        for r in ROLES:
            at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
            at.session_state["role"] = r
            at.session_state["nav_surface"] = "control"
            at.run()
            excs = list(at.exception)
            check(f"⑤ 空库 {r} × 控制室 0 未捕获异常", not excs, str(excs[:1]))
        os.remove(empty)
    finally:
        shutil.copy(bak, DB)
        os.remove(bak)

    # ===== ⑥ 红线：权限矩阵不动 + 真实库 md5 不变 =====
    print("\n== ⑥ 红线：ROLE_PERMS 基线一致 + 真实 DB 逐字节未动 ==")
    check("ROLE_PERMS 与基线完全一致（分面纯呈现层，硬 gate 未削弱）",
          ROLE_PERMS == EXPECTED_ROLE_PERMS, str(ROLE_PERMS))
    check("ROLE_WORKSPACE 全集未因分面改变（7 角色可见 tab 不增不减）",
          {r: sorted(v) for r, v in ROLE_WORKSPACE.items()}
          == {r: sorted(EXPECTED_WORK[r] + EXPECTED_CONTROL[r]) for r in ROLES})
    md5_after = hashlib.md5(open(DB, "rb").read()).hexdigest()
    check("真实 DB md5 逐字节一致（纯读；空库 swap 已还原）", md5_before == md5_after,
          f"{md5_before} != {md5_after}")

    print(f"\n{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（CN 翻转/空库均在临时副本或已还原的 swap 上进行，data/ontology.sqlite 未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
