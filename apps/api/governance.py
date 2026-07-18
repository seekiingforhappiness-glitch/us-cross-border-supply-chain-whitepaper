#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 可信度正脸——治理放权档位摘要端点（波U·U3，spec docs/superpowers/specs/2026-07-16-waveU-user-facing.md）。

一句话：给驾驶舱「AI 运营账卡」一个只读端点，把 data/gating_report.json（放权门禁引擎的
display-only 产物）翻译成老板语言的"当前档位 + 白话为什么"，绝不触碰任何工具授权。

红线（本模块的存在理由）：
  · **只读 JSON 文件**——绝不 import agent.gating / 不实时算档 / 不碰 build_tool_defs/ROLE_PERMS/
    FORBIDDEN。报告由离线的放权门禁引擎生成落盘，本端点纯搬运（display-only 语义原样传达）。
  · 文件不存在 → HTTP 200 + {available:false, reason} 诚实空态（绝不 404/500——前端要能画出
    "治理报告尚未生成"的空态卡，而非报错）。JSON 损坏同样降级为 available:false，不放大为故障。
  · display_only 声明原样透传：报告里 display_only=true 意为"只算档不放权"，这条必须一字不改
    传到前端，避免任何"档位=已授权"的误读。

路由工厂由 main.py 尾部注入挂载（同 cockpit/decisions：本模块不反向 import main → 零循环导入）。
报告路径为模块级常量 GATING_REPORT_PATH，请求时现读——测试可 monkeypatch 该常量指到临时文件
（存在/不存在两态），无需起真服务、无需碰真报告文件。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

_HERE = Path(__file__).resolve().parent          # apps/api/
REPO_ROOT = _HERE.parent.parent                  # 仓库根（同 cockpit 路径推导，不依赖 cwd）
GATING_REPORT_PATH = REPO_ROOT / "data" / "gating_report.json"


def _strip_paths(sources):
    """证据来源里的任何字符串值若形如路径，只留 basename——不泄露主机目录结构（对抗复核发现1）。"""
    if not isinstance(sources, dict):
        return {}
    out = {}
    for k, v in sources.items():
        if isinstance(v, str) and "/" in v:
            out[k] = Path(v).name
        elif isinstance(v, dict):
            out[k] = _strip_paths(v)
        else:
            out[k] = v
    return out


def _summarize(report: dict) -> dict:
    """把 gating_report.json 收敛成驾驶舱 AI 账卡要的字段（整体档位分布 + 每域白话 + config 版本 +
    display_only 原样）。只做字段挑选/重排与缺键防御，绝不重算任何档位/阈值（那是离线引擎的活）。"""
    cfg = report.get("config") or {}
    summary = report.get("summary") or {}
    # 每域压缩为 {name, domain, group, tier, next_tier, rate, ci, n, escalation, gaps(白话)}——
    # gaps 本就是引擎写好的白话短语（"缺样本：n=0 < 30"…），原样传即"白话为什么差一档"。
    domains = []
    for d in report.get("domains", []):
        domains.append({
            "domain": d.get("domain"),
            "name": d.get("plain") or d.get("domain"),   # plain=老板语言域名（"延误击穿承诺"）
            "group": d.get("group"),
            "tier": d.get("tier"),
            "next_tier": d.get("next_tier"),
            "n": d.get("n"),
            "hits": d.get("hits"),
            "rate": d.get("rate"),
            "ci": d.get("ci"),
            "low_sample": d.get("low_sample"),
            "escalation": d.get("escalation"),
            "gaps": d.get("gaps", []),                    # 白话差距清单，原样透传
        })
    return {
        "available": True,
        # display_only 原样透传（报告顶层键）：只算档不放权的声明，一字不改
        "display_only": report.get("display_only"),
        "generated_at": report.get("generated_at"),
        "config_version": cfg.get("version"),
        "config_status": cfg.get("status"),
        "ladder": cfg.get("ladder", []),                  # 四档阶梯 + 每档白话（老板语言"为什么/是什么"）
        "promotions": cfg.get("promotions", {}),          # 升档门槛 + _plain 白话
        "tier_distribution": summary.get("by_tier", {}),  # 整体档位分布（各档域数）
        "domain_count": summary.get("domain_count"),
        "reached_auto": summary.get("reached_auto", []),
        "summary_note": summary.get("note"),
        # 算档证据来源。对抗复核发现1：报告里的库路径是主机绝对路径（含 OS 用户名/目录结构），
        # HTTP 响应不得外泄——只保留文件名与行数证据，路径取 basename。
        "sources": _strip_paths(report.get("sources", {})),
        "telemetry": report.get("telemetry", {}),         # 真实 AI 调用遥测（只读）
        "honest_note": report.get("honest_note"),
        "domains": domains,
    }


def build_governance_router() -> APIRouter:
    """路由工厂（main.py 尾部挂载，注入式风格同 cockpit/decisions，本模块不反向 import main）。
    无 DB 依赖——gating_report.json 是世界无关的离线治理产物（源=shadow.sqlite + 主库真 AI 证据），
    故不接 X-World/get_db_path。报告路径现读 GATING_REPORT_PATH 模块常量（测试可 monkeypatch）。"""
    router = APIRouter(prefix="/governance", tags=["governance"])

    @router.get("/gating")
    def governance_gating() -> dict:
        """AI 放权档位摘要（display-only）：整体档位分布 + 每域 {name,tier,rate,ci,n,gap 白话} +
        config 版本 + display_only 原样。文件缺失/损坏 → 200 + {available:false, reason} 诚实空态。"""
        path = GATING_REPORT_PATH                          # 模块常量现读（测试 monkeypatch 生效）
        if not path.exists():
            return {"available": False,
                    "reason": f"治理报告尚未生成（{path.name} 不存在）——放权门禁引擎需先离线跑一遍"
                              f"落盘 data/gating_report.json，本端点只读不算。"}
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            return {"available": False,
                    "reason": f"治理报告文件存在但无法解析（{type(exc).__name__}）：{exc}——"
                              f"如实报缺，绝不用旧值/猜测冒充当前档位。"}
        if not isinstance(report, dict):
            return {"available": False,
                    "reason": "治理报告文件格式异常（顶层非对象），如实报缺。"}
        return _summarize(report)

    return router
