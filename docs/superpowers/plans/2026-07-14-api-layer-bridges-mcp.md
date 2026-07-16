# API 层实施 Plan：桥2 声明驱动 + 桥3 结构生成 + MCP 正式化

> **For agentic workers:** 本 plan 按宪法 §4 三层模型分工执行——主会话（Fable）规划评审验收，
> 执行开发派发子代理（每个 Milestone 一单，派发时显式指定 model）。子代理不得 git commit、
> 不得改 plan/决策日志，规格歧义列明在报告中而非自行裁决。
> 粒度说明：本项目派发单位是"Milestone 任务书"而非 2-5 分钟步骤（宪法优先，先例 X2-X4/C1）；
> 每个 Milestone 内给出精确文件路径、完整规则、验收命令与期望输出，零占位符标准不变。

**Goal:** 把本体 JSON 从"挂在墙上的图纸"变成"发动机的一部分"——权限/AI 工具从本体生成（桥2）、
表结构/校验从本体生成（桥3）、AI 经 MCP 真调用本体工具（正式化），并为驾驶舱立起 FastAPI 服务层。

**Architecture:** 按变化频率分层选型（V5 调研结论）：结构性低频（DDL/Pydantic 模型）用**生成型**
（产物文件入版本控制，改本体重跑生成器）；高频组合（权限过滤/工具暴露/脱敏）用**解释型**
（运行时读本体 JSON 现场组装）。桥1 闸门（`pipeline/ontology_lint.py`，15 处基线）是全程收敛度量——
每个 Milestone 结束差异数只减不增，M3 后 `--strict` 可入发布流程。

**Tech Stack:** Python 3.11 / SQLite / Streamlit（既有锁定栈）+ fastapi 0.115.12 / pydantic 2.10.3
（V4 决议批准的双应用架构依赖，环境已装）。MCP server 维持零依赖手写 stdio JSON-RPC（PoC 报告 §2 决策）。

**授权依据：** plan v0.2 §4 决议 V5（Daniel 三项全批，2026-07-13）①三座桥并入 API 层 ②MCP PoC
（已终验 3/3 收口）③透视镜 v3 后置。STATUS 已载明"桥2 补本体、勿删代码授权"。V4 决议批准
React+FastAPI 双应用。**本 plan 不改 KPI、不改评估判定、不碰真值数据、不动 v0.1 只读文件。**

---

## 全局红线（每个 Milestone 的任务书都必须原样携带）

1. `engine/evaluate.py` 判定逻辑与 ground truth（`expected_risk_events` / `injected_noise_log`）只读。
2. 权限行为零变化：`app/test_agent_security.py` 的 `EXPECTED_ROLE_PERMS` / `EXPECTED_ADM_PERMS`
   等基线断言**一行不改**——迁移后它们自动变成"本体生成结果必须等于人批基线"的守护。
3. 冻结区四动作（ApproveMitigation / ApproveQuoteDecision / CloseRiskEvent / RejectOrRequestMoreInfo）
   任何形态下不得成为 AI 可调用工具。
4. 每个 Milestone 收尾跑全量回归：
   `python3 -m datagen.generate && python3 -m datagen.verify && python3 -m pipeline.build_ontology
   && python3 -m pipeline.evaluate && python3 -m engine.detect && python3 -m engine.evaluate
   && python3 -m datagen.seed_demo_ops && python3 -m app.test_closed_loop && python3 -m app.test_admission_loop
   && python3 -m app.test_cost_loop && python3 -m app.test_agent_security && python3 -m agent.evaluate`
   全绿 + `python3 -m pipeline.ontology_lint` 差异数 ≤ 上一 Milestone。
   ［勘误#2 2026-07-14，M1 执行发现：原链缺 `datagen.seed_demo_ops`——build_ontology 重建清空
   demo 种子后 test_agent_security 依赖的种子锚点（TSK-51BEB35072 等）查空即崩；seed 须在
   engine.evaluate 之后（evaluate 先于 seed，避开运营字段偏移）、app.test_* 之前。已修正上链。］
5. 真值文件 md5 全程 byte-identical（datagen 固定种子产物）。
6. 子代理不 commit；主会话复核以重跑为准（不信报告只信输出）。

## 待 Daniel 裁决（不阻塞执行，全部按最保守读法先行）

