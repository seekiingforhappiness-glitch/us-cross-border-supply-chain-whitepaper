#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本体 MCP server（PoC，只读）—— V5 决议第②条。

一句白话：这是一个"翻译官"进程。claude CLI（订阅通道的大模型）通过标准输入/输出
和它对话，模型说"我要查 SHP-2026-0068 这票货"，它就去 SQLite 里查出来交给模型。
它只会"查"，绝不"改"——数据库一律以只读模式打开，不含任何写路径。

技术选择（如实记录，见报告）：
  本应优先用官方 `mcp` SDK，但在本机 conda base 环境安装 mcp 会把 pydantic 从
  2.10 升到 2.13、starlette 从 0.46 升到 1.3，破坏既有 gradio/fastapi 版本约束
  （违反 AGENTS.md §5/§7 锁定栈红线）。故改为**零依赖手写 stdio JSON-RPC**：
  仅用 Python 标准库，任何 python3 都能跑，便于"环境就绪后直接复制执行"。

协议：MCP 走 stdio 传输 = 换行分隔的 JSON-RPC 2.0 报文。
  握手：initialize -> (notifications/initialized) -> tools/list -> tools/call ...

暴露 3 个只读工具：
  1. get_object(object_type, object_id)          按类型+ID 查单实体
  2. search_risk_events(rule_id?, severity?, limit?)  查风险事件列表
  3. traverse_link(object_type, object_id, link_type) 沿本体关系走到关联对象

安全红线（本文件的硬约束）：
  - 只读：sqlite3 一律 `mode=ro` URI 打开；无 INSERT/UPDATE/DELETE 代码路径。
  - 冻结区对 AI 不存在：审批/关闭/花钱类动作**不注册为工具**（这里根本没有动作工具）。
  - 每次工具调用写 call_log.jsonl —— "工具调用真实发生"的铁证。

stdout 纪律：stdout **只能**输出 JSON-RPC 报文；一切诊断信息走 stderr / call_log。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径解析：不依赖启动时的 cwd（claude CLI 可能从任意目录 spawn 本进程）。
# 一律相对本文件位置定位仓库根，也允许环境变量覆盖。
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent               # poc/mcp-ontology/
_REPO_ROOT = _HERE.parents[1]                          # 仓库根

DB_PATH = Path(os.environ.get("ONTOLOGY_DB_PATH", _REPO_ROOT / "data" / "ontology.sqlite"))
ONTOLOGY_JSON_PATH = Path(os.environ.get("ONTOLOGY_JSON_PATH", _REPO_ROOT / "ontology" / "control-tower-ontology.json"))
CALL_LOG_PATH = Path(os.environ.get("MCP_CALL_LOG", _HERE / "call_log.jsonl"))

SERVER_NAME = "ontology"
SERVER_VERSION = "0.1.0-poc"
# 若客户端未在 initialize 里给出 protocolVersion，则回落到这个已知版本。
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

MAX_LIMIT = 100           # search_risk_events 单次返回硬上限
DEFAULT_LIMIT = 20


