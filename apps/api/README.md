# apps/api —— M5 FastAPI 服务层骨架

驾驶舱与透视镜 v3 的数据/动作底座。全部路由消费本体（`ontology/control-tower-ontology.json`）
与运行时/生成层（`pipeline/ontology_runtime.py`、`pipeline/ontology_models.py`），不平行硬编码
任何类型/权限/字段清单；写动作原样转发给 `app.actions` / `app.admission_actions` 既有函数，
maker-checker 语义与审计路径保持原封不动。

## 依赖核对

执行前已核对 `python3 -c "import uvicorn"` —— **成功**（本环境 uvicorn 0.34.2，
fastapi 0.115.12，pydantic 2.10.3，均为 plan 声明的既装版本，未擅装任何包）。
因此下面给的是真实可用的启动命令；测试则完全不需要它（`fastapi.testclient.TestClient`
直接驱动 ASGI app，见「测试」一节）。

## 启动

```bash
# 仓库根目录下执行
uvicorn apps.api.main:app --port 8100
```

默认读取 `data/ontology.sqlite`（仓库内绝对路径解析，不依赖启动时的 cwd）。可用环境变量
`ONTOLOGY_DB` 覆盖数据库路径——这是驾驶舱（`apps/cockpit`）双世界切换的唯一开关：

```bash
# 验证世界（缺省）：data/ontology.sqlite，datagen 种子库，规则档案 R/P=1.000 对照源
uvicorn apps.api.main:app --port 8100

# 模拟世界：data/simworld.sqlite，14 个月连续活世界（见 sim/store.py）
ONTOLOGY_DB=data/simworld.sqlite uvicorn apps.api.main:app --port 8100
```

`ONTOLOGY_DB_PATH`（`agent/mcp_server.py` 的既有同名环境变量约定）仍兼容识别，`ONTOLOGY_DB`
优先——只加不减，旧变量名不会悄悄失效。`GET /ontology` 响应的 `world` 字段会如实反映当前连接的
库文件名推断出的世界标识（`ontology.sqlite`→`"verification"`，`simworld.sqlite`→`"simulation"`，
其他文件名原样回退）。

启动后可访问 `http://127.0.0.1:8100/docs`（FastAPI 自带 OpenAPI 交互文档）。

### CORS（跨源资源共享，浏览器阻止网页直连别的端口/域名的默认安全机制）

已内置 `CORSMiddleware`（`main.py`），当前仅放行本地两个开发端口——驾驶舱
`http://localhost:5174`、透视镜 `http://localhost:5173`（`apps/cockpit`、`apps/builder-console`
的 `vite.config.ts` 固定端口）。日常开发驾驶舱走 Vite dev/preview server 自带的同源代理
（`/api` 前缀转发），不触发也不依赖这层 CORS；补上是为独立部署（前端脱离 Vite 代理、
直连 `apps/api` 真实地址）与本地直连调试预留。生产部署时应把允许源改成从配置/环境变量
读取，而非沿用这里硬编码的 localhost 列表。

已实测：`uvicorn apps.api.main:app --port 8100` 真实起服后，`GET /ontology`、
`GET /objects/Supplier?limit=2`、`GET /objects/BogusType`（422）、
`POST /actions/ApproveMitigation`（404，冻结区）、
`POST /actions/AssignTask`（X-Role 无权时 403）均返回预期结果——不仅是
`fastapi.testclient` 内的模拟请求，是真实 HTTP 往返。

