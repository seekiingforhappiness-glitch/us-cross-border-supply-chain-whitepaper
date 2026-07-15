# FDE × Ontology 企业 AI 落地操作手册

> 版本：v1.0（2026-07-12）  
> 适用范围：从一个真实业务决策出发，在 1—5 天内做出可验证原型，再逐步扩展为可运营系统。  
> 非目标：复制 Palantir 产品、把所有数据库表“本体化”、先建平台后找场景、让 AI 无人监管地改生产系统。

## 1. 研究结论

“纳米巨人”的 11 集系列可以内化为一句工程原则：

> **FDE 负责把现场的隐性决策翻译成系统；Ontology 把决策表示为对象、关系、逻辑、动作和权限；AI 只能在这套可验证、可审计、可回滚的结构内工作。**

系列最有价值的不是 Palantir 商业史，而是以下因果链：

```text
真实运营痛点 → 现场观察与共同定义结果 → 对象 / 属性 / 关系 / 状态 / 动作 / 权限
→ 数据映射与对象化 → 规则、函数与 AI 提案 → 人审或受控自动执行
→ 写回、审计、结果测量 → 把重复脏活产品化
```

官方资料支持的部分：Ontology 面向真实世界实体而非源系统表；Action 能创建、修改对象与关系；AIP Logic 可读取 Ontology，并把编辑自动应用或暂存为人工提案；权限与安全是运行时约束；Bootcamp 用客户真实用例在 1—5 天内验证价值。

需要降级处理的系列表述：

- “中国出不了 Palantir”是解释框架，不是已证实的普遍定律。
- “知识图谱只读、Ontology 可写”适合作为教学对比，但知识图谱并非定义上只能读。
- “80% 变化自动切全量索引”“外部系统多写入必然原子提交”等属于具体实现主张，不能直接推广到自建系统。
- “Ontology 自动形成世界观、认知演化”是愿景。生产事实仍是数据、规则、评估和人类修正被持续维护；未发现系统会自动修正本体、提示词或模型且无需治理。
- 视频中的营收、市值、合同、岗位数量只能说明时代背景，不能证明某个客户项目能产生 ROI。

## 2. 五个不可颠倒的原则

1. **先决策，后数据。** 先问“谁在什么条件下要做什么决定”，再确定需要哪些对象与字段。
2. **先闭环，后覆盖。** 第一个版本必须从信号走到动作回写；只有查询和大屏不算完成。
3. **模型现实，不镜像系统。** `PurchaseOrder` 是对象，`SAP_EKKO_Row` 不是业务对象。
4. **AI 默认提案，不默认执行。** 读、算、建议、起草可以先开放；不可逆、高金额、跨主体动作必须人工批准。
5. **以证据泛化。** 第一家客户允许“过拟合”；第二家记录共性；第三次重复才抽象为平台能力。

## 3. 项目立项门：不满足就不做

| 问题 | 合格答案的形态 |
| --- | --- |
| 谁痛？ | 一个明确角色，如采购经理、运营专员 |
| 痛在哪个决定？ | 如“是否切换供应商”，不是“数据不统一” |
| 今天怎么做？ | 可观察的步骤、系统、Excel、群聊、审批 |
| 决定频率？ | 每日/每周发生，足以积累反馈 |
| 错误代价？ | 金额、时效、客户、合规或人时可量化 |
| 能否闭环？ | 至少有一个系统内动作或可确认的外部执行 |
| 5 天能证明什么？ | 明确的前后对比或走查断言 |

直接否决：只有“做个 AI”“建数据中台”“把所有数据接进来”“做一个领导驾驶舱”而没有具体决策和动作。

## 4. 1—5 天 Bootcamp 作战法

### Day 0：签订实验契约

```yaml
decision: 是否对延误订单执行拆单先发
actor: 运营专员
trigger: ETA 变化导致承诺日被击穿
current_process: 查运输表 -> 查订单 -> 问仓库 -> 群内协调 -> 经理批准
target_outcome: 10 分钟内生成可审计提案
success_metric: 15 个设计案例影响定位 100%，高危漏判 0
allowed_data: 合成或脱敏样本
forbidden: 真实外部写回、自动审批、生产凭证
as_of_date: 2026-07-12
```

同时冻结验收样本、指标定义、可编辑文件、禁止动作和终止条件。不能为了过关改真值或验收口径。

### Day 1：走现场，不开产品演示

让操作者用一条真实或脱敏案例完成工作，FDE 只追问：

