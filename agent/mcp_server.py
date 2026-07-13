#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正式本体 MCP server（API 层 plan M4，V5 决议②正式化）—— 只读、角色感知、审计入库。

迁移自 `poc/mcp-ontology/server.py`（该 PoC 目录冻结为历史证据，一字不改；本文件为其正式版）。
相对 PoC 补齐 PoC 报告 §6 建议 1-4（四道硬门槛）：

  门槛1 角色过滤 + 字段脱敏：启动带 `--role <role>`（缺省 ops，亦读环境变量 ONTOLOGY_MCP_ROLE）。
        tools/list 只返回该角色可见工具——读工具按本体 aiQueryTools[].domain × 角色域矩阵，
        与 agent/tools.py 的 Session scoping **同一来源与语义**（直接复用 allowed_tools_for_role）；
        返回数据按本体 sensitiveFieldRules 声明驱动脱敏（如 Customer.tier 仅 cs/manager 可见），
        掩码值 = agent.tools.MASK。tools.py 既有 COST_FIELDS/INVOICE_COST_FIELDS 会话层脱敏本次不动
        （由被复用的 Session 读方法原样执行；*_usd 成本模式规则仍由会话层承载）。

  门槛2 冻结区机制化 + 零写工具：工具清单从 pipeline.ontology_runtime.build_tool_defs() **派生**，
        按名单过滤掉 6 个写工具（exposed_as_tool=true 动作的 snake），只留 11 个读工具 + traverse。
        冻结区 4 动作（审批/关闭/拒接）在生成层就不在读工具集中——纵深防御：暴露集 ∩ frozen == ∅、
        暴露集 ∩ 6 写工具 == ∅（裁2 第一版纯只读，写提案工具不入，待创始人裁决后另开任务）。

  门槛3 审计入库：双连接——业务查询连接维持 `mode=ro`（物理只读红线，写路径不存在）；审计另开写连接，
        启动跑一次幂等迁移把 llm_calls.call_type CHECK 扩到含 'mcp_tool'，其后仅执行
        INSERT INTO llm_calls（call_type='mcp_tool'）。同时保留 jsonl 落盘（PoC 取证格式兼容）。

  门槛4 input_schema 本体驱动：traverse 的 object_type/link_type 枚举从本体生成（PoC 已做）；
        对象字段结构复用 pipeline.ontology_models 的 model_json_schema（M3 合流点，34 类型全覆盖）。

协议：MCP 走 stdio 传输 = 换行分隔的 JSON-RPC 2.0 报文（握手 initialize → tools/list → tools/call）。
stdout 纪律：stdout **只能**输出 JSON-RPC 报文；一切诊断走 stderr / jsonl（依赖导入已自证无 stdout 污染）。
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ── 路径解析：不依赖 spawn 时的 cwd（claude CLI 可能从任意目录起本进程），一律相对本文件定位仓库根 ──
_HERE = Path(__file__).resolve().parent               # agent/
REPO_ROOT = _HERE.parent                              # 仓库根
if str(REPO_ROOT) not in sys.path:                    # 便于 `python3 agent/mcp_server.py` 直起（非 -m）
    sys.path.insert(0, str(REPO_ROOT))

DB_PATH = Path(os.environ.get("ONTOLOGY_DB_PATH", REPO_ROOT / "data" / "ontology.sqlite"))
ONTOLOGY_JSON_PATH = Path(os.environ.get(
    "ONTOLOGY_JSON_PATH", REPO_ROOT / "ontology" / "control-tower-ontology.json"))
# jsonl 落盘（PoC 取证格式兼容）：默认 data/（.gitignore 已忽略 data/，不污染版本库）
CALL_LOG_PATH = Path(os.environ.get("MCP_CALL_LOG", REPO_ROOT / "data" / "mcp_call_log.jsonl"))

SERVER_NAME = "ontology"                    # tool 命名前缀 mcp__ontology__*（与 PoC / mcp-config 一致）
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"     # 客户端未给 protocolVersion 时的回落
DEFAULT_ROLE = "ops"

# ── 复用运行时/工具层（桥2 M2 + 桥3 M3 的合流消费点；本文件不重复实现任何权限/脱敏/遍历逻辑） ──
from pipeline.ontology_runtime import (           # noqa: E402  路径注入后再导入
    build_forbidden_tools, build_tool_defs, load_ontology, snake_case, traverse as onto_traverse)
