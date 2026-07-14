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

  [M2] 动作 → 权限字典键（M1 规则3/4/9 起改为**读本体声明字段**，不再靠内置动作名清单）：
       动作按其 `permission_key` 字段（缺省=name）在 5 个权限字典里查键——协调域 6 动作 A20-A25
       声明 permission_key=ManageCoordination（组权限键）。动作的 `enforcement` 字段声明执行路径：
       role_dict（默认，经权限字典执行，逐条核对 executors）/ engine_internal（引擎内部执行、无人类
       RBAC 键，豁免键要求）/ proposal_flow（经 propose→approve 提案流执行，无独立键，豁免）。
       运行时权限分散在 5 个字典（actions.py ROLE_PERMS / admission_actions.py ADM_PERMS /
       procurement_actions.py PROC_PERMS / warehouse_actions.py WH_PERMS /
       coordination_actions.py COORD_PERMS）；executors 括号注解（如 "compliance (more_info)"）剥离取角色名。

  [M3] AI 工具 → 动作（M1 规则9 起改为**读本体声明字段** exposed_as_tool / ai_executable，
       不再用动作名前缀 Approve/Close/Reject 猜冻结区）：
       写工具集 == snake(exposed_as_tool=true 动作)（assign_task→AssignTask 等）；读工具
       （list_*/get_*/explain_*）是对象查询工具，本体无"读动作"故溯源到对象而非动作（预期，不算差异）；
       冻结区 = ai_executable=frozen 动作，**不得**出现在 TOOL_DEFS，且须在 FORBIDDEN_TOOLS 逐一拉黑
       （FORBIDDEN_TOOLS == snake(ai_executable=frozen 动作)，不多不少）。

  [M4] 关系 → 外键列（M1 规则7 起：links[] 的 `storage` 声明把"重建猜测"变"显式声明"）：
       有 `storage` 声明的关系直接核对声明列（kind=reverse_json/column 等）是否存在；
       `status=declared_only` 的关系豁免"缺失"判定并登记为报告尾部的待裁决豁免（诚实：豁免≠消音）。
       未声明 storage 的关系仍走派生：N:1 外键落在 source 表（列名=target 主键）、1:N 落在 target 表、
       N:M 存 source 对象 json_list 列（命名多为 affected_<target>_ids）；SQLite 未声明 FOREIGN KEY
       约束（核对深度=**列名存在性**，非引用完整性），标准列名缺失时二次尽力扫描反向 json_list / 软命名列，
       找到即报【漂移】、彻底找不到才报【缺失】。

