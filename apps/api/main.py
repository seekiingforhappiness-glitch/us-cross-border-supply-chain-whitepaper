#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M5 FastAPI 服务层骨架（API 层 plan `docs/superpowers/plans/2026-07-14-api-layer-bridges-mcp.md`
M5 节）——驾驶舱与透视镜 v3 的数据/动作底座。

设计红线（全部路由消费本体/运行时/M3 生成层，禁止平行硬编码任何类型/权限/字段清单）：
  · GET  /ontology                          本体自描述（version + 对象/关系/动作清单摘要）
  · GET  /objects/{type}                    列表 + 等值过滤（type∈本体34类型，否则422；
                                             过滤列名白名单=该类型本体 properties）
  · GET  /objects/{type}/{id}               单对象；M3 Pydantic 模型校验语义 + X-Role 脱敏
  · GET  /objects/{type}/{id}/links/{link}  调 pipeline.ontology_runtime.traverse 同源遍历
  · POST /actions/{name}                    仅 exposed_as_tool=true 的 6 个动作；X-Role 经
                                             build_role_perms 鉴权；写入原样走 app.actions/
                                             app.admission_actions 既有函数（maker-checker 语义原封）

鉴权原型级：请求头 X-Role（缺省 ops），与 Streamlit 同一权限模型，不引入新认证栈。
GET 一律走 mode=ro 只读连接（物理只读红线）；POST 走 app.actions.connect() 的连接管理
（该函数已 row_factory=Row，与 app 层各动作函数的期望一致，不自建写路径）。