## 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/ontology` | 本体自描述：version + world（当前连接库推断的世界标识）+ 对象/关系/动作清单摘要（驾驶舱与透视镜 v3 的元数据源） |
| GET  | `/objects/{type}` | 列表 + 等值过滤（`?字段名=值`，任意本体属性列；`limit` 默认 100）。`type` 必须 ∈ 本体 34 类型，否则 422；过滤列名不在该类型属性白名单内也 422 |
| GET  | `/objects/{type}/{id}` | 单对象；经 M3 Pydantic 模型 (`pipeline.ontology_models.MODEL_BY_TYPE`) 校验语义；不存在返回 404 |
| GET  | `/objects/{type}/{id}/links/{link}` | 调 `pipeline.ontology_runtime.traverse` 同源遍历；`declared_only` 关系或未知 `link` 返回 422（人话消息） |
| POST | `/actions/{name}` | 仅 6 个 `exposed_as_tool=true` 动作可调（`AssignTask` / `ProposeMitigation` / `CreateAdmissionCase` / `RunCompliancePrecheck` / `BuildLogisticsPlan` / `CalculateCostScenario`，PascalCase 或 snake_case 均可）；其余（含冻结区 4 动作）404 |
| GET  | `/cockpit/vitals` | 驾驶舱公司体征带（B1）：七掌控区体征块（钱/履约/客户/供应商/库存/AI/待拍板），每区 `{zone, headline_*, trend, alert_count, detail}`；缺域指标 `{"value": null, "reason": …}` 如实标注；trend 无历史支撑一律 null。逐指标 SQL 口径见 `apps/api/cockpit.py` 模块 docstring 总表 |
| GET  | `/cockpit/panorama` | 小全景分层图数据：五层节点（customers/orders/shipments/suppliers/warehouses，单层 >40 实体自动聚合分组）+ 本体关系投影边 + open 风险锚定标注 + 迷你指标（航线在途货值、仓库击穿计数） |
| GET  | `/cockpit/ai-flow` | AI 工作流时间线：`llm_calls` / `action_log` 中 `actor='ai-agent'` 行 / 提案状态流转 /（simworld）`sim_ai_activity` 留痕（带 `sim: true` 徽标）按时间倒序合并；`?limit=` 默认 50（1..500） |

## 鉴权（原型级）

请求头 `X-Role`（缺省 `ops`），与 Streamlit 同一权限模型（`app/actions.py` 等模块的
`ROLE_PERMS`/`ADM_PERMS`，二者本身就是 `pipeline.ontology_runtime.build_role_perms()` 的切片），
不引入新认证栈。`GET /objects/*` 按 `X-Role` 对敏感字段脱敏（复用
`agent/mcp_server.SensitiveFieldMasker`，例：`Customer.tier` 对 `ops` 掩码、对 `cs` 明文）；
`POST /actions/{name}` 按 `X-Role` 鉴权，无权 → 403，且拒绝调用照常落 `action_log`
审计（不自建写路径——始终转发给 `app.actions`/`app.admission_actions` 原函数，由它们自己的
`_denied()` 留痕，路由层只据此选 HTTP 状态码）。

## 数据库连接

- `GET /*`：`mode=ro` 只读连接（物理只读；红线：GET 路由绝不写库）。
- `POST /actions/{name}`：`app.actions.connect()` 的连接管理（该函数已 `row_factory=Row`，
  与各动作函数期望一致），写入/事务/审计全部沿用 app 层既有实现。

`/cockpit/*` 三端点全部 `mode=ro` 只读；`X-Role` 脱敏两层——本体 `sensitiveFieldRules`
具名字段（同五路由）+ 金额类聚合掩码（与 UI `COST_FIELDS` 同规：非 `finance`/`manager`
角色看不到 `_usd` 后缀聚合金额与毛利分布，计数/比率照常可见）。双世界口径：`ONTOLOGY_DB`
指向 `data/simworld.sqlite` 时，该世界未灌的域（采购收货/准入/`llm_calls` 审计等）逐指标
返回 `null + reason`，端点不 500。

## 测试

```bash
python3 -m pytest apps/api/ -v      # test_api.py（五路由 18 例）+ test_cockpit.py（驾驶舱 16 例）
```

不需要起真 uvicorn 服务——`fastapi.testclient.TestClient` 直接驱动 ASGI app；数据库路径经
`app.dependency_overrides` 换成 `data/ontology.sqlite` 的临时副本（`shutil.copy` 到 pytest
临时目录），全程不触碰真库（与 `app/test_agent_security.py` 同一隔离纪律）。已验证：
测试运行前后 `data/ontology.sqlite` 的 md5 逐字节一致。

## 边界

不改 `agent/` / `pipeline/`（只 import）、不改 `app/` 既有动作函数（只 import 调用）、
不碰真值数据/`ontology/*.json`/`poc/`。`apps/api/` 内为本文件 + `main.py` + `test_api.py`
+ `cockpit.py`（B1 驾驶舱聚合层，`main.py` 尾部挂载，不反向 import）+ `test_cockpit.py`
五个文件，`apps/` 目录本身是既有目录，未新增顶层结构。
