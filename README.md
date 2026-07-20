# 跨境供应链智能运营控制塔（Cross-Border Supply Chain Ontology Control Tower）

参考 Palantir Ontology / AIP 理念自研的统一本体控制塔原型：**一套 ontology 承载 6 个业务闭环，一套治理机制，一层对象中心的可信 AI。**

> **诚实边界**：不是数据分析看板，不是聊天机器人，**不接真实企业系统**（数据为基于真实调研的高真实感合成数据）。它证明的是**方法与能力**——怎么把复杂供应链建成对象-关系-动作模型、AI 怎么可信地嵌进去——不是"已经接好了你的 ERP"。这是 FDE（前向部署）打法。

## 六个业务场景（一套 ontology，同一套 `RiskEvent→Task` 闭环）

| 场景 | 规则 | 一句话 |
| --- | --- | --- |
| **延误运营** | R1-R3 | 运输延误 → 沿对象图行级定位受影响客户订单 → 风险/任务 → 改期/加急/接受，全程审计 |
| **费用稽核** | R4-R6 | 账单 vs 基准 → 超收/重复/计划外 → 争议/接受/按贸易术语转嫁 |
| **准入合规** | 门禁 G1-G4 | 建案 → 合规预审 → 物流方案 → 三情景成本 → 经理审批（三重门禁挡该挡的） |
| **采购** | R7-R15 | 三方对账（PO×收货×发票）+ 预付款敞口 + 供应商资质 + 单一来源 + maverick |
| **仓储库存** | R16-R18 | 断货 / 不可履约 / 盘点差异；**延误时用现货拆单先发 + 余量改期** |
| **资金流** | R19-R21 | 逾期应收 / 现金水位预警（14 天净流出）/ 重复或不符付款 → AI 催收提案候人批 |

**跨场景连成一张网**：采购收货 → 上架 → 库存 → 预留驱动订单履约 → 延误时查目的仓现货 → **SuggestSubstitution 保客户承诺**（白皮书业务问题的落点）。**21 条风险规则全 P/R=1.000。**

## 对象中心 + 可信 AI（Foundry 范式）

- **6 个核心决策对象有富工作台 + permission-aware 对象级 agent**：RiskEvent / Task / Invoice / AdmissionCase / PurchaseOrder / Warehouse；其余 27 个对象走自动标准视图（对象图可导航）。
- **对象级 agent 三条硬约束**（均已独立对抗验证）：只提案不审批（maker-checker）；数据范围**完全继承 UI**（`test_scope_parity` 证明）；越权在动作层挡回不在提示词（注入 "you are admin" 被拒 + 留审计）。

## 本体即发动机（V5 三座桥，2026-07-14 起）

本体 JSON 不再只是图纸：**权限字典、AI 工具清单、表结构、数据校验器、MCP 工具、REST 路由全部由它生成或解释**——改本体一行，全链同步。桥1 一致性闸门（`pipeline/ontology_lint.py --strict`）入发版门，图纸与发动机对不上时机器拦截发布。**AI 经 MCP 真调用本体工具**（订阅通道零 API 费）：UI「询问」按钮下模型自己多轮查库作答，每次调用留痕 `llm_calls` 审计账本；7 个写提案工具走既有 dispatch（无第二写路径），审批类动作在协议层就不存在。

## 双 React 应用（FastAPI 底座，`apps/`）

- **驾驶舱 `apps/cockpit/`**（前台，给运营者）：七区指挥墙（钱/履约/客户/供应商/库存/AI/待拍板，真数字+告警排前）→ 四段式下钻（墙→队列→影响面板/对象卡→动作）→ 履约卡一键切**夜景真地图**（真实海岸线+港口经纬度+航线弧）；AI 工作流故事卡（人话主句零内部码）。双世界一键切换（验证世界/模拟世界 `ONTOLOGY_DB`）。
- **透视镜 `apps/builder-console/`**（后台，给建造者）：14 视图——全局跨类型搜索（32k 实例）、影响分析（改它牵连什么的五维消费面）、动作↔AI 工具联动（选中动作即见其工具形态，冻结区红标）、等距本体地图（7 域浮板+35 类型棱柱+关系走廊）等。
- **API `apps/api/`**：五组 REST 路由 + 驾驶舱聚合三端点，全本体驱动零平行硬编码，36 用例含手工 SQL 对照。

## 快速开始

