# 本体运行时化与透视镜最佳实践调研（V5 决议的证据底座）

> 2026-07-13。调研方式：WebSearch + WebFetch，官方一手来源优先；同时读取本项目 ontology JSON、build_ontology.py、agent/tools.py、builder-console 现状核对落地性。与 2026-07-10 Palantir 深调研不重复——只补本次任务点名的新角度：OSDK 代码生成机制、schema 驱动开发、LLM 工具自动生成、工具爆炸治理、系统浏览器 UI 范式。
> 标注体例：【官方】一手文档直接抓取；【官方-摘要】官方页面经搜索摘要获得未逐句核验；【第三方】非原厂分析；【推断】综合推断无直接证据。
> 触发背景：Daniel 看透视镜 v2 后"还缺点什么"+"实体/关系/动作如何存在、大模型如何调用，总感觉不托底"。内部核验结论：本体 JSON 只被展示层引用，引擎/动作/AI 层零引用（两张皮）；主通道 claude_cli 单发合成（模型朗读预取简报，非真工具调用）。

---

## 方向一：本体如何成为运行时（ontology-as-runtime）

### 1. Palantir Foundry OSDK 的机制

**代码生成而非运行时解释**——OSDK 采用编译时/构建时代码生成：Developer Console 按勾选的本体实体子集生成类型化 SDK（NPM/TS、Pip/Conda、Maven；其他语言走 OpenAPI 导出）。生成的函数与类型"基于与你相关的那部分本体子集"，属性名与描述衍生自本体元数据。【官方】palantir.com/docs/foundry/ontology-sdk/overview ｜ /python-osdk

**同步机制的诚实说明**：官方卖点是"集中式维护减少维护负担"，但**未明确描述本体变更后 SDK 如何自动同步**——从文档结构看是"重新生成/重新发布"的 pull 模型，非热更新。即 OSDK 也没解决"改一行本体全链路自动生效"，它解决的是"生成的代码和本体不会手写错位"，生成动作仍需显式触发。【官方，该细节为推断】

**Action Types 是声明式配置非代码**：Parameters（输入接口）、Rules（参数→本体编辑，11 种规则类型）、Submission Criteria（提交前置校验）均经 Ontology Manager 图形化配置。关键语义：定义变更**对新提交的动作立即生效，历史记录不追溯**。【官方】palantir.com/docs/foundry/action-types/overview

### 2. 开源/轻量实现：schema 驱动开发

- **Schema-Driven Development Manifesto**：先声明数据形状再派生一切——单一定义、定义先行、semver 版本、"形状与含义分离"、**全面派生**（存储/API/校验/表单/文档同源生成）。对生成 vs 解释的立场："Agent 让代码变得廉价，稀缺注意力应放在维护共享 schema 定义上"。社区宣言，权重低于厂商官方。【第三方】schema-driven.dev
- **Pydantic**：`model_json_schema()` 一次定义随处复用；`ConfigDict(extra='forbid')` 严格模式。【官方】docs.pydantic.dev
- **SQLModel**（FastAPI 作者）：`SQLModelMetaclass` 同时继承 Pydantic ModelMetaclass 与 SQLAlchemy DeclarativeMeta，**一个类同时是校验模型和 ORM 表模型**，`table=True` 决定是否落表——Python 生态"单一 schema 驱动 ORM+API 校验"最成熟先例。【官方】sqlmodel.tiangolo.com
- **JSON Schema → SQL DDL**：jsonschema2ddl / jsonschema2sql 可从 JSON Schema 生成 CREATE TABLE（约定 id 主键，x-primaryKey 覆盖）。【第三方开源】
- **语义层产品**：dbt Semantic Layer=纯定义层（YAML 随项目版本控制，无独立服务）；Cube=定义+服务层（独立服务器、预聚合缓存、REST/GraphQL/SQL 多协议、含 MCP Server 供 AI）。"定义层 vs 定义+服务层"分野对应运行时解释的两种深浅。【官方】cube.dev ｜ getdbt.com

### 3. 提炼：本体改一行→全链路同步的最小可行模式

业界无银弹，按**变化频率分层**：

| 层 | 变化频率 | 业界倾向 | 例证 |
|---|---|---|---|
| 表结构/字段类型 | 低频结构性 | **代码生成**（落地文件入版本控制，改动重跑生成，可控可 review） | OSDK 重新生成；SQLModel/jsonschema2ddl |
| 权限/脱敏/工具暴露 | 高频按角色/请求 | **运行时解释**（启动或按请求读 JSON 现场组装） | Cube 运行时编译查询；Action Submission Criteria 提交时实时评估 |

连 OSDK 都是生成型非热更新——"生成 vs 解释"从来不是全有全无，而是**同一系统不同部分分别选型**。混合模式是业界实际答案。【推断，基于多个一手来源综合】

---

## 方向二：LLM 如何真正调用本体（function calling from ontology）

### 1. 从 schema 自动生成 LLM 工具定义