| # | 问题（业务语言） | 最保守读法（本 plan 采用） | 归属 |
|---|---|---|---|
| 裁1 | 「风险直连 SKU」这条关系图纸画了但库里没有——删掉，还是补上？ | 本体标 `"status": "declared_only"`，闸门显式豁免并每次报告提示，不删不补 | M1 |
| 裁2 | AI 聊天里能不能直接发处置提案（还是维持只从 UI 表单发）？ | MCP server 第一版**纯只读**（11 读工具），写提案工具不入 | M4 |
| 裁3 | AI 问答改为真查库后每次要 40-50 秒（现在的"朗读简报"是秒级）——接受吗？ | 新旧双通道并存，配置一键回退，默认切新通道，Daniel 体验后定 | M4 |

---

## Milestone 依赖与派发

```text
M1 桥2-本体侧（Opus 4.8） → M2 桥2-运行时侧（Opus 4.8） → M3 桥3 结构生成（Opus 4.8）
                                                        ↘ M4 MCP 正式化（Opus 4.8，可与 M3 并行）
M3 + M4 → M5 FastAPI 服务层骨架（Sonnet 5）
```

每单一个验收点，主会话评审通过并 commit 后才开下一单。

---

## M1 桥2-本体侧：本体 0.9.0 → 0.10.0，追平已批现实 + 声明化（Opus 4.8）

**Files:**
- Modify: `ontology/control-tower-ontology.json`（版本号 0.9.0→0.10.0）
- Modify: `pipeline/ontology_lint.py`（读取新声明字段，内置豁免知识声明化）
- Test: 复用 `python3 -m pipeline.ontology_lint` 作为验收器

**改动规则（逐条，无自由裁量）：**

1. **roles[] 补 `procurement`**（P4 裁决 2026-07-09 追平，勿删代码授权）；
   `actions[]` 中 `ProposeMitigation.executors` 追加 `"procurement"`。
2. **全部 31 个 action 增两字段**（V5 决议点名的字段名）：
   - `"exposed_as_tool"`: bool——初值从 `agent/tools.py` 现状反推，恰好 6 个 true：
     AssignTask / ProposeMitigation / CreateAdmissionCase / RunCompliancePrecheck /
     BuildLogisticsPlan / CalculateCostScenario；其余 25 个 false。
   - `"ai_executable"`: `"auto" | "confirm" | "never" | "frozen"` 四值枚举——初值：
     上述 6 个=`auto`；冻结区 4 个（ApproveMitigation / ApproveQuoteDecision / CloseRiskEvent /
     RejectOrRequestMoreInfo）=`frozen`；其余 21 个=`never`。`confirm` 本次不使用（留给 L 系列
     自主分级），但枚举定义必须含它。
   - 不变式（lint 新增断言）：`frozen ⇒ exposed_as_tool=false`；`exposed_as_tool=true ⇔ ai_executable=auto`。
3. **action 增 `"permission_key"` 字段（可选，缺省=name）**：协调域 6 动作（A20-A25）设为
   `"ManageCoordination"`（对应 `app/coordination_actions.py:22` COORD_PERMS 的组键现实）。
4. **action 增 `"enforcement"` 字段**：`"role_dict"`（默认，权限由 5 个权限字典执行）/
   `"engine_internal"`（引擎内部执行，无人类 RBAC 键）/ `"proposal_flow"`（经提案审批流执行）。
   初值按 lint 基线报告 B 节"覆盖情况"照录：IngestMilestone、CreateRiskEvent、RecordPurchasePayment、
   RecordSupplierQualification=`engine_internal`（执行者须先在代码中核对这 4 个动作的真实执行路径，
   与报告不符时列明歧义）；SuggestSubstitution / AdjustInventory / InitiateSecondSource /
   BlockNonPoPayment / BackfillPo=`proposal_flow`；其余=`role_dict`。
   lint B 类断言升级：仅 `enforcement=role_dict` 的动作要求权限字典有键——3 个登记盲区从此不再是盲区。
5. **A 类登记滞后 4 列补入本体 properties**（带 description 说明来源）：
   - `Sku.supplier_id`（string，`"description": "Denormalized FK, 承载 supplier_provides 关系"`）
   - `Shipment.last_event_time`（string，derived 事件戳）
   - `Shipment.po_ids`（string，`"一票多 PO 反向 json 列，承载 po_shipped_by（D2）"`）
   - `Shipment.status_source`（string，状态来源标记）
