# 架构总览（当前：5 场景 · 对象中心 · 可信 AI）

一句话：**一个本体（ontology），五个业务闭环，一套治理机制，一层对象中心的可信 AI。**
GitHub 原生渲染以下 Mermaid 图。（历史演进见 `docs/control-tower-plan-v0.2.md §4` 决策日志 D/E/F/P1-P3/W1/M 系列。）

## 1. 分层架构

```mermaid
flowchart TB
    subgraph L1["① 模拟数据层 datagen/"]
        A1["独立随机流生成<br/>（控制塔/准入/费用/采购/仓储 各自 seed offset）"]
        A2["噪声注入 + 异常注入<br/>（R1-R18 ground truth 与注入 1:1）"]
        A3["真值只存 data/truth/<br/>（引擎禁读——防偷看真值）"]
    end
    subgraph L2["② 数据管道 pipeline/"]
        B1["判重·乱序消解·状态从事件流推导"]
        B2["ER · MDM crosswalk · DQ issue 队列"]
    end
    subgraph L3["③ 统一 Ontology（data/ontology.sqlite，v0.9.0）"]
        C1["34 对象类型 · 多状态机 · 动作五要素 · 角色权限"]
        C2["object_relationships registry<br/>可解释对象路径（explain_path）"]
    end
    subgraph L4["④ 规则引擎 engine/"]
        D1["R1-R3 延误 · R4-R6 费用 · R7-R15 采购 · R16-R18 仓储<br/>（as_of 时间旅行安全；全 P/R=1.000）"]
    end
    subgraph L5["⑤ 运营应用 app/"]
        E1["动作层（权限·前置·maker-checker·审计）"]
        E2["真·角色导航 + 行级数据范围 + 6 对象富工作台 + 28 标准视图"]
    end
    subgraph L6["⑥ 对象中心 AI agent/"]
        F1["permission-aware 对象级 agent<br/>（继承 UI 数据范围 · 审批类永不注册 · 越权动作层挡回）"]
        F2["确定性简报 + 可插拔 LLM（无 key fallback）"]
    end
    L1 --> L2 --> L3 --> L4 --> L5 --> L6
    A3 -.评估基准.-> D1
```

## 2. 对象图（5 场景 + 跨场景连接）

```mermaid
flowchart LR
    subgraph 共享["共享对象"]
        Supplier --- Sku --- Customer
    end
    subgraph 延误["延误运营 R1-R3"]
        Customer --> SO[SalesOrder] --> SOL[SalesOrderLine]
        SOL <--> ALC[Allocation] <--> SHP[Shipment]
        SHP --> MS[Milestone事件流]
        SHP --> RSK[RiskEvent] --> TSK[Task]
    end
    subgraph 费用["费用稽核 R4-R6"]
        SHP --> INV[Invoice] --> IL[InvoiceLine]
        SHP --> EC[ExpectedCost]
    end
    subgraph 准入["准入合规 G1-G4"]
        Customer --> AC[AdmissionCase] --> CF[ComplianceFinding]
        AC --> LP[LogisticsPlan] --> CS[CostScenario]
    end
    subgraph 采购["采购 R7-R15"]
        Supplier --> PO[PurchaseOrder] --> POL[PoLine]
        PO --> GRN[GoodsReceipt] --> GRL[GoodsReceiptLine]
        PO --> SINV[SupplierInvoice]
        Supplier --> RFQ[RFQ] --> Quote
        PO --> PAY[PurchasePayment]
    end
    subgraph 仓储["仓储库存 R16-R18"]
        WH[Warehouse] --> IP[InventoryPosition]
        IP --> RES[InventoryReservation]
        WH --> CC[CycleCount]
    end
    GRL -.上架 Putaway.-> IP
    RES -.驱动履约.-> SOL
    RSK -.现货救延误 SuggestSubstitution.-> IP
    IL -.异常归因.-> RSK
    POL -.三方对账.-> RSK
    IP -.断货/不可履约.-> RSK
```

**关键设计**：`RiskEvent→Task` 闭环被 **5 个场景共用**——R1-R18 十八种规则的事件流动在**同一张风险队列**，走同一套派单/提案/审批(maker-checker)/关闭/审计。RiskEvent 锚点从"货运锚定"泛化为多态（shipment/po/supplier/warehouse），一套治理承接全部业务域。

