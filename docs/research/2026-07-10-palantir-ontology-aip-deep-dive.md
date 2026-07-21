# Palantir Foundry Ontology 与 AIP 底层机制调研报告

> 调研执行：general-purpose 子代理（WebSearch + WebFetch），2026-07-10
> **信源分级**：【官方】=palantir.com/docs 或官方博客/PR 直接抓取；【官方-摘要】=官方页面但仅经搜索摘要获得；【第三方】；【推断】=本报告综合推断。文档持续改版（Agent Studio 已更名 Chatbot Studio），URL 以 2026-07 抓取为准。

---

## 一、Ontology 核心概念清单与语义

**它是什么。** 官方定义：Ontology 是"组织的数字孪生"。更深一层的官方定位：**"The Ontology is a system designed to represent the decisions in an enterprise, not simply the data"**——本体表示的是企业的"决策"，不只是数据。每个运营决策被拆成四要素：data（信息）、logic（评估逻辑）、action（执行）、security（治理）。【官方】
- https://www.palantir.com/docs/foundry/ontology/core-concepts
- https://www.palantir.com/docs/foundry/ontology/why-ontology

**完整概念清单**（均官方定义，出处 core-concepts）：

| 概念 | 官方语义 |
|---|---|
| Object Type | "the schema definition of a real-world entity or event"；object type≈数据集，object≈行，property≈列 |
| Property / Shared Property | 实体特征的 schema 定义；shared property 可跨 object type 复用 |
| Link Type | "the schema definition of a relationship between two object types" |
| Action Type | "the definition of a set of changes or edits to objects, property values, and links that a user can take at once"，含提交时触发的 side effect |
| Function | 代码逻辑单元，与本体原生集成（可以 object/object set 为输入，被 action 和上层应用调用） |
| Interface | 描述 object type 形状与能力的类型，提供多态 |
| Object View | 对象的 360° 页面（关联对象、指标、分析、应用入口） |
| Role | "the central permissioning model in the Ontology" |

**为什么这样设计。** 架构文档关键论断：**"The data objects, or 'nouns', must be complemented by 'verbs' in order to model decisions; semantics must be paired with kinetics"**——名词（对象/链接）必须配动词（action/function）才能建模决策。本体组织为三部分：Language（语义建模）、Engine（读写引擎）、Toolchain（OSDK）。【官方】https://www.palantir.com/docs/foundry/architecture-center/ontology-system

**Action Type 的完整结构**（全部【官方】）：

1. **Parameters**——动作输入，是 Rules 与上层应用之间的接口。
2. **Rules**——参数转本体编辑的变换逻辑，共 11 种：create/modify/create-or-modify/delete object(s)、create/delete link(s)、**function rule**（函数背书的动作，排他）、4 种 interface 规则。属性赋值四种来源：from parameter、object parameter property、static value、Current User/Time。有顺序约束（"objects cannot be deleted before they are added or modified"）。
3. **Submission Criteria（前置校验）**——决定动作能否提交，"support encoding business logic into data editing permissions"。条件结构 `[Value 1] [Operator] [Value 2]`；支持 all/any/none 组合嵌套；每根条件可配失败提示文案。**关键模板 Current User**：校验提交者用户 ID、群组、multipass 属性——"谁能批、谁能提"的落点。与本体权限相互独立：有编辑权限仍可能被 criteria 拦住。
4. **Side Effects**——criteria 未通过则不触发。两类：
   - **Writeback webhook**（回写外部系统）：在其他 rule 之前执行，"if the webhook execution fails, no other changes will be made"（失败则整个动作中止——跨系统事务性保证；每 action 只能一个；输出可喂后续 rules）。
   - **Side-effect webhook**：本体编辑成功后异步执行，"best-effort"，可多个。用于向 SAP/Salesforce 等发 REST；OAuth2 由 Foundry 代管。
   - **Notifications**：收件人静态/参数/对象属性/函数动态生成；**权限硬约束："Users may only receive notifications containing data which they are allowed to view"**，两种失败模式可选（任一收件人无权则整体失败回滚，或只发给有权者）。
5. **权限与审计**——每次提交进 action log，有 action metrics。
- https://www.palantir.com/docs/foundry/action-types/{overview,rules,submission-criteria,webhooks,notifications}

## 二、Action 执行模型：自动执行 vs 人工审批

**结论先行：Palantir 没有单一"审批开关"，而是四层机制的组合。**

1. **Automate（自动化引擎）**【官方】：条件（时间型/对象数据型/流式/组合）持续评估，满足即执行效果。效果四类：Submit Foundry actions / Trigger AIP Logic functions / Execute Foundry functions / Send notifications。支持从触发对象映射参数、批量模式、重试（指数退避+抖动）、**"at-least-once"语义（同一触发可能执行多次，需要幂等设计）**。https://www.palantir.com/docs/foundry/automate/overview