```bash
pip install -r requirements.txt

# 完整链（改代码后 streamlit 要完整重启，别热重载）
python3 -m datagen.generate && python3 -m datagen.verify
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate
python3 -m engine.detect && python3 -m engine.evaluate && python3 -m engine.evaluate_cost \
  && python3 -m engine.evaluate_procurement && python3 -m engine.evaluate_warehouse   # R1-R18 全 P/R=1.000
python3 -m datagen.seed_demo_ops                                                        # 运营快照（活的数据）
python3 -m app.test_closed_loop && python3 -m app.test_cost_loop \
  && python3 -m app.test_admission_loop && python3 -m app.test_procurement_loop \
  && python3 -m app.test_warehouse_loop && python3 -m app.test_sourcing_loop            # 各场景闭环
python3 -m app.test_scope_parity && python3 -m agent.evaluate                           # agent 数据范围==UI + AI 评估
streamlit run app/streamlit_app.py                                                      # 过渡操作台：切角色→点对象→审批
python3 -m engine.evaluate_finance                                                       # 资金流 R19-R21 P/R=1.000

# 驾驶舱（活的模拟世界）
python3 -m sim.backfill                                                                  # 生成模拟世界（一次）
ONTOLOGY_DB=data/simworld.sqlite uvicorn apps.api.main:app --port 8100                   # API（另终端）
cd apps/cockpit && npm install && npm run dev                                            # → http://localhost:5174

# 透视镜（建造者视角）
cd apps/builder-console && python3 export_data.py && npm install && npm run dev          # → http://localhost:5173
```

**面客走查台本**：`docs/demo-walkthrough.md`（5 分钟故事：真角色导航 → 对象工作台+AI → 现货救延误 → 越权被拒 → 五场景精度可验）。

AI 对话——**默认走本账号 Claude 订阅（`claude` CLI）+ Opus 4.8，无需 API key**：
```bash
python3 -m agent.llm_agent "SHP-2026-0099 为什么有风险？该怎么处理？"   # 默认 AGENT_PROVIDER=claude_cli
# 亦可切用付费 API：export AGENT_PROVIDER=openai（需 OPENAI_API_KEY）或 anthropic（需 ANTHROPIC_API_KEY）
```
UI 里 6 个对象工作台的「询问」按钮即用 Opus 4.8 作答：仅据该对象的权限脱敏简报合成回答（继承 UI 数据范围、
不编造、只解释不审批；订阅/CLI 不可用时优雅降级为确定性简报）。

## 架构（六层）

见 `docs/architecture.md`（Mermaid 图：分层/对象图/跨场景连接/现货救延误时序/治理体系）。

| 层 | 目录 | 要点 |
| --- | --- | --- |
| 模拟数据 | `datagen/` | 各场景独立随机流；R1-R18 注入 + ground truth（真值只存 `data/truth/`、引擎禁读） |
| 数据管道 | `pipeline/` | 判重/乱序消解/ER/MDM/DQ 队列；状态只从事件流推导 |
| Ontology | `ontology/` + `data/ontology.sqlite` | **35 对象/56 关系/32 动作** · 多状态机 · AI 暴露声明 · 敏感字段规则（v0.11.2，运行时唯一权威源） |
| 风险引擎 | `engine/` | R1-R23；as_of 时间旅行安全；真值禁读 |
| 运营应用 | `app/` | 真·角色导航（7 角色）+ 行级数据范围 + maker-checker + **6 富工作台 + 28 标准视图** |
| 对象中心 AI | `agent/` | MCP server（角色过滤/声明脱敏/审计入库）+ permission-aware agent（审批永不注册 · 越权动作层挡回） |
| 服务与前端 | `apps/` | FastAPI（本体驱动路由）+ 驾驶舱 React + 透视镜 React（双世界数据源） |
| 模拟世界 | `sim/` | 14 个月连续历史 + 异常连锁 + AI 闭环留痕（与验证世界物理隔离，驾驶舱/透视镜的活数据） |

## 导航

| 想…… | 看 |
| --- | --- |
| **面客一页纸（全景图，浏览器直接打开）** | `docs/control-tower-overview.html`（自包含单文件，五场景/R1-R18/可信 AI/治理护城河） |
| **面客演示（5 分钟台本）** | `docs/demo-walkthrough.md` |
| 看架构（Mermaid 图） | `docs/architecture.md` |
| **首次接手 / 5 分钟上手** | `ONBOARDING.md`（导航图：一条命令验全绿、心智模型、目录图、会咬你的规则） |
| 当前状态 / 任何模型接手 | `STATUS.md`（顶部摘要） |
| **发版/面客自检门（全绿证据集）** | `docs/release-checklist.md`（一键复现 + 23 模块全绿 + 真值 md5 可证不变） |
| 查全部决策与理由 | `docs/control-tower-plan-v0.2.md §4`（决策日志 D/E/F/M/P/V 系列，append-only；V5-V11=本体运行时化与驾驶舱） |
| 协作规则（AI 会话从这开始） | `AGENTS.md` |
| 领域知识参考 | `美国跨境供应链实战白皮书_可视化增强版.html` |

## 治理与护城河

- **决策日志 append-only**：每次建模变更有编号、理由、Daniel 亲批（业务语义决策走人裁决）。
- **多评估器同种子逐字节可复现**：任何回归当场暴露；R1-R23 全 P/R=1.000。
- **AI 护栏靠架构**：审批工具从未注册、事实字段只能来自对象、权限对人对 AI 同规、agent 数据范围==UI。
- **诚实文化**：如实记录的 KPI 失败（XE3）；真值引擎禁读、md5 可证不变；controller "不信报告只信输出" 每次独立重跑。
