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


# ═══════════════════════════════════════════════════════════════════════════
# 桥3（API 层 plan M3）：通用关系遍历 traverse——把 poc/mcp-ontology/server.py 的
# traverse_link 泛化为读 links[] 承载声明的运行时原语。MCP server（M4）与透视镜 v3 同源
# 消费本函数（poc/ 目录本身一字不改）。仅新增函数，不改任何既有查询调用方。
# ═══════════════════════════════════════════════════════════════════════════
def _pluralize(word: str) -> str:
    """英语末词复数化（与 lint.pluralize 同规；表名派生用）。"""
    if word.endswith("y") and (len(word) < 2 or word[-2] not in "aeiou"):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def _object_table_name(obj: dict) -> str:
    """对象类型 → 表名（M1 契约：显式 table 优先，否则 PascalCase→snake→复数）。"""
    if obj.get("table"):
        return obj["table"]
    return _pluralize(snake_case(obj["type"]))


def _parse_id_list(raw) -> list[str]:
    """承载列多值解析：兼容 JSON 数组（affected_*_ids）与竖线分隔（po_ids）。空值→[]。"""
    if raw is None:
        return []
    s = str(raw).strip()
    if s == "":
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x) for x in v if x not in (None, "")]
    except (ValueError, TypeError):
        pass
    return [p for p in s.split("|") if p]


def _find_link(ontology: dict, link_type: str) -> dict:
    for link in ontology.get("links", []):
        if link.get("linkType") == link_type:
            return link
    known = sorted({l["linkType"] for l in ontology.get("links", [])})
    raise ValueError(f"未知关系 link_type '{link_type}'——本体 links[] 未声明。已声明关系：{known}")


def traverse(con, source_type: str, source_id: str, link_type: str) -> list[str]:
    """沿本体声明的关系从 (source_type, source_id) 走到邻居对象，返回邻居主键 id 列表（去重保序）。

    读 links[] 承载声明处理四种承载 + 标准外键推导（双向可走：source_type 命中关系 source 端走
    正向、命中 target 端走反向）：
      · storage.kind=="column"       标量外键列（shipment_to_warehouse: shipments.destination_warehouse）；
                                     可带 discriminator 判别式（F1 三 payment 关系共用 payments.ref_id、
                                     由 ref_type 值区分）——正反向都按 discriminator_value 过滤
      · storage.kind=="reverse_json" 反向多值列（po_shipped_by: shipments.po_ids 承 PO 列表）
      · via（affected_*_ids）          N:M 多值列落 source 表（risk_affects_line: risk_events.affected_so_line_ids）
      · 无声明                          标准外键推导（N:1 外键落 source 表 / 1:N 落 target 表）

    拒绝（raise ValueError，消息可读）：status=declared_only 的关系 / 未知 link_type / source_type 非端点。
    只读语义：仅 SELECT，不写库；con 由调用方管理（本函数按单列位置取值，不依赖 row_factory）。"""
    ontology = load_ontology()
    link = _find_link(ontology, link_type)
    if link.get("status") == "declared_only":
        raise ValueError(
            f"关系 '{link_type}' status=declared_only（本体仅声明、运行时无承载列，裁1 待创始人裁决）"
            f"——不可 traverse；补列或删关系后方可")
    s, t = link["source"], link["target"]
    if source_type not in (s, t):
        raise ValueError(
            f"object_type '{source_type}' 不是关系 '{link_type}' 的端点（端点：{s} / {t}）")

    tbl = {o["type"]: _object_table_name(o) for o in ontology["objects"]}
    pk = {o["type"]: o.get("primaryKey") for o in ontology["objects"]}
    storage = link.get("storage")
    via = link.get("via")
    card = link.get("cardinality", "")

    def _scalar_q(sql: str, extra: tuple = ()) -> list[str]:
        out: list[str] = []
        for row in con.execute(sql, (source_id, *extra)).fetchall():
            v = row[0]
            if v not in (None, "") and str(v) not in out:
                out.append(str(v))
        return out

    def _list_forward(col: str, htbl: str, hpk: str) -> list[str]:
        out: list[str] = []
        for (raw,) in con.execute(f'SELECT "{col}" FROM "{htbl}" WHERE "{hpk}"=?', (source_id,)):
            for x in _parse_id_list(raw):
                if x not in out:
                    out.append(x)
        return out

    def _list_reverse(col: str, htbl: str, hpk: str) -> list[str]:
        out: list[str] = []
        for hid, raw in con.execute(f'SELECT "{hpk}", "{col}" FROM "{htbl}"'):
            if source_id in _parse_id_list(raw) and str(hid) not in out:
                out.append(str(hid))
        return out

    # 承载 1：storage.kind == column（标量外键列；可带 discriminator 判别式承载多关系）
    if storage and storage.get("kind") == "column":
        htype = s if tbl[s] == storage["table"] else t
        col, htbl, hpk = storage["column"], storage["table"], pk[htype]
        # F1 判别式（ref_type+ref_id 单列承载三 payment 结算/催收关系）：值列(ref_id)须按判别列
        # (ref_type) 分支——**正反向都按 discriminator_value 过滤**。反向尤为关键：从 SalesOrder
        # 反查 payments 时若不加 ref_type='sales_order'，会把 ref_id 恰好相同的 supplier_invoice/
        # invoice 付款一并误召（判别盲）；正向亦过滤，使从非本类型付款走本关系正确返回空。
        # 无 discriminator 的既有 column 声明（shipment_to_warehouse）：disc_sql 空、extra 空，行为不变。
        disc, disc_val = storage.get("discriminator"), storage.get("discriminator_value")
        disc_sql = f' AND "{disc}"=?' if disc else ""
        extra = (disc_val,) if disc else ()
        if source_type == htype:
            return _scalar_q(f'SELECT "{col}" FROM "{htbl}" WHERE "{hpk}"=?{disc_sql}', extra)
        return _scalar_q(f'SELECT "{hpk}" FROM "{htbl}" WHERE "{col}"=?{disc_sql}', extra)

    # 承载 2：storage.kind == reverse_json（反向多值列）
    if storage and storage.get("kind") == "reverse_json":
        htype = s if tbl[s] == storage["table"] else t
        col, htbl, hpk = storage["column"], storage["table"], pk[htype]
        if source_type == htype:
            return _list_forward(col, htbl, hpk)
        return _list_reverse(col, htbl, hpk)

    # 承载 3：via（N:M 多值列落 source 表）
    if via:
        col, htbl, hpk = via.split()[0], tbl[s], pk[s]
        if source_type == s:
            return _list_forward(col, htbl, hpk)
        return _list_reverse(col, htbl, hpk)

    # 承载 4：标准外键推导
    if card == "N:1":
        holder, other = s, t
    elif card == "1:N":
        holder, other = t, s
    else:
        raise ValueError(
            f"关系 '{link_type}' 基数 '{card}' 无 storage/via 声明且非 N:1/1:N，无法推导承载列")
    fk_col, htbl, hpk = pk[other], tbl[holder], pk[holder]
    if source_type == holder:
        return _scalar_q(f'SELECT "{fk_col}" FROM "{htbl}" WHERE "{hpk}"=?')
    return _scalar_q(f'SELECT "{hpk}" FROM "{htbl}" WHERE "{fk_col}"=?')
