# v0.3 规划 — SKU 准入报价闭环（ontology 复用验证）

版本：v0.3 ｜ 起草：2026-07-06 ｜ 状态：**已批准（E1-E5 全部批准，2026-07-06），执行中**
本文件即 v0.3 决策日志载体：E1-E5 为 append-only 决策，变更须走 AGENTS.md §3 协议。
上游文档：`docs/cross-border-ontology-manual.md`（v0.1，领域规格）+ `docs/control-tower-plan-v0.2.md`（工程约定与决策日志）

## 1. 目标与验证命题

实现 v0.1 手册定义的 SKU 准入报价闭环，**与控制塔共享同一个 ontology 存储、同一套动作/审计/权限机制**。

核心验证命题（Palantir 模式的核心卖点）：
> 第二个业务场景上线时，共享对象（Supplier/Sku/Customer）**零重复定义**，
> 治理机制（动作五要素、审计、权限、AI 护栏）**零重建**，只新增场景特有的对象与动作。

反目标：不重做控制塔已有的东西；不做真实 HTS/关税计算（合规规则全部模拟）；不接外部系统。

## 2. 业务闭环（v0.1 §1 的最小闭环，原样采纳）

```text
销售创建 AdmissionCase（针对候选 SKU）
→ 合规预审 ComplianceFinding（HTS/PGA/文件缺口/UFLPA）
→ 运营出 LogisticsPlan（路线/贸易术语/时效）
→ 财务算 CostScenario（保守/中性/乐观三情景）
→ 经理审批（approve / quote_with_conditions / reject / needs_more_info）
→ AI 全程只出结构化建议（v0.1 §7 schema），人是唯一审批入口
```

## 3. 集成决策（E1-E5，本方案的实质内容，批准后并入决策日志）

**E1 — Sku 对象合并 + 生命周期。** 不建第二个 SKU 对象。现有 Sku 增加准入属性
（declared_value_usd、包装重量尺寸、battery/food_contact/children 三个监管 flag、platform）
和 `sku_status`（candidate/active）。现有 20 个目录 SKU = active（准入字段可空，语义为
"当年准入时未数字化"——真实遗留数据的样子）；datagen 新增候选 SKU 供案件使用。
准入 approved 后 SKU 可转 active——两个场景经由生命周期真实咬合。

**E2 — Customer/Supplier 原对象扩展。** Customer 增 business_model、ior_capability、
broker_status、risk_tier(敏感)；Supplier 增 factory_audit_status、compliance_docs_status、
uflpa_risk_flag(敏感)、origin_evidence_status。复用性的直接体现：下单的客户和被准入
审查的客户是同一批对象，控制塔的延误历史未来可反哺准入风险评估（v0.4 候选）。

**E3 — 角色并集（3→6）。** 新增 sales、compliance、finance；ops/manager 两场景共用；
cs 仅控制塔。敏感字段规则按 v0.1 §6 落地：sales 不可见成本与 risk_tier；
compliance 不可见报价底线；finance 可见成本利润。UI 角色切换器扩到 6 角色。

**E4 — 审批门禁做进动作前置（不是风险规则）。** v0.1 的三条硬门禁——
critical 合规发现禁批、DDP 无 IOR 禁批、无成本情景禁批——实现为
ApproveQuoteDecision 的前置校验（同 A5/A6 的模式），失败拒绝并审计。
准入是工作流不是事件流，不产生 RiskEvent，不依赖 as_of 时钟（审计时间戳仍用统一约定）。

**E5 — AI 输出沿用 v0.1 §7 结构化 schema。** 新增 get_admission_context 查询工具 +
admission briefing（确定性计算 risk_level/missing_documents/recommended_route 等字段），
ApproveQuoteDecision / RejectOrRequestMoreInfo 对 AI 永不注册（与 v0.2 同一护栏模式）。

## 4. 交付物与四段执行计划（10h+/周，约 4 周）

### V1 建模对齐（提案→人批准）
- manual v0.3 增补文档：3 对象扩展字段表、4 新对象（AdmissionCase/ComplianceFinding/
  LogisticsPlan/CostScenario）按 v0.2 表格式重写、6 动作五要素（v0.1 §5 已有八成，补函数签名）、
  6 角色权限矩阵
- ontology JSON v0.3 合并（单文件，控制塔+准入）
- `docs/demo-assertions-admission.md`：一条 happy path + 两条门禁反断言 + 权限/AI 断言（约 15 条）
- **验收**：断言清单覆盖闭环五步；E1-E5 并入决策日志

### V2 数据与对象层
- datagen 扩展：候选 SKU ~15、准入案件 ~40（各状态分布）、合规发现/物流方案/成本情景、
  设计案例 ≥5（happy path / critical 阻断 / DDP 无 IOR 阻断 / 补资料回流 / 三情景毛利为负）
- ground truth：门禁预期结果表（哪些案件的批准尝试必须被拒）
- pipeline 扩展建表；**控制塔全链路回归必须保持全绿**
- **验收**：datagen verify 新增断言全过 + 旧断言零回归

### V3 动作与应用
- 6 个动作实现（app/actions.py 同风格：权限/前置/审计）
- UI 新增"📋 准入工作台"标签页：案件列表、案件详情（对象跳转）、动作表单
- 无头闭环测试：happy path 走通 + 三条门禁拒绝 + 越权拒绝
- **验收**：非开发者 5 分钟走完一个案件从 draft 到 approved

### V4 AI 与收尾
- agent 工具扩展 + admission briefing（v0.1 §7 schema）+ 评估集 +6 题（含诱导批准 DDP、
  编造 HTS 两条红线题——v0.1 §7 禁止行为的直接测试）
- README/retrospective 更新；复用性结论写入复盘
- **验收**：AI 评估全过；全项目六个评估器一次跑通

## 5. KPI（全部可测）

| 指标 | 目标 |
| --- | --- |
| 复用性：重复定义的对象类型 | **0**（无 Sku_2/Customer_2 之类） |
| 复用性：控制塔回归 | 5 个既有评估器零回归 |
| 门禁正确率（设计案例） | 100%（该拒的批准尝试全部被拒且审计） |
| 准入断言 | ~15 条全过 |
| AI 红线 | 编造 HTS=0、越权批准=0 |

## 6. 风险

| 风险 | 应对 |
| --- | --- |
| 对象扩展打破控制塔（最大风险） | V2 起每次提交前跑全量回归；扩展字段一律可空 |
| 成本情景计算发散成定价引擎 | 三情景成本=输入项加总+毛利率一条公式，不做费率表 |
| 角色×场景权限矩阵膨胀 | 只实现 v0.1 §6 明文列出的规则，不发明新规则 |
