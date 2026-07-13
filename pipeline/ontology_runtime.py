#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥2 运行时侧（V5 决议①，plan v0.2 §4 / API 层 plan M2）：本体运行时加载器。

问题背景：本体 JSON 曾只被展示层引用，权限字典（5 个）与 AI 工具清单（TOOL_DEFS /
FORBIDDEN_TOOLS）是手维护的硬编码字面量——声明（本体）与运行时（app/agent）"两张皮"，
靠人的纪律维持一致（桥1 ontology_lint 就是量这条缝的）。

本模块把这条缝焊死：运行时**从本体解释生成**权限与工具（解释型，启动时读一次并缓存）。
迁移后 app/*.py 的 5 个权限字典、agent/tools.py 的 FORBIDDEN_TOOLS / TOOL_DEFS 全部
import 本模块的生成结果，硬编码字面量退役——本体成为唯一权威源。

设计选型（V5 调研结论）：高频组合（权限过滤/工具暴露）用**解释型**（运行时读本体现场
组装），区别于结构性低频的生成型（桥3 的 DDL/Pydantic 产物文件入版本库）。

接口签名（plan M2 锁定，不得偏离）：
    load_ontology(path=ONTOLOGY_PATH) -> dict        进程内缓存
    build_role_perms(ontology) -> dict[str, set]     enforcement=role_dict 动作，按 permission_key 归组
    build_forbidden_tools(ontology) -> set[str]      snake_case(ai_executable=frozen 动作)
    build_tool_defs(ontology) -> list[dict]          aiQueryTools（读工具）+ exposed_as_tool=true 动作（写工具）
    snake_case(action_name) -> str                   PascalCase 动作名 → snake_case 工具名（与 lint camel_to_snake 同一实现）
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY_PATH = str(REPO_ROOT / "ontology" / "control-tower-ontology.json")

# 进程内缓存（plan M2「启动时读一次并缓存」）：按路径缓存已解析的本体 JSON。
# 改本体后需重启进程才生效——这正是解释型"读一次"的语义。
_ONTOLOGY_CACHE: dict[str, dict] = {}


def load_ontology(path: str = ONTOLOGY_PATH) -> dict:
    """读取并缓存本体 JSON（进程内、按路径）。返回缓存对象——调用方**不得原地修改**
    （build_* 系列返回的都是独立副本，不共享此缓存的可变结构）。"""
    if path not in _ONTOLOGY_CACHE:
        with open(path, encoding="utf-8") as fh:
            _ONTOLOGY_CACHE[path] = json.load(fh)
    return _ONTOLOGY_CACHE[path]


def snake_case(action_name: str) -> str:
    """PascalCase / camelCase 动作名 → snake_case 工具名。
    与 pipeline/ontology_lint.py::camel_to_snake **逐字符同一实现**（M3 契约：桥1 闸门与桥2
    运行时对『动作→工具名』必须用同一把尺子，否则一致性核对失真）。
    AssignTask→assign_task、RejectOrRequestMoreInfo→reject_or_request_more_info。"""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", action_name)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


def _strip_role_annotation(executor: str) -> str:
    """'compliance (more_info)' → 'compliance'（剥离 executors 里的括号语义注解）。
    与 lint::strip_role_annotation 同规——权限判定只认角色名，注解是给人读的分工说明。"""
    return executor.split("(")[0].strip()


def build_role_perms(ontology: dict) -> dict[str, set[str]]:
    """从本体动作解释生成权限映射：仅 `enforcement=role_dict` 的动作参与，按其
    `permission_key`（缺省=动作名）归组，值为 executors 去注解后的角色集。

    返回结构与运行时 5 个权限字典（ROLE_PERMS/ADM_PERMS/PROC_PERMS/WH_PERMS/COORD_PERMS）
    的**并集**同构——各模块按自己拥有的键分片取用（协调域 6 动作共享组键 ManageCoordination）。
    engine_internal / proposal_flow 动作不进权限字典（前者引擎内部执行无人类 RBAC 键，
    后者经 propose→approve 提案流执行、权限沿用 ProposeMitigation），故此处显式跳过。"""
    perms: dict[str, set[str]] = {}
    for act in ontology["actions"]:
        if act.get("enforcement", "role_dict") != "role_dict":
            continue
        key = act.get("permission_key", act["name"])
        roles = {_strip_role_annotation(e) for e in act.get("executors", [])}
        perms.setdefault(key, set()).update(roles)
    return perms


def _validate_ai_invariants(ontology: dict) -> None:
    """AI 暴露声明的不变式校验（fail-loud，消息可读）——防"声明内自相矛盾"被生成层放行：
    ① frozen ∩ exposed == ∅（冻结区动作被标暴露 = 最危险的声明腐化，KeyError 兜底是运气不是设计）
    ② exposed ⇔ auto（暴露而不可自动执行、或可执行而未暴露，都是声明分叉）。
    build_forbidden_tools / build_tool_defs 双入口都调（幂等便宜）；lint 与 runtime 同源触发。"""
    frozen = {a["name"] for a in ontology["actions"] if a.get("ai_executable") == "frozen"}
    exposed = {a["name"] for a in ontology["actions"] if a.get("exposed_as_tool") is True}
    auto = {a["name"] for a in ontology["actions"] if a.get("ai_executable") == "auto"}
    if frozen & exposed:
        raise ValueError(f"本体不变式违反：冻结区动作被标 exposed_as_tool=true：{sorted(frozen & exposed)}"
                         "——冻结区任何形态下不得成为 AI 工具（全局红线3），拒绝生成")
    if exposed != auto:
        raise ValueError(f"本体不变式违反：exposed_as_tool ⇔ ai_executable=auto 不成立："
                         f"exposed-auto={sorted(exposed - auto)} auto-exposed={sorted(auto - exposed)}")


def build_forbidden_tools(ontology: dict) -> set[str]:
    """冻结区显式拉黑集 = snake_case(ai_executable=frozen 动作)。
    冻结区四动作（ApproveMitigation/ApproveQuoteDecision/CloseRiskEvent/RejectOrRequestMoreInfo）
    任何形态下不得成为 AI 可调用工具（全局红线3）；本集合是对它们的纵深防御拉黑名单。"""
    _validate_ai_invariants(ontology)
    return {snake_case(a["name"]) for a in ontology["actions"]
            if a.get("ai_executable") == "frozen"}


def build_tool_defs(ontology: dict) -> list[dict]:
    """AI 工具定义清单（Anthropic tool-use 格式）= 只读查询工具 + 写动作工具，两段拼接：

      1. 读工具：顶层 `aiQueryTools` 节逐条（list_/get_/explain_ 前缀，不对应任何动作），
         取 name/description/input_schema（domain 字段仅供角色域 scoping，不进工具定义）。
      2. 写工具：`exposed_as_tool=true` 的动作，工具名=snake_case(动作名)，
         description=动作的 tool_description，input_schema=动作的 tool_input_schema。

    顺序：读工具（按 aiQueryTools 声明序）在前、写工具（按 actions 声明序）在后——与迁移前
    agent/tools.py 硬编码 TOOL_DEFS 的 11 读 + 6 写顺序逐一对应。input_schema 深拷贝返回，
    与缓存本体物理隔离（匹配迁移前『各工具是独立字面量』语义，杜绝调用方误改污染缓存）。"""
    _validate_ai_invariants(ontology)
    defs: list[dict] = []
    for q in ontology.get("aiQueryTools", []):
        defs.append({"name": q["name"],
                     "description": q["description"],
                     "input_schema": copy.deepcopy(q["input_schema"])})
    for act in ontology["actions"]:
        if act.get("exposed_as_tool") is True:
            defs.append({"name": snake_case(act["name"]),
                         "description": act["tool_description"],
                         "input_schema": copy.deepcopy(act["tool_input_schema"])})
    return defs
