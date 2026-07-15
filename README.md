# 跨境供应链智能运营控制塔（Cross-Border Supply Chain Ontology Control Tower）

参考 Palantir Ontology / AIP 理念自研的统一本体控制塔原型：**一套 ontology 承载 5 个业务闭环，一套治理机制，一层对象中心的可信 AI。**

> **诚实边界**：不是数据分析看板，不是聊天机器人，**不接真实企业系统**（数据为基于真实调研的高真实感合成数据）。它证明的是**方法与能力**——怎么把复杂供应链建成对象-关系-动作模型、AI 怎么可信地嵌进去——不是"已经接好了你的 ERP"。这是 FDE（前向部署）打法。

## 五个业务场景（一套 ontology，同一套 `RiskEvent→Task` 闭环）

| 场景 | 规则 | 一句话 |
| --- | --- | --- |
| **延误运营** | R1-R3 | 运输延误 → 沿对象图行级定位受影响客户订单 → 风险/任务 → 改期/加急/接受，全程审计 |
| **费用稽核** | R4-R6 | 账单 vs 基准 → 超收/重复/计划外 → 争议/接受/按贸易术语转嫁 |
| **准入合规** | 门禁 G1-G4 | 建案 → 合规预审 → 物流方案 → 三情景成本 → 经理审批（三重门禁挡该挡的） |
| **采购** | R7-R15 | 三方对账（PO×收货×发票）+ 预付款敞口 + 供应商资质 + 单一来源 + maverick |
| **仓储库存** | R16-R18 | 断货 / 不可履约 / 盘点差异；**延误时用现货拆单先发 + 余量改期** |

**跨场景连成一张网**：采购收货 → 上架 → 库存 → 预留驱动订单履约 → 延误时查目的仓现货 → **SuggestSubstitution 保客户承诺**（白皮书业务问题的落点）。**18 条风险规则全 P/R=1.000。**

## 对象中心 + 可信 AI（Foundry 范式）

- **6 个核心决策对象有富工作台 + permission-aware 对象级 agent**：RiskEvent / Task / Invoice / AdmissionCase / PurchaseOrder / Warehouse；其余 27 个对象走自动标准视图（对象图可导航）。
- **对象级 agent 三条硬约束**（均已独立对抗验证）：只提案不审批（maker-checker）；数据范围**完全继承 UI**（`test_scope_parity` 证明）；越权在动作层挡回不在提示词（注入 "you are admin" 被拒 + 留审计）。

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
streamlit run app/streamlit_app.py                                                      # UI：切角色→点对象→对象级 agent
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
| Ontology | `ontology/` + `data/ontology.sqlite` | **34 对象** · 多状态机 · 动作五要素 · 角色权限（v0.9.0） |
| 风险引擎 | `engine/` | R1-R18；as_of 时间旅行安全；真值禁读 |
| 运营应用 | `app/` | 真·角色导航（7 角色）+ 行级数据范围 + maker-checker + **6 富工作台 + 28 标准视图** |
| 对象中心 AI | `agent/` | permission-aware 对象级 agent（继承 UI 范围 · 审批永不注册 · 越权动作层挡回） |

## 导航

| 想…… | 看 |
| --- | --- |
| **面客一页纸（全景图，浏览器直接打开）** | `docs/control-tower-overview.html`（自包含单文件，五场景/R1-R18/可信 AI/治理护城河） |
| **面客演示（5 分钟台本）** | `docs/demo-walkthrough.md` |
| 看架构（Mermaid 图） | `docs/architecture.md` |
| **首次接手 / 5 分钟上手** | `ONBOARDING.md`（导航图：一条命令验全绿、心智模型、目录图、会咬你的规则） |
| 当前状态 / 任何模型接手 | `STATUS.md`（顶部摘要） |
| **发版/面客自检门（全绿证据集）** | `docs/release-checklist.md`（一键复现 + 23 模块全绿 + 真值 md5 可证不变） |
| 查全部决策与理由 | `docs/control-tower-plan-v0.2.md §4`（决策日志 D/E/F/M/P1-P3/W1，append-only） |
| 协作规则（AI 会话从这开始） | `AGENTS.md` |
| 领域知识参考 | `美国跨境供应链实战白皮书_可视化增强版.html` |

## 治理与护城河

- **决策日志 append-only**：每次建模变更有编号、理由、Daniel 亲批（业务语义决策走人裁决）。
- **多评估器同种子逐字节可复现**：任何回归当场暴露；R1-R18 全 P/R=1.000。
- **AI 护栏靠架构**：审批工具从未注册、事实字段只能来自对象、权限对人对 AI 同规、agent 数据范围==UI。
- **诚实文化**：如实记录的 KPI 失败（XE3）；真值引擎禁读、md5 可证不变；controller "不信报告只信输出" 每次独立重跑。