def _stderr(msg: str) -> None:
    """诊断信息只允许走 stderr，绝不污染 stdout 的 JSON-RPC 流。"""
    sys.stderr.write(f"[ontology-mcp] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# 本体加载：从 control-tower-ontology.json 推导 类型->表名 / 主键 / 标题键 / 关系。
# 这一段是"桥 2 预演"——工具的 input_schema 的枚举值直接由本体生成，而非手写。
# ---------------------------------------------------------------------------
def _to_snake(name: str) -> str:
    """CamelCase -> snake_case，正确处理缩写连写（RFQ->rfq, RFQLine->rfq_line）。"""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


class Ontology:
    """把本体 JSON 解析成运行时可查的映射表。"""

    def __init__(self, json_path: Path, existing_tables: set[str]):
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        self.type_to_table: dict[str, str] = {}
        self.type_to_pk: dict[str, str] = {}
        self.type_to_title: dict[str, str | None] = {}
        self.unmapped_types: list[str] = []

        for obj in raw.get("objects", []):
            t = obj["type"]
            self.type_to_pk[t] = obj.get("primaryKey")
            self.type_to_title[t] = obj.get("titleKey")
            table = self._resolve_table(t, existing_tables)
            if table:
                self.type_to_table[t] = table
            else:
                self.unmapped_types.append(t)

        # 关系登记表：linkType -> (source, target, cardinality)
        self.links: list[dict] = raw.get("links", [])
        self.link_types: list[str] = sorted({l["linkType"] for l in self.links})
        # 每个 linkType 允许的端点类型（用于校验 traverse_link 的 object_type 合法性）
        self.link_endpoints: dict[str, set[str]] = {}
        for l in self.links:
            self.link_endpoints.setdefault(l["linkType"], set()).update([l["source"], l["target"]])

        self.object_types: list[str] = [o["type"] for o in raw.get("objects", [])]

    @staticmethod
    def _resolve_table(type_name: str, existing: set[str]) -> str | None:
        snake = _to_snake(type_name)
        candidates: list[str] = []
        if snake.endswith("y"):
            candidates.append(snake[:-1] + "ies")
        if snake.endswith("s"):
            candidates.append(snake)
        candidates.append(snake + "s")
        candidates.append(snake)
        for c in candidates:
            if c in existing:
                return c
        return None


# ---------------------------------------------------------------------------
# 只读数据访问层
# ---------------------------------------------------------------------------
class ReadOnlyDB:
    """只读 SQLite 访问。连接以 mode=ro 打开——物理上无法写。"""

    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {db_path}")
        # file:...?mode=ro —— 只读；immutable 不设，保证读到最新落盘数据。
        uri = f"file:{db_path}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def tables(self) -> set[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self.conn.execute(sql, params)
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# 调用日志：每次工具调用追加一行 —— "真实发生"的铁证。
# ---------------------------------------------------------------------------
def log_call(tool: str, args: dict, result_rows: int, ok: bool, error: str | None = None) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": args,
        "result_rows": result_rows,
        "ok": ok,
    }
    if error:
        rec["error"] = error
    try:
        with CALL_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # 日志失败不能拖垮服务，但要在 stderr 留痕
        _stderr(f"写 call_log 失败: {e}")


# ---------------------------------------------------------------------------
# 工具实现（全部只读）
# ---------------------------------------------------------------------------
class Tools:
    def __init__(self, db: ReadOnlyDB, onto: Ontology):
        self.db = db
        self.onto = onto
        # severity 枚举从数据里实探（read-only），供 schema 使用
        try:
            sev_rows = self.db.query("SELECT DISTINCT severity FROM risk_events ORDER BY 1")
            self.severities = [r["severity"] for r in sev_rows if r["severity"]]
        except Exception:
            self.severities = ["critical", "high", "medium"]

    # ---- 工具 1：按类型+ID 查单实体 ------------------------------------
    def get_object(self, object_type: str, object_id: str) -> dict:
        table = self.onto.type_to_table.get(object_type)
        if not table:
            raise ToolError(
                f"未知 object_type '{object_type}'。合法取值：{', '.join(self.onto.object_types)}"
            )
        pk = self.onto.type_to_pk[object_type]
        rows = self.db.query(f'SELECT * FROM "{table}" WHERE "{pk}" = ? LIMIT 1', (object_id,))
        if not rows:
            return {"found": False, "object_type": object_type, "object_id": object_id,
                    "message": f"{object_type} {object_id} 不存在"}
        row = rows[0]
        title_key = self.onto.type_to_title.get(object_type)
        return {
            "found": True,
            "object_type": object_type,
            "object_id": object_id,
            "table": table,
            "title": row.get(title_key) if title_key else None,
            "properties": row,
        }

    # ---- 工具 2：查风险事件列表 ----------------------------------------
    def search_risk_events(self, rule_id: str | None = None, severity: str | None = None,
                           limit: int | None = None) -> dict:
        where, params = [], []
        if rule_id:
            where.append("rule_id = ?")
            params.append(rule_id)
        if severity:
            where.append("severity = ?")
            params.append(severity)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        lim = DEFAULT_LIMIT if limit is None else max(1, min(int(limit), MAX_LIMIT))
        sql = (
            "SELECT risk_event_id, rule_id, type, severity, shipment_id, status, "
            "detected_at, root_cause, affected_value_usd "
            f"FROM risk_events{clause} ORDER BY detected_at DESC, risk_event_id LIMIT ?"
        )
        rows = self.db.query(sql, tuple(params) + (lim,))
        return {
            "filter": {"rule_id": rule_id, "severity": severity, "limit": lim},
            "count": len(rows),
            "risk_events": rows,
        }

    # ---- 工具 3：沿本体关系走到关联对象 --------------------------------
    def traverse_link(self, object_type: str, object_id: str, link_type: str) -> dict:
        if object_type not in self.onto.object_types:
            raise ToolError(
                f"未知 object_type '{object_type}'。合法取值：{', '.join(self.onto.object_types)}"
            )
        if link_type not in self.onto.link_types:
            raise ToolError(
                f"未知 link_type '{link_type}'。本体声明的关系：{', '.join(self.onto.link_types)}"
            )

        # 关系在 object_relationships 表里按有向边物化。用户可能想正向或反向走，
        # 故两个方向都查：正向(本对象是 source) + 反向(本对象是 target)。
        fwd = self.db.query(
            "SELECT target_type AS neighbor_type, target_id AS neighbor_id "
            "FROM object_relationships "
            "WHERE source_type=? AND source_id=? AND relationship_type=?",
            (object_type, object_id, link_type),
        )
        rev = self.db.query(
            "SELECT source_type AS neighbor_type, source_id AS neighbor_id "
            "FROM object_relationships "
            "WHERE target_type=? AND target_id=? AND relationship_type=?",
            (object_type, object_id, link_type),
        )
        neighbors = ([{**r, "direction": "outgoing"} for r in fwd] +
                     [{**r, "direction": "incoming"} for r in rev])

        result = {
            "object_type": object_type,
            "object_id": object_id,
            "link_type": link_type,
            "count": len(neighbors),
            "neighbors": neighbors,
        }
        if not neighbors:
            # 空结果时给出该对象实际拥有哪些关系，便于模型自我纠正（可发现性）。
            avail = self.db.query(
                "SELECT relationship_type, 'outgoing' AS dir FROM object_relationships "
                "WHERE source_type=? AND source_id=? "
                "UNION SELECT relationship_type, 'incoming' FROM object_relationships "
                "WHERE target_type=? AND target_id=?",
                (object_type, object_id, object_type, object_id),
            )
            result["hint"] = {
                "message": f"{object_type} {object_id} 在关系 '{link_type}' 上没有邻居",
                "available_links": avail,
            }
        return result


