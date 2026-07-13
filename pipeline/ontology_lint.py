#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥1：本体一致性闸门 (V5 决议①，plan v0.2 §4 V5 条目 / control-tower-plan-v0.2.md:301)。

问题：本体 JSON（ontology/control-tower-ontology.json，34 对象 / 53 关系 / 31 动作 /
状态机 / 敏感字段规则）目前只被展示层引用，引擎/动作/AI 层零引用——声明（本体）与
运行时（SQLite 表、app 权限字典、agent 工具清单）是"两张皮"，一致性靠人的纪律维持。

本文件是一个**纯只读的自动检查员**：把本体声明与运行时实现逐条比对，任何不一致都能被
机器发现，并在 `--strict` 模式下以退出码 1 阻断发布。四类断言：

    A 对象↔表结构   objects[].properties  ↔  SQLite 实际表列
    B 动作↔权限     actions[].executors   ↔  app/*.py 的角色权限字典（主：actions.py ROLE_PERMS）
    C 动作↔AI 工具   agent/tools.py 的 TOOL_DEFS / FORBIDDEN_TOOLS  ↔  本体 actions
    D 关系↔外键     links[].source/target ↔  数据库实际列（外键列存在性）

运行：
    python3 -m pipeline.ontology_lint            # 报告模式（默认）：打印差异，退出码 0
    python3 -m pipeline.ontology_lint --strict   # 闸门模式：有差异即退出码 1

═══════════════════════════════════════════════════════════════════════════════
映射契约（本工具的核心假设——桥2 要固化的正是这些映射规则；此处逐条写清，
供复现与后续机制化）：

  [M1] 对象类型 → 表名：
       优先用本体对象的显式 `table` 字段（当前仅 RFQ→rfqs / RFQLine→rfq_lines /
       Quote→quotes 三个缩写词声明了显式表名，避免 R_F_Q 误拆）；否则按算法派生：
       PascalCase 型名 → snake_case → 末词复数化（y→ies，s/x/z/ch/sh→es，否则 +s）。
       这条规则复刻自 pipeline/build_ontology.py 里 34 处 `table("<名>", ...)` 硬编码调用，
       实测对全部 34 个对象类型均解析到真实存在的表（见 build_ontology.py:442-657）。
       —— 注意：build_ontology.py 里表名是硬编码字符串、无单一派生函数可 import；
       本工具的派生规则是对那份硬编码映射的**重建**，故对每个派生名都再核验其在 DB 中
       确实存在，派生不出或表不存在则报为差异而非崩溃。

  [M2] 动作 → 权限字典键：本体 action.name 直接等于代码权限字典的键
       （AssignTask/ProposeMitigation/ApproveMitigation/CloseRiskEvent 即 ROLE_PERMS 的 4 键）。
       任务点名的主对象是 app/actions.py 的 ROLE_PERMS；但运行时权限分散在 5 个字典
       （actions.py ROLE_PERMS / admission_actions.py ADM_PERMS / procurement_actions.py
       PROC_PERMS / warehouse_actions.py WH_PERMS / coordination_actions.py COORD_PERMS）。
       本工具以 ROLE_PERMS 为主、其余 4 个为扩展交叉核对（每条差异标明来自哪个字典哪个文件）。
       协调域 6 动作 A20-A25 共用一个组权限键 ManageCoordination（组权限，非每动作一键）。
       executors 里的括号注解（如 "compliance (more_info)"）在比对时被剥离，只取角色名。

  [M3] AI 工具 → 动作：写工具名 = snake_case(action.name)
       （assign_task→AssignTask 等）。读工具（list_*/get_*/explain_*）是**对象查询工具**，
       本体的 31 个 action 全是写/变更动作、无"读动作"，故读工具溯源到对象而非动作（预期，不算差异）。
       冻结区动作（审批/关闭/拒接）判定：动作名以 Approve/Close/Reject 开头者视为冻结区，
       它们**不得**出现在 TOOL_DEFS，且核心 4 个应在 FORBIDDEN_TOOLS 显式拉黑。

  [M4] 关系 → 外键列：
       N:1（source→target）外键落在 source 表，列名 = target 主键；
       1:N（source→target）外键落在 target 表，列名 = source 主键；
       N:M 存为 source 对象上的 json_list 列（命名多为 affected_<target>_ids）或联结表。
       SQLite 未声明 FOREIGN KEY 约束（核对深度=**列名存在性**，非引用完整性）；
       标准列名缺失时做二次尽力扫描（反向 json_list / 软命名列如 destination_warehouse），
       找到即报【漂移】、彻底找不到才报【缺失】。