读写边界（红线）：数据库一律以 `mode=ro` 只读 URI 打开；**从不 import/执行任何 app 或 agent 模块**，
对运行时零副作用、绝不修改 ROLE_PERMS / FORBIDDEN_TOOLS / 任何现有代码。
  · 桥2 M2 前：app/agent 的权限/工具常量用 `ast.literal_eval` 静态解析源码字面量提取。
  · 桥2 M2 后：这些常量已从本体解释生成（app/*.py 权限字典、tools.py 的 TOOL_DEFS/FORBIDDEN_TOOLS
    不再是可静态解析的字面量），B/C 断言的运行时侧改为消费同一个生成器 `pipeline.ontology_runtime`
    （pipeline 模块、非 app/agent——红线不破）读『生成后的运行时』，与『生成前的声明』（本体
    executors / exposed_as_tool / ai_executable）比对。两张皮焊死后，这条比对从「抓 drift」
    转为「证单一源」；A/D 断言仍直接读只读库，与生成无关。
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

# 桥2 M2：B/C 断言的运行时侧改为消费本体运行时生成器（app/agent 的权限/工具已从本体生成、
# 不再有可 ast 静态解析的字面量）。ontology_runtime 是 pipeline 模块、非 app/agent——只读红线不破。
from pipeline.ontology_runtime import (build_forbidden_tools as _rt_forbidden_tools,
                                       build_role_perms as _rt_role_perms,
                                       build_tool_defs as _rt_tool_defs)

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
def load_perm_dicts(onto: dict) -> tuple[dict, dict]:
    """返回 (perm_key→(role_set, source, file) 的映射, {source: 权限映射})。
    perm_key = 权限键（含协调域组权限键 ManageCoordination）。

    桥2 M2 前：AST 静态解析 5 个 app/*.py 权限字典的字面量（见 PERM_DICTS_SPEC 记录其provenance）。
    桥2 M2 后：这 5 个字典已从本体解释生成、app/*.py 无字面量可静态解析，故改为消费同一个生成器
    `pipeline.ontology_runtime.build_role_perms`（读 enforcement=role_dict 动作的 executors、按
    permission_key 归组）——即 assert_b 比对的『生成后的运行时』侧。红线仍守（ontology_runtime 是
    pipeline 模块、非 app/agent）。"""
    generated = _rt_role_perms(onto)   # {perm_key: {role,...}}，与运行时 5 字典并集同构
    src, rel = "生成 · ontology_runtime.build_role_perms", "pipeline/ontology_runtime.py"
    by_key = {key: (set(roles), src, rel) for key, roles in generated.items()}
    raw = {src: generated}
    return by_key, raw


def assert_b_actions_perms(onto: dict) -> tuple[list[Diff], list[str]]:
    """M1 规则4/9 机制化：动作的 `enforcement` 字段（role_dict/engine_internal/proposal_flow）
    显式声明执行路径——仅 enforcement=role_dict 的动作要求权限字典有键并逐条核对 executors；
    engine_internal/proposal_flow 由声明豁免（旧的 3 处「登记盲区」从此消解，不再靠内置动作名清单）。"""
    diffs: list[Diff] = []
    notes: list[str] = []
    by_key, raw = load_perm_dicts(onto)

    role_dict_covered, engine_internal, proposal, missing_key = [], [], [], []
    for act in onto["actions"]:
        name = act["name"]
        enforcement = act.get("enforcement", "role_dict")
        onto_execs = {strip_role_annotation(e) for e in act.get("executors", [])}
        if enforcement == "engine_internal":
            engine_internal.append(name)
            continue
        if enforcement == "proposal_flow":
            proposal.append(name)
            continue
        # enforcement == "role_dict"（默认）：必须能按 permission_key 在权限字典找到键
        perm_key = act.get("permission_key", name)
        if perm_key not in by_key:
            diffs.append(Diff("B", MISSING, name,
                              f"动作 {name} 声明 enforcement=role_dict（permission_key=`{perm_key}`），"
                              f"但 5 个权限字典均无此键"))
            missing_key.append(name)
            continue
        code_roles, dict_name, rel = by_key[perm_key]
        role_dict_covered.append((name, perm_key, dict_name))
        # onto 声明但代码无 → 代码缺失该角色
        for r in sorted(onto_execs - code_roles):
            diffs.append(Diff("B", MISSING, name,
                              f"本体 action {name}.executors 含角色 `{r}`，但 {dict_name}"
                              f"『{perm_key}』({rel}) 未授予"))
        # 代码有但 onto 未声明 → 代码多授予
        for r in sorted(code_roles - onto_execs):
            diffs.append(Diff("B", EXTRA, name,
                              f"{dict_name}『{perm_key}』({rel}) 授予角色 `{r}`，但本体 action "
                              f"{name}.executors 未声明"))

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
    notes.append(f"role_dict 动作（enforcement=role_dict，经权限字典执行，逐条核对 executors）："
                 f"{[n for n, _, _ in role_dict_covered]}")
    notes.append(f"engine_internal 动作（引擎内部执行、无人类 RBAC 键，声明化后不再要求权限字典有键）："
                 f"{engine_internal}")
    notes.append(f"proposal_flow 动作（经 propose→approve maker-checker 提案流执行，无独立权限键）："
                 f"{proposal}")
    notes.append("B 类断言机制化（M1 规则4/9）：仅 enforcement=role_dict 要求权限字典有键；"
                 "engine_internal / proposal_flow 由 enforcement 字段显式声明豁免——"
                 "旧的 3 处登记盲区（CreateRiskEvent/RecordPurchasePayment/RecordSupplierQualification）从此消解。")
    return diffs, notes


# ===========================================================================
# 断言 C：动作 ↔ AI 工具
# ===========================================================================
def assert_c_actions_tools(onto: dict) -> tuple[list[Diff], list[str]]:
    """M1 规则9 机制化：不再用动作名前缀（Approve/Close/Reject）猜冻结区，改读本体声明字段——
    「TOOL_DEFS 写工具集 ⊆ snake(exposed_as_tool=true 动作)」且「FORBIDDEN_TOOLS == snake(ai_executable=frozen 动作)」。
    M1 时 tools.py 仍硬编码，断言两侧此刻皆成立即证声明↔运行时一致。"""
    diffs: list[Diff] = []
    notes: list[str] = []
    # 桥2 M2：tools.py 的 TOOL_DEFS/FORBIDDEN_TOOLS 已从本体解释生成（不再是可 ast 静态解析的
    # 字面量），故读『生成后的运行时』改为消费同一生成器——与『生成前的声明』（exposed_as_tool /
    # ai_executable 字段）比对。红线仍守（ontology_runtime 是 pipeline 模块、非 app/agent）。
    tool_names = {t["name"] for t in _rt_tool_defs(onto)}
    forbidden = _rt_forbidden_tools(onto)

    action_by_snake = {camel_to_snake(a["name"]): a["name"] for a in onto["actions"]}
    exposed_snakes = {camel_to_snake(a["name"]) for a in onto["actions"]
                      if a.get("exposed_as_tool") is True}
    frozen_snakes = {camel_to_snake(a["name"]) for a in onto["actions"]
                     if a.get("ai_executable") == "frozen"}

    # 读工具（list_/get_/explain_ 前缀）是对象查询工具、不对应动作（本体无读动作），从写工具核对中排除
    read_tools = sorted(t for t in tool_names if re.match(r"^(list_|get_|explain_)", t))
    write_tools = sorted(t for t in tool_names if t not in read_tools)

    # C.1 每个"写工具"必须是某个 exposed_as_tool=true 动作的 snake（TOOL_DEFS 写工具集 ⊆ exposed）
    for tool in write_tools:
        if tool not in exposed_snakes:
            if tool in action_by_snake:
                diffs.append(Diff("C", EXTRA, action_by_snake[tool],
                                  f"TOOL_DEFS 暴露写工具 `{tool}`（→{action_by_snake[tool]}），"
                                  f"但该动作 exposed_as_tool≠true"))
            else:
                diffs.append(Diff("C", EXTRA, tool,
                                  f"TOOL_DEFS 暴露工具 `{tool}`，既非读工具(list_/get_/explain_)"
                                  f"也无法溯源到任一 exposed_as_tool=true 动作"))
    # C.1b 每个 exposed_as_tool=true 动作都应在 TOOL_DEFS 有对应写工具（声明暴露却缺工具=漏装）
    for snake in sorted(exposed_snakes):
        if snake not in tool_names:
            diffs.append(Diff("C", MISSING, action_by_snake[snake],
                              f"动作 {action_by_snake[snake]} 声明 exposed_as_tool=true，"
                              f"但 TOOL_DEFS 无对应工具 `{snake}`"))

    # C.2 冻结区动作（ai_executable=frozen）绝不出现在 TOOL_DEFS
    for snake in sorted(frozen_snakes):
        if snake in tool_names:
            diffs.append(Diff("C", DRIFT, action_by_snake.get(snake, snake),
                              f"冻结区动作(ai_executable=frozen) {action_by_snake.get(snake, snake)} "
                              f"对应工具 `{snake}` 竟出现在 TOOL_DEFS——绝不应暴露给 AI（致命）"))

    # C.3 FORBIDDEN_TOOLS == snake(frozen 动作)：既无死条目/拼写漂移，又逐一覆盖
    for tool in sorted(forbidden - frozen_snakes):
        if tool in action_by_snake:
            diffs.append(Diff("C", EXTRA, action_by_snake[tool],
                              f"FORBIDDEN_TOOLS 含 `{tool}`（→{action_by_snake[tool]}），"
                              f"但该动作 ai_executable≠frozen"))
        else:
            diffs.append(Diff("C", DRIFT, tool,
                              f"FORBIDDEN_TOOLS 含 `{tool}`，但按 snake_case 溯源不到任一本体 action"
                              f"（拼写漂移或指向已删动作？）"))
    for snake in sorted(frozen_snakes - forbidden):
        diffs.append(Diff("C", MISSING, action_by_snake.get(snake, snake),
                          f"冻结区动作 {action_by_snake.get(snake, snake)}（工具名 `{snake}`）"
                          f"未在 FORBIDDEN_TOOLS 显式拉黑（纵深防御缺口）"))

    notes.append(f"exposed_as_tool=true 动作（snake）：{sorted(exposed_snakes)}")
    notes.append(f"TOOL_DEFS 写工具：{write_tools}（应 == exposed 集，逐一相等即一致）")
    notes.append(f"TOOL_DEFS 读工具（对象查询，本体无『读动作』故不溯源到动作，预期）：{read_tools}")
    notes.append(f"ai_executable=frozen 动作（snake）：{sorted(frozen_snakes)}；"
                 f"FORBIDDEN_TOOLS={sorted(forbidden)}（应逐一相等）")
    notes.append("说明：花钱/写库类动作（如 RecordPurchasePayment/BlockNonPoPayment）exposed_as_tool=false "
                 "即 AI 不可达；FORBIDDEN_TOOLS 是对 ai_executable=frozen 4 动作的显式纵深拉黑。")
    return diffs, notes


# ===========================================================================
# 断言 D：关系 ↔ 外键
# ===========================================================================
def assert_d_links_fks(onto: dict, schema: dict) -> tuple[list[Diff], list[str], list[tuple]]:
    """M1 规则7：links[] 的 `storage` 声明把 M4 映射契约从"重建猜测"变"显式声明"——有 storage 的
    关系直接核对声明列是否存在；`status=declared_only` 的关系豁免"缺失"判定但登记为待裁决豁免
    （在报告尾部输出，诚实：豁免≠消音，裁1）。返回 (diffs, notes, exemptions)。"""
    diffs: list[Diff] = []
    notes: list[str] = []
    exemptions: list[tuple] = []
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

        # M1 规则7：declared_only 状态 → 豁免"缺失"判定，登记为待裁决豁免（裁1，报告尾部提示）
        if link.get("status") == "declared_only":
            carrier = link.get("via") or (link.get("storage") or {}).get("column") or "(无承载列)"
            exemptions.append((lt, s, t, card, carrier))
            continue

        # M1 规则7：显式 storage 声明 → 核对声明列是否存在（不再靠派生"重建猜测"）。
        # F1（V8-②）扩展：storage 可带 `discriminator` 判别式（ref_type+ref_id 单列承载多关系，
        # 3 条 payment 结算/催收关系共用 payments.ref_id、由 ref_type 值区分）——此时 D 类除核对
        # 值列(column=ref_id)存在，还须核对判别列(discriminator=ref_type)存在（"按 ref_type 分支"）。
        # 无 discriminator 的既有 storage 声明行为不变（po_shipped_by/shipment_to_warehouse 全绿不动）。
        storage = link.get("storage")
        if storage:
            kind, stbl, scol = storage.get("kind"), storage.get("table"), storage.get("column")
            tbl_cols = {c[0] for c in schema.get(stbl, [])}
            disc = storage.get("discriminator")   # 判别列名（如 ref_type），可选
            disc_val = storage.get("discriminator_value")
            missing_cols = [c for c in (scol, disc) if c and c not in tbl_cols]
            if not missing_cols:
                disc_note = (f"，判别式 `{disc}`={disc_val!r}（列存在）" if disc else "")
                notes.append(f"[storage 显式声明] {lt} ({s}→{t} {card}) 由 `{stbl}`.`{scol}` 承载"
                             f"（kind={kind}）{disc_note}，列存在，一致")
            else:
                diffs.append(Diff("D", MISSING, lt,
                                  f"关系 {lt} 的 storage 声明指向 `{stbl}`（kind={kind}"
                                  f"{f'，判别 {disc}' if disc else ''}），"
                                  f"但列 {missing_cols} 在表 `{stbl}` 中不存在"))
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
    if exemptions:
        notes.append(f"待裁决豁免 {len(exemptions)} 项（status=declared_only，不计入差异，明细见报告尾部）。")
    return diffs, notes, exemptions


# ===========================================================================
# 报告
# ===========================================================================
SECTION_TITLES = {
    "A": "A 对象↔表结构  (objects[].properties ↔ SQLite 表列)",
    "B": "B 动作↔权限    (actions[].executors ↔ app/*.py 权限字典，主 ROLE_PERMS)",
    "C": "C 动作↔AI 工具 (agent/tools.py TOOL_DEFS / FORBIDDEN_TOOLS ↔ 本体 actions)",
    "D": "D 关系↔外键    (links[].source/target ↔ 数据库实际列)",
}


def render_report(sections: dict, strict: bool, exemptions: list[tuple] | None = None) -> tuple[str, int]:
    exemptions = exemptions or []
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

    # 待裁决豁免（M1 规则7/裁1）：declared_only 关系不计入差异，但必须在报告尾部显式提示——
    # 诚实原则：豁免≠消音。豁免不影响 exit_code（--strict 不因豁免阻断）。
    if exemptions:
        lines.append("")
        lines.append("─" * 78)
        lines.append(f"待裁决豁免（不计入差异、不阻断 --strict；共 {len(exemptions)} 项，等待创始人裁决）：")
        for (lt, s, t, card, carrier) in exemptions:
            lines.append(f"  【待裁决豁免】关系 {lt} ({s}→{t} {card}) status=declared_only："
                         f"本体声明但运行时无承载列（预期 {carrier}）——闸门豁免『缺失』判定，"
                         f"待裁决：补列 or 删关系（裁1）")
        lines.append("─" * 78)

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
        d_diffs, d_notes, exemptions = assert_d_links_fks(onto, schema)
        sections = {
            "A": assert_a_objects_tables(onto, schema),
            "B": assert_b_actions_perms(onto),
            "C": assert_c_actions_tools(onto),
            "D": (d_diffs, d_notes),
        }
    finally:
        con.close()
    report, exit_code = render_report(sections, strict, exemptions)
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