字段脱敏复用 agent/mcp_server.py::SensitiveFieldMasker——净验证：该类只依赖 (ontology dict,
role, mask) 三个纯参数，无 MCP 协议/会话耦合，可直接 import 复用，不需要最小抽取（plan M5
预留的"若无法干净复用则抽取"条款本次未触发）。
"""
from __future__ import annotations

import os
import sqlite3
import sys

from pydantic import ValidationError
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# ── 路径解析：不依赖 uvicorn 启动时的 cwd，一律相对本文件定位仓库根（同 agent/mcp_server.py 惯例）──
_HERE = Path(__file__).resolve().parent                      # apps/api/
REPO_ROOT = _HERE.parent.parent                               # 仓库根
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app.actions as app_actions                                       # noqa: E402
import app.admission_actions as app_admission_actions                    # noqa: E402
from app.command_bus import execute_command                             # noqa: E402  波2 写总线（三门贯通）
from agent.mcp_server import SensitiveFieldMasker                        # noqa: E402
from pipeline.ontology_models import MODEL_BY_TABLE, MODEL_BY_TYPE       # noqa: E402
from pipeline.ontology_runtime import (                                  # noqa: E402
    build_role_perms, load_ontology, snake_case, traverse as onto_traverse)

DEFAULT_DB_PATH = REPO_ROOT / "data" / "ontology.sqlite"
SIMWORLD_DB_PATH = REPO_ROOT / "data" / "simworld.sqlite"    # 模拟世界库（X-World: sim 的解析目标）
DATAGEN_CFG_PATH = REPO_ROOT / "config" / "datagen.yaml"
ONTOLOGY_JSON_PATH = REPO_ROOT / "ontology" / "control-tower-ontology.json"

with open(DATAGEN_CFG_PATH, encoding="utf-8") as _fh:
    _DATAGEN_CFG = yaml.safe_load(_fh)
# 仿真时钟：与 Streamlit AS_OF（app/streamlit_app.py）、AgentSession.as_of（agent/tools.py）
# 同一来源（config/datagen.yaml window.as_of），不新增平行日期常量。
AS_OF = _DATAGEN_CFG["window"]["as_of"]

# 写动作 actor 标识：仿 agent/tools.py 的 AI_ACTOR="ai-agent" 常量模式，供审计留痕辨识调用来源。
# 请求体按 plan「原样透传给 app 层函数」处理——不从 body 里再抽取/覆盖 actor 字段。
API_ACTOR = "api-caller"


def get_ontology_dict() -> dict:
    """本体 JSON（pipeline.ontology_runtime 进程内缓存，读一次不重复解析）。"""
    return load_ontology(str(ONTOLOGY_JSON_PATH))


# ═══════════════════════════════════════════════════════════════════════════
# 本体派生的静态映射：启动时算一次（与 ontology_runtime「解释型/启动时读一次并缓存」同一哲学）。
# 全部由本体 JSON + M3 生成的 pipeline.ontology_models 注册表推导，零手写类型/表名字面量。
# ═══════════════════════════════════════════════════════════════════════════
_ONTO = get_ontology_dict()

# type → table：靠 MODEL_BY_TABLE / MODEL_BY_TYPE 两个生成注册表按「同一 Pydantic 类」配对得出
# （34 个对象在本体里都未声明显式 table，真实表名由 pipeline.generate_models 的复数化规则决定；
# 该规则是 pipeline.ontology_runtime 的私有实现细节，此处不重新实现/不猜测，改用两个公开生成
# 注册表的类恒等匹配复用同一结果——与 traverse() 内部用的表名保证同源，不会漂移）。
_TABLE_BY_MODEL_ID = {id(cls): table for table, cls in MODEL_BY_TABLE.items()}
TABLE_BY_TYPE: dict[str, str] = {t: _TABLE_BY_MODEL_ID[id(cls)] for t, cls in MODEL_BY_TYPE.items()}

_ONTO_OBJECTS = {o["type"]: o for o in _ONTO["objects"]}
PK_BY_TYPE: dict[str, str] = {t: o["primaryKey"] for t, o in _ONTO_OBJECTS.items()}
PROPS_BY_TYPE: dict[str, set[str]] = {
    t: {p["name"] for p in o["properties"]} for t, o in _ONTO_OBJECTS.items()
}

_ACTION_MODULES = (app_actions, app_admission_actions)


def _exposed_actions(ontology: dict) -> list[dict]:
    return [a for a in ontology["actions"] if a.get("exposed_as_tool") is True]


def _resolve_action_func(action_name: str):
    """action['name']（本体 PascalCase）→ 实现函数：在 app.actions / app.admission_actions
    两个模块（plan M5 Files 列明的实现所在）里找 snake_case(action_name) 同名可调用对象。
    动态解析而非手写 6 项字典——本体新增/改名 exposed 动作后只要 app 层有同名函数即自动生效，
    不需要同步改这层路由代码（同样是「消费本体层、不平行硬编码」的体现）。"""
    fn_name = snake_case(action_name)
    for mod in _ACTION_MODULES:
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            return fn
    return None


def _action_alias_map(ontology: dict) -> dict[str, dict]:
    """{动作名(PascalCase): action 字典, snake_case(动作名): action 字典}——仅 exposed_as_tool=true
    的动作在此出现。冻结区/其余 25 个动作不在此字典里 ⇒ POST /actions/{name} 对它们天然 404
    （"路由生成层就不存在"，与 M4 MCP server 的 build_readonly_tool_defs 同一保证方式）。
    两种别名都收录：GET /ontology 里 actions[].name 是 PascalCase（本体原始声明），
    而 aiQueryTools/MCP 工具命名一律 snake_case——客户端用任一形式都应能调用。"""
    out: dict[str, dict] = {}
    for a in _exposed_actions(ontology):
        out[a["name"]] = a
        out[snake_case(a["name"])] = a
    return out


ACTION_BY_ALIAS = _action_alias_map(_ONTO)

# 启动期自检（fail fast）：exposed 动作必须都能解析到实现函数——防「本体声明可暴露」与
# 「代码里其实没有」这种声明-实现悄悄漂移（越晚发现越贵，宁可服务直接起不来）。
_unresolved = [a["name"] for a in _exposed_actions(_ONTO) if _resolve_action_func(a["name"]) is None]
if _unresolved:
    raise RuntimeError(
        f"本体声明 exposed_as_tool=true 但未在 app.actions/app.admission_actions 找到实现函数："
        f"{_unresolved}（snake_case 命名契约漂移，需先修复再起服务）")


def _require_known_type(object_type: str) -> None:
    if object_type not in MODEL_BY_TYPE:
        raise HTTPException(
            status_code=422,
            detail=f"未知对象类型 '{object_type}'——本体共 {len(MODEL_BY_TYPE)} 个类型："
                   f"{sorted(MODEL_BY_TYPE)}")


# ═══════════════════════════════════════════════════════════════════════════
# 依赖注入：DB 路径 / 只读连接。测试用 app.dependency_overrides 直接换路径（fastapi 标准做法，
# 见 apps/api/test_api.py），不依赖环境变量重载/进程重启。
# ═══════════════════════════════════════════════════════════════════════════
# X-World 请求头 → 世界库解析别名（U1）：verify/verification=验证世界，sim/simulation/simworld=
# 模拟世界。大小写不敏感、去空白。未知值 fail-fast 422（不静默回退默认，避免"以为切了世界其实没切"）。
_WORLD_DB_ALIASES = {
    "verify": "verification", "verification": "verification",
    "sim": "simulation", "simulation": "simulation", "simworld": "simulation",
}


def get_db_path(x_world: str | None = Header(default=None, alias="X-World")) -> str:
    """读端点的双世界解析（U1）+ 向后兼容。优先级：
      1. X-World 请求头（驾驶舱世界切换钮的传导）：verify→data/ontology.sqlite、
         sim→data/simworld.sqlite；未知值 → 422 fail-fast。不重启进程即切库。
      2. 缺省（无 X-World）→ 现行 ONTOLOGY_DB / ONTOLOGY_DB_PATH 环境变量 → DEFAULT_DB_PATH，
         **逐字等价于升级前**（byte-identical 向后兼容：既有测试/启动脚本零感知）。ONTOLOGY_DB_PATH
         是 agent/mcp_server.py 的既有同名变量，兼容识别、ONTOLOGY_DB 优先——只加不减。
    DEFAULT_DB_PATH/SIMWORLD_DB_PATH 用模块全局在调用时解析——测试 monkeypatch 这两个常量即可用
    临时副本验证 X-World 路由，不碰真库（GET 全 mode=ro，读真库也不改 md5，但用副本更守纪律）。
    既有测试用 app.dependency_overrides 整体替换本依赖（lambda 无参），X-World 形参不影响其生效。
    裸函数直调兼容：FastAPI 只在真实请求的 DI 流程里才会把 Header(...) 哨兵替换成实际请求头值/None；
    脱离 DI 直接 Python 调用 get_db_path() 时，x_world 会绑定到 Header(...) 这个 FieldInfo 哨兵对象本身
    （非 str），故先归一化为 None 再判断，使裸调用与 DI 调用行为一致（test_api.py 显式覆盖此路径）。"""
    if not isinstance(x_world, str):
        x_world = None
    if x_world and x_world.strip():
        world = _WORLD_DB_ALIASES.get(x_world.strip().lower())
        if world == "verification":
            return str(DEFAULT_DB_PATH)
        if world == "simulation":
            return str(SIMWORLD_DB_PATH)
        raise HTTPException(
            422, detail=f"未知 X-World 值 '{x_world}'——仅支持 verify（验证世界）/ sim（模拟世界）"
                        f"（大小写不敏感；也认 verification/simulation/simworld）。")
    return os.environ.get("ONTOLOGY_DB", os.environ.get("ONTOLOGY_DB_PATH", str(DEFAULT_DB_PATH)))


def get_ro_connection(db_path: str = Depends(get_db_path)):
    """GET 路由专用：mode=ro 物理只读连接（红线：GET 一律不可写库）。请求结束即关闭。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


