"""行级数据范围脚本测试：python3 -m app.test_data_scope

验证 app/data_scope（纯函数 + 对真实库临时副本的视图级过滤）：
① scope_predicate 逐 role/mode 返回正确过滤（manager=all 无过滤；ops mine=仅自己 assignee）
② 对真实库：manager 任务数 ≥ 某 ops actor「我的任务」数，且「我的任务」⊆ 全部（严格子集）
③ mode=all 恢复全集（python 谓词 与 SQL WHERE 两条路等价）
④ 过滤是视图级：跑前后 tasks 表行数不变（不删数据）
⑤ ROLE_PERMS 断言不变（未改动作层）

不改任何对象/规则/KPI；只读断言 + 临时副本。
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import yaml

from .actions import ROLE_PERMS, assign_task
from .data_scope import (MODE_LABELS, default_mode, region_of_locode,
                         resolve_actor, risk_in_region_scope, scope_for_role,
                         scope_predicate)

FAILS = []
ROLES = ["ops", "cs", "finance", "sales", "compliance", "manager"]

EXPECTED_ROLE_PERMS = {  # manual §6 + cost-manual §5——本次不得改动
    "AssignTask": {"ops", "system"},
    "ProposeMitigation": {"ops", "cs", "finance"},
    "ApproveMitigation": {"manager"},
    "CloseRiskEvent": {"ops"},
}


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _fetch_tasks(con):
    return [dict(r) for r in con.execute("SELECT * FROM tasks").fetchall()]


def _sql_scoped(con, scope):
    where, params = scope.task_where("t")
    return {r["task_id"] for r in con.execute(
        f"SELECT t.task_id FROM tasks t WHERE {where}", params)}


def main():
    print("== ① 纯谓词：逐 role/mode 过滤正确 ==")
    # manager 强制 all、无过滤、label=Global
    msc = scope_for_role("manager")
    check("manager 默认 all（不受限）", msc.mode == "all")
    check("manager label = Global", msc.label == "Global", msc.label)
    check("manager matches_task 恒 True",
          all(msc.matches_task(t) for t in [
              {"assignee_user_id": "u-ops-us", "assignee_team_id": "team-ops-us"},
              {"assignee_user_id": None, "assignee_team_id": None}]))

    # ops 默认 mine，actor=u-ops-us，region=US
    osc = scope_for_role("ops")
    check("ops 默认 mode = mine", osc.mode == "mine")
    check("ops 解析 actor=u-ops-us / region=US",
          osc.actor_id == "u-ops-us" and osc.region == "US",
          f"{osc.actor_id}/{osc.region}")
    check("ops label = 'US · 我的任务'", osc.label == "US · 我的任务", osc.label)
    mine_task = {"assignee_user_id": "u-ops-us", "assignee_team_id": "team-ops-us"}
    other_task = {"assignee_user_id": "u-cs-us", "assignee_team_id": "team-cs-us"}
    check("ops mine 命中自己 assignee", osc.matches_task(mine_task))
    check("ops mine 排除他人 assignee", not osc.matches_task(other_task))

    # team：同 region 命中（跨角色同区可见）
    tsc = scope_predicate("ops", "u-ops-us", "US", "team")
    check("ops team 命中同 region 他人（team-cs-us）", tsc.matches_task(other_task))
    check("ops team 排除异 region（team-ops-cn）",
          not tsc.matches_task({"assignee_team_id": "team-ops-cn"}))

    # all：恒命中
    asc = scope_predicate("ops", "u-ops-us", "US", "all")
    check("ops all 恒命中（含无 assignee 行）",
          asc.matches_task(mine_task) and asc.matches_task({"assignee_user_id": None}))
    check("ops all label = 'US · 全部'", asc.label == "US · 全部", asc.label)

    # 非法 mode 回退默认；roster 无 owner 的角色（sales）actor=None → mine 恒 False
    check("非法 mode 回退角色默认", scope_predicate("ops", "u-ops-us", "US", "x").mode == "mine")
    ssc = scope_for_role("sales")
    check("sales 无 roster owner → actor_id=None", ssc.actor_id is None)
    check("MODE_LABELS 覆盖三 mode", set(MODE_LABELS) == {"mine", "team", "all"})

    print("== ⑤ 动作层权限（ROLE_PERMS）未被改动 ==")
    check("ROLE_PERMS 与基线完全一致（硬 gate 未削弱）",
          ROLE_PERMS == EXPECTED_ROLE_PERMS, str(ROLE_PERMS))

    print("== region 辅助（风险按目的地 region 过滤）==")
    check("region_of_locode USLAX → US", region_of_locode("USLAX") == "US")
    check("region_of_locode CNYTN → CN", region_of_locode("CNYTN") == "CN")
    check("risk all 模式不挡任何风险（保 demo 走查）",
          risk_in_region_scope(scope_for_role("manager"), "USLAX"))
    check("risk 本区域：US actor 命中 US 目的地",
          risk_in_region_scope(scope_predicate("ops", "u-ops-us", "US", "team"), "USLAX"))

    print("== ②③④ 对真实库临时副本：视图级过滤 + 子集 + 全集恢复 ==")
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    as_of = cfg["window"]["as_of"]
    tmp = Path(tempfile.mkdtemp()) / "scope.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    open_rids = [r["risk_event_id"] for r in con.execute(
        "SELECT risk_event_id FROM risk_events WHERE status='open' ORDER BY risk_event_id LIMIT 8")]
    # 4 个派给 ops（→u-ops-us），2 个派给 cs（→u-cs-us）；actor 均为 ops 角色（AssignTask 权限）
    for rid in open_rids[:4]:
        assign_task(con, rid, "ops", "P2", as_of, actor="daniel", role="ops", as_of=as_of)
    for rid in open_rids[4:6]:
        assign_task(con, rid, "cs", "P2", as_of, actor="daniel", role="ops", as_of=as_of)

    rows_before = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
    all_tasks = _fetch_tasks(con)
    check("测试数据已建（6 个任务）", len(all_tasks) == 6, str(len(all_tasks)))

    ops_scope = scope_for_role("ops")            # mine
    mgr_scope = scope_for_role("manager")        # all
    ops_mine = [t for t in all_tasks if ops_scope.matches_task(t)]
    mgr_all = [t for t in all_tasks if mgr_scope.matches_task(t)]

    check("② manager(all) 任务数 ≥ ops(mine) 任务数",
          len(mgr_all) >= len(ops_mine), f"{len(mgr_all)} vs {len(ops_mine)}")
    check("② ops mine ⊆ 全部",
          {t["task_id"] for t in ops_mine} <= {t["task_id"] for t in all_tasks})
    check("② ops mine 严格子集（有 cs 任务不归 ops）",
          0 < len(ops_mine) < len(all_tasks), f"{len(ops_mine)}/{len(all_tasks)}")
    check("② ops mine 全部 assignee = u-ops-us",
          all(t["assignee_user_id"] == "u-ops-us" for t in ops_mine))

    # ③ mode=all 恢复全集；python 谓词 与 SQL WHERE 等价
    ops_all_scope = scope_predicate("ops", "u-ops-us", "US", "all")
    ops_all = [t for t in all_tasks if ops_all_scope.matches_task(t)]
    check("③ mode=all 恢复全集", len(ops_all) == len(all_tasks))
    check("③ python 谓词 与 SQL WHERE 等价（ops mine）",
          {t["task_id"] for t in ops_mine} == _sql_scoped(con, ops_scope))
    check("③ python 谓词 与 SQL WHERE 等价（ops team=同区全集）",
          {t["task_id"] for t in all_tasks
           if scope_predicate('ops', 'u-ops-us', 'US', 'team').matches_task(t)}
          == _sql_scoped(con, scope_predicate('ops', 'u-ops-us', 'US', 'team')))
    check("③ manager all SQL 命中全集", _sql_scoped(con, mgr_scope) ==
          {t["task_id"] for t in all_tasks})

    # ④ 过滤是视图级：跑前后 tasks 表行数不变
    rows_after = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
    check("④ 过滤前后 tasks 表行数不变（只过滤不删）",
          rows_before == rows_after == 6, f"{rows_before}->{rows_after}")
    con.close()

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试，data/ontology.sqlite 未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