2. **自动 vs 暂存审批的分级：AIP Logic × Automate 的 staged proposals**【官方】：原文"**AIP Logic can now be automated such that Ontology edits can be automatically applied or staged for human review**"。配置为暂存时，Agent proposals 进入 **Proposals 标签页**，审阅者可查看提案生成原因、预览将执行的 Action、并通过 **Agent decision log** 检查 LLM 生成该提案的决策过程；接受即执行，卡片移入 Applied。**这就是官方产品化的"AI 只提案、人来批"。** https://www.palantir.com/docs/foundry/logic/aip-logic-integration-automate

3. **Agent 工具级确认**【官方】：AIP Agent 的 Action 工具"**can be configured to run automatically or to run after confirmation from the user**"——每个动作工具单独配置。https://www.palantir.com/docs/foundry/agent-studio/tools

4. **业务审批流**：人提交动作无通用审批开关；官方路径：(a) **Submission Criteria + 状态机建模**实现业务四眼审批【官方能力+模式推断】；(b) 平台治理层 **Approvals 应用**（群组成员、Project 访问、本体 schema 变更，"All tasks associated with a request must be approved"）；(c) Project 默认审批策略可配最少审批人数；(d) 本体 schema 变更走 ontology proposals 评审。

5. **渐进自治的官方叙事**【官方博客 "Connecting Agents to Decisions"，2026-04】：提案先经人审、可"safely staged as scenarios"沙箱推演；随信任累积，运营方"**surgically choose which trusted, well-worn AI processes can automatically close the action loop without human review**"——外科手术式挑选哪些久经考验的 AI 流程可免审闭环，授权可随表现扩大或收回。https://blog.palantir.com/connecting-agents-to-decisions-277dee8ddb40

## 三、权限模型

**层级**【官方】：
- **Organization**：强制隔离墙（每用户属一个 org，可作多 org 的 guest）
- **Space**：“high-level containers of projects with one common ontology”；多 org space 支持跨企业协作——跨境供应链多主体协作的参考结构
- **Project**："**the primary security boundary in Foundry**"；默认角色 Owner/Editor/Viewer/Discoverer 向下继承
- **强制 vs 自主双轨**：角色是自主（discretionary）授权，但"**mandatory controls, Organizations and Markings, will always prevent an ineligible user from accessing a resource, regardless of the user's role**"——Marking（如 PII）是合取的强制控制，随血缘传播（衍生数据自动继承），Owner 也不能未经授权移除。
- https://www.palantir.com/docs/foundry/security/{orgs-and-spaces,projects-and-roles,markings}

**行/列/单元格级**【官方】：
- **Restricted Views**（数据集侧行级）：用 granular policy 比较用户属性与列值
- **Object Security Policies**（对象存储 v2）：对象级（行级）+ 属性级（列级）叠加=单元格级；未通过属性策略者看到 null 而非报错
- https://www.palantir.com/docs/foundry/object-permissioning/object-security-policies

**Action 权限校验**【官方】：执行动作须同时满足——能查看被编辑对象、通过 submission criteria、有 datasource 访问权。特别机制：**Actions-only object types 下"users can create objects that they cannot view"**（可创建自己看不见的对象——如"司机上报异常但看不到全网数据"）。

**AIP 的权限继承**：
- 交互场景（Logic/Agent 被用户调用）：官方"granting an LLM access only to what is necessary"；第三方更直白"agent 继承调用用户的权限"——方向一致，措辞属第三方。
- **无人值守场景（Automate）以 automation owner 身份执行**【官方】："Condition evaluation: Uses automation owner's permissions"；"Action and Logic effects: Execute as the automation owner"（submission criteria 也按 owner 校验；审计记 owner；编辑自动化必须接管所有权）；可由 service user 持有保证连续性；**唯通知效果例外：按每个收件人各自权限过滤**。https://www.palantir.com/docs/foundry/automate/permissions
- 数据面保证【官方】："No customer data contained in prompts or completions is retained by the applicable third party"、"no customer data is used to retrain such models"。https://www.palantir.com/docs/foundry/aip/aip-security

## 四、AIP 架构

**总体**【官方】：AIP 把 LLM 接到 Ontology 上；本体把"运营流程的名词与动词变成人与 agent 都可读的形式"，工具服务与本体构成"**an ever-evolving tool factory**"。

