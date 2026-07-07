# 架构总览（v0.5 作品集）

一句话：**一个本体（ontology），三个业务闭环，一套治理机制。**
GitHub 原生渲染以下 Mermaid 图。

## 1. 分层架构

```mermaid
flowchart TB
    subgraph L1["① 模拟数据层 datagen/"]
        A1["干净世界生成<br/>（三条独立随机流 seed/+1000/+2000）"]
        A2["噪声注入 emit 层<br/>（重复/乱序/冲突/空值/名称变体）"]
        A3["ground truth 三表<br/>（风险/门禁/费用异常，与注入 1:1）"]
    end
    subgraph L2["② 数据管道 pipeline/"]
        B1["判重·乱序消解·状态冲突纠正<br/>（状态只从事件流推导）"]
        B2["供应商名称 ER"]
    end
    subgraph L3["③ 统一 Ontology（data/ontology.sqlite）"]
        C1["19 对象 · 23 关系 · 6 状态机<br/>12 动作五要素 · 6 角色权限"]
    end
    subgraph L4["④ 规则引擎 engine/"]
        D1["R1-R3 物流风险 + R4-R6 费用异常<br/>（as_of 时间旅行安全）"]
    end
    subgraph L5["⑤ 运营应用 app/"]
        E1["动作层（权限·前置·审计）"]
        E2["Streamlit 五工作台"]
    end
    subgraph L6["⑥ AI 协同 agent/"]
        F1["工具白名单（审批类永不注册）"]
        F2["确定性简报（溯源靠架构）+ 可插拔 LLM"]
    end
    L1 --> L2 --> L3 --> L4 --> L5 --> L6
    A3 -.评估基准.-> D1
```

## 2. 对象图（核心链路）

```mermaid
flowchart LR
    subgraph 共享对象
        Supplier --- Sku --- Customer
    end
    subgraph v0.2控制塔
        Customer --> SO[SalesOrder] --> SOL[SalesOrderLine]
        Supplier --> PO[PurchaseOrder] --> SHP[Shipment]
        SHP --> MS[Milestone事件流]
        SOL <--> ALC[Allocation] <--> SHP
        SHP --> RSK[RiskEvent] --> TSK[Task]
    end
    subgraph v0.3准入
        Customer --> AC[AdmissionCase] --> CF[ComplianceFinding]
        AC --> LP[LogisticsPlan] --> CS[CostScenario]
        AC -->|approved: candidate→active| Sku
    end
    subgraph v0.4费用
        SHP --> CONT[Container]
        SHP --> INV[Invoice] --> IL[InvoiceLine]
        SHP --> EC[ExpectedCost]
        IL -.异常归因.-> RSK
    end
```

关键设计：`RiskEvent→Task` 闭环被三个场景共用——延误、缺文件、停滞、超收、重复计费、
计划外费用六种规则的事件流动在**同一张风险队列**，走同一套派单/提案/审批/关闭/审计。

## 3. 闭环时序（以延误为例）

```mermaid
sequenceDiagram
    participant M as Milestone事件
    participant E as 引擎(as_of)
    participant Q as 风险队列
    participant O as 运营(ops)
    participant G as 经理(manager)
    participant L as action_log
    M->>E: eta_change 08-08 → 延误7天
    E->>E: 沿 Allocation 传播影响<br/>（行级精确到客户）
    E->>Q: RiskEvent critical（A2，审计）
    O->>Q: 派单 AssignTask（A3）
    O->>G: 提案 reschedule 08-27（A4，pending）
    Note over O,G: ops 试图自批 → 拒绝且审计（越权是一等公民事件）
    G->>Q: 批准（A5）→ 承诺日回写，行 at_risk→allocated
    O->>Q: 关闭 mitigated（A6）
    Q->>L: 全链 6 条审计，时序单调，单 SQL 可取
```

## 4. 治理与评估体系（本仓库真正的护城河）

- **决策日志 append-only**：D1-D11 / E1-E5 / F1-F5 + 勘误 N1-N5、P1-P4，
  每次建模变更有编号、有理由、有人批准
- **九个评估器**：datagen.verify（60+ 项）/ pipeline.evaluate / engine.evaluate /
  engine.evaluate_cost / 三个闭环无头测试 / agent.evaluate（22 题含红线）——
  同种子逐字节可复现，任何回归当场暴露
- **AI 护栏靠架构**：审批工具从未注册、事实字段只能来自对象、权限对人对 AI 同规——
  不编造不越权不是靠提示词求出来的
- **一次如实记录的 KPI 失败**：XE3（复盘 §0-v4），失败拆解出比达标更准的认知

## 5. 关键数字

| | |
| --- | --- |
| 业务场景 / 对象 / 关系 / 状态机 | 3 / 19 / 23 / 6 |
| 动作（五要素）/ 角色 / 风险规则 | 12 / 6 / 6 |
| Python 代码 | ~5,100 行（23 次提交，每次可验收） |
| 断言 | 70 条（69 过，1 条如实记败） |
| 演示数据 | 120 票货、147 柜、296 张发票、40 准入案件、363 条审计 |