class ToolError(Exception):
    """业务级工具错误（如未知类型）；作为 isError 结果返回，非协议错误。"""


# ---------------------------------------------------------------------------
# 工具定义（tools/list 返回）—— input_schema 的枚举由本体生成（桥 2 预演）。
# ---------------------------------------------------------------------------
def build_tool_defs(onto: Ontology, severities: list[str]) -> list[dict]:
    types_enum = onto.object_types
    links_enum = onto.link_types
    return [
        {
            "name": "get_object",
            "description": (
                "按对象类型与主键 ID 查询单个实体的全部属性。"
                "例如 get_object('Shipment','SHP-2026-0068') 返回该票货的状态、ETA、清关状态等。只读。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "object_type": {"type": "string", "enum": types_enum,
                                    "description": "本体对象类型，如 Shipment / RiskEvent / SalesOrderLine"},
                    "object_id": {"type": "string", "description": "该类型的主键 ID，如 SHP-2026-0068"},
                },
                "required": ["object_type", "object_id"],
            },
        },
        {
            "name": "search_risk_events",
            "description": (
                "查询风险事件列表，可按 rule_id（如 R1..R18）和 severity 过滤。只读。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string", "description": "风险规则编号，如 R1（延误传导）、R3（静默停滞）"},
                    "severity": {"type": "string", "enum": severities, "description": "严重度"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT,
                              "description": f"返回条数，默认 {DEFAULT_LIMIT}，上限 {MAX_LIMIT}"},
                },
                "required": [],
            },
        },
        {
            "name": "traverse_link",
            "description": (
                "沿本体声明的关系(link_type)从给定对象走到关联对象，返回邻居的类型与 ID。"
                "例如 traverse_link('Shipment','SHP-2026-0068','risk_on_shipment') 找出这票货的风险事件。"
                "正反两个方向都会返回。只读。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "object_type": {"type": "string", "enum": types_enum},
                    "object_id": {"type": "string"},
                    "link_type": {"type": "string", "enum": links_enum,
                                  "description": "本体 links[] 声明的关系名，如 risk_on_shipment / so_has_line"},
                },
                "required": ["object_type", "object_id", "link_type"],
            },
        },
    ]


