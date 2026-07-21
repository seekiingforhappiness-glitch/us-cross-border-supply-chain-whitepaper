# MCP PoC 报告：订阅通道能否真实调用本体工具

**日期**：2026-07-13　**执行**：Opus 复杂执行层　**依据**：plan v0.2 §4 V5 决议第②条
**状态**：**通（终验完成）**——server 侧 8/8；Daniel /login 后端到端终验 3/3 全过（第 1 次 Daniel 亲跑、
第 2/3 次接管会话补跑，证据见 §4 回填表格与 `call_log.jsonl` 第 18-32 行）。订阅通道真工具调用成立。

---

## 给创始人的白话总结（先看这段）

- **我们想验证什么**：现在系统里"大模型回答问题"是假的自主——Python 先把答案查好、拼成一段简报，模型只是**照着念**。我们想知道：能不能反过来，让模型**自己开口要数据**（"我要查这票货""顺着关系找它的风险"），系统当场去数据库查了再给它。如果能，就同时解决两件事：Daniel 说的"不托底"感（模型真的在查真数据，不是背稿子），以及**不额外花 API 的钱**（走的是订阅通道，不是按次计费的 API）。
- **这次做出来的东西**：一个叫 **MCP server** 的小程序（MCP = 大模型和外部工具对话的标准接口，可以理解成给模型配的"数据库查询遥控器"）。它对外开了 **3 个只读按钮**：查一个对象、查风险事件列表、顺着关系走到关联对象。它**只会查、不会改**——数据库是用只读方式打开的，物理上写不进去。
- **验证结果**：我们**没有用 claude 本体**、而是自己写了个独立小程序按 MCP 协议把这 3 个按钮各按了一次，答案和数据库里的真值**逐字对上了**，而且每次按按钮都留了日志（谁、几点、按了哪个、返回几行）。所以——**"工具这套机制本身是通的、正确的"**。
- **唯一没验完的一环**：把这套接到 claude 本体上时，命令行提示 **"Not logged in"（没登录）**。这个环境是自动化跑的、没法弹出登录窗，所以到这就卡住了。**登录后复制一条命令就能验完**，命令我写在报告末尾。
- **一句话**：机制已证明可行，只差"登录后按一下"。

---

## 1. 结论

| 维度 | 结论 | 依据 |
| --- | --- | --- |
| MCP server 本身正确性 | **通** | 独立客户端 8/8 断言通过，3 工具输出与库中真值逐字一致 |
| "真工具调用"可留证 | **通** | `call_log.jsonl` 每次调用留痕（时间/工具/参数/行数/成败） |
| 只读安全红线 | **通** | `mode=ro` 打开，实测写操作被 SQLite 拒绝；仅注册 3 个只读工具 |
| claude CLI 支持 MCP 的机制 | **通（已确认存在）** | CLI v2.1.204 具备 `--mcp-config` / `--allowedTools` / `--strict-mcp-config` / `--permission-mode` |
| **订阅通道端到端真调用** | **通（3/3，2026-07-13 终验）** | 三轮 `claude -p` 均真调用工具（call_log +15 行）、答案与真值逐字一致、stream-json 有 `mcp__ontology__*` tool_use 事件；见 §4 回填表格 |

**总判定：通。** server 与协议层独立证明可行且可留证（§3）；Daniel 完成 `/login` 后端到端终验三轮全过（§4）——
模型经订阅通道真实多轮调用本体工具（每轮 `num_turns=7`，先查货件、沿关系走到风险、再逐个深查风险详情），
非单发朗读。原"部分通"判定于终验后升级，历史卡点记录保留于 §4 供追溯。

---

## 2. 交付物

全部位于 `poc/mcp-ontology/`（新目录，未碰任何现有代码）：

| 文件 | 作用 |
| --- | --- |
| `server.py` | 零依赖 stdio JSON-RPC MCP server，3 个只读工具，input_schema 由本体生成 |
| `test_client.py` | 独立测试客户端（不依赖 claude CLI），走 MCP 协议自测 + 真值对照 |
| `mcp-config.json` | claude CLI `--mcp-config` 用的 server 声明 |
| `call_log.jsonl` | 运行时调用日志（本报告引用的铁证） |