读写边界（红线）：数据库一律以 `mode=ro` 只读 URI 打开；app/agent 的 Python 常量一律用
`ast.literal_eval` 静态解析源码提取，**从不 import/执行**任何 app 或 agent 模块——保证本工具
对运行时零副作用、绝不修改 ROLE_PERMS / FORBIDDEN_TOOLS / 任何现有代码。
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径（相对仓库根，脚本可从任意目录运行）
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY_PATH = REPO_ROOT / "ontology" / "control-tower-ontology.json"
DB_PATH = REPO_ROOT / "data" / "ontology.sqlite"
ACTIONS_PATH = REPO_ROOT / "app" / "actions.py"
ADMISSION_PATH = REPO_ROOT / "app" / "admission_actions.py"
PROCUREMENT_PATH = REPO_ROOT / "app" / "procurement_actions.py"
WAREHOUSE_PATH = REPO_ROOT / "app" / "warehouse_actions.py"
COORDINATION_PATH = REPO_ROOT / "app" / "coordination_actions.py"
TOOLS_PATH = REPO_ROOT / "agent" / "tools.py"

# 差异种类
MISSING = "缺失"   # 本体声明了、运行时找不到
EXTRA = "多余"     # 运行时存在、本体未声明
DRIFT = "漂移"     # 两侧都在但对不上（角色集不同 / 主键不符 / 列名/类型偏移）


@dataclass
class Diff:
    """一条可复现的差异：section=A/B/C/D，kind=缺失/多余/漂移，entity=涉及的对象/动作/关系/表，
    detail=核对了哪个文件哪个字段 vs 哪张表哪列（写清以便复现）。"""
    section: str
    kind: str
    entity: str
    detail: str


# ===========================================================================
# 通用小工具
# ===========================================================================
def camel_to_snake(name: str) -> str:
    """PascalCase / camelCase → snake_case（保守处理连续大写缩写）。"""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


def pluralize(word: str) -> str:
    """英语规则末词复数化（见映射契约 M1）。"""
    if word.endswith("y") and (len(word) < 2 or word[-2] not in "aeiou"):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def object_table_name(obj: dict) -> str:
    """对象类型 → 表名（映射契约 M1）：显式 table 字段优先，否则算法派生。"""
    if obj.get("table"):
        return obj["table"]
    return pluralize(camel_to_snake(obj["type"]))


def sqlite_affinity(decl_type: str) -> str:
    """按 SQLite 官方类型亲和规则，从声明类型字符串推断亲和类。"""
    t = (decl_type or "").upper()
    if "INT" in t:
        return "INTEGER"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if t == "" or "BLOB" in t:
        return "BLOB"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def expected_affinities(onto_type: str) -> set[str]:
    """本体属性类型 → 可接受的 SQLite 亲和类集合（best-effort，见 A 的局限说明）。"""
    if onto_type in ("string", "enum", "date", "datetime", "json_list", "json"):
        return {"TEXT"}
    if onto_type in ("number", "integer", "float"):
        return {"INTEGER", "REAL", "NUMERIC"}
    if onto_type == "boolean":
        # SQLite 无布尔型；0/1 存 INTEGER、'true'/'false' 存 TEXT 都常见，两者皆接受（避免噪声）。
        return {"INTEGER", "NUMERIC", "TEXT"}
    return set()  # 未知本体类型：不做硬判定


def strip_role_annotation(executor: str) -> str:
    """'compliance (more_info)' → 'compliance'（剥离 executors 里的括号注解，见 M2）。"""
    return executor.split("(")[0].strip()