6. **删除 `ShipmentMilestone.payload` 属性**（A#10 已查明：M3 血缘改造后原始报文下沉
   `source_events.payload_json`，对象层不再承载）；在 ShipmentMilestone 对象 description 注明下沉去向。
7. **links[] 增 `"storage"` 声明字段（可选）**，把 lint 的 M4 映射契约从"重建猜测"变成"显式声明"：
   - `po_shipped_by`: `{"kind": "reverse_json", "table": "shipments", "column": "po_ids"}`
   - `shipment_to_warehouse`: `{"kind": "column", "table": "shipments", "column": "destination_warehouse"}`
   - `risk_affects_sku`: 增 `"status": "declared_only"`（裁1 最保守读法）——lint 对 declared_only
     豁免"缺失"判定但必须在报告尾部输出【待裁决豁免】提示行。
8. **本体 Sku 的 5 个尺寸/金额字段（A#2-6）不动**——number 是正确意图，表侧 TEXT 是桥3（M3）修。
9. `pipeline/ontology_lint.py` 同步升级：读取 storage/enforcement/exposed_as_tool/ai_executable
   字段替代内置豁免清单；C 类断言机制化为"`TOOL_DEFS ⊆ snake_case(exposed_as_tool=true 动作)` 且
   `FORBIDDEN_TOOLS == snake_case(ai_executable=frozen 动作)`"（M1 时 tools.py 还是硬编码，
   断言两侧此刻都成立即证一致）。

**验收（全部满足才算过）：**
```bash
python3 -m pipeline.ontology_lint          # 期望：差异 15 → 5（恰为 A#2-6 类型漂移，规则 8 留 M3 修）
                                           # + 1 行【待裁决豁免】risk_affects_sku
python3 -m pipeline.ontology_lint --strict # 期望：退出码 1（5 条漂移仍在，M3 清零后才 strict-clean）
# 全局红线第 4 条全量回归全绿；本体 JSON 仅上述规则内的键新增/修正，git diff 可逐条对读本节
```
［勘误#1 2026-07-14，M1 执行发现原验收数字"15→0 / strict 退出码 0"与规则 8 及 M3 节自相矛盾
（M3 明写"A 类 5 处漂移清零→累计差异 0"与影子模式"预期差异恰好 5 处"）——按规则一致态修正为
5+1/退出码 1。M1 实际验收结果即 5+1，与修正后期望一致。评审裁决：主会话（属工程一致性笔误，
非业务语义，无需 Daniel）。］

---

## M2 桥2-运行时侧：权限/工具从本体解释生成，硬编码退役（Opus 4.8）

**Files:**
- Create: `pipeline/ontology_runtime.py`（唯一新模块：本体运行时加载器）
- Create: `app/test_ontology_runtime.py`（迁移一致性测试）
- Modify: `app/actions.py` / `app/admission_actions.py` / `app/procurement_actions.py` /
  `app/warehouse_actions.py` / `app/coordination_actions.py`（5 个权限字典改为 import 生成结果）
- Modify: `agent/tools.py`（FORBIDDEN_TOOLS 与 TOOL_DEFS 的写工具部分改为生成；读工具见下）
- Modify: `ontology/control-tower-ontology.json`（新增顶层 `aiQueryTools` 节 + action 增
  `tool_description` / `tool_input_schema` 字段——内容自现 TOOL_DEFS **原样搬家**）

**核心设计（解释型，启动时读一次并缓存）：**

```python
# pipeline/ontology_runtime.py 接口签名（实现由执行者完成，签名不得偏离）
def load_ontology(path: str = ONTOLOGY_PATH) -> dict: ...          # 进程内缓存
def build_role_perms(ontology: dict) -> dict[str, set[str]]:       # enforcement=role_dict 的动作
    """按 permission_key 归组；返回值结构与现 5 个字典完全同构，供各模块分片取用"""
def build_forbidden_tools(ontology: dict) -> set[str]:             # snake_case(ai_executable=frozen)
def build_tool_defs(ontology: dict) -> list[dict]:                 # aiQueryTools + exposed 动作
def snake_case(action_name: str) -> str:                           # 与 lint 的 M3 契约同一实现
```