from agent.tools import MASK, allowed_tools_for_role  # noqa: E402  脱敏掩码 + 角色域 scoping 单一来源
from agent.egress_gate import (                   # noqa: E402
    log_llm_call, migrate_llm_calls_call_type_check, sanitize_for_egress)


def _stderr(msg: str) -> None:
    """诊断信息只允许走 stderr，绝不污染 stdout 的 JSON-RPC 流。"""
    sys.stderr.write(f"[ontology-mcp] {msg}\n")
    sys.stderr.flush()


class ToolError(Exception):
    """业务级工具错误（未知类型/参数错/越域）；作为 isError 结果返回，非协议错误。"""


# ═══════════════════════════════════════════════════════════════════════════
# 门槛2：只读工具集派生（build_tool_defs 结果过滤掉 6 个写工具 + 追加 traverse）
# ═══════════════════════════════════════════════════════════════════════════
def write_tool_names(ontology: dict) -> set[str]:
    """6 个写工具名 = snake_case(exposed_as_tool=true 动作)。裁2 依据：第一版纯只读，
    写动作（派单/提案/准入准备）一个不注册为 MCP 工具——它们只经 UI 表单 + maker-checker 执行。"""
    return {snake_case(a["name"]) for a in ontology["actions"] if a.get("exposed_as_tool") is True}


def _traverse_tool_def(ontology: dict) -> dict:
    """通用遍历工具定义（桥3 traverse 的 MCP 门面）。object_type/link_type 枚举本体驱动（门槛4）：
    object_type = 全部对象类型；link_type = 已声明关系中**可遍历**者（排除 status=declared_only，
    那些运行时无承载列、traverse 会拒绝，故不放进枚举免得模型空选）。"""
    object_types = [o["type"] for o in ontology["objects"]]
    link_types = sorted({l["linkType"] for l in ontology.get("links", [])
                         if l.get("status") != "declared_only"})
    return {
        "name": "traverse",
        "description": (
            "沿本体声明的关系(link_type)从给定对象走到关联对象，返回邻居对象的主键 ID 列表。"
            "例如 traverse('Shipment','SHP-2026-0068','risk_on_shipment') 找出这票货的风险事件 ID。"
            "读 links[] 的承载声明（列/反向多值/N:M/标准外键）自动双向遍历。只读。"
            "拿到邻居 ID 后可用 get_risk / get_shipment_context 等工具深查其字段（字段结构见本体模型）。"),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_type": {"type": "string", "enum": object_types,
                                "description": "起点对象类型，如 Shipment / RiskEvent / SalesOrderLine"},
                "source_id": {"type": "string", "description": "起点对象主键 ID，如 SHP-2026-0068"},
                "link_type": {"type": "string", "enum": link_types,
                              "description": "本体 links[] 声明的关系名，如 risk_on_shipment / so_has_line"},
            },
            "required": ["source_type", "source_id", "link_type"],
        },
    }


def build_readonly_tool_defs(ontology: dict) -> list[dict]:
    """MCP 暴露工具集（角色过滤前的全集）= build_tool_defs() 去掉 6 写工具 + traverse。
    结果恒为 11 读工具 + traverse = 12（写工具一个不入；冻结区动作本就不在 build_tool_defs 里）。"""
    writes = write_tool_names(ontology)
    defs = [t for t in build_tool_defs(ontology) if t["name"] not in writes]  # 11 读工具
    defs.append(_traverse_tool_def(ontology))                                 # + traverse
    return defs