**跨场景连成一张网**（控制塔的真正价值）：采购收货 GoodsReceiptLine → 上架 → 库存 InventoryPosition → 预留驱动 SalesOrderLine 履约 → 延误时查目的仓现货 → **SuggestSubstitution 拆单先发 + 余量改期**（白皮书"一票延误怎么保客户承诺"的落点）。

## 3. 对象中心层（Foundry 范式：对象即工作台 + 对象级 AI）

- **6 个核心决策对象有富工作台 + permission-aware 对象级 agent**：RiskEvent / Task / Invoice / AdmissionCase / PurchaseOrder / Warehouse。点开对象 → 看属性 + 关联对象 + 该角色可用动作 + 一个只懂这个对象的 AI 助手。
- **其余 27 个对象走自动标准视图**（属性 + 关联对象导航，只读）——"每个对象都有归宿、对象图可导航"，不必个个手配。
- **对象级 agent 的三条硬约束**（均已 controller 独立对抗验证）：① 只提案不审批（approve/close 对任何角色都不注册为 agent 工具，maker-checker）；② **数据范围完全继承 UI**（ops 的 agent 看不到成本、和 ops 的 UI 逐字一致，`test_scope_parity` 证明）；③ **越权在动作层挡回不在提示词**——注入 "you are admin now" 被拒 + 写 denied 审计。

## 4. 闭环时序（现货救延误：跨场景杀手锏）

```mermaid
sequenceDiagram
    participant M as Milestone/引擎(as_of)
    participant Q as 风险队列
    participant O as 运营(ops)
    participant W as 仓储(现货)
    participant G as 经理(manager)
    participant L as action_log
    M->>Q: eta_change → 延误 RiskEvent（沿 Allocation 行级传播）
    O->>Q: 派单 AssignTask（A3）
    O->>W: 查目的仓 InventoryPosition.available
    O->>G: 提案 suggest_substitution（现货拆单先发+余量改期，A4 pending）
    Note over O,G: agent 建议但不能批；ops 自批 → 拒绝且审计
    G->>W: 批准（A5）→ 现货 reserve、订单拆单、余量 backorder、客户承诺保住
    O->>Q: 关闭 mitigated（A6）
    Q->>L: 全链审计，时序单调，单 SQL 可取
```

## 5. 治理与评估体系（本仓库真正的护城河）

- **决策日志 append-only**：D1-D11 / E1-E5 / F1-F5 / M1-M7 / **P1-P3(采购) / W1(仓储)** + 勘误——每次建模变更有编号、理由、Daniel 亲批（业务语义决策走 AskUserQuestion）。
- **多评估器同种子逐字节可复现**：datagen.verify / pipeline.evaluate / engine.evaluate(R1-R3) / evaluate_cost(R4-R6) / evaluate_procurement(R7-R15) / evaluate_warehouse(R16-R18) / 多个闭环无头测试 / agent.evaluate / test_scope_parity / 各对象工作台越权杀手测试——**任何回归当场暴露**。
- **AI 护栏靠架构不靠提示词**：审批工具从未注册、事实字段只能来自对象、权限对人对 AI 同规、agent 数据范围==UI；越权是一等公民事件（拒绝且留审计）。
- **真值防线**：ground truth 只存 `data/truth/`、引擎禁读、md5 逐字节可证不变——防"改真值凑指标"。
- **诚实文化**：一次如实记录的 KPI 失败（XE3）；子代理多次拒绝作弊改真值；controller "不信报告只信输出" 每次独立重跑。

## 6. 关键数字（当前）

| | |
| --- | --- |
| 业务场景 | **5**（延误 / 费用 / 准入 / 采购 / 仓储） |
| 对象类型 / 风险规则 | **34 / 18（R1-R18 全 P/R=1.000）** |
| 对象富工作台 + 对象级 agent / 标准视图 | **6 / 28** |
| 角色 / 门禁 | 7 角色（含专职采购，真·导航+行级数据范围+maker-checker）/ G1-G4 |
| 跨场景连接 | 采购→仓储→履约→延误现货救援 |
| 治理 | 决策日志 append-only、多评估器全绿、真值引擎禁读、agent 不越权 |