报告：本文件 `docs/research/2026-07-13-mcp-poc-report.md`。

### 技术路线选择（如实记录）

任务要求**优先用官方 `mcp` SDK**。实测：`pip install mcp` 在本机 conda base 能装上（mcp-1.28.1），但它会把 **pydantic 2.10.3→2.13.4、starlette 0.46.2→1.3.1** 强升，破坏既有 `gradio`/`fastapi` 的版本约束——这违反 AGENTS.md §5/§7 的**锁定栈红线**（新增依赖须走 §3 人批）。

**决策：改走零依赖手写 stdio JSON-RPC**，并已把被升级的包**还原回原版本、卸载 mcp**，把共享环境恢复原状。理由：① 手写版仅用 Python 标准库，任何 `python3` 都能跑，最利于"环境就绪后直接复制执行"；② 不给锁定栈引入未经批准的重依赖；③ CLI 当前未登录，SDK 相对手写版唯一的额外价值（协议版本协商由 SDK 兜底）此刻也无法被检验。**代价**：手写版的协议实现由我方维护，需自证协议正确——已用独立客户端跑通握手→tools/list→tools/call 全链路来兜住这一点（见 §3）。

---

## 3. 证据：server 自测（已发生、可复现）

命令：`python3 poc/mcp-ontology/test_client.py`（退出码 0）。

```
[PASS] initialize 返回 serverInfo — serverInfo={'name': 'ontology', ...}
[PASS] 暴露 3 个工具 — tools=['get_object', 'search_risk_events', 'traverse_link']
[PASS] input_schema 枚举由本体生成 — 34 个对象类型枚举
[PASS] get_object 命中且 status 与真值一致
[PASS] traverse_link 找到 3 个风险事件且与真值一致
[PASS] search_risk_events 过滤生效且命中目标事件
[PASS] 未知类型返回 isError
[PASS] server 只注册只读工具（无写/审批/花钱工具）
PASS: 8  |  FAIL: 0
```

### 对照真值（对象 `SHP-2026-0068`，事先从库中查定）

| 字段/关系 | 库中真值 | 工具返回 | 一致 |
| --- | --- | --- | --- |
| `status` | `in_transit` | `in_transit` | ✅ |
| `eta_current` | `2026-08-31` | `2026-08-31` | ✅ |
| `customs_status` | `not_filed` | `not_filed` | ✅ |
| 关联风险事件（`risk_on_shipment`） | `RSK-0038, RSK-0039, RSK-0040` | 同左 | ✅ |
| `search_risk_events(R1, critical)` | 13 条，含 RSK-0038 | 13 条，含 RSK-0038 | ✅ |

### call_log 摘录（铁证——工具调用真实发生）

```jsonl
{"ts":"2026-07-13T15:15:34.662Z","tool":"get_object","args":{"object_type":"Shipment","object_id":"SHP-2026-0068"},"result_rows":1,"ok":true}
{"ts":"2026-07-13T15:15:34.663Z","tool":"traverse_link","args":{"object_type":"Shipment","object_id":"SHP-2026-0068","link_type":"risk_on_shipment"},"result_rows":3,"ok":true}
{"ts":"2026-07-13T15:15:34.663Z","tool":"search_risk_events","args":{"rule_id":"R1","severity":"critical","limit":50},"result_rows":13,"ok":true}
{"ts":"2026-07-13T15:15:34.664Z","tool":"get_object","args":{"object_type":"Nonexistent","object_id":"X"},"result_rows":0,"ok":false,"error":"未知 object_type ..."}
```

### 只读红线实测

对 `data/ontology.sqlite?mode=ro` 连接执行 `UPDATE risk_events ...` → SQLite 抛
`attempt to write a readonly database`。**写路径物理上不存在**。

### 加分项已实现：input_schema 由本体生成（桥 2 预演）

`get_object` / `traverse_link` 的 `object_type` 枚举（34 个类型）与 `link_type` 枚举（53 个关系）
**直接从 `control-tower-ontology.json` 生成**，非手写。类型→表名映射用缩写感知的 snake 化 +
实表校验推导（如 `RFQ→rfqs`、`SalesOrderLine→sales_order_lines`），34/34 全部命中。
这正是 V5 桥 2"声明驱动"的最小预演。