- **AIP Logic**：no-code LLM 函数环境。blocks 链式组成：Use LLM（可挂本体工具）、**Apply Action 块（不经 LLM、确定性调用动作）**、Execute Function、循环块。发布后可被 Automate/Workshop/Actions 调用。——**"LLM 负责判断，确定性代码负责执行"的分离。**
- **AIP Agent Studio（现名 Chatbot Studio）**：配置=LLM+系统提示+检索上下文+工具+状态变量。**检索上下文三类**：Ontology context（静态对象或向量语义检索 K 个）、Document context、Function-backed context。带 citations。**工具七类**：Action（自动或需确认）、Object Query（过滤/聚合/沿 link 遍历）、Function、Update variable、Command、Request clarification、语义搜索（遗留）。
- **AIP Evals**：LLM 函数的测试环境。评估套件=测试用例（**可直接用本体 object set 作批量用例**）+目标函数+评估函数。内置评估器：精确匹配、正则、Levenshtein、数值范围、**LLM-as-a-judge**；可自定义函数。按目标配 pass/fail；官方建议每用例至少跑 3 次聚合。支持 experiments（对比 prompt/模型版本）。
- **Model Catalog**：按厂商（OpenAI/Anthropic/Google/Meta/Mistral/xAI）与生命周期（Experimental→Stable→Sunset→Deprecated）管理；BYOM 支持自带微调模型。
- **Guardrails**：官方证实=权限在推理点强制执行+管理员按功能开关+模型生命周期管控+审计+Evals 作上线闸门；"每次调用内容过滤/PII"说法来自第三方，未官方证实。
- https://www.palantir.com/docs/foundry/{logic/overview,logic/blocks,agent-studio/overview,agent-studio/tools,agent-studio/retrieval-context,aip-evals/overview,model-catalog/overview}

## 五、关键问题："越用越好"的真实机制

**结论：不是模型自我进化，而是"本体沉淀的决策数据 + 人工迭代 + Evals 回归 + 渐进放权"的组织级复利。** 四个官方证实的组件：

1. **决策数据被结构化沉淀**【官方】：本体"捕获运营用户日常工作产生的决策数据"，**"end-to-end decision lineage"**（何时、基于哪个版本数据、经哪个应用做的决策）自动捕获，作为"**contextual fuel for optimizing the performance of humans and agents over time**"。
2. **人审提案产生反馈数据（用途是上下文与评估，不是自动改模型）**【官方博客】："Every piece of feedback gathered within a workflow can be securely incorporated into continuous learning loops, and used to power the journey from augmentation to automation"——反馈成为本体数据，供改 prompt/改逻辑/建评估集。
3. **AIP Evals 是迭代安全网**【官方】：改 prompt、换模型前后跑同一套件对比。配套方法论《Evaluating Generative AI: A Field Manual》（2026-02）：ground-truth 集、**二元判定的 LLM-as-a-Judge（"Define clear, binary pass/fail criteria... Avoid complex scoring"）**、扰动测试、每用例多次运行捕捉方差。https://blog.palantir.com/evaluating-generative-ai-a-field-manual-0cdaf574a9e1
4. **"越好"体现在自动化半径扩大**【官方博客】：先全量人审提案，随通过率与信任上升，把特定"trusted, well-worn AI processes"切到免审自动闭环，权限可收可放。

**关于"自动学习/微调"的如实回答**：本次调研**未发现**任何"系统根据使用自动微调模型/自动改写 prompt"的产品机制。官方叙事一致把改进归因于数据沉淀+评估+人迭代——这本身是 Palantir 的卖点（可控、可审计），而非缺陷。

## 六、FDE（Forward Deployed Engineer）模式

**分工结构**【官方博客 "Dev versus Delta"】：Devs 做"one capability, many customers"，Deltas（FDE）做"**one customer, many capabilities**"；与咨询的区别："we are actually deploying existing software products"；现场反哺产品。

**日常**【官方博客】：FDSE 与终端用户并肩迭代；"**some of our most valuable product additions originated in the field**"。

**深层机制**【第三方：前员工 Nabeel Qureshi 八年 FDE 复盘，权重高但非官方】：
- 驻场即知识获取："**Going onsite to your customers means you capture the tacit knowledge of how they work**"（每周 3-4 天在现场）
- 数据整合的阻力一半是政治，解法是把安全做进集成层（角色/行级策略/审计）——ontology 成为护城河的机制之一
- 产品化路径："**FDEs went to customer sites, had to do a bunch of cruft work manually, and PD engineers built tools that automated the cruft work**"——FDE 刻意"过拟合"单客户，PD 再泛化成平台件（最终堆成 Foundry）
- 案例：驻场 Airbus 图卢兹一年，A350 产线"4x'ing the pace of manufacturing"
- https://nabeelqu.substack.com/p/reflections-on-palantir