- **OpenAI**：`model_json_schema()` 生成 schema 传入 tools；SDK 提供 `pydantic_function_tool()` 一步转换；strict 模式要求 `extra='forbid'`。【官方】
- **Anthropic**：工具定义的 `input_schema` 本身就是标准 JSON Schema，同样可从 Pydantic/dataclass 派生（本项目 TOOL_DEFS 即手写的该格式）。
- **MCP**：工具=name+description+inputSchema（JSON Schema），`tools/list` 端点让服务器自描述，客户端运行时发现而非硬编码。【官方】modelcontextprotocol.io/specification
- **LangChain SQLDatabaseToolkit**：自动 inspect 数据库 schema 生成 4 个通用工具（list_tables/schema/checker/query），不为每张表手写——"自动从数据模型生成工具"的真实先例，但粒度是通用 SQL 而非业务对象/动作级。【官方】
- **Cube MCP Server**：语义层模型暴露为 MCP 工具，LLM 走 Discover（查治理过的目录，非原始 DDL）→ Select（结构化请求非手写 SQL）→ Execute（语义层编译 SQL+访问规则+缓存）三步。**本体本身就是那层语义目录**——理论上可直接从 ontology.json 生成 MCP 工具目录。【官方】cube.dev/blog

### 2. Palantir AIP 暴露 objects/actions（补 7-10 调研之外）

Object Query 工具与 Action 工具机制见 2026-07-10 调研（不重复）。新发现：第三方分析描述面向外部 agent 的 **Ontology MCP（OMCP）**桥接层，OAuth 2.0（user/service level），管理员显式配置可访问的对象类型与操作——"分层暴露"的一种实现。**该文为第三方博客（zerofuturetech.substack.com），其"五层架构"命名为作者归纳非官方用词，降权使用。**【第三方，低权重】

### 3. 关键工程问题的实践答案

**工具数量爆炸**（本次信息量最大、来源最权威）：

- **Anthropic《Introducing advanced tool use》**（2026）：① **Tool Search Tool**——工具标 `defer_loading: true`，仅约 500 token 的搜索工具常驻，按需检索展开。实测：5 个 MCP 服务器 58 工具全量加载约 55K token；引入后 **token 减 85%**，Opus 4 任务完成率 49%→74%，Opus 4.5 79.5%→88.1%。② **Programmatic Tool Calling**——模型生成代码在沙箱调工具，中间结果不进主上下文：某场景 43,588→27,297 token（约 **-37%**）。【官方】anthropic.com/engineering/advanced-tool-use
- **Anthropic《Writing effective tools for agents》**：更根本的解法是**源头只做目标明确、影响力高的少量工具**，命名空间前缀分组（asana_search/jira_search）防选错。【官方】
- **Tool RAG / RAG-MCP 学术方向**：检索增强式工具选择，摘要称可提升选择准确率、减 50%+ prompt token——**WRITER 原文 403 未核验，二手信息**。【官方-摘要】

**本项目现状对照**：TOOL_DEFS 手写 17 个工具（从 34 对象/31 动作人工精选），规模已符合"少而精"建议，**暂不需要** Tool Search 类机制（该机制在 58 工具/55K token 规模才有明显收益）。真问题不是工具多，而是**精选结果与本体定义的一致性靠手工、无机制保证同步**。

**写动作的安全模式**：

- **MCP 官方《Tool Annotations as Risk Vocabulary》**：`readOnlyHint`/`destructiveHint`/`idempotentHint`/`openWorldHint` 四字段构成风险词汇表，可信服务器的 readOnlyHint 可跳过确认、destructiveHint 触发警告。**规范明确：这些是 hint 不是保证**——不可信服务器的标注不能盲信，真正的安全保证来自网络控制/沙箱而非布尔值。【官方】blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/
- **Anthropic 研究**：粗粒度"每动作都人工确认"导致麻木——telemetry 显示约 93% 批准率，提示越多每条看得越不认真。设计导向应是"可监督+随时介入"，非"逐条点 Approve"。【官方-摘要】
- **Palantir**：Action 工具默认需确认可配自动；AIP Logic 的 Apply Action 块**不经 LLM、确定性调用**——最高风险的执行步骤特意不交给 LLM 决定，LLM 只生成参数。
- **本项目现状**：已实现同构的"AI 只提案人来批"（FORBIDDEN_TOOLS 硬编码、approve/close 永不注册），方向一致；**差距在标注化程度**——"这个动作能否给 AI 执行"散落在 tools.py 多组硬编码集合，本体 JSON 未显式声明该属性，即风险词汇表未落到唯一权威源头。

---

## 方向三：系统透视/本体浏览器展示最佳实践

### 1. Palantir Ontology Manager

三层导航：顶部栏（搜索/新建/分支导航）+侧栏（跨资源）+主内容区。**对象类型页 7 板块**：元数据→属性→操作类型→关系类型图→依赖项→数据→使用情况。关系类型页=概览+数据源两页；操作类型页=概览/逻辑/可观测性三页；Discover 首页可定制（收藏/最近浏览/分组）。【官方】palantir.com/docs/foundry/ontology-manager/overview

### 2. 数据目录与图浏览器的验证模式