---

## 4. CLI 侧：卡点与登录后验证方法

### 卡在哪一步（如实）

- `claude --version` → **2.1.204**（可用）。
- `claude --help` → MCP 接法已确认：`--mcp-config <files...>`、`--allowedTools <tools...>`、
  `--strict-mcp-config`、`--permission-mode <mode>`、`-p/--print`、`--output-format`。
- 实跑目标命令 → **21ms 内返回 `Not logged in · Please run /login`**，`call_log.jsonl` **无新增行**
  ——即 CLI 在鉴权阶段就短路了，**根本没走到 spawn MCP server**。本会话为非交互环境，无法完成
  `/login` 的 OAuth 交互流程，故到此为止。

> 判读：这是**环境登录态**问题，不是 MCP 机制问题。机制侧（server + 协议）已在 §3 独立证明。

### 登录后一条命令验完（Daniel / 编排层可直接复制）

前置：在**交互式** `claude` 里跑 `/login` 完成订阅登录一次。之后：

```bash
cd /Users/Zhuanz/Desktop/数智供应链

# 单次验证（看模型是否真调用工具并答对）
claude -p "SHP-2026-0068 这票货现在是什么状态？它关联了哪些风险事件？请用可用工具查询本体后回答，给出风险事件ID。" \
  --mcp-config poc/mcp-ontology/mcp-config.json \
  --strict-mcp-config \
  --allowedTools "mcp__ontology__get_object,mcp__ontology__search_risk_events,mcp__ontology__traverse_link" \
  --output-format stream-json --verbose
```

判定标准（三者同时满足才算"通"）：
1. **真调用**：`poc/mcp-ontology/call_log.jsonl` 出现**新增行**，参数含 `SHP-2026-0068`；
2. **答对**：回答含 `status=in_transit` 且列出 `RSK-0038/0039/0040`（与 §3 真值一致）；
3. **可留证**：stream-json 输出里能看到 `mcp__ontology__*` 的 tool_use 事件。

跑 **3 次**、记录每次的"是否真调用 / 答案是否对 / 耗时"，填入下表即完成本 PoC 收尾：

| 次数 | 是否真调用(call_log新增) | 答案与真值一致 | 耗时 |
| --- | --- | --- | --- |
| 1（Daniel 亲跑） | ✅ +5 行（get_object×3 深查风险 + traverse_link + get_object 货件） | ✅ in_transit + RSK-0038/0039/0040 | 44.9s |
| 2（接管会话补跑） | ✅ +5 行，stream-json 6 个 tool_use 事件 | ✅ 同上四项全中 | 44.2s |
| 3（接管会话补跑） | ✅ +5 行，stream-json 5 个 tool_use 事件 | ✅ 同上四项全中 | 47.6s |

> **终验回填（2026-07-13）**：三轮判定标准三条全满足。call_log 17→32 行（+15），每轮模型自主决定
> 调用序列（先 `get_object` 查货件 → `traverse_link` 沿 `risk_on_shipment` 走到 3 个风险 →
> 逐个 `get_object` 深查风险详情），回答含真查出的货值/费用明细（如 RSK-0038 受影响货值 $44,737.54）。
> 每轮 `num_turns=7`、耗时 44-48s、订阅通道（未烧 API key）。PoC 就此收口，进入 V5 正式化。

> 若登录后 `--allowedTools` 仍触发交互授权（`-p` 模式下不应有），追加 `--permission-mode bypassPermissions`
> —— 本 PoC 工具**均只读**，可安全放行。
> 备注：tool 命名规则为 `mcp__<server名>__<工具名>`，本 server 名为 `ontology`（见 mcp-config.json）。

---

## 5. 已知局限（本次**未**验证，勿当已解决）