# ---------------------------------------------------------------------------
# JSON-RPC over stdio 主循环
# ---------------------------------------------------------------------------
class Server:
    def __init__(self):
        self.db = ReadOnlyDB(DB_PATH)
        self.onto = Ontology(ONTOLOGY_JSON_PATH, self.db.tables())
        self.tools = Tools(self.db, self.onto)
        self.tool_defs = build_tool_defs(self.onto, self.tools.severities)
        self._dispatch = {
            "get_object": self.tools.get_object,
            "search_risk_events": self.tools.search_risk_events,
            "traverse_link": self.tools.traverse_link,
        }
        if self.onto.unmapped_types:
            _stderr(f"警告：以下对象类型未匹配到表：{self.onto.unmapped_types}")
        _stderr(f"就绪：{len(self.onto.type_to_table)} 类型已映射表，DB={DB_PATH}（只读）")

    # ---- 报文写出（单行 JSON + flush）---------------------------------
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

    # ---- 各方法处理 ----------------------------------------------------
    def handle_initialize(self, params: dict) -> dict:
        # 协议版本协商：回显客户端请求的版本（若支持），否则回落默认版本。
        client_ver = (params or {}).get("protocolVersion")
        return {
            "protocolVersion": client_ver or DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def handle_tools_call(self, params: dict) -> dict:
        name = (params or {}).get("name")
        args = (params or {}).get("arguments", {}) or {}
        fn = self._dispatch.get(name)
        if fn is None:
            log_call(str(name), args, 0, False, "unknown tool")
            return self._tool_result({"error": f"未知工具 '{name}'"}, is_error=True)
        try:
            data = fn(**args)
            rows = self._count_rows(name, data)
            log_call(name, args, rows, True)
            return self._tool_result(data, is_error=False)
        except ToolError as e:
            log_call(name, args, 0, False, str(e))
            return self._tool_result({"error": str(e)}, is_error=True)
        except TypeError as e:
            # 参数不匹配（缺必填/多余参数）
            log_call(name, args, 0, False, f"bad arguments: {e}")
            return self._tool_result({"error": f"参数错误: {e}"}, is_error=True)
        except Exception as e:
            _stderr("工具执行异常:\n" + traceback.format_exc())
            log_call(name, args, 0, False, repr(e))
            return self._tool_result({"error": f"内部错误: {e}"}, is_error=True)

    @staticmethod
    def _count_rows(name: str, data: dict) -> int:
        if name == "get_object":
            return 1 if data.get("found") else 0
        if name == "search_risk_events":
            return data.get("count", 0)
        if name == "traverse_link":
            return data.get("count", 0)
        return 0

    @staticmethod
    def _tool_result(data: dict, is_error: bool) -> dict:
        # MCP 约定：工具结果放 content[].text（文本），业务错误用 isError 标记。
        return {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
            "isError": is_error,
        }

    # ---- 主循环 --------------------------------------------------------
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
            is_notification = "id" not in msg  # 通知无 id，不回响应
            params = msg.get("params") or {}

            try:
                if method == "initialize":
                    self._reply(req_id, self.handle_initialize(params))
                elif method == "notifications/initialized" or method == "initialized":
                    pass  # 通知，无响应
                elif method == "tools/list":
                    self._reply(req_id, {"tools": self.tool_defs})
                elif method == "tools/call":
                    self._reply(req_id, self.handle_tools_call(params))
                elif method == "ping":
                    self._reply(req_id, {})
                elif method in ("resources/list", "prompts/list"):
                    # 未声明这些能力；返回空列表以最大化客户端兼容性。
                    key = "resources" if method.startswith("resources") else "prompts"
                    self._reply(req_id, {key: []})
                elif is_notification:
                    pass  # 其他通知一律忽略
                else:
                    self._reply(req_id, error={"code": -32601, "message": f"Method not found: {method}"})
            except Exception as e:
                _stderr("主循环异常:\n" + traceback.format_exc())
                if not is_notification:
                    self._reply(req_id, error={"code": -32603, "message": f"Internal error: {e}"})


def main() -> None:
    # stdout/stdin 统一 utf-8，避免平台换行/编码问题污染协议流。
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        Server().serve()
    except Exception as e:
        _stderr("启动失败:\n" + traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