**AIP Bootcamp（FDE 的规模化变体）**【官方】："from zero to use case in just one to five days"，做客户真实用例不做预制 demo；"The correct technical approach... has to be empirical, not theological"；案例：某建筑工程公司两天做出投产级"disruption manager"，节省约 1000 万美元。https://www.palantir.com/platforms/aip/bootcamp/

**对"一人+AI 创业"的启示**【推断】：(1) FDE 本质是"用人把隐性流程知识翻译成 ontology/action/评估集"——翻译环节无法省略，但可由"创始人驻场访谈 + AI 代写建模/管道/评估"完成：**你本人是自己的 Delta，AI 是你的 PD**；(2) 复制 bootcamp：不卖平台卖"5 天从零到一个真实用例上线"；(3) 复制"过拟合→泛化"节奏：单客户定制先做脏做快，第二三个客户出现重复才抽象成产品件；(4) 安全/审计不是成本项而是进门钥匙——靠它化解客户的数据政治。

## 七、供应链/物流领域实际案例

- **Wendy's QSCC**（约 40 亿美元采购额）【官方 PR，正文经 PYMNTS 转述】：数字孪生覆盖 3,500 辆卡车/铁路/船、60 家核心伙伴、250+ 发货点、34 个配送中心、6,450 家门店；糖浆短缺案例：**系统 5 分钟内识别全网短缺 10,200 箱、定位可调拨 8,300 箱、算出需补订 3,500 箱——过去需 15 人干一整天**。
- **财富 100 消费品公司（匿名）**【官方文档用例】：7 套遗留 ERP 5 天内接入；一周上线 BOM 优化工作流；估算年省最高 1 亿美元。
- **Tyson Foods**【第三方，未经官方核实】：两年 20 个用例省 2 亿美元；卡车装载率 120 天从 46% 到 87%。
- **Lear**【官方 PR】：五年扩展合作，全球工厂推广 Foundry + Warp Speed + AIP。
- **Warp Speed**（制造操作系统）【官方 PR】：客户含 Anduril（供应链短缺管理效率提升 200 倍）、L3Harris、Panasonic Energy NA 等。
- **Walgreens**【第三方转述】：45 天建数字孪生，任务耗时降 30%，8 个月从 10 店扩到 4,000 店。

## 查不到 / 未证实事项（如实声明）

1. 人工提交 action 的通用"审批开关"：未发现——要么走 Automate/agent 的 staged proposals，要么 submission criteria+状态机自建（模式推断）。
2. "每次 LLM 调用有内容过滤/PII guardrail"的官方文档级证据：未找到，仅第三方。
3. 基于使用数据的自动微调/自动 prompt 优化产品机制：官方材料未检索到。
4. SKF、Cardinal Health 等传闻案例：未核实，未采用。

## 与现有原型对照（一句话版，【推断】）

已有的"对象-关系-动作-权限网络、AI proposal-only、审计"与 Palantir 同构。差距主要五处：(1) action 的完整事务语义（writeback webhook 失败全滚回、side effect 与 criteria 的先后契约）；(2) 权限双轨制（discretionary 角色 + mandatory markings，及行/列/单元格级对象策略）；(3) 无人值守自动化的身份模型（automation owner/service user，而非"以 AI 自己"或"以触发者"身份）；(4) Evals 作为改 prompt/换模型的回归闸门+真实对象集直接当测试集；(5) "越用越好"的产品化：决策血缘沉淀为数据资产 + 按工作流粒度渐进放权，而非指望模型变聪明。

## 主要信源清单

palantir.com/docs（ontology/core-concepts、ontology/why-ontology、architecture-center/{ontology-system,aip-architecture}、action-types/{overview,rules,submission-criteria,webhooks,notifications,permissions}、automate/{overview,effect-actions,effect-settings,permissions,third-party-app-ownership}、logic/{overview,blocks,aip-logic-integration-automate}、agent-studio/{overview,tools,retrieval-context}、aip-evals/{overview,create-suite}、model-catalog/overview、aip/{overview,aip-security,supported-llms,bring-your-own-model}、security/{orgs-and-spaces,projects-and-roles,markings,restricted-views}、object-permissioning/object-security-policies、approvals/overview、use-case-examples/optimizing-production-with-erp-data-across-the-supply-chain）；官方博客（connecting-agents-to-decisions、evaluating-generative-ai-a-field-manual、a-day-in-the-life-of-a-palantir-fdse、dev-versus-delta、how-aip-bootcamps-work）；官方 PR（investors.palantir.com Wendy's、businesswire Warp Speed、lear.com）；第三方（nabeelqu.substack.com、pymnts.com、proteinsignals.com——按标注使用边界）。