- 你看到什么信号才开始？
- 你依次打开哪些系统？为什么？
- 哪些字段同名不同义？谁有最终解释权？
- 哪一步需要经验判断？依据是什么？
- 哪一步会等别人？如何催、如何升级、何时放弃？
- 最终改变了什么状态？谁批准？失败后怎么恢复？
- 三个最常见例外是什么？

产出不是会议纪要，而是“决策轨迹”：触发 → 取数 → 判断 → 协调 → 批准 → 执行 → 复核。

### Day 2：建最小本体

按以下顺序建模：

1. **对象**：有稳定身份、独立生命周期、会被查询或动作改变的业务实体。
2. **属性**：做当前决定不可缺的事实；有业务定义、来源、更新时点和空值语义。
3. **关系**：运营人员真实会说出的关系，如“订单行分配到运输批次”。
4. **状态机**：允许状态、迁移、终态、回迁与二次触发。
5. **派生逻辑**：规则、聚合、模型或函数；输入和版本必须可追溯。
6. **动作**：对对象、关系或外部世界的受控改变。
7. **权限**：谁能看、提议、批准、执行、关闭；数据范围与动作权限分开。

每个动作写全五要素：输入参数、执行权限、成功状态、失败处理、审计字段。再增加三个生产字段：幂等键、批准策略、外部副作用承诺（强事务或最终一致）。

### Day 3：映射数据与构造真值

```text
source event → canonical event envelope（来源、时间、幂等键）
→ 清洗与实体解析 → object-backing tables → objects + links
→ derived signals / risks
```

保留源记录 ID、摄取时间、业务事件时间、变换版本、对象主键来源。主键要稳定且确定，禁止按行号或随机值生成。

准备正常、明确异常、灰区/对抗三类样本。真值与运行库隔离；检测逻辑不得读取真值。

### Day 4：闭环应用与 AI

最小 UI 只需工作队列、对象详情、处置表单、审计记录。页面围绕对象和动作，不围绕数据库表。

AI 上线顺序：

1. 只读解释并引用对象 ID；
2. 计算影响范围并展示证据路径；
3. 生成结构化提案；
4. 人工批准后由确定性动作层执行；
5. 同一动作长期稳定、可回滚、有监控后，才讨论自动闭环。

提示词不是安全边界。工具注册、对象范围、字段脱敏、动作层权限、审批和审计必须在代码中强制。

### Day 5：验收与选择

验收人亲手按脚本走查，而不是观看 FDE 演示。至少测试：正常链路、高风险链路、重复/乱序事件、字段缺失、错误角色动作、提示注入、外部写回失败、重复提交、as-of 重放，以及审计能否重建“当时为什么这样决定”。

最后只能选一种：`停止`、`再做一个窄切片`、`进入生产化`。不能用“很有潜力”代替证据。

## 5. Ontology 设计模板

### 对象卡

```yaml
object_type: Shipment
business_definition: 一次具有统一运输计划与状态生命周期的运输执行单元
primary_key: shipment_id
title_key: shipment_id
owner_role: ops
source_of_truth: tms
states: [planned, in_transit, arrived, customs, delivered]
key_properties: [eta_current, mode, destination_port, customs_status]
links: [contains_po, allocated_to_so_line, has_milestone]
actions: [ingest_milestone, create_risk_event]
security: region + role + sensitive-field policy
```

### 动作卡

```yaml
action: ApproveMitigation
actor: manager
target: Task
preconditions: [task.status == proposed, proposer != approver]
inputs: [decision, note, as_of_date]
effects: [update task, update affected objects, append audit]
external_effect: none
idempotency_key: task_id + proposal_version
failure: rollback local transaction; preserve failed audit
human_review: required
```

### 反模式

- 源系统镜像对象或一个万能对象塞进所有字段；
- 只建名词，不建状态、动作和权限；
- 把自动变换做成用户动作，或把需审批决策藏进管道；
- 主键变化导致对象、关系、编辑和审计失联；
- 多个团队各建一套 Supplier；
- AI 绕过动作层直接写数据库；
- 用 null 冒充无权限，导致 AI 把“不可见”误判为“不存在”。

## 6. 权限与自主权矩阵