# ═══════════════════════════════════════════════════════════════════════════
# 门槛1：字段脱敏——本体 sensitiveFieldRules 声明驱动（掩码值 = agent.tools.MASK）
# ═══════════════════════════════════════════════════════════════════════════
class SensitiveFieldMasker:
    """按本体 sensitiveFieldRules 对工具返回结构递归脱敏。

    只处理**具名字段**规则：Customer.tier(cs/manager)、Customer.credit_terms/risk_tier(finance/
    manager)、Supplier.uflpa_risk_flag(compliance/manager)、AdmissionCase.conditions(finance/
    manager)、Task.proposal_params.est_cost_usd(ops/manager)。这些字段名在本体内**各自唯一归属
    一个对象类型**（tier/credit_terms/risk_tier→Customer、uflpa_risk_flag→Supplier、conditions→
    AdmissionCase），故按字段名递归匹配即等价于按类型脱敏，无跨类型误伤。

    CostScenario '*_usd and margin fields' 是**模式规则**（非具名列），且成本字段脱敏已由被复用的
    Session 读方法用 COST_FIELDS/INVOICE_COST_FIELDS 在会话层执行（门槛1: 本次不动）——故此处跳过该
    模式规则，避免对遍地皆是的 *_usd 列（affected_value_usd 等运营必读字段）盲目脱敏。

    与 Session 既有 tier 脱敏叠加是幂等的（会话已 MASK → 再判仍 MASK；会话给真值且角色可见 → 不动）。"""

    def __init__(self, ontology: dict, role: str, mask: str = MASK):
        self.role = role
        self.mask = mask
        self.flat: dict[str, set[str]] = {}          # 具名列 → 可见角色集
        self.nested: dict[str, dict[str, set[str]]] = {}  # 承载列 → {子键: 可见角色集}
        for rule in ontology.get("sensitiveFieldRules", []):
            field = rule.get("field")
            if not field or "margin" in field:       # 跳过 CostScenario 模式规则（会话层承载）
                continue
            visible = set(rule.get("visibleTo", []))
            if "." in field:                          # 嵌套：proposal_params.est_cost_usd
                col, sub = field.split(".", 1)
                self.nested.setdefault(col, {})[sub] = visible
            else:
                self.flat[field] = visible

    def _blocked(self, visible: set[str]) -> bool:
        return self.role not in visible

    def mask_value(self, data):
        """递归脱敏：dict 按 key 匹配具名规则 / 嵌套规则；list 逐元素递归。返回原对象（就地改）。"""
        if isinstance(data, dict):
            for key, val in list(data.items()):
                if key in self.flat and self._blocked(self.flat[key]) and val is not None:
                    data[key] = self.mask
                    continue
                if key in self.nested:
                    data[key] = self._mask_nested(key, val)
                    continue
                self.mask_value(val)
        elif isinstance(data, list):
            for item in data:
                self.mask_value(item)
        return data

    def _mask_nested(self, col: str, val):
        """嵌套承载列（如 tasks.proposal_params 存 JSON）：解析→脱敏子键→回写。
        val 可能是 dict（已解析）或 JSON 字符串。非法/缺子键则原样返回（不放大为故障）。"""
        rules = self.nested[col]
        obj, was_str = val, False
        if isinstance(val, str):
            try:
                obj = json.loads(val)
                was_str = True
            except (ValueError, TypeError):
                return val
        if not isinstance(obj, dict):
            return val
        changed = False
        for sub, visible in rules.items():
            if sub in obj and self._blocked(visible) and obj[sub] is not None:
                obj[sub] = self.mask
                changed = True
        if not changed:
            return val
        return json.dumps(obj, ensure_ascii=False) if was_str else obj


# ═══════════════════════════════════════════════════════════════════════════
# 只读会话包装：复用 agent.tools.AgentSession 的 11 读方法，但连接强制 mode=ro（门槛3 物理只读）
# ═══════════════════════════════════════════════════════════════════════════
def _make_readonly_session(db_path: Path, role: str):
    """构造 AgentSession（注入 role → 复用其角色域 scoping 与既有脱敏），随即把其连接替换为
    mode=ro URI 连接（AgentSession 默认开读写连接；本 server 只读，物理断掉写路径 = 纵深防御）。"""
    from agent.tools import AgentSession
    session = AgentSession(db_path=str(db_path), role=role)
    try:
        session.con.close()
    except Exception:  # noqa: BLE001
        pass
    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA busy_timeout=5000")   # 读连接遇写锁短暂等待（稳态单进程无争用，纵深稳健）
    session.con = ro
    return session