1. **端到端订阅真调用**：未验（阻塞在登录）。SDK 相对手写版是否与 CLI 的协议版本协商更稳，亦未在真 CLI 上比对。
2. **配置格式未被 CLI 实解析**：因 21ms 登录短路，`--mcp-config` 是否被正确解析尚未被 CLI 实际执行验证（格式为标准 `mcpServers` schema，但未经运行时确认）。
3. **权限/角色隔离**：本 PoC **不做**按角色过滤。`sensitiveFieldRules`（如 Customer.tier 仅 cs/manager 可见、Supplier.uflpa_risk_flag 仅 compliance/manager 可见）当前**未生效**——任何调用者都能读到全字段。正式化前必须补（见 §6）。
4. **冻结区**：本 PoC 天然满足（根本没注册任何动作/写工具），但"从本体 actions 自动派生工具清单时如何保证冻结区永不注册"这套**机制**未建。
5. **并发 / 超时 / 崩溃恢复**：单进程单连接 stdio，未测并发调用、慢查询超时、server 崩溃后 CLI 的重连行为。
6. **审计闭环**：调用留在 `call_log.jsonl`（文件），**未**接入库内 `llm_calls` 表（该表现为 0 行）——正式化应统一进审计表。
7. **数据新鲜度**：`mode=ro` 每次读最新落盘数据；但未设 `immutable`，若 server 常驻期间库被重建，WAL/锁行为未测。
8. **规模**：`traverse_link` 对超大扇出（如某对象 700+ 邻居）只返回 id 列表未分页，未压测。

---

## 6. 下一步建议

### 若登录后端到端验通（大概率）——正式化为 agent 层能力时须补：

1. **按角色过滤工具与字段**：MCP server 按调用身份（role）决定①能看到哪些工具②返回时脱敏
   `sensitiveFieldRules` 字段。这是从"能跑"到"能上线"的第一硬门槛。
2. **冻结区工具永不注册（机制化）**：桥 2 从本体 `actions` 派生工具清单时，
   凡 `需审批 / 花钱 / 关闭` 类一律**不生成为工具**；写动作只暴露为"提案"（proposal），
   真正执行仍走人审批。用一致性闸门（桥 1）断言"工具清单 ⊆ 允许清单"。
3. **审计接入 `llm_calls` 表**：把 call_log 的内容落进库内 `llm_calls`（现 0 行），
   与主通道审计统一，可追溯"哪次模型调用查了哪些对象"。
4. **超时 / 并发 / 只读连接池**：给工具查询加超时；多请求下用只读连接池；server 崩溃 CLI 自动重连验证。
5. **input_schema 全量本体驱动**：本 PoC 已预演枚举生成，正式化时把 properties→JSON Schema 全字段生成
   （桥 2/桥 3 合流），并用桥 1 闸门校验"本体↔表↔工具"三者一致。
6. **SDK vs 手写的再决策**：若要用官方 `mcp` SDK，须在**独立 venv** 里装（勿污染锁定栈），
   并走 §3 变更协议正式登记依赖；否则维持零依赖手写版（当前推荐，因其可移植、无依赖冲突）。

### 若登录后端到端**验不通**——备选路径：

- **A. 排障**：核对 tool 命名（`mcp__ontology__*`）、`--strict-mcp-config` 是否误屏蔽、
  `--permission-mode` 是否需放行、server stderr 是否有握手报错（`claude --mcp-debug` 若该版本支持）。
- **B. 协议兜底**：若怀疑手写协议与 CLI 协商不合，临时用官方 SDK（独立 venv）复跑同一测试，
  二分定位是"CLI↔MCP 通道"问题还是"本 server 实现"问题。
- **C. 保底方案**：维持现主通道，但把"单发合成"升级为**受控多轮**——Python 端按模型的结构化意图
  多次预取（伪工具调用），虽非原生 MCP，但能部分缓解"不托底"感，零 API 成本，作为 MCP 未通时的过渡。

---

## 7. 治理备注（须编排层/人处理）

- `poc/` 是**新顶层目录**。AGENTS.md §5 规定新增顶层目录须走 §3 变更协议（人批 + 决策日志）。
  本目录为 V5 决议②授权的隔离 PoC 沙盒、不碰现有代码；**建议在编排层验收 commit 时于决策日志追认**，
  或明确其为临时沙盒并相应 gitignore。
- 本次**未 git commit**（遵红线，留编排层统一提交）；**未改** plan/决策日志/任何现有代码；
  期间对共享 conda 环境的临时改动（装 mcp）**已还原**。
- `STATUS.md` 尚未更新（留待编排层验收后按会话结束协议统一登记）。