app = FastAPI(
    title="控制塔 Ontology API",
    version=_ONTO["version"],
    description="M5 FastAPI 服务层骨架——驾驶舱与透视镜 v3 的数据/动作底座；全部路由消费本体/运行时层。",
)

# CORS：仅放行本地两个开发端口（驾驶舱 5174、透视镜 5173，见 apps/cockpit/vite.config.ts /
# apps/builder-console 同款端口约定）——驾驶舱当前经 Vite dev/preview 代理同源访问，不依赖这层；
# 补上是为未来生产环境静态部署（不经 Vite 代理）、以及本地直连调试预留。生产部署时应改成从配置/
# 环境变量读取允许源，而非像现在这样硬编码 localhost（挂账，不在本单授权范围内一并做）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════
# §二 API 契约硬化：统一错误信封（向后兼容——既有 detail 键**逐字保留**，仅新增 error 对象）。
# error = {code(机器码), message(白话), detail?(原始非字符串 detail 的容器)}；既有测试读 detail 不受影响。
# _StateConflict（写锁复检 / 幂等在飞冲突，spec §二）→ HTTP 409。
# ═══════════════════════════════════════════════════════════════════════════
_ERROR_CODE_BY_STATUS = {
    400: "bad_request", 403: "forbidden", 404: "not_found",
    409: "state_conflict", 422: "unprocessable_entity", 500: "internal_error",
}
_GENERIC_ERROR_MSG = {
    400: "请求有误", 403: "无权执行", 404: "资源不存在",
    409: "状态冲突（并发写或提案已变更）", 422: "请求无法处理", 500: "服务内部错误",
}