- **Neo4j Bloom**：核心 **search-first**——非整图堆砌，用近自然语言短语或逐步构建图模式检索式探索，找到后展开关联、沿关系继续。GPU 渲染，codeless。【官方】
- **Atlan**：血缘导航"展开上下游+一键影响报告"——识别受影响资产、通知所有者、变更前降险。"改一个字段谁受影响"的一键化。【官方】atlan.com/demos/impact-analysis-through-the-ui/
- **DataHub**：table/column 级血缘；新方向"Context Platform"——runbook/FAQ/policy 文档作图中一等公民节点，官方称 agent 生成 SQL 准确率 90%+（**厂商自述未经第三方验证**）。【官方-部分自述】
- **Amundsen**：类 Google 搜索快且易上手，人气信号强项；血缘治理偏弱。【第三方评测】

### 3. Stripe 文档范式

三栏结构（左产品导航+Quickstart，中概念叙述，右实时可执行代码随滚动联动；多语言并列+语言选择器全站记忆；自研 Markdoc 承载）。**三篇第三方 teardown 互证方向一致，未做一手视觉核验**。【第三方】

---

## 对本项目的建设性建议清单（已被 V5 决议采纳/裁决）

### 透视镜 v3 展示层（Daniel 裁决：跟在 API 层后）

1. **全局跨类型搜索**——v2 是"先选类型再搜实例"两步式，缺随时输入 SHP-SIM-00055 即跳转的全局入口。依据：Bloom search-first、Amundsen 类 Google 搜索。
2. **影响分析专题视图**——补"这条规则/字段改了牵连哪些下游"的一键影响面展开。依据：Atlan impact analysis。
3. **动作↔LLM 工具联动**——选中动作同步展示"作为 LLM 工具的 JSON Schema 长这样"，直观看本体定义与 AI 工具是否一致。依据：Stripe 概念与代码联动。
4. **关联卡补"依赖项/使用情况"两板块**（现有 5 块：字段/状态机/关系/动作/规则）——评估改动半径。依据：Ontology Manager 7 板块。
5. 【推断/暂缓】角色视角切换全站记忆（类比 Stripe 语言选择器）——镜子暂只给创始人看，缓。

### 本体运行时化路线（Daniel 裁决：三座桥并入 API 层）

1. **【最小第一步→桥1 扩展为一致性闸门】**读 objects[].properties 生成 Pydantic 校验（string→str/number→float/enum→Literal），build_ontology 插入前 model_validate，DDL 同源生成。依据：SDD 宣言全面派生 + SQLModel/jsonschema2ddl。
2. **actions 加执行元数据**：`ai_executable: auto|confirm|never` + `exposed_as_tool: bool`，FORBIDDEN_TOOLS/allowed_tools_for_role 改读本体字段——改 JSON 一行 agent 层同步。依据：Palantir Action 提交时生效语义 + MCP 标注驱动客户端行为。
3. **links 纳入生成**：从 source/target/cardinality 生成通用 traverse(source_type, source_id, link_type)，替代手写关系查询。依据：OSDK 本体可查 link 的轻量类比。
4. **【需核实】脱敏双维护风险**：_can_see_tier/_can_see_cost 写在 tools.py，sensitiveFieldRules 声明在本体——若独立维护则建薄查询服务层统一从本体出发，AI/透视镜/工作台共用。依据：语义层"定义一次多处消费"。
5. **生成 vs 解释分层选型**：结构性低频（DDL/Pydantic）用生成型（落地文件入版本控制，改 JSON 重跑脚本）；高频组合（权限过滤/工具暴露/脱敏）用解释型（运行时读 JSON）。依据：OSDK 与 Cube/dbt 在不同层的不同选择。

### LLM 工具化路线（Daniel 裁决：MCP PoC 立即做）

1. **从 actions[] 自动生成 TOOL_DEFS 雏形**：为标记开放的 action 写转换函数（signature→input_schema），人工只维护 description 文案。依据：pydantic_function_tool 模式 + SQLDatabaseToolkit 先例。
2. **工具加机器可读行为标注**（readOnly/destructive/idempotent/openWorld 落为本体 action 字段）——dispatch 层/透视镜/审计同源读取。**原样转述 MCP 警告：annotation 是 hint 不是保证**，内部工具自声明可信，对接外部第三方工具不能仅凭标注做安全决策。
3. **工具爆炸是预防性问题非当前问题**：17 工具远低于 58 工具/55K token 的收益规模，现在不引入分层检索加载；保留 allowed_tools_for_role 雏形，工具数显著增长后再议。依据：Anthropic 实测数据 + "少而精"原则。

---

## 查不到/未一手核验（如实声明）

1. Stripe 三栏视觉布局——WebFetch 只抓文本无法核验排版，仅三篇第三方互证。
2. Tool RAG/RAG-MCP 准确率数字——WRITER 原文 403，仅搜索摘要。
3. Palantir 官方是否有"工具数量上限/分组建议"直接表述——未发现，可能未公开。
4. OMCP"五层架构"为第三方作者归纳，palantir.com/docs 无对应原文。