**关键取舍（执行者不得擅改，评审时对读）：**
- `tool_input_schema` 本阶段是**从 tools.py 原样搬进本体**（本体成为唯一权威源），不是从
  properties 凭空派生——派生增强属 M3/M4。搬家后 tools.py 不再含任何工具 schema 字面量。
- 11 个读工具（list_/get_/explain_ 前缀）不对应动作，声明入本体顶层 `aiQueryTools`：
  `[{"name", "description", "input_schema", "domain": "risk|cost|admission|..."}]`——
  `domain` 值取自 tools.py 现有 RISK_READ_TOOLS 等域分组常量，角色 scoping 逻辑改读该字段。
- FORBIDDEN_TOOLS 生成结果必须 == 现硬编码 4 元素集合（frozen 恰 4 个动作保证之）。

**迁移一致性测试（先写、先跑红、再迁移、跑绿——本 Milestone 的 TDD 主线）：**

```python
# app/test_ontology_runtime.py 必须包含的断言（可增不可减）
def test_generated_equals_legacy():
    onto = load_ontology()
    assert build_role_perms(onto)["ProposeMitigation"] == {"ops", "cs", "finance", "procurement"}
    # ……5 个字典逐键与 test_agent_security.py 的 EXPECTED_* 基线（人批口径）完全相等
    assert build_forbidden_tools(onto) == {"approve_mitigation", "close_risk_event",
                                           "approve_quote_decision", "reject_or_request_more_info"}
    legacy_tool_names = {...}  # 迁移前用一次性脚本从旧 TOOL_DEFS 抓取落死在测试里（17 个名字）
    assert {t["name"] for t in build_tool_defs(onto)} == legacy_tool_names
    # input_schema 逐工具深度相等（json 归一化比较）
```

**验收：**
```bash
python3 -m app.test_ontology_runtime   # 新测试全绿
python3 -m pipeline.ontology_lint      # C 类断言现在读"生成前的声明"vs"生成后的运行时"，仍 0 差异
# 全局红线第 4 条全量回归全绿（test_agent_security 的 EXPECTED_* 基线一行未改而通过 = 行为零变化铁证）
grep -rn "ROLE_PERMS = {" app/actions.py   # 期望：无硬编码字面量（改为 import + 分片）
```

---

## M3 桥3 结构生成：properties → Pydantic + DDL，影子模式先行（Opus 4.8）

**Files:**
- Create: `pipeline/generate_models.py`（生成器脚本）
- Create: `pipeline/ontology_models.py`（生成产物：34 个 Pydantic 模型，文件入版本控制，头部标
  `# GENERATED FROM ontology v0.10.0 — DO NOT EDIT，重跑 python3 -m pipeline.generate_models`）
- Create: `pipeline/generate_ddl.py`（DDL 生成 + `--shadow` 影子对比模式)
- Modify: `pipeline/build_ontology.py`（34 处硬编码 `table(...)` 改为消费生成 DDL；插入前
  `model_validate` 校验，分 warn→enforce 两档）
- Modify: `ontology/control-tower-ontology.json`（若 M1 后仍有派生列缺声明，此处补齐）
  ［勘误#3 2026-07-14：原文将 `llm_calls.call_type` CHECK 扩值划入 M3——错误。`llm_calls` 的
  DDL 家在 `agent/egress_gate.py`（审计基础设施表），不在本体 34 对象表生成范围，M3 影子 diff
  不会也不该看它。CHECK 扩值移交 M4（其 Files 增 `agent/egress_gate.py`），M3 影子模式预期差异
  修正为**纯 5 处（A#2-6）**。］

**类型映射（唯一权威表）：**
| 本体 type | Pydantic | SQLite DDL |
|---|---|---|
| string | `str` | TEXT |
| number | `float` | REAL |
| integer | `int` | INTEGER |
| boolean | `bool` | INTEGER（0/1，沿 SQLite 惯例） |
| enum（properties 带 enum 列表） | `Literal[...]` | TEXT |
| required=false | `Optional[...] = None` | 无 NOT NULL |

**执行顺序（影子模式是安全带，不得跳过）：**
1. 生成器 + 模型产出，`python3 -m pipeline.generate_ddl --shadow`：逐表 diff 生成 DDL vs 实库
   schema，**预期差异恰好 5 处**（A#2-6：`skus` 的 declared_value_usd / package_l_cm / package_w_cm /
   package_h_cm / package_weight_kg，TEXT→REAL；勘误#3 后不含 llm_calls）。多一处少一处都停下报告。