def _error_envelope(status_code: int, detail) -> dict:
    """{detail:<原样保留>, error:{code,message,detail?}}。detail 为字符串（现存全部情形）时
    error.message 即该白话；非字符串（如校验错误列表）时 message 用通用白话、原 detail 落 error.detail。"""
    code = _ERROR_CODE_BY_STATUS.get(status_code, f"http_{status_code}")
    if isinstance(detail, str):
        error = {"code": code, "message": detail}
    else:
        error = {"code": code, "message": _GENERIC_ERROR_MSG.get(status_code, "请求未成功"),
                 "detail": detail}
    return {"detail": detail, "error": error}


@app.exception_handler(StarletteHTTPException)
def _http_exception_envelope(request: Request, exc: StarletteHTTPException):
    """既有 HTTPException 响应统一裹信封：detail 逐字保留 + 附 error 对象。透传原 headers（如有）。"""
    return JSONResponse(status_code=exc.status_code,
                        content=_error_envelope(exc.status_code, exc.detail),
                        headers=getattr(exc, "headers", None))


@app.exception_handler(app_actions._StateConflict)
def _state_conflict_envelope(request: Request, exc: app_actions._StateConflict):
    """写锁复检 / 幂等在飞冲突 → 409（spec §二 _StateConflict→HTTP 409）。总线抛出即到这。"""
    return JSONResponse(status_code=409, content=_error_envelope(409, str(exc)))


# 所连库文件名 → 世界标识（驾驶舱地基单：双世界切换的可见锚点）：ontology.sqlite=验证世界
# （datagen 种子库，规则档案 R/P=1.000 对照源）；simworld.sqlite=模拟世界（14 个月连续活世界，
# 见 sim/store.py 头注）；其他文件名原样回退成自己——新库先诚实标注文件名，不强行归类成
# 已知两个世界之一，避免误导。
_WORLD_LABELS = {"ontology.sqlite": "verification", "simworld.sqlite": "simulation"}


def _infer_world(db_path: str) -> str:
    return _WORLD_LABELS.get(Path(db_path).name, Path(db_path).name)


