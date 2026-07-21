"""多区域行级数据范围「可见过滤」证明：python3 -m app.test_region_scope

把「region 可见地过滤」从口头声明变成可验证、被测试锁定的属性。在 seed 后的
ontology.sqlite 的临时副本（只读查询，绝不污染真实库）上，实证多区域分区过滤是
真的、可见的、无泄漏的：

① manager=Global 看全部     —— manager 可见集同时含 team-*-us 与 team-ops-cn，且 == 全集
② CN 运营只看 CN            —— CN actor 的 team 谓词 admit 仅 CN、exclude 所有 US（含 team-ops-us）
③ US 运营只看 US            —— US actor 的 team 谓词 admit 仅 US、exclude CN
④ 分区干净无泄漏 + 两区非空 —— US∩CN=∅；US>0 且 CN>0；US∪CN=全集（每 task 恰落一区，无孤儿）
⑤ sql_predicate 与 matches_task 口径一致 —— python 谓词过滤 == SQL LIKE '%-<region>' 过滤（防口径漂移）
⑥ region_of_locode 边界     —— US..→US、CN..→CN、未知/空/None→默认

设计说明（≤5 行「为什么这样建」）：
- 不造数据、不改真值：demo 数据已多区域（US 3 队 + CN 1 队），本测试只做只读断言。
- 复用 test_data_scope 的行构造/SQL 过滤 helper（_fetch_tasks/_sql_scoped）与 data_scope
  的 _task_region/谓词，不另造一套口径——延续 scope_parity 精神，两套过滤必须字字对齐。
- 临时副本 + 幂等种子：即便真实库未 seed 本测试仍自足，且绝不污染 data/ontology.sqlite。
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from datagen.seed_demo_ops import run as seed_demo_ops

from .actions import DEFAULT_DEMO_REGION, DEMO_ROSTER
from .data_scope import (_task_region, region_of_locode, scope_for_role,
                         scope_predicate)
from .test_data_scope import _fetch_tasks, _sql_scoped

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def _actor_in_region(role, region, roster=DEMO_ROSTER):
    """按 role+region 取 roster 第一个 active owner（复用 roster，不硬编码 actor）。"""
    for owner in roster:
        if owner.active and owner.role == role and owner.region == region:
            return owner.actor_id
    return None


def main():
    tmp = Path(tempfile.mkdtemp()) / "region_scope.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    seed_demo_ops(db_path=str(tmp))          # 幂等，在副本上补运营快照
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    all_tasks = _fetch_tasks(con)
    all_ids = {t["task_id"] for t in all_tasks}
    us_tasks = [t for t in all_tasks if _task_region(t) == "US"]
    cn_tasks = [t for t in all_tasks if _task_region(t) == "CN"]

    # 构造三个 scope：manager(all/Global) + US 运营 team + CN 运营 team
    mgr = scope_for_role("manager")                                   # all → Global
    us_actor = _actor_in_region("ops", "US")                          # u-ops-us
    cn_actor = _actor_in_region("ops", "CN")                          # u-ops-cn
    us_scope = scope_predicate("ops", us_actor, "US", "team")
    cn_scope = scope_predicate("ops", cn_actor, "CN", "team")

    print("== 前置：种子已填多区域运营快照 ==")
    check("tasks 非空", len(all_tasks) > 0, str(len(all_tasks)))
    check("US actor 解析 = u-ops-us", us_actor == "u-ops-us", str(us_actor))
    check("CN actor 解析 = u-ops-cn", cn_actor == "u-ops-cn", str(cn_actor))
    check("cn_scope label 携带 CN 区域", cn_scope.label == "CN · 本组", cn_scope.label)

    print("== ① manager=Global 看全部（US ∪ CN 两区域都可见）==")
    mgr_visible = [t for t in all_tasks if mgr.matches_task(t)]
    mgr_ids = {t["task_id"] for t in mgr_visible}
    mgr_regions = {_task_region(t) for t in mgr_visible}
    has_us_team = any(str(t["assignee_team_id"]).endswith("-us") for t in mgr_visible)
    has_cn_team = any(t["assignee_team_id"] == "team-ops-cn" for t in mgr_visible)
    check("manager mode = all / label = Global", mgr.mode == "all" and mgr.label == "Global", mgr.label)
    check("① manager 可见集 == 全部 task", mgr_ids == all_ids, f"{len(mgr_ids)}/{len(all_ids)}")
    check("① manager 同时可见 team-*-us 与 team-ops-cn", has_us_team and has_cn_team,
          f"us_team={has_us_team} cn_team={has_cn_team}")
    check("① manager 可见集覆盖 US 与 CN 两区域", {"US", "CN"} <= mgr_regions, str(sorted(mgr_regions)))

    print("== ② CN 运营只看 CN（admit 仅 CN、exclude 所有 US）==")
    cn_visible = [t for t in all_tasks if cn_scope.matches_task(t)]
    cn_ids = {t["task_id"] for t in cn_visible}
    check("② CN scope admit 仅 CN task", all(_task_region(t) == "CN" for t in cn_visible),
          str(sorted({_task_region(t) for t in cn_visible})))
    check("② CN scope exclude 所有 US task",
          not any(cn_scope.matches_task(t) for t in us_tasks))
    check("② team-ops-us 的 task 对 CN 不可见",
          not any(cn_scope.matches_task(t) for t in all_tasks
                  if t["assignee_team_id"] == "team-ops-us"))
    check("② CN 可见非空（切 CN 队列有真实内容）", len(cn_visible) > 0, str(len(cn_visible)))

    print("== ③ US 运营只看 US（admit 仅 US、exclude CN）==")
    us_visible = [t for t in all_tasks if us_scope.matches_task(t)]
    us_ids = {t["task_id"] for t in us_visible}
    check("③ US scope admit 仅 US task", all(_task_region(t) == "US" for t in us_visible),
          str(sorted({_task_region(t) for t in us_visible})))
    check("③ US scope exclude 所有 CN task",
          not any(us_scope.matches_task(t) for t in cn_tasks))
    check("③ US 可见非空", len(us_visible) > 0, str(len(us_visible)))

    print("== ④ 分区干净无泄漏 + 两区非空 ==")
    check("④ US ∩ CN = ∅（无泄漏）", us_ids & cn_ids == set(), str(us_ids & cn_ids))
    check("④ US 可见 > 0 且 CN 可见 > 0（可见过滤实证）",
          len(us_ids) > 0 and len(cn_ids) > 0, f"US={len(us_ids)} CN={len(cn_ids)}")
    check("④ US ∪ CN = 全集（每 task 恰落一区，无孤儿）",
          us_ids | cn_ids == all_ids, f"{len(us_ids | cn_ids)}/{len(all_ids)}")

    print("== ⑤ sql_predicate 与 matches_task 口径一致（防两套口径漂移）==")
    check("⑤ US: python 谓词 == SQL LIKE '%-us'", us_ids == _sql_scoped(con, us_scope),
          f"py={len(us_ids)} sql={len(_sql_scoped(con, us_scope))}")
    check("⑤ CN: python 谓词 == SQL LIKE '%-cn'", cn_ids == _sql_scoped(con, cn_scope),
          f"py={len(cn_ids)} sql={len(_sql_scoped(con, cn_scope))}")
    check("⑤ manager: python 谓词 == SQL（全集）", mgr_ids == _sql_scoped(con, mgr))

    print("== ⑥ region_of_locode 边界 ==")
    check("⑥ US..→US", region_of_locode("USLAX") == "US")
    check("⑥ CN..→CN", region_of_locode("CNYTN") == "CN")
    check("⑥ 未知(DEHAM)→默认", region_of_locode("DEHAM") == DEFAULT_DEMO_REGION, DEFAULT_DEMO_REGION)
    check("⑥ None→默认", region_of_locode(None) == DEFAULT_DEMO_REGION)
    check("⑥ 空串→默认", region_of_locode("") == DEFAULT_DEMO_REGION)
    con.close()

    print(f"\n实测：manager 可见={len(mgr_ids)}  US 可见={len(us_ids)}  CN 可见={len(cn_ids)}  "
          f"US∩CN={'∅' if not (us_ids & cn_ids) else us_ids & cn_ids}  全集={len(all_ids)}")
    print(f"{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试，data/ontology.sqlite 未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