2. 影子报告确认后切换 build_ontology 建表路径；`model_validate` 先 warn-only 全量跑一遍
   （期望 0 违例——datagen 产的就是合法数据），随后切 enforce。
3. 重建库全量回归。**特别验证**：engine 数值规则在 REAL 列上的 R/P 仍 1.000、真值 md5 不变；
   若任何评估偏移，立即停下报告（严禁改判定逻辑迁就）。
4. 通用 traverse 下沉：把 PoC server 里的 `traverse_link` 泛化为
   `pipeline/ontology_runtime.py::traverse(con, source_type, source_id, link_type) -> list[str]`
   （读 links[].storage 声明处理 reverse_json/column/affected_*_ids 三种承载），MCP server（M4）
   与后续透视镜 v3 同源消费。本阶段仅新增函数+单测，不改既有查询调用方。

**验收：**
```bash
python3 -m pipeline.generate_ddl --shadow   # 期望：仅上述 5+1 处差异，其余 33 表逐字节一致
python3 -m pipeline.ontology_lint           # A 类 5 处漂移清零 → 累计差异 0 + 1 豁免
# 全局红线第 4 条全量回归全绿 + 真值 md5 与 M2 收尾时一致
python3 - <<'EOF'                            # 类型修正生效证据
import sqlite3; con = sqlite3.connect('data/ontology.sqlite')
print(con.execute("SELECT type FROM pragma_table_info('skus') WHERE name='declared_value_usd'").fetchone())
EOF
# 期望输出：('REAL',)
```

---

## M4 MCP 正式化：PoC → agent 层正式能力（Opus 4.8，可与 M3 并行启动，traverse 合流点在后）

**Files:**
- Create: `agent/mcp_server.py`（自 `poc/mcp-ontology/server.py` 迁移重构；poc/ 目录冻结为
  历史证据不再改动，其 README 头注指向正式版）
- Create: `agent/test_mcp_server.py`（角色过滤/脱敏/审计/冻结区四类断言）
- Create: `agent/mcp-config.json`（正式 config，`--role` 参数化）
- Modify: `agent/llm_agent.py`（新增 provider `claude_cli_mcp`：`claude -p --mcp-config ...` 多轮
  真调用；原 `claude_cli` 单发合成保留为回退档）
- Modify: `agent/egress_gate.py`（勘误#3 移入：`llm_calls.call_type` CHECK 扩值 `'mcp_tool'`，
  该表 DDL 的家在此文件；SQLite 改 CHECK 需重建表，注意存量行迁移）
- Modify: `config/agent.yaml`（或现有 agent 配置文件，执行者核对实际路径）：
  `provider: claude_cli_mcp` + `fallback_provider: claude_cli`——裁3 的一键回退开关
- Modify: `app/` 各对象工作台「询问」按钮的 provider 读取处（沿 config，代码应零改动或仅传参）

**四道硬门槛（PoC 报告 §6 建议 1-4 的落地，逐条对应）：**
1. **角色过滤**：server 启动带 `--role <role>`；`tools/list` 只返回该角色可见工具
   （读工具按 aiQueryTools.domain × 角色域矩阵——与 tools.py Session scoping 同一本体来源）；
   返回字段按本体 `sensitiveFieldRules` 脱敏（Customer.tier 对非 cs/manager 返回 MASK 等）。
   tools.py 既有 COST_FIELDS 等硬编码脱敏集**本次不动**（全量声明化登记为后续项，防手术过大）。
2. **冻结区机制化**：工具清单调用 M2 的 `build_tool_defs()` 派生——frozen/never 动作在生成层
   就不存在；`agent/test_mcp_server.py` 断言"server 暴露集 ∩ snake_case(frozen 动作) == ∅"。
3. **审计入库**：双连接——业务查询连接维持 `mode=ro`（物理只读红线不破），审计另开仅执行
   `INSERT INTO llm_calls(call_type='mcp_tool', ...)` 的写连接；jsonl 留存兼容 PoC 取证格式。
4. **input_schema 全量本体驱动**：枚举（对象/关系类型）+ 字段结构从本体生成（M3 的模型可用则
   复用 `model_json_schema()`，M3 未合流时先沿 PoC 的枚举生成路径，合流点后收敛）。

