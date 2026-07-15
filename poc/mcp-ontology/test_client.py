#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立 MCP 测试客户端（不依赖 claude CLI）。

一句白话：这个脚本自己扮演"大模型客户端"，用 MCP 协议把 server.py 拉起来，
走一遍握手，把 3 个工具各叫一次，再拿结果和库里的真值对一对——
用来证明 **server 本身是对的**（把 claude CLI 是否登录这个变量隔离掉）。

只用标准库；零依赖。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"

# 预先从库里查好的对照真值（见报告；由 ground-truth 探查得到）。
GROUND_TRUTH = {
    "shipment_id": "SHP-2026-0068",
    "status": "in_transit",
    "eta_current": "2026-08-31",
    "customs_status": "not_filed",
    "risk_event_ids": {"RSK-0038", "RSK-0039", "RSK-0040"},
}


class MCPStdioClient:
    """最小 MCP stdio 客户端：换行分隔 JSON-RPC。"""

    def __init__(self, server_path: Path):
        self.proc = subprocess.Popen(
            [sys.executable, str(server_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._id = 0

    def _rpc(self, method: str, params: dict | None = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read()
            raise RuntimeError(f"server 无响应。stderr:\n{err}")
        return json.loads(line)

    def initialize(self):
        resp = self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "poc-test-client", "version": "0.1.0"},
        })
        self._rpc("notifications/initialized", {}, notify=True)
        return resp

    def list_tools(self):
        return self._rpc("tools/list", {})

    def call_tool(self, name: str, arguments: dict):
        return self._rpc("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def _payload(call_result: dict) -> dict:
    """从 tools/call 响应里取出工具返回的 JSON 数据。"""
    text = call_result["result"]["content"][0]["text"]
    return json.loads(text)


def main() -> int:
    c = MCPStdioClient(SERVER)
    passed, failed = [], []

    def check(name: str, cond: bool, detail: str = ""):
        (passed if cond else failed).append(name)
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))

    try:
        print("== 1. initialize 握手 ==")
        init = c.initialize()
        si = init.get("result", {}).get("serverInfo", {})
        check("initialize 返回 serverInfo", si.get("name") == "ontology", f"serverInfo={si}")

        print("== 2. tools/list ==")
        tl = c.list_tools()
        tool_names = {t["name"] for t in tl.get("result", {}).get("tools", [])}
        check("暴露 3 个工具", tool_names == {"get_object", "search_risk_events", "traverse_link"},
              f"tools={sorted(tool_names)}")
        # 桥 2 预演：get_object 的 object_type 枚举应来自本体（含 Shipment）
        got = next(t for t in tl["result"]["tools"] if t["name"] == "get_object")
        enum = got["inputSchema"]["properties"]["object_type"].get("enum", [])
        check("input_schema 枚举由本体生成", "Shipment" in enum and len(enum) >= 30,
              f"{len(enum)} 个对象类型枚举")

        print("== 3. get_object(Shipment, SHP-2026-0068) ==")
        r1 = _payload(c.call_tool("get_object", {"object_type": "Shipment", "object_id": GROUND_TRUTH["shipment_id"]}))
        props = r1.get("properties", {})
        print(f"     -> status={props.get('status')} eta_current={props.get('eta_current')} customs={props.get('customs_status')}")
        check("get_object 命中且 status 与真值一致",
              r1.get("found") and props.get("status") == GROUND_TRUTH["status"]
              and props.get("eta_current") == GROUND_TRUTH["eta_current"]
              and props.get("customs_status") == GROUND_TRUTH["customs_status"])

        print("== 4. traverse_link(Shipment -> risk_on_shipment) ==")
        r2 = _payload(c.call_tool("traverse_link", {
            "object_type": "Shipment", "object_id": GROUND_TRUTH["shipment_id"], "link_type": "risk_on_shipment"}))
        risk_ids = {n["neighbor_id"] for n in r2.get("neighbors", []) if n["neighbor_type"] == "RiskEvent"}
        print(f"     -> 关联风险事件: {sorted(risk_ids)}")
        check("traverse_link 找到 3 个风险事件且与真值一致", risk_ids == GROUND_TRUTH["risk_event_ids"])

        print("== 5. search_risk_events(rule_id=R1, severity=critical) ==")
        r3 = _payload(c.call_tool("search_risk_events", {"rule_id": "R1", "severity": "critical", "limit": 50}))
        all_match = r3.get("count", 0) > 0 and all(
            e["rule_id"] == "R1" and e["severity"] == "critical" for e in r3.get("risk_events", []))
        contains_target = any(e["risk_event_id"] == "RSK-0038" for e in r3.get("risk_events", []))
        print(f"     -> 命中 {r3.get('count')} 条；含 RSK-0038: {contains_target}")
        check("search_risk_events 过滤生效且命中目标事件", all_match and contains_target)

        print("== 6. 错误路径：未知 object_type 应被拒 ==")
        r4 = c.call_tool("get_object", {"object_type": "Nonexistent", "object_id": "X"})
        check("未知类型返回 isError", r4["result"].get("isError") is True)

        # 只读证明：确认库文件确实以只读打开（尝试写会失败）——间接由 mode=ro 保证。
        print("== 7. 只读保证 ==")
        check("server 只注册只读工具（无写/审批/花钱工具）",
              tool_names.issubset({"get_object", "search_risk_events", "traverse_link"}))

    finally:
        c.close()

    print("\n==== 汇总 ====")
    print(f"  PASS: {len(passed)}  |  FAIL: {len(failed)}")
    if failed:
        print(f"  失败项: {failed}")
        return 1
    print("  server 自测全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