# ═══════════════════════════════════════════════════════════════════════════
# server 主体
# ═══════════════════════════════════════════════════════════════════════════
class OntologyMCPServer:
    def __init__(self, role: str = DEFAULT_ROLE, db_path: Path = DB_PATH,
                 ontology_json: Path = ONTOLOGY_JSON_PATH, audit: bool = True):
        self.role = role
        self.db_path = Path(db_path)
        self.ontology = load_ontology(str(ontology_json))
        self.session = _make_readonly_session(self.db_path, role)   # mode=ro 业务连接
        self.masker = SensitiveFieldMasker(self.ontology, role, MASK)

        # 门槛2：只读工具全集（12）+ 派生的写工具名单 / 冻结区名单（供纵深防御与自检）
        self.readonly_defs = build_readonly_tool_defs(self.ontology)
        self.exposed_names = {t["name"] for t in self.readonly_defs}
        self.write_names = write_tool_names(self.ontology)          # 6，一个不暴露
        self.forbidden = build_forbidden_tools(self.ontology)       # 4 冻结区
        self.read_tool_names = self.exposed_names - {"traverse"}    # 11 读工具（映射 Session 方法）

        # 门槛4：对象字段结构复用 M3 生成模型的 json schema（合流点，34 类型覆盖自检）
        self.type_schemas = self._load_type_schemas()

        # 门槛3：审计写连接（与业务只读连接物理分开）+ 幂等 CHECK 迁移
        self.audit_con = None
        self.audit_status = "disabled"
        if audit:
            self.audit_con = sqlite3.connect(str(self.db_path))
            self.audit_con.execute("PRAGMA busy_timeout=5000")  # 审计写遇锁等待而非即失败
            self.audit_status = migrate_llm_calls_call_type_check(self.audit_con)

        # 11 读工具 → Session 绑定方法（名称即方法名，无硬编码清单；桥2 声明驱动）
        self._read_handlers = {name: getattr(self.session, name) for name in self.read_tool_names}
        self.tool_defs = self.readonly_defs

    def close(self) -> None:
        """释放业务只读连接 + 审计写连接（单进程 stdio 生命周期长驻，仅退出/测试时用）。"""
        for con in (getattr(self.session, "con", None), self.audit_con):
            try:
                if con is not None:
                    con.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _load_type_schemas(self) -> dict:
        """{对象类型: Pydantic 模型.model_json_schema()}（M3 pipeline.ontology_models）。
        缺模型不炸（返回覆盖到的子集），构造后自检覆盖率写 stderr。"""
        try:
            import pipeline.ontology_models as models
        except Exception as exc:  # noqa: BLE001  模型缺失不致命，仅少一处 schema 富化
            _stderr(f"警告：ontology_models 不可用（{exc}），跳过 model_json_schema 富化")
            return {}
        out = {}
        for obj in self.ontology["objects"]:
            model = getattr(models, obj["type"], None)
            if model is not None and hasattr(model, "model_json_schema"):
                try:
                    out[obj["type"]] = model.model_json_schema()
                except Exception:  # noqa: BLE001
                    pass
        return out

    # ── 角色可见工具（门槛1 角色过滤）──────────────────────────────────────
    def visible_tool_defs(self) -> list[dict]:
        """该 role 可见工具 = 角色域 scoping（allowed_tools_for_role，与 tools.py 同一来源）∩ 读工具，
        并入 traverse（结构原语，只返回 ID 无字段、对全角色可见）。写工具从不在 readonly_defs 中。"""
        allowed = allowed_tools_for_role(self.role)
        return [t for t in self.readonly_defs
                if t["name"] == "traverse" or t["name"] in allowed]

    def visible_tool_names(self) -> set[str]:
        return {t["name"] for t in self.visible_tool_defs()}

    # ── 工具执行（门槛1 脱敏 + 门槛3 审计）────────────────────────────────
    def call_tool(self, name: str, args: dict) -> dict:
        """执行只读工具：纵深防御拦截（冻结区/写工具/越域）→ 调用 → 声明脱敏 → 出境闸门 → 审计。
        返回 MCP content 结果（isError 标注业务错误）。"""
        args = args or {}
        t0 = time.time()

        # 纵深防御 1：冻结区 / 写工具永不执行（本就不在 tools/list，双保险）
        if name in self.forbidden or name in self.write_names:
            return self._finish(name, args, {"error":
                "该动作未向 AI 开放：审批/关闭/写动作只经人工 UI 表单执行（proposal-only 护栏，裁2）"},
                is_error=True, t0=t0)
        # 纵深防御 2：未知工具
        if name not in self.exposed_names:
            return self._finish(name, args, {"error": f"未知工具 '{name}'"}, is_error=True, t0=t0)
        # 纵深防御 3：角色域外（tools/list 已过滤，越域调用仍拒并审计）
        if name not in self.visible_tool_names():
            return self._finish(name, args, {"error":
                f"工具 '{name}' 未向角色 '{self.role}' 开放（越域读，本次已记审计）"},
                is_error=True, t0=t0)

        try:
            data = self._invoke(name, args)
        except ToolError as exc:
            return self._finish(name, args, {"error": str(exc)}, is_error=True, t0=t0)
        except TypeError as exc:
            return self._finish(name, args, {"error": f"参数错误: {exc}"}, is_error=True, t0=t0)
        except Exception as exc:  # noqa: BLE001
            _stderr("工具执行异常:\n" + traceback.format_exc())
            return self._finish(name, args, {"error": f"内部错误: {exc}"}, is_error=True, t0=t0)

        self.masker.mask_value(data)                 # 门槛1：声明驱动字段脱敏
        return self._finish(name, args, data, is_error=False, t0=t0)

    def _invoke(self, name: str, args: dict):
        """路由：11 读工具 → Session 方法；traverse → 桥3 运行时原语（同源消费 links[].storage）。"""
        if name == "traverse":
            return self._traverse(args)
        return self._read_handlers[name](**args)

    def _traverse(self, args: dict) -> dict:
        source_type = args.get("source_type")
        source_id = args.get("source_id")
        link_type = args.get("link_type")
        if not (source_type and source_id and link_type):
            raise ToolError("traverse 需要 source_type / source_id / link_type 三个参数")
        try:
            neighbor_ids = onto_traverse(self.session.con, source_type, source_id, link_type)
        except ValueError as exc:                    # 未知关系/declared_only/端点不符 → 业务错误
            raise ToolError(str(exc))
        neighbor_type = self._neighbor_type(source_type, link_type)
        result = {"source_type": source_type, "source_id": source_id, "link_type": link_type,
                  "neighbor_type": neighbor_type, "count": len(neighbor_ids),
                  "neighbor_ids": neighbor_ids}
        if not neighbor_ids:                         # 空结果给可发现性提示（本对象类型能走哪些关系）
            result["hint"] = {"message": f"{source_type} {source_id} 在关系 '{link_type}' 上无邻居",
                              "available_links": self._links_for_type(source_type)}
        return result

    def _neighbor_type(self, source_type: str, link_type: str) -> str | None:
        for link in self.ontology.get("links", []):
            if link.get("linkType") == link_type:
                return link["target"] if source_type == link["source"] else link["source"]
        return None

    def _links_for_type(self, object_type: str) -> list[str]:
        return sorted({l["linkType"] for l in self.ontology.get("links", [])
                       if object_type in (l["source"], l["target"])
                       and l.get("status") != "declared_only"})

    # ── 结果封装 + 出境闸门 + 双落盘审计 ──────────────────────────────────
    def _finish(self, name: str, args: dict, data: dict, is_error: bool, t0: float) -> dict:
        """工具结果 → 出境 PI 摘除（回传模型=再次出境，spec §9）→ 审计双落盘（llm_calls + jsonl）。"""
        raw = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        clean, report = sanitize_for_egress(raw)     # 业务编号豁免，PI 才摘（多为空操作）
        rows = self._count_rows(name, data, is_error)
        duration_ms = int((time.time() - t0) * 1000)
        self._audit(name, args, rows, ok=not is_error,
                    error=(data.get("error") if is_error else None),
                    duration_ms=duration_ms, input_chars=len(json.dumps(args, ensure_ascii=False)),
                    output_chars=len(clean), redactions=report)
        return {"content": [{"type": "text", "text": clean}], "isError": is_error}

    @staticmethod
    def _count_rows(name: str, data: dict, is_error: bool) -> int:
        if is_error or not isinstance(data, dict):
            return 0
        for key in ("count", "result_rows"):
            if isinstance(data.get(key), int):
                return data[key]
        if data.get("found") is True or "risk" in data or "invoice" in data or "case" in data:
            return 1
        for key in ("risks", "cases", "invoices", "neighbor_ids", "entries", "edges"):
            if isinstance(data.get(key), list):
                return len(data[key])
        return 1 if "error" not in data else 0

    def _audit(self, tool: str, args: dict, rows: int, ok: bool, error, duration_ms: int,
               input_chars: int, output_chars: int, redactions: dict) -> None:
        """门槛3 双落盘：① llm_calls（call_type='mcp_tool'，审计写连接，仅 INSERT）；② jsonl（PoC 格式）。
        任一落盘失败只 stderr 告警，绝不拖垮工具返回（审计故障不放大为回答故障）。"""
        if self.audit_con is not None:
            try:
                log_llm_call(self.audit_con, call_type="mcp_tool", provider="mcp_server",
                             model=SERVER_NAME, status="ok" if ok else "error",
                             input_chars=input_chars, output_chars=output_chars,
                             duration_ms=duration_ms, error=(str(error)[:300] if error else None),
                             redactions=redactions)
            except Exception as exc:  # noqa: BLE001
                _stderr(f"审计入库失败（不影响返回）：{exc}")
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "tool": tool, "role": self.role,
               "args": args, "result_rows": rows, "ok": ok}
        if error:
            rec["error"] = str(error)[:300]
        try:
            with CALL_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            _stderr(f"写 jsonl 失败（不影响返回）：{exc}")

    # ── JSON-RPC 方法处理 ────────────────────────────────────────────────
    def handle_initialize(self, params: dict) -> dict:
        client_ver = (params or {}).get("protocolVersion")
        return {"protocolVersion": client_ver or DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}

    def handle_tools_list(self) -> dict:
        # MCP 线格式用 inputSchema（camel）；内部定义用 input_schema——在此转换，深拷贝防污染。
        out = []
        for t in self.visible_tool_defs():
            out.append({"name": t["name"], "description": t["description"],
                        "inputSchema": copy.deepcopy(t["input_schema"])})
        return {"tools": out}

    def handle_tools_call(self, params: dict) -> dict:
        name = (params or {}).get("name")
        args = (params or {}).get("arguments", {}) or {}
        return self.call_tool(name, args)