| 等级 | AI 能力 | 默认控制 |
| --- | --- | --- |
| L0 | 检索、解释、引用 | 只读、范围过滤、敏感字段脱敏 |
| L1 | 计算、排序、生成草稿 | 结果可复算，禁止状态改变 |
| L2 | 结构化提案 | 人工审批，展示影响预览与依据 |
| L3 | 自动执行低风险动作 | 白名单动作、幂等、可回滚、断路器 |
| L4 | 多步协调与异常恢复 | 服务身份、预算/时限、全过程审计 |

升一级必须同时满足：足够真实样本、固定评估集稳定通过、失败可检测、影响可限制、回滚演练通过、业务负责人批准。出错自动降级。

## 7. 指标体系

1. **数据**：完整性、及时性、重复率、实体解析命中率；
2. **检测**：precision、recall、高危漏判、噪声误报；
3. **影响**：受影响对象集合与数量是否精确；
4. **动作**：成功率、重复执行率、回滚率、审批耗时、闭环率；
5. **业务**：处理时长、避免损失、现金回收、客户影响等可验证结果。

AI 评估包含对象引用正确、范围一致、拒绝越权、不编造、对抗输入、同题多次稳定性。模型、提示词、工具或本体变化后跑同一套回归。

## 8. FDE 组织运行法

FDE 对一个客户负责多种能力；平台工程对一种能力服务多个客户：

```text
现场手工解决一次 → 记录步骤与例外
第二次复用 → 保留客户差异
第三次重复 → 提取稳定接口、模板、测试和配置
平台化后 → FDE 不再手工做这部分
```

每周复盘新业务语义与冲突、本体变更影响、客户特例是否值得泛化、AI 错误是否进入评估集、动作该扩权还是降级、哪些集成仍靠人工搬运。

## 9. 与本仓库的映射

| 手册能力 | 仓库实现 |
| --- | --- |
| 对象、属性、关系、动作 | `ontology/control-tower-ontology.json` |
| 稳定合成数据与真值 | `datagen/`、`data/truth/` |
| 数据清洗与对象化 | `pipeline/` |
| 风险与影响传播 | `engine/` |
| 对象工作台与动作回写 | `app/` |
| 权限感知 AI 工具 | `agent/` |
| 验收断言 | `docs/demo-assertions.md` |

下一阶段不应继续加对象或规则来“显得完整”。优先验证真实缺口：外部信息进入、真实旧系统写回、决策时世界快照、协调回复渠道、租户级强隔离、真实用户样本形成评估集。这些必须在真实客户或明确授权的测试环境中验证，不能靠合成数据宣称完成。

## 10. 交付物清单

- 一页实验卡与验收结论；
- 现场决策轨迹；
- 对象/关系/状态图和属性口径冲突表；
- 动作五要素、幂等和失败语义；
- 权限与自主权矩阵；
- 源到对象的数据血缘；
- 固定真值和评估报告；
- 人工走查脚本；
- 审计与决策快照；
- 已知限制、未验证假设、下一次停止条件；
- 重复脏活清单与候选产品化项。

## 11. 证据与溯源

视频一手材料：2026-07-12 通过 Record & Replay 确认作者主页系列边界，定位第 1—11 集；逐集取得真实 MP4（合计约 75 分钟），用本地 Whisper small 离线转写后归纳。自动转写存在专名误识别，因此本文不把转写文本当作数字和专名的最终证据。

官方核验：

- Palantir, [Ontology design: Best practices](https://www.palantir.com/docs/foundry/ontology/ontology-best-practices)
- Palantir, [Object and link types reference](https://www.palantir.com/docs/foundry/object-link-types/type-reference)
- Palantir, [The Ontology system](https://www.palantir.com/docs/foundry/architecture-center/ontology-system)
- Palantir, [AIP Logic overview](https://www.palantir.com/docs/foundry/logic)
- Palantir, [Action rules](https://www.palantir.com/docs/foundry/action-types/rules)
- Palantir, [Create an object type](https://www.palantir.com/docs/foundry/object-link-types/create-object-type/index.html)
- Palantir, [Solution Design](https://www.palantir.com/docs/foundry/use-case-life-cycle/solution-design)
- OpenAI, [OpenAI Deployment Company](https://openai.com/index/openai-launches-the-deployment-company/)
- Blackstone, [Anthropic enterprise AI services firm](https://www.blackstone.com/news/press/anthropic-partners-with-blackstone-hellman-friedman-and-goldman-sachs-to-launch-enterprise-ai-services-firm/)

仓库内更深机制核验见 `docs/research/2026-07-10-palantir-ontology-aip-deep-dive.md`；面向非工程学习者的内化教材见 `docs/product-foundations-primer.md`。