# ═══════════════════════════════════════════════════════════════════════════
# GET /ontology —— 本体自描述
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/ontology")
def get_ontology_summary(db_path: str = Depends(get_db_path)) -> dict:
    onto = get_ontology_dict()
    objects = [{
        "type": o["type"], "primaryKey": o["primaryKey"], "titleKey": o.get("titleKey"),
        "ownerRole": o.get("ownerRole"), "table": TABLE_BY_TYPE[o["type"]],
        "properties": o["properties"],
    } for o in onto["objects"]]
    links = [{
        "linkType": l["linkType"], "source": l["source"], "target": l["target"],
        "cardinality": l.get("cardinality"), "status": l.get("status", "active"),
        "storage": l.get("storage"),
    } for l in onto["links"]]
    actions = [{
        "name": a["name"], "id": a.get("id"),
        "permission_key": a.get("permission_key", a["name"]),
        "enforcement": a.get("enforcement", "role_dict"),
        "exposed_as_tool": bool(a.get("exposed_as_tool", False)),
        "ai_executable": a.get("ai_executable"),
        "executors": a.get("executors", []),
    } for a in onto["actions"]]
    return {
        "version": onto["version"], "name": onto.get("name"), "displayName": onto.get("displayName"),
        "world": _infer_world(db_path),
        "roles": onto.get("roles", []),
        "objects": objects, "links": links, "actions": actions,
        "summary": {
            "object_types": len(objects), "links": len(links), "actions": len(actions),
            "exposed_actions": sum(1 for a in actions if a["exposed_as_tool"]),
            "frozen_actions": sum(1 for a in actions if a["ai_executable"] == "frozen"),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# GET /objects/{type} —— 列表 + 等值过滤
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/objects/{type}")
def list_objects(type: str, request: Request,
                  x_role: str = Header(default="ops", alias="X-Role"),
                  con: sqlite3.Connection = Depends(get_ro_connection),
                  db_path: str = Depends(get_db_path)) -> dict:
    _require_known_type(type)
    qp = dict(request.query_params)
    # §二 keyset 游标翻页：cursor 参数**在场**才启用（缺省行为逐字节不变，见下方 not cursor_mode 分支）。
    cursor_mode = "cursor" in qp
    cursor = qp.pop("cursor", None)
    raw_limit = qp.pop("limit", None)
    if raw_limit in (None, ""):
        limit = 100
    else:
        try:
            limit = int(raw_limit)
        except ValueError:
            raise HTTPException(422, detail=f"limit 必须是整数，收到 '{raw_limit}'")
    if limit <= 0:
        raise HTTPException(422, detail="limit 必须 > 0")

    valid_cols = PROPS_BY_TYPE[type]
    bad_cols = sorted(set(qp) - valid_cols)
    if bad_cols:
        raise HTTPException(
            422, detail=f"未知过滤字段 {bad_cols}——{type} 合法属性：{sorted(valid_cols)}")

    table = TABLE_BY_TYPE[type]
    model = MODEL_BY_TYPE[type]
    masker = SensitiveFieldMasker(get_ontology_dict(), x_role)

    if not cursor_mode:
        # 无 cursor：与升级前**逐字节一致**（无 ORDER BY、响应无 next_cursor 字段）——加用例钉死。
        where_sql = " AND ".join(f'"{k}"=?' for k in qp)
        sql = f'SELECT * FROM "{table}"' + (f" WHERE {where_sql}" if where_sql else "") + " LIMIT ?"
        rows = con.execute(sql, (*qp.values(), limit)).fetchall()
        items = [model.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        masker.mask_value(items)
        # world 信封字段（U1）：既有字段全不动，仅附加所连世界标识；单对象端点无信封故不加（保对象契约）。
        return {"type": type, "world": _infer_world(db_path),
                "count": len(items), "limit": limit, "items": items}

    # cursor 在场 → keyset：ORDER BY 主键 + WHERE pk > cursor（cursor 非空时）。主键全序稳定 ⇒
    # 逐页 next_cursor 串联的并集 = 全量、无重漏（加用例钉死）。首页传空 cursor（cursor=）即从头翻。
    pk = PK_BY_TYPE[type]
    where_clauses, params = [], []
    if cursor:                                        # 空串=从头，不加下界；非空=严格大于上页末键
        where_clauses.append(f'"{pk}" > ?')
        params.append(cursor)
    for k, v in qp.items():                           # 保留既有等值过滤（与无 cursor 分支同白名单）
        where_clauses.append(f'"{k}"=?')
        params.append(v)
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    sql = f'SELECT * FROM "{table}"{where_sql} ORDER BY "{pk}" LIMIT ?'
    params.append(limit)
    rows = con.execute(sql, params).fetchall()
    # 满页才给下页游标（末行主键）；不足一页即到底（next_cursor=None）。游标取自原始行主键，不受脱敏影响。
    next_cursor = rows[-1][pk] if len(rows) == limit else None
    items = [model.model_validate(dict(r)).model_dump(mode="json") for r in rows]
    masker.mask_value(items)
    return {"type": type, "world": _infer_world(db_path),
            "count": len(items), "limit": limit, "items": items, "next_cursor": next_cursor}


# ═══════════════════════════════════════════════════════════════════════════
# GET /objects/{type}/{id} —— 单对象
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/objects/{type}/{id}")
def get_object(type: str, id: str,
                x_role: str = Header(default="ops", alias="X-Role"),
                con: sqlite3.Connection = Depends(get_ro_connection)) -> dict:
    _require_known_type(type)
    table, pk = TABLE_BY_TYPE[type], PK_BY_TYPE[type]
    row = con.execute(f'SELECT * FROM "{table}" WHERE "{pk}"=?', (id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail=f"{type} '{id}' 不存在")

    model = MODEL_BY_TYPE[type]
    try:
        obj = model.model_validate(dict(row)).model_dump(mode="json")
    except ValidationError as exc:
        # 读路径韧性：历史/模拟数据违反本体契约时不 500——原样返回并如实暴露违规清单
        # （校验的强制口岸在写路径与 build 期 enforce；读端点的职责是"呈现 + 指出问题"。
        #   例：simworld 的 S1/S2 历史行存在枚举外值/缺列，对齐工作挂账 sim 侧，见 STATUS）
        obj = dict(row)
        obj["_validation_warnings"] = [
            {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
            for e in exc.errors()]
    SensitiveFieldMasker(get_ontology_dict(), x_role).mask_value(obj)
    return obj


# ═══════════════════════════════════════════════════════════════════════════
# GET /objects/{type}/{id}/links/{link} —— M3 traverse 同源遍历
# ═══════════════════════════════════════════════════════════════════════════
@app.get("/objects/{type}/{id}/links/{link}")
def get_object_links(type: str, id: str, link: str,
                      con: sqlite3.Connection = Depends(get_ro_connection),
                      db_path: str = Depends(get_db_path)) -> dict:
    _require_known_type(type)
    try:
        neighbor_ids = onto_traverse(con, type, id, link)
    except ValueError as exc:                      # declared_only / 未知关系 / 端点不符
        raise HTTPException(422, detail=str(exc))
    return {"source_type": type, "world": _infer_world(db_path),     # world 信封字段（U1）
            "source_id": id, "link_type": link,
            "count": len(neighbor_ids), "neighbor_ids": neighbor_ids}


# ═══════════════════════════════════════════════════════════════════════════
# POST /actions/{name} —— 仅 exposed_as_tool=true 的 6 个动作
# ═══════════════════════════════════════════════════════════════════════════
@app.post("/actions/{name}")
def post_action(name: str, body: dict = Body(default={}),
                 x_role: str = Header(default="ops", alias="X-Role"),
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                 db_path: str = Depends(get_db_path)) -> dict:
    action = ACTION_BY_ALIAS.get(name)
    if action is None:                              # 冻结区/其余 25 个动作 = 路由生成层就不存在
        exposed = sorted({a["name"] for a in _exposed_actions(get_ontology_dict())})
        raise HTTPException(
            404, detail=f"未知或未向 API 开放的动作 '{name}'——仅 {exposed} 可 POST"
                        "（冻结区/其余动作在路由生成层就不存在，全局红线3）")

    fn = _resolve_action_func(action["name"])
    if fn is None:  # pragma: no cover — 启动期自检已保证不会发生，此处仅防御
        raise HTTPException(
            500, detail=f"动作 '{action['name']}' 声明为 exposed_as_tool 但未找到实现函数（内部配置错误）")

    # X-Role 经 build_role_perms 鉴权：ROLE_PERMS/ADM_PERMS 本身就是 build_role_perms(本体) 的
    # 切片（app/actions.py、app/admission_actions.py 模块级常量），所以下面无论 permitted 与否都
    # 照常调用 fn——它会用同一份权限数据再判一次并在拒绝时走自己的 _denied()（写 action_log
    # denied 审计）。这里独立算 permitted 只是为了选 HTTP 状态码，不重建一条平行写路径。
    permission_key = action.get("permission_key", action["name"])
    allowed_roles = build_role_perms(get_ontology_dict()).get(permission_key, set())
    permitted = x_role in allowed_roles

    # 波2：写入经 execute_command（写总线，spec §一.4）——落 commands 台账 + Idempotency-Key 透传总线，
    # 再原样调既有 fn（maker-checker/门禁语义原封）。幂等键命中 → 原样回首次结果；在飞冲突 → 总线抛
    # _StateConflict → 上面注册的处理器映射 409。签名不匹配的 TypeError 仍就地映射 422（向后兼容）。
    con = app_actions.connect(db_path)
    try:
        try:
            result = execute_command(con, action=action["name"], params=body,
                                     actor=API_ACTOR, role=x_role, as_of=AS_OF,
                                     action_func=fn, idempotency_key=idempotency_key)
        except TypeError as exc:
            raise HTTPException(
                422, detail=f"请求体参数与动作 '{action['name']}' 签名不匹配：{exc}")
    finally:
        con.close()

    if not permitted:
        raise HTTPException(
            403, detail=result.get("error") or f"角色 '{x_role}' 无权执行 '{action['name']}'（已记录审计）")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# 驾驶舱聚合层（B1，V8 决议③）：GET /cockpit/{vitals,panorama,ai-flow} 三只读端点。
# 文件尾挂载 + 工厂注入本模块 get_db_path/get_ro_connection/_infer_world——cockpit 不反向
# import main（零循环导入），且测试对 get_db_path 的 dependency_overrides 对 /cockpit/* 同样生效。
# ═══════════════════════════════════════════════════════════════════════════
from apps.api.cockpit import build_cockpit_router                        # noqa: E402

app.include_router(build_cockpit_router(get_db_path, get_ro_connection, _infer_world))


# ═══════════════════════════════════════════════════════════════════════════
# 人类决策通道（A-1，V13①）：POST /decisions/{name}——冻结区四动作的「人类专用」HTTP 通道。
# 与 /actions（AI 面平行验证通道）物理隔离：白名单恰为 ai_executable=frozen 集，AI 面 build_tool_defs
# 永不含它们（暴露集 ∩ frozen == ∅）。同 cockpit 注入式挂载（decisions 不反向 import main → 零循环）；
# 复用本模块 _resolve_action_func（同一解析器）与 AS_OF（同一仿真时钟），不建平行映射/平行日期常量。
# 启动期 fail-fast（build_decisions_router 内）：frozen 动作解析不到实现函数则拒起服务。
# ═══════════════════════════════════════════════════════════════════════════
from apps.api.decisions import build_decisions_router                    # noqa: E402

app.include_router(build_decisions_router(get_db_path, _resolve_action_func, AS_OF))


# ═══════════════════════════════════════════════════════════════════════════
# 波U 新只读端点（同 cockpit/decisions 注入式挂载，零循环导入）：
#   · U3 GET /governance/gating       —— AI 放权档位摘要（只读 data/gating_report.json，不 import gating）
#   · U6 GET /collaboration/threads   —— 协作流 coordination_threads（X-World 双世界 + X-Role 脱敏）
# governance 无 DB 依赖（读世界无关的离线治理产物）；collaboration 复用 get_db_path/get_ro_connection/
# _infer_world ⇒ X-World 双世界与测试 dependency_overrides 自动生效。
# ═══════════════════════════════════════════════════════════════════════════
from apps.api.governance import build_governance_router                  # noqa: E402
from apps.api.collaboration import build_collaboration_router            # noqa: E402

app.include_router(build_governance_router())
app.include_router(build_collaboration_router(get_db_path, get_ro_connection, _infer_world))


# ═══════════════════════════════════════════════════════════════════════════
# ① runtime 治理 API（V19，spec 2026-07-17-wave2-final-smart-face §①）：
#   GET/POST /runtime/runs{,/{id}{,/resume,/kill}} —— 持久 Agent runtime 的 HTTP 门面。
# 同 governance/decisions/collaboration 注入式挂载（runtime 不反向 import main → 零循环导入）；
# 复用本模块 get_db_path（X-World 双世界解析）/_infer_world（world 信封）/AS_OF（控制面审计 as_of）。
# 红线：本路由只暴露 list/detail/start/resume/kill——无一能执行冻结区审批/关闭/报价裁决（runtime 写面恒
# 为 Toolbox 7 写工具、经 dispatch 单门拦截，API 不新开通往冻结区的路）；GET 走 mode=ro（runtime.py 自管）。
# ═══════════════════════════════════════════════════════════════════════════
from apps.api.runtime import build_runtime_router                        # noqa: E402

app.include_router(build_runtime_router(get_db_path, _infer_world, AS_OF))