# ═══════════════════════════════════════════════════════════════════════════
# stdio JSON-RPC 主循环
# ═══════════════════════════════════════════════════════════════════════════
class StdioLoop:
    def __init__(self, server: OntologyMCPServer):
        self.server = server

    @staticmethod
    def _send(msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _reply(self, req_id, result=None, error=None) -> None:
        msg = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self._send(msg)

    def serve(self) -> None:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._reply(None, error={"code": -32700, "message": "Parse error"})
                continue
            method = msg.get("method")
            req_id = msg.get("id")
            is_notification = "id" not in msg
            params = msg.get("params") or {}
            try:
                if method == "initialize":
                    self._reply(req_id, self.server.handle_initialize(params))
                elif method in ("notifications/initialized", "initialized"):
                    pass
                elif method == "tools/list":
                    self._reply(req_id, self.server.handle_tools_list())
                elif method == "tools/call":
                    self._reply(req_id, self.server.handle_tools_call(params))
                elif method == "ping":
                    self._reply(req_id, {})
                elif method in ("resources/list", "prompts/list"):
                    key = "resources" if method.startswith("resources") else "prompts"
                    self._reply(req_id, {key: []})
                elif is_notification:
                    pass
                else:
                    self._reply(req_id, error={"code": -32601,
                                               "message": f"Method not found: {method}"})
            except Exception as exc:  # noqa: BLE001
                _stderr("主循环异常:\n" + traceback.format_exc())
                if not is_notification:
                    self._reply(req_id, error={"code": -32603, "message": f"Internal error: {exc}"})


def resolve_role(argv: list[str]) -> str:
    """角色优先级：--role <role>（最高）> 环境变量 ONTOLOGY_MCP_ROLE > 缺省 ops。
    未知角色不硬失败——allowed_tools_for_role 对未知角色仅给 risk 读域、脱敏掩全部敏感字段（最保守）。"""
    role = None
    for i, a in enumerate(argv):
        if a == "--role" and i + 1 < len(argv):
            role = argv[i + 1]
        elif a.startswith("--role="):
            role = a.split("=", 1)[1]
    role = role or os.environ.get("ONTOLOGY_MCP_ROLE") or DEFAULT_ROLE
    return role.strip()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    role = resolve_role(sys.argv[1:])
    try:
        server = OntologyMCPServer(role=role)
    except Exception:  # noqa: BLE001
        _stderr("启动失败:\n" + traceback.format_exc())
        sys.exit(1)
    _stderr(f"就绪：role={role}，暴露 {len(server.visible_tool_names())} 工具"
            f"（只读全集 {len(server.exposed_names)}），审计={server.audit_status}，DB={server.db_path}（只读）")
    StdioLoop(server).serve()


if __name__ == "__main__":
    main()