def extract_module_literal(path: Path, name: str):
    """静态（AST）解析源码文件，取出顶层赋值 `name = <字面量>` 的值。
    只用 ast.literal_eval，从不执行模块——这是本工具"只读、零副作用"的关键保证。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found as a top-level literal in {path}")


def extract_tool_names(path: Path) -> list[str]:
    """从 agent/tools.py 的 TOOL_DEFS（list[dict]）静态提取每个工具的 name。"""
    tool_defs = extract_module_literal(path, "TOOL_DEFS")
    return [t["name"] for t in tool_defs]


# ===========================================================================
# 加载本体与数据库结构
# ===========================================================================
def load_ontology() -> dict:
    return json.loads(ONTOLOGY_PATH.read_text(encoding="utf-8"))


def load_db_schema(con: sqlite3.Connection) -> dict[str, list[tuple]]:
    """{table: [(col_name, decl_type, is_pk), ...]}——一次性抽出全部表的列信息。"""
    cur = con.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    schema = {}
    for t in tables:
        # PRAGMA 不支持参数占位；表名来自 sqlite_master 本身，非外部输入，安全。
        info = cur.execute(f"PRAGMA table_info({t})").fetchall()
        schema[t] = [(r[1], r[2], r[5]) for r in info]  # name, type, pk-order(>0 表示主键)
    return schema


# ===========================================================================
# 断言 A：对象 ↔ 表结构
# ===========================================================================
def assert_a_objects_tables(onto: dict, schema: dict) -> tuple[list[Diff], list[str]]:
    diffs: list[Diff] = []
    notes: list[str] = []
    for obj in onto["objects"]:
        otype = obj["type"]
        table = object_table_name(obj)
        cols = schema.get(table)
        if cols is None:
            diffs.append(Diff("A", MISSING, otype,
                              f"对象 {otype} 按映射规则解析到表 `{table}`，但数据库中不存在该表"))
            continue
        col_types = {c[0]: c[1] for c in cols}
        col_names = set(col_types)
        db_pks = [c[0] for c in cols if c[2] and c[2] > 0]

        props = {p["name"]: p for p in obj["properties"]}
        prop_names = set(props)

        # 缺列：本体声明了属性但表无对应列
        for missing in sorted(prop_names - col_names):
            diffs.append(Diff("A", MISSING, otype,
                              f"本体属性 {otype}.{missing} 在表 `{table}` 无对应列"))
        # 多列：表有列但本体未声明为属性
        for extra in sorted(col_names - prop_names):
            diffs.append(Diff("A", EXTRA, otype,
                              f"表 `{table}` 有列 `{extra}`，本体对象 {otype} 未声明该属性"))
        # 主键不符
        onto_pk = obj.get("primaryKey")
        if onto_pk and db_pks != [onto_pk]:
            diffs.append(Diff("A", DRIFT, otype,
                              f"主键不符：本体 {otype}.primaryKey=`{onto_pk}`，表 `{table}` 实际主键={db_pks}"))
        # 类型漂移（best-effort，仅对两侧都存在的列判定）
        for name in sorted(prop_names & col_names):
            onto_type = props[name].get("type")
            exp = expected_affinities(onto_type)
            if not exp:
                continue
            aff = sqlite_affinity(col_types[name])
            if aff not in exp:
                diffs.append(Diff("A", DRIFT, otype,
                                  f"类型漂移(尽力)：{otype}.{name} 本体类型={onto_type}，"
                                  f"表 `{table}`.`{name}` 声明={col_types[name] or '(无)'}(亲和={aff})"))
    notes.append("类型漂移为 best-effort：SQLite 用类型亲和、非严格类型；boolean 因 SQLite 无布尔型，"
                 "0/1(INTEGER) 与 'true'/'false'(TEXT) 均视为兼容、不报漂移。")
    notes.append("『多余』列多为 FK/派生/去规范化列（如 skus.supplier_id 承载 supplier_provides 关系、"
                 "shipments.po_ids 为一票多 PO 的反向 json 列），是实现细节未登记进本体，非数据错误。")
    return diffs, notes


# ===========================================================================
# 断言 B：动作 ↔ 权限
# ===========================================================================
# 5 个运行时权限字典（键=动作名或组名，值=角色集）。ROLE_PERMS 是任务点名的主字典，其余为扩展交叉核对。
PERM_DICTS_SPEC = [
    ("ROLE_PERMS", ACTIONS_PATH, "app/actions.py"),
    ("ADM_PERMS", ADMISSION_PATH, "app/admission_actions.py"),
    ("PROC_PERMS", PROCUREMENT_PATH, "app/procurement_actions.py"),
    ("WH_PERMS", WAREHOUSE_PATH, "app/warehouse_actions.py"),
    ("COORD_PERMS", COORDINATION_PATH, "app/coordination_actions.py"),
]
# 协调域组权限键 → 覆盖的动作名（A20-A25 共用 ManageCoordination）。
COORD_GROUP_KEY = "ManageCoordination"
COORD_ACTIONS = {"OpenCoordination", "RecordOutreach", "RecordResponse",
                 "EscalateCoordination", "ResolveCoordination", "MarkDeadEnded"}
# approve_mitigation 提案子类型动作：经 propose→approve maker-checker 流执行，无独立权限键（预期，不报缺失）。
PROPOSAL_SUBTYPE_ACTIONS = {"SuggestSubstitution", "AdjustInventory", "InitiateSecondSource",
                            "BlockNonPoPayment", "BackfillPo"}


def load_perm_dicts() -> tuple[dict, dict]:
    """返回 (action_name→(role_set, dict_name, file) 的统一映射, {dict_name: 原始字典})。"""
    raw = {}
    unified = {}
    for dict_name, path, rel in PERM_DICTS_SPEC:
        d = extract_module_literal(path, dict_name)
        raw[dict_name] = d
        for key, roles in d.items():
            role_set = {strip_role_annotation(r) for r in roles}
            if key == COORD_GROUP_KEY:
                for act in COORD_ACTIONS:  # 组权限展开到每个协调动作
                    unified[act] = (role_set, dict_name, rel)
            else:
                unified[key] = (role_set, dict_name, rel)
    return unified, raw


def assert_b_actions_perms(onto: dict) -> tuple[list[Diff], list[str]]:
    diffs: list[Diff] = []
    notes: list[str] = []
    unified, raw = load_perm_dicts()

    governed, system_only, proposal, uncovered = [], [], [], []
    for act in onto["actions"]:
        name = act["name"]
        onto_execs = {strip_role_annotation(e) for e in act.get("executors", [])}
        if name in unified:
            code_roles, dict_name, rel = unified[name]
            governed.append((name, dict_name))
            # onto 声明但代码无 → 代码缺失该角色
            for r in sorted(onto_execs - code_roles):
                diffs.append(Diff("B", MISSING, name,
                                  f"本体 action {name}.executors 含角色 `{r}`，但 {dict_name}『{name}』"
                                  f"({rel}) 未授予"))
            # 代码有但 onto 未声明 → 代码多授予
            for r in sorted(code_roles - onto_execs):
                diffs.append(Diff("B", EXTRA, name,
                                  f"{dict_name}『{name}』({rel}) 授予角色 `{r}`，但本体 action "
                                  f"{name}.executors 未声明"))
        elif onto_execs <= {"system"}:
            system_only.append(name)
        elif name in PROPOSAL_SUBTYPE_ACTIONS:
            proposal.append(name)
        else:
            uncovered.append((name, sorted(onto_execs)))

    # 角色枚举核对：运行时权限字典引用的角色 是否都在本体 roles[] 里声明
    onto_roles = set(onto.get("roles", []))
    runtime_roles = set()
    for d in raw.values():
        for roles in d.values():
            runtime_roles |= {strip_role_annotation(r) for r in roles}
    for r in sorted(runtime_roles - onto_roles):
        diffs.append(Diff("B", MISSING, "roles[]",
                          f"运行时权限字典使用角色 `{r}`，但本体 roles[] 未声明"
                          f"（本体 roles={sorted(onto_roles)}）"))

    # 覆盖情况（信息，不计差异）
    notes.append(f"ROLE_PERMS(主) 覆盖动作：{[n for n, dn in governed if dn == 'ROLE_PERMS']}")
    notes.append(f"其余动作由兄弟权限字典管辖："
                 f"{sorted(set(dn for n, dn in governed if dn != 'ROLE_PERMS'))} "
                 f"（ADM/PROC/WH/COORD，逐条已交叉核对）")
    notes.append(f"系统级动作（executors⊆{{system}}，引擎执行、无人类 RBAC 键，预期）：{system_only}")
    notes.append(f"提案子类型动作（经 propose→approve maker-checker 执行，无独立权限键，预期）：{proposal}")
    if uncovered:
        notes.append(f"⚠ 未在任何权限字典找到对应键的动作（可能内联 gate 或尚未接 UI 权限）："
                     f"{[n for n, _ in uncovered]}")
    return diffs, notes


# ===========================================================================
# 断言 C：动作 ↔ AI 工具
# ===========================================================================
FREEZE_PREFIXES = ("Approve", "Close", "Reject")  # 冻结区：审批/关闭/拒接类动作名前缀


def assert_c_actions_tools(onto: dict) -> tuple[list[Diff], list[str]]:
    diffs: list[Diff] = []
    notes: list[str] = []
    tool_names = set(extract_tool_names(TOOLS_PATH))
    forbidden = set(extract_module_literal(TOOLS_PATH, "FORBIDDEN_TOOLS"))

    # 动作名 → snake_case 工具名 的双向映射
    action_by_snake = {camel_to_snake(a["name"]): a["name"] for a in onto["actions"]}
    freeze_actions = {a["name"] for a in onto["actions"]
                      if a["name"].startswith(FREEZE_PREFIXES)}
    freeze_snakes = {camel_to_snake(n) for n in freeze_actions}

    # C.1 暴露的工具能否溯源到本体动作？（读工具溯源到对象，是预期，不算差异）
    read_tools, write_tools_traced, untraceable = [], [], []
    for tool in sorted(tool_names):
        if tool in action_by_snake:
            write_tools_traced.append(f"{tool}→{action_by_snake[tool]}")
        elif re.match(r"^(list_|get_|explain_)", tool):
            read_tools.append(tool)
        else:
            untraceable.append(tool)
    for tool in untraceable:
        diffs.append(Diff("C", EXTRA, tool,
                          f"TOOL_DEFS 暴露工具 `{tool}`，既非读工具(list_/get_/explain_)"
                          f"也无法按 snake_case 溯源到任一本体 action"))

    # C.2 冻结区动作是否确实不在 TOOL_DEFS？
    for snake in sorted(freeze_snakes):
        if snake in tool_names:
            diffs.append(Diff("C", DRIFT, action_by_snake.get(snake, snake),
                              f"冻结区动作对应工具名 `{snake}` 竟出现在 TOOL_DEFS——审批/关闭/拒接"
                              f"类动作绝不应暴露给 AI（致命）"))

    # C.3 FORBIDDEN_TOOLS 覆盖是否完整？
    #   ① FORBIDDEN 里每条是否都能溯源到一个本体冻结区动作（防止拉黑了不存在的工具/拼写漂移）
    for tool in sorted(forbidden):
        if tool not in action_by_snake:
            diffs.append(Diff("C", DRIFT, tool,
                              f"FORBIDDEN_TOOLS 含 `{tool}`，但按 snake_case 溯源不到任一本体 action"
                              f"（拼写漂移或指向已删动作？）"))
        elif tool not in freeze_snakes:
            notes.append(f"FORBIDDEN_TOOLS 的 `{tool}` 溯源到非冻结区动作 "
                         f"{action_by_snake[tool]}（额外拉黑，偏保守，非差异）")
    #   ② 每个冻结区动作是否都已被 FORBIDDEN_TOOLS 显式拉黑（缺一即覆盖不全）
    for snake in sorted(freeze_snakes):
        if snake not in forbidden:
            diffs.append(Diff("C", MISSING, action_by_snake.get(snake, snake),
                              f"冻结区动作 {action_by_snake.get(snake, snake)}（工具名 `{snake}`）"
                              f"未在 FORBIDDEN_TOOLS 显式拉黑（虽未注册进 TOOL_DEFS，建议显式拉黑做纵深防御）"))

    notes.append(f"写工具已溯源到本体动作：{write_tools_traced}")
    notes.append(f"读工具（对象查询，本体无『读动作』故溯源到对象而非动作，预期）：{read_tools}")
    notes.append(f"本体冻结区动作（名以 {FREEZE_PREFIXES} 起）：{sorted(freeze_actions)}；"
                 f"FORBIDDEN_TOOLS={sorted(forbidden)}")
    notes.append("说明：花钱/写库类动作（如 RecordPurchasePayment/BlockNonPoPayment）未注册进 TOOL_DEFS "
                 "即 AI 不可达；FORBIDDEN_TOOLS 是对最易诱导的审批/关闭/拒接 4 动作的显式纵深拉黑。")
    return diffs, notes


# ===========================================================================
# 断言 D：关系 ↔ 外键
# ===========================================================================
def assert_d_links_fks(onto: dict, schema: dict) -> tuple[list[Diff], list[str]]:
    diffs: list[Diff] = []
    notes: list[str] = []
    pk = {o["type"]: o.get("primaryKey") for o in onto["objects"]}
    tbl = {o["type"]: object_table_name(o) for o in onto["objects"]}
    cols_of = {o["type"]: {c[0] for c in schema.get(tbl[o["type"]], [])} for o in onto["objects"]}

    def find_ref(cols: set[str], target_type: str):
        """在列集 cols 里找一个指向 target_type 的承载列，返回 (匹配方式, 列名) 或 (None, None)。
        匹配优先级（均以 target 主键为基准，绝不用无差别的 `_ids` 通配以免误配）：
          exact — 列名 == target 主键（如 supplier_id）；标准外键。
          json  — 列名 == affected_<主键去_id>_ids 或 <主键去_id>_ids（如 affected_so_line_ids / po_ids）；json_list。
          soft  — 列名包含 target 类型全小写蛇名子串（如 Warehouse→destination_warehouse）；软命名。
        """
        tpk = pk[target_type]                                   # 'so_line_id' / 'warehouse_id' / 'po_id'
        stem = tpk[:-3] if tpk.endswith("_id") else tpk         # 'so_line' / 'warehouse' / 'po'
        tsnake = camel_to_snake(target_type)                    # 'sales_order_line' / 'warehouse'
        if tpk in cols:
            return "exact", tpk
        for jv in (f"affected_{stem}_ids", f"{stem}_ids"):
            if jv in cols:
                return "json", jv
        softs = sorted(c for c in cols if tsnake in c)
        if softs:
            return "soft", softs[0]
        return None, None

    for link in onto["links"]:
        lt, s, t, card = link["linkType"], link["source"], link["target"], link.get("cardinality", "")
        if s not in tbl or t not in tbl:
            diffs.append(Diff("D", MISSING, lt,
                              f"关系 {lt} 的端点类型 {s}/{t} 有一端不在本体对象列表"))
            continue

        if card == "N:M":
            # 期望 source 表上一个指向 target 的 json_list 列（本项目约定 affected_<target主键去_id>_ids）。
            how, col = find_ref(cols_of[s], t)
            if how:
                kind = "标准 affected_*_ids" if how == "json" else f"{how} 命名"
                notes.append(f"[N:M] {lt} ({s}→{t}) 由 `{tbl[s]}`.`{col}` 承载（{kind}，json_list）")
            else:
                diffs.append(Diff("D", MISSING, lt,
                                  f"N:M 关系 {lt} ({s}→{t}) 在 `{tbl[s]}` 找不到承载列"
                                  f"（期望 `affected_{(pk[t][:-3] if pk[t].endswith('_id') else pk[t])}_ids` 的 json_list 列）"))
            continue

        # N:1 → 外键落在 source 表指向 target；1:N → 外键落在 target 表指向 source。
        if card == "N:1":
            holder_tbl, holder_type, ref_type = tbl[s], s, t
            inv_tbl, inv_holder_type, inv_ref_type = tbl[t], t, s
        else:  # 1:N
            holder_tbl, holder_type, ref_type = tbl[t], t, s
            inv_tbl, inv_holder_type, inv_ref_type = tbl[s], s, t
        fk_col = pk[ref_type]

        how, col = find_ref(cols_of[holder_type], ref_type)
        if how == "exact":
            continue  # 标准外键列存在——一致
        if how in ("json", "soft"):
            diffs.append(Diff("D", DRIFT, lt,
                              f"关系 {lt} ({s}→{t} {card}) 期望标准外键列 `{holder_tbl}`.`{fk_col}` 不存在；"
                              f"疑由{'软命名' if how == 'soft' else 'json_list'}列 `{holder_tbl}`.`{col}` 承载"
                              f"（列名不符 `<target主键>` 命名约定）"))
            continue
        # holder 上找不到 → 尝试反向表示（如 N:1 PurchaseOrder→Shipment 实际存 shipments.po_ids）
        inv_how, inv_col = find_ref(cols_of[inv_holder_type], inv_ref_type)
        if inv_how:
            diffs.append(Diff("D", DRIFT, lt,
                              f"关系 {lt} ({s}→{t} {card}) 期望外键列 `{holder_tbl}`.`{fk_col}` 不存在；"
                              f"疑以反向表示存于 `{inv_tbl}`.`{inv_col}`（方向/表示与声明的基数相反）"))
        else:
            diffs.append(Diff("D", MISSING, lt,
                              f"关系 {lt} ({s}→{t} {card}) 期望外键列 `{holder_tbl}`.`{fk_col}` 不存在，"
                              f"两端表均未扫到可承载该关系的列"))

    notes.append("核对深度=列名存在性：SQLite 表未声明 FOREIGN KEY 约束，本断言只核对承载列是否存在，"
                 "不验证引用完整性（孤儿行/悬挂引用需另做数据级检查）。")
    return diffs, notes


# ===========================================================================
# 报告
# ===========================================================================
SECTION_TITLES = {
    "A": "A 对象↔表结构  (objects[].properties ↔ SQLite 表列)",
    "B": "B 动作↔权限    (actions[].executors ↔ app/*.py 权限字典，主 ROLE_PERMS)",
    "C": "C 动作↔AI 工具 (agent/tools.py TOOL_DEFS / FORBIDDEN_TOOLS ↔ 本体 actions)",
    "D": "D 关系↔外键    (links[].source/target ↔ 数据库实际列)",
}


def render_report(sections: dict, strict: bool) -> tuple[str, int]:
    lines = []
    onto = load_ontology()
    lines.append("=" * 78)
    lines.append("本体一致性闸门 · ontology_lint （桥1 / V5 决议①）")
    lines.append(f"本体：{ONTOLOGY_PATH.relative_to(REPO_ROOT)}  v{onto.get('version')}  "
                 f"（{len(onto['objects'])} 对象 / {len(onto['links'])} 关系 / {len(onto['actions'])} 动作）")
    lines.append(f"数据库：{DB_PATH.relative_to(REPO_ROOT)}  (只读 mode=ro)")
    lines.append(f"模式：{'strict（有差异→退出码1）' if strict else '报告（退出码0）'}")
    lines.append("=" * 78)

    counts = {}
    total = 0
    for sec in ("A", "B", "C", "D"):
        diffs, notes = sections[sec]
        by_kind = {MISSING: 0, EXTRA: 0, DRIFT: 0}
        for d in diffs:
            by_kind[d.kind] += 1
        counts[sec] = by_kind
        total += len(diffs)

        lines.append("")
        lines.append("─" * 78)
        lines.append(f"【{SECTION_TITLES[sec]}】")
        lines.append(f"  差异 {len(diffs)} 条  ["
                     f"缺失 {by_kind[MISSING]} / 多余 {by_kind[EXTRA]} / 漂移 {by_kind[DRIFT]}]")
        lines.append("─" * 78)
        if diffs:
            for i, d in enumerate(diffs, 1):
                lines.append(f"  {i:>2}. 【{d.kind}】[{d.entity}] {d.detail}")
        else:
            lines.append("  ✓ 无差异")
        if notes:
            lines.append("  · 说明与覆盖情况：")
            for n in notes:
                lines.append(f"      - {n}")

    lines.append("")
    lines.append("=" * 78)
    lines.append("汇总（各类差异计数）：")
    for sec in ("A", "B", "C", "D"):
        c = counts[sec]
        lines.append(f"  {sec}: 缺失 {c[MISSING]} / 多余 {c[EXTRA]} / 漂移 {c[DRIFT]}  "
                     f"= {c[MISSING] + c[EXTRA] + c[DRIFT]} 条")
    lines.append(f"  合计：{total} 条差异")
    lines.append("=" * 78)

    exit_code = 1 if (strict and total > 0) else 0
    if strict:
        lines.append(f"strict 模式：{'发现差异，退出码 1（阻断发布）' if total else '零差异，退出码 0（放行）'}")
    else:
        lines.append("报告模式：退出码 0（差异仅报告、不阻断；接发布闸门请加 --strict）")
    return "\n".join(lines), exit_code


# ===========================================================================
# 入口
# ===========================================================================
def run(strict: bool = False) -> int:
    onto = load_ontology()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # 红线：只读打开
    try:
        schema = load_db_schema(con)
        sections = {
            "A": assert_a_objects_tables(onto, schema),
            "B": assert_b_actions_perms(onto),
            "C": assert_c_actions_tools(onto),
            "D": assert_d_links_fks(onto, schema),
        }
    finally:
        con.close()
    report, exit_code = render_report(sections, strict)
    print(report)
    return exit_code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m pipeline.ontology_lint",
        description="桥1 本体一致性闸门：本体声明 vs 运行时实现 四类只读一致性核对")
    parser.add_argument("--strict", action="store_true",
                        help="有差异即退出码 1（供发布闸门接入）；默认报告模式退出码 0")
    args = parser.parse_args(argv)
    return run(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