**只读边界（裁2 最保守读法）：** 第一版注册全部 11 个读工具 + `traverse`，**零写工具**——
`mode=ro` 主连接从物理上保证。写提案工具入 MCP 待 Daniel 裁决后另开任务。

**验收：**
```bash
python3 -m agent.test_mcp_server            # 角色过滤/脱敏/审计/冻结区断言全绿
python3 -m agent.evaluate                   # 确定性评估集零回归
python3 -m app.test_agent_security          # 安全对抗（含诱导越权）零回归
# 端到端（登录态环境）：PoC 报告 §4 同款命令改用 agent/mcp-config.json，3 项判定全过；
# llm_calls 表出现 call_type='mcp_tool' 行（audit 写连接生效证据）
```

---

## M5 FastAPI 服务层骨架：驾驶舱底座（Sonnet 5）

**Files:**
- Create: `apps/api/main.py`（FastAPI 应用；apps/ 已是既有目录，非新增顶层）
- Create: `apps/api/test_api.py`（fastapi.testclient，无需起真服务）
- Create: `apps/api/README.md`（启动：`uvicorn apps.api.main:app --port 8100`；执行者先
  `python3 -c "import uvicorn"` 核对，缺失则停下报告走 §3 依赖审批，不得擅装）

**路由（全部从本体/运行时层生成或消费，禁止平行硬编码）：**
```text
GET  /ontology                          # 本体自描述（版本/对象/关系/动作清单）——驾驶舱与透视镜 v3 的元数据源
GET  /objects/{type}?{field}={value}    # 列表+等值过滤，type 必须 ∈ 本体 34 类型（枚举校验）
GET  /objects/{type}/{id}               # 单对象，响应模型=M3 Pydantic，字段按 X-Role 脱敏
GET  /objects/{type}/{id}/links/{link}  # M3 traverse 同源消费
POST /actions/{name}                    # 仅 exposed_as_tool=true 动作；X-Role 经 M2 build_role_perms
                                        # 鉴权；一切写入走 app.actions 既有函数（维持 maker-checker 与审计）
```
- 鉴权原型级：请求头 `X-Role`（与 Streamlit 同一权限模型，不引入新认证栈）。
- 冻结区动作在路由生成层就不存在（消费 M2 生成结果，与 MCP 同一保证）。

**验收：**
```bash
python3 -m pytest apps/api/test_api.py -v
# 必含用例：①对象读与库真值一致 ②未知类型 422 ③无权角色 POST 动作 403 且审计留痕
# ④敏感字段对无权角色返回 MASK ⑤POST 提案后 tasks/提案表新增行走的是 app.actions 原函数
# 全局红线第 4 条全量回归全绿
```

---

## 收尾（主会话执行）

- [x] 桥1 `--strict` 接入 `docs/release-checklist.md` 发布门（M3 起，release-checklist.md §A）
- [x] STATUS.md 更新 + 复盘补记（每 Milestone 一行进度；docs/retrospective.md §6/§7 收官段）
- [x] 决策日志追加候选：裁1-裁3 的 Daniel 裁决结果（V6 条目，Daniel 原话"1.补；2.可以；3.了解"）
- [x] 透视镜 v3 排队提醒（V5 决议③已交付，commit 50fe2ef：四板块+等距图归位）

> 勘误（2026-07-14，实施计划状态审计发现）：以上四项工作本身早已完成并在别处记录
> （release-checklist.md / STATUS.md / 决策日志 V6 / 透视镜 v3 交付），仅本文件自己的
> checkbox 字面从未回填勾选——纯文档滞后，非工程遗漏。

## Self-Review 记录（writing-plans 清单）

- 规格覆盖：V5 决议①桥2=M1+M2、桥3=M3、②MCP 正式化=M4、V4 API 层=M5；PoC 报告 §6 建议 1-5
  逐条映射 M4 四门槛与 M3 影子模式；§6-6（SDK 再决策）维持手写版无任务，符合"当前推荐"。
- 占位符扫描：无 TBD/TODO；M2 `legacy_tool_names {...}` 为"迁移前落死基线"的明确指令非占位符。
- 类型一致性：`snake_case` / `build_tool_defs` / `traverse` 签名在 M2 定义、M3/M4/M5 消费，名称一致；
  `ai_executable` 四值枚举全文统一；`aiQueryTools` 节名全文统一。
