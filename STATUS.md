# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-14（V5 API 层收官：桥2+桥3+MCP 正式化五单全交付，两张皮 15→0）

## 当前位置

> **V5 本体运行时化·第一批交付（2026-07-13，Daniel 三项全批后执行）**：缘起 Daniel 照透视镜 v2 后
> "感觉不托底"——诊断证实本体 JSON 只被展示层引用（引擎/动作/AI 层零引用，两张皮），主通道 claude_cli
> 为单发合成（模型朗读预取简报非真工具调用）。决议见决策日志 V5；调研存档 docs/research/
> 2026-07-13-ontology-runtime-best-practices.md。**桥 1 一致性闸门已交付**：pipeline/ontology_lint.py
> （四类断言 A 对象↔表/B 动作↔权限/C 动作↔AI 工具/D 关系↔外键，报告/--strict 双模式），首跑基线
> **15 处差异**（A10/B2/C0/D3，docs/research/2026-07-13-ontology-runtime-gap-baseline.md）——
> 其中 **C 类冻结区零差异**（审批/关闭类动作既不在 TOOL_DEFS 又被 FORBIDDEN 显式拉黑）；
> 疑似真问题 #1：ROLE_PERMS 含 procurement→ProposeMitigation 而本体未声明——核对为 P4 裁决（人批）
> 后本体滞后，**桥 2 补本体，勿删代码授权**。B 类另有 3 动作无权限登记（CreateRiskEvent/A9/A10），桥 2 厘清。
> **MCP PoC 部分通**（poc/mcp-ontology/，新顶层目录=V5-② 工作区，此处登记）：零依赖 stdio MCP server
> 3 只读工具，独立客户端 8/8 断言过、答案与库真值逐字一致、call_log.jsonl 留证、mode=ro 物理只读；
> 工具 schema 的对象/关系枚举已从本体 JSON 生成（桥 2 预演）。独立 Sonnet 验收 7 项 6 过；唯一不符项
> （engine.evaluate 50/51）经裁决为**评估基线状态前提**：seed_demo_ops 会改运营字段（设计内），跑过后
> 个别匹配项偏移，从 build_ontology 重建起步则全绿（已实测两轮，test_closed_loop/agent.evaluate 用临时副本
> 不污染工作库）。
> **MCP PoC 终验 3/3 全过，PoC 收口（2026-07-13）**：Daniel /login 后第 1 次亲跑 + 接管会话补跑 2 次，
> 三轮全部满足三条判定（call_log 17→32 行真调用留痕 / 答案与真值逐字一致 in_transit+RSK-0038/0039/0040 /
> stream-json 可见 mcp__ontology__* tool_use 事件），每轮 num_turns=7、44-48s、订阅通道零 API 费——
> 模型自主规划调用序列（查货件→沿关系走风险→逐个深查详情），"朗读简报"时代结束。
> 回填见 PoC 报告 §4（docs/research/2026-07-13-mcp-poc-report.md）。
> **API 层五单全交付（2026-07-14，worktree 分支 claude/project-handoff-api-layer-cd4f51，
> Fable 编排评审 + Opus×4/Sonnet×1 执行，每单主会话重跑复核后提交）**——plan：
> docs/superpowers/plans/2026-07-14-api-layer-bridges-mcp.md（执行中勘误 3 处，全部由执行层论证抓出）：
> **M1 桥2-本体侧**（31f7ba2）：本体 0.9.0→0.10.0——roles 补 procurement（P4 追平）、31 动作加
> exposed_as_tool/ai_executable(auto6/frozen4/never21)/enforcement/permission_key、A 类 4 列补登记、
> payload 删除、links 加 storage 承载声明；lint 改读声明字段，差异 15→5+1 豁免。
> **M2 桥2-运行时侧**（ea01134）：pipeline/ontology_runtime.py——5 权限字典+FORBIDDEN+TOOL_DEFS
> 从本体解释生成，硬编码退役；迁移零行为漂移（落死基线+EXPECTED_* 人批口径双守护）；评审变异
> 测试暴露"KeyError 运气防线"后加 _validate_ai_invariants 显式断言（frozen∩exposed=∅ 等）+
> 三变异用例固化常驻。**M3 桥3 结构生成**（aeba1ac）：34 Pydantic 模型+DDL 从本体生成（GENERATED
> 入版本库），影子模式确认恰 5 处（skus 五字段 TEXT→REAL）才切换 build_ontology；插入前
> model_validate（enforce 0 违例）；本体 30 处 number→integer 精化（评审审计金额/费率/尺寸零降级）；
> traverse 通用遍历下沉 ontology_runtime；**两张皮差异清零，--strict 退出码 0 入发版门**
> （release-checklist §A 新增）。**M4 MCP 正式化**（ca9360b）：agent/mcp_server.py 四门槛——
> 角色过滤（aiQueryTools.domain×角色矩阵）+本体 sensitiveFieldRules 脱敏、冻结区机制化+零写工具
> （裁2 保守）、审计入库（llm_calls 加 call_type='mcp_tool'，查询连接 mode=ro 物理只读+审计连接
> 仅 INSERT）、schema 本体驱动；llm_agent 新 provider claude_cli_mcp+回退链；**UI 询问按钮
> provider 感知切换**（评审判执行层"UI 暂缓"为实质缺口发回补做——config/llm.yaml 一处开关控制
> 全部入口，新通道模型真查库带审计、简报明示"本次回答未以其为依据"，旧单发档保留一行可回退）。
> **M5 FastAPI 骨架**（7360f3c）：apps/api/ 五路由全本体驱动（/ontology 自描述、objects 读+过滤、
> traverse、actions 仅 exposed 6 动作走 app.actions 原函数留审计），16 用例+真 uvicorn 冒烟绿——
> React 驾驶舱与透视镜 v3 的底座。
> **全程红线**：每单全量回归链（含 seed_demo_ops 步，勘误#2）+288 注入对抗+真值 md5 全绿；
> EXPECTED_* 基线零改动。
> **V6 三裁决已全部落地（2026-07-14，Daniel 亲批"1.补；2.可以；3.了解"）**：
> **裁1=补（328129f）**：本体 0.10.1 增 RiskEvent.affected_sku_ids 正式承载列，declared_only
> 豁免退场——**闸门首次 0 差异 + 0 豁免完全干净**；R14 历史 hack（affected_po_line_ids 装 sku_id）
> 经证据链核查为评估器匹配键所消费，按 V6 约束保留为兼容载体、新列并行写入（双列逐字节相等）。
> **裁2=可以（ecefb94）**：MCP server 开放 6 写提案工具，调用走 agent/tools.py 既有 dispatch
> （ROLE_PERMS+FORBIDDEN+action_log 同一套，无第二写路径），冻结区协议层拦截（永不到达 dispatch）；
> 端到端实弹：模型查 RSK-0001→真实 assign_task→TSK 落库+审计尾行 ai-agent/ok+llm_calls +2，
> 模型自述 maker-checker 边界。执行层于验收阶段 watchdog 停滞，主会话接手验收收口（改动无缺陷）。
> **裁3=了解**：新通道维持默认，结案。
> **驾驶舱已启动**：画面复述候 Daniel 对齐（docs/superpowers/specs/2026-07-14-cockpit-screen-
> narrative.md，六段画面+有意不做清单——回"对"即开工画面级实现）。**地基已交付（51fd4b8）**：
> apps/cockpit React 骨架（Vite+React18+TS 照透视镜栈零新依赖，三占位视图，连接状态条全字段来自
> /ontology）+ apps/api 双世界数据源（ONTOLOGY_DB 环境变量切 verification/simulation，18 用例）；
> CORS 经 vite 同源反代解决（独立静态部署时需补 CORS 中间件，README 挂账）。
> **启动**：`uvicorn apps.api.main:app --port 8100` → `cd apps/cockpit && npm install && npm run dev`
> （或 ./start.sh）；模拟世界：`ONTOLOGY_DB=data/simworld.sqlite uvicorn ...`（simworld 由
> `python3 -m sim.backfill` 生成）。**下一步：Daniel 回画面复述 → 驾驶舱画面级实现 → 透视镜 v3**。**遗留挂账（后续项）**：credit_terms/risk_tier 本体声明与 tools.py gate
> 分叉、COST_FIELDS 等硬编码脱敏集全量声明化。
> **Daniel 验收走查（5 分钟）**：完整重启 streamlit → 任意风险工作台点「询问」问一个问题
> （预期 40-105 秒，回答头标"真实查库作答"，简报折叠区明示未被用作依据）→ 控制室审计视图查
> llm_calls 出现 call_type='mcp_tool' 行。**合流**：worktree 分支领先 codex 主干 8 commit
> （d1ad559..7360f3c+收尾），在主仓库跑 `git merge --ff-only claude/project-handoff-api-layer-cd4f51`
> 即快进（纯 ff 无冲突；先关闭运行中的 streamlit）。**下一步：前台驾驶舱 → 透视镜 v3（V4/V5 序）**。

> **FDE × Ontology 系列研究手册（2026-07-12）**：已用 Record & Replay 确认“纳米巨人”抖音主页第 1—11 集系列边界，逐集取得真实媒体（约 75 分钟）并用本地 Whisper small 离线转写；结合 Palantir/OpenAI/Blackstone 官方资料完成事实核验与方法内化。新增 `docs/fde-ontology-delivery-playbook.md`：把系列观点转成 1—5 天 Bootcamp、最小本体、动作/权限、自主权、评估与 FDE 产品化的可执行手册，并明确未证实实现细节与本仓库真实缺口。未修改 ontology、规则、代码、KPI 或决策日志。

> **企业 AI 实施手册（2026-07-12）**：基于 Stanford Digital Economy Lab 2026-04 官方报告
> *The Enterprise AI Playbook: Lessons from 51 Successful Deployments* 的 51 个成功部署研究，新增
> `docs/enterprise-ai-implementation-playbook.md`。手册没有改变既有范围或路线，维持 B（已完成）→C1→A→C2/C3→D；
> 把外部证据落成六阶段门（基线与责任→C1 决策血缘→A 影子接入→受控试点→分域放权→复制/停止）、
> 四类指标、Sponsor 周节奏、变更闸门与现场清单。特别记录证据边界：报告是成功案例导向、自报为主，
> “异常升级制 71% 中位生产率提升”存在任务选择混杂，不能据此取消 proposal-only / maker-checker。
> **下一验收点仍为 C1 处置记忆→更好提案；本次只交付研究与实施手册，未批准或修改对象/动作/KPI/代码。**

> **产品化轨已并入主干（2026-07-12 深夜合流，Daniel 裁决方案甲：Codex 线为主干）。**
> 07-10~12 的 Claude 产品化会话成果全部并入本仓库：
> ① 《底层逻辑教材》`docs/product-foundations-primer.md`（十章+术语白话表+裁决题，Daniel 已全部答题过关）
> ② 三份深调研 + **六路对抗评审**（完备性/挑剔客户/商业验尸/坟场/工程/合规）`docs/research/`——
> 交叉综合与 J1-J6 裁决见 `docs/research/2026-07-12-adversarial-review-synthesis.md`
> ③ 设计 v2.1（已批准）与 **v2.2 合流决议** `docs/superpowers/specs/`——v2.2 是两线统一图景：
> 能力主线=本仓库路线图（B 已完成→**下一步 C1 处置记忆**→A 信息流入→C2/C3→D，Daniel 裁决维持此排序），
> 产品化层按时点归类（立即适用：运维骨架/评估分级闸门/冷启动静音/宪法九条/对抗评审常态化；
> A 能力时：接入防毒化/断货模型/出境白名单闸门/PI 摘除；商业化时：合规后置包）。
> ④ 关键裁决：LLM 主力=claude_cli 订阅通道（出境闸门对它同样适用，A 接真实数据前必须就位）；
> 新产品 repo 推迟至第二家非亲友付费客户；双轨验证——**访谈轨（Mom Test 三问，8-10 家陌生货主+3 家货代）
> 由 Daniel 亲自启动，不受能力排序影响**；反目标"不接真实数据"的推翻生效于 A 能力首次摄入真实数据之时。
> ⑤ 教训与流程修正：本次两线并行三天才互相发现——**任何模型接管必须先 `git branch -a` 并对照各活跃分支
> 的 STATUS**（本条已成为接管自检第 0 步）。
> ⑥ **后续增补（07-12 深夜）**：agency-agents 内化（打法卡格式规范 v1 + 三同事 voice 规范 v1 +
> 第七路检查表评审 5 条入 v2.2 §6）；**实施手册 v1.1 采纳**（`docs/enterprise-ai-implementation-playbook.md`，
> 斯坦福 51 部署报告的项目翻译版，关键数字已核验；§3 已按 Daniel"不等访谈"裁决改并行）；
> **L 编号统一**：L0-L4=自主等级（手册 §4 为基准），动作风险用文字名，宪法⑤改"冻结区人审不可放弃"。
> **C1 处置记忆已完成并通过主会话独立复核（2026-07-13 凌晨）**：resolution_memory 表（审批时归档
> 决定+关闭时回填结果与质量标签）、同 rule_id+同航线（LOCODE 对）只读检索、提案先例区块（1 条讲透+
> 汇总行，数字现查现算可回查）、DecisionRecord 血缘雏形（提案版本戳+场外依据框）、AI 仅获只读工具
> get_similar_resolutions。**复核以重跑为准**：真值 md5 逐字节一致、engine R/P=1.000、新测试 28 断言+
> agent_security+closed_loop+outbox+agent.evaluate 全绿；FORBIDDEN/ROLE_PERMS 零改动（diff 为证）。
> **待 Daniel：五分钟 UI 走查**（任务台看先例区块→审批填场外依据→风险塔关闭打质量标签）。
> 子代理列明 5 条业务语义歧义（modified 决定不可达/非航线域第二维度/rejected 计入统计口径/
> 一案多决定回填/无记忆行打标）——均按最保守读法实现，Daniel 有空时裁决，不阻塞。
> 下一步：A 信息流入（前置：介质普查半天+接真实数据前重走 §3）+ Daniel 访谈轨并行。
> **知识图谱板块已完成并通过独立复核（2026-07-13，Daniel 直接提出，纯呈现层切片）**：新 tab 挂 ops/manager
> ——本体地图（34 类型 7 业务域着色，实线=登记表真实关系 15/虚线=声明关系 43）+对象邻域 trace 视图
> （中心高亮、≤2 跳、节点仅类型/ID/状态，敏感字段零出现，ops 过 data_scope）。零新依赖
> （st.graphviz_chart）；纯只读（"本图只读，操作请回各工作台"）；52 项断言+全角色 AppTest 0 异常+
> 四红线 diff 零触碰。7 条实现歧义按最保守读法记录在案（registry 仅覆盖 13 类故补充 FK 口径边等）。
> **验证轨线上证据 4/5 已落盘**（中文社区 22 案例/行业硬数据/司法判例 13 件/付费代理证据），
> **验证轨已收口（2026-07-13）**：英文社区路经 Daniel 停止取消；线上证据综合报告+**判据变更提案 v2 候批**
> （`docs/research/2026-07-13-online-evidence-synthesis.md`——付费验证改挂产品可演示后，demo 即访谈工具）。
> **A 前置基础设施已完成（commit 1369948，子代理经 Daniel 停止后由主会话接手验收）**：出境白名单摘除闸门
> （四类 PI 摘除+ISO6346 柜号豁免）、llm_calls 全路径日志、上下文预算（绝不静默截断）、cli 连续失败降级；
> 主会话完整链复跑全绿、真值 md5 一致、C1 与安全对抗零回归。
> **前后台界面分离已完成并通过主会话独立复核+浏览器亲验（2026-07-13，Daniel 裁决"前端使用界面与
> 后端信息界面特别分开"）**：sidebar 导航二分——工作台（默认，首屏「我的今天」四计数卡按角色+data_scope
> 现算且与各 tab 口径逐字一致，下挂 6 个操作型 tab）/控制室（琥珀标识条"理解与监督视图"，挂 KPI/对象详情/
> 知识图谱/DQ/审计）。纯呈现层重排：业务函数零改动、ROLE_PERMS/FORBIDDEN 零 diff、161 断言+全套回归绿。
> 7 条歧义按保守读法记录（子代理报告）。**注意：Daniel 需完整重启 streamlit 才能看到新布局与知识图谱。**
> **判据变更 v2 已入账**（J4-v2，Daniel 授权代行）。
> **控制室三问句重构+图谱三刀已完成并通过主会话独立复核+浏览器亲验（2026-07-13，Daniel 批准方案 A）**：
> 控制室 5 黑话标签→3 问句标签（全局概览/追查一件事[对象详情+邻域图合并,本体地图降折叠]/操作与异常
> 记录[审计+「待核对的数据」，用户侧 DQ 字样清零]）；图谱三刀：同类配角>4 收「类型×N」聚合框（主干
> 7 类永不收）、rankdir=LR 流向阅读、大白话旁白现查现算按角色脱敏（Invoice 邻域 52→21 节点）。
> 权限语义零变化（组可见=成员并集，测试双算验证）；全套回归绿。8 条歧义保守读法记录。
> **宪法 §4 已升级三层模型分工**（Daniel 指定：Fable 编排/Opus4.8 复杂执行/Sonnet5 常规执行，
> 派发必须显式指定 model）。
> **三角色陌生人 UX 测试与 P0/P1 修复已完成（2026-07-13/14，Daniel 批"修"）**：三个 Sonnet agent
> 扮演新专员/老板/财务禁读文档实测，综合报告 `docs/research/2026-07-13-ux-stranger-test.md`
> （8 项高置信缺陷+愿付数据：现状 800-1500/月→修好翻译层 5000+/月）。P0 四件（Opus）：财务断头路
> （UI 对齐 ROLE_PERMS）/AI 故障人话降级+可用性徽标/表单事前检查/默认选中首行。P1 六件（Sonnet）：
> ux_copy.py 清洗层（回执与报错零内部码）/确定性简报置顶默认展开/我的今天卡片真按钮跳转/费种与规则
> 图例+根因人话化（R1-R3 模板翻译）/首屏健康度一句话/风险与发票列表搜索排序+只看异常。
> 主会话复核：全量测试绿+浏览器走查（健康度行/默认RSK-0031/根因人话/内部码清零/finance有任务台）。
> 注：AI 助手可用需运行环境 claude CLI 已登录（Not logged in 时优雅降级为简报）。
> **V4 产品形态重构决议已确认（2026-07-14，费曼对齐三轮收口，决策日志 V4）**：前台分级驾驶舱+
> AI 透明可视化+后台建造者透视镜+**模拟世界引擎（重投入）**；React+FastAPI 双应用，Python 核心不丢；
> 顺序：模拟引擎设计提案候批 ∥ 透视镜 v1 先行 → 引擎实现 → API → 驾驶舱。Streamlit 降为过渡操作台。
> **V4 执行进度**：模拟引擎提案已获批（三裁决点定案）→ **S1 世界骨架+时间引擎+14 个月历史已交付**
> （sim/ 目录，30/30 验证绿，季节故事因果涌现，同种子逐字节复现，与验证世界物理隔离）；
> **建造者透视镜 v1 已交付**（apps/builder-console/，六视图各答一问，等距三层图+决策血缘回放，
> 空表诚实标注；启动：cd apps/builder-console && python3 export_data.py && npm install && npm run dev）。
> **S2 已交付**（异常276起五族/连锁101环caused_by可溯/AI闭环236检测→186提案→处置真改世界→先例186条,
> 全部sim标记不冒充）；**透视镜 v2 已交付**（12视图:关联织网可穿梭+实体全景带连锁时间线+规则档案+
> 权限热力+演进史+世界设定集,活世界数据+sim徽标）。**下一步：Daniel 二次照镜验收 → API 层 → 前台驾驶舱**。
> 介质普查四个数字仍是 A 的前提。S3 备忘：为采购/准入/协调域补灌活世界实例（当前16/34类型有实例）。
> ⑦ **完整版规格 v3.0 已转正（Daniel 裁决，唯一执行规格）**：`docs/superpowers/specs/2026-07-12-product-spec-v3-consolidated.md`
> ——v2.1+v2.2+手册纪律层+全部裁决的无新增合并版（单一文档看全貌，附文档地图）；
> v2.1/v2.2 降为裁决记录存档。**当前动作：C1 设计提案已备好等 Daniel 亲批（§3 流程），批后写代码。**

> **顶部摘要（2026-07-08 刷新，任何接手模型先读这段）。** 下方历史 bullet(M1-M7/P1-P3/W1/口径收敛)保留为详细过程；决策日志见 `docs/control-tower-plan-v0.2.md §4`。

> **v0.8 收官（2026-07-09，loop 15 迭代完成）**：自定步调 loop（§4 加固，不擅开新域）交付完成——见 `docs/release-notes-v0.8.md`（发布说明）+ `docs/release-checklist.md`（23 模块 + 2 可复现性门全绿证据集）+ `docs/loop-state.md`（15 项逐条追踪）。新接手先读 `ONBOARDING.md`（5 分钟）。本轮修 2 个真崩溃点、补对抗安全/多区域/健壮性测试、面客一页纸 `docs/control-tower-overview.html`。真值 md5 全程 byte-identical。

> **LLM 基座接通（2026-07-09，Daniel 亲批：用本账号 Claude 订阅 + Opus 4.8）**：`agent/llm_agent.py`
> 新增默认 provider `claude_cli`——经登录态 `claude` CLI 无头子进程调 **claude-opus-4-8**，**用订阅不烧 API key**
> （订阅≠API：openai/anthropic provider 仍需各自 key）。因 CLI 是 agentic 工具（喂工具清单会诱发 native
> tool_use 撞 --max-turns 报错），采用**单发合成**：UI 各对象工作台用权限脱敏简报作 grounding，Opus 仅据此
> 合成中文回答（`answer_over_context`）——模型无行动能力（更安全）、继承 UI 脱敏、不编造、拒注入越权（均已实测）。
> 6 个对象工作台「询问」按钮已从占位改为真 Opus 4.8 作答（LLM 不可用时优雅降级为简报）。写动作仍只走 UI 表单 + maker-checker。
> **下一步（需 Daniel §3 亲批才动）**：新增业务域（关务/资金风控等）、或接真实企业系统数据源。

> **P4 专职采购角色（2026-07-09，Daniel 亲批，§3 决策日志 P4）**：6→7 角色，新增 `procurement`（采购处置台，
> 落地页=采购工作台）；ops 去掉 po 回归纯物流；权限仅 +ProposeMitigation（采购处置提案），
> **ApproveMitigation 仍仅 manager、CloseRiskEvent 仍仅 ops**（maker-checker 铁律不动，形成采购提案→经理审批→运营关闭三方分离）。
> 全套回归绿（17 app 测试 + agent.evaluate + 4 评估器）、procurement agent 无审批工具、R1-R18 与 truth md5 未动。

> **CL1 协调回路第一切片（2026-07-09，Daniel §3 亲批，决策日志 CL1）**：用真实数据做缺口分析发现系统核心闭环漏了
> **跨外部主体的协调/催办/谈判**（真实世界最耗时的活）。新增对象 `CoordinationThread`（锚 Task，对手方 supplier/
> forwarder/customs_broker/bank/customer，状态机 open→awaiting→responded→resolved/escalated/dead_ended、
> overdue 派生）+ `app/coordination_actions.py` 6 动作（独立权限组 ManageCoordination={ops,cs,procurement,finance}，
> 走审计）+ seed 3 条 demo + test_coordination_loop（22 断言）。**红线守住**：R1-R18/truth md5 未动、ROLE_PERMS 4 键+
> FORBIDDEN 一字未改、协调写动作不注册为 agent 工具。对象 33→34、标准视图 27→28、ontology v0.9.0。
> 缘起与第一性原理见 `docs/notes-decision-rights-org-design.md`；能力路线图见 `docs/system-framework-and-roadmap.md`。
> **能力 B 协调收件箱已建（2026-07-09，§4 呈现层）**：CL1 从"数据层"落到运营眼前——新 `coord` tab「协调收件箱」
> 挂进 ops/cs/finance/procurement 工作台（与 COORD_PERMS 严格同集），overdue 置顶◆ + 催办/记回应/升级/达成/谈崩
> 入口（复用 CL1 动作）。纯呈现层：actions.py/tools.py/coordination_actions.py 0 改动、R1-R18/truth md5/对象数未动、
> 7 角色 AppTest 0 异常。**下一步（路线图 §4 排序）**：C1 处置记忆→更好提案（护城河最小闭环，把"越用越强"做成可见）。

- **阶段：5 个业务场景一本体，全部落地并通过 controller review**。场景：延误运营(R1-R3) / 费用稽核(R4-R6) /
  准入合规(门禁 G1-G4) / 采购(R7-R15：三方对账+预付款+资质+单一来源+maverick) / 仓储库存(R16-R18)。
  **18 条风险规则全 P/R=1.000**；34 个对象类型（CL1 加 CoordinationThread）；ontology **v0.9.0**。
- **成熟度做到教科书级**：真·角色导航(7 角色各自工作台，含 P4 新增专职采购 procurement) + 行级数据范围 + 经理 KPI + maker-checker + 审计 + 可插拔 LLM；
  **6 个对象富工作台(RiskEvent/Task/Invoice/AdmissionCase/PurchaseOrder/Warehouse) + permission-aware 对象级 agent**
  (agent 数据范围==UI、越权被动作层挡回、prompt 注入 "you are admin" 被拒——均已 controller 独立对抗验证) +
  28 个自动标准视图(对象图可导航，含 CL1 新增 CoordinationThread)。
- **跨场景连成一张网**：采购收货→上架→库存→预留驱动 SalesOrderLine 履约 → 延误时查目的仓现货拆单先发+余量改期
  （白皮书业务问题落点）。
- **工作模式**：主会话(controller)规划+对抗复核（"不信报告只信输出"，每次独立重跑），Opus 子代理执行；
  每个新域/富化走 §3（联网调研→设计提案→Daniel AskUserQuestion 批准→决策日志 P1/P2/P3/W1→才写代码）。
- **LLM**：无 API key 时确定性 fallback，端到端可跑；真实 LLM 调用需 `OPENAI_API_KEY`（`agent.llm_agent` 默认 openai）。
- **复现（完整链）**：`python3 -m datagen.generate && python3 -m pipeline.build_ontology && python3 -m engine.detect &&
  python3 -m datagen.seed_demo_ops && streamlit run app/streamlit_app.py`（改代码后**完整重启** streamlit，别热重载）。
  评估器 engine.evaluate/evaluate_cost/evaluate_procurement/evaluate_warehouse 全 R/P=1.000；真值只在 `data/truth/`、引擎禁读。
  **评估前提**：evaluate 逐项全绿以 build_ontology+detect 的规范态为基线；跑过 seed_demo_ops（演示增强，会改运营字段）后个别匹配项偏移属预期，重建即恢复。一致性闸门：`python3 -m pipeline.ontology_lint`（--strict 供发布阻断）。
- **面客走查台本**：`docs/demo-walkthrough.md`（5 分钟故事：切角色→点对象→对象级 agent→现货救延误→越权被挡）。
- **M1 Task 1 已完成并通过 controller review**：Daniel 已批准 demo named actor + maker-checker 的动作边界升级；
  已按批准范围实现 `app/action_context.py`、动作层 proposer/approver 校验、稳定任务 ID、事务 helper 与 schema 文档；四项动作回归测试全绿。
- **M2 Task 2 已完成并通过 controller review**：Daniel 已批准 demo named owner + SLA state +
  escalation_level；实现范围限定为 `Task` 字段、demo roster 分配、显式 `as_of_date` SLA 计算和
  UI 呈现，不接真实用户目录/通知/排班系统，不推进 M3+。子代理规格/质量复审均已通过；验证：
  work_queue、action_governance、全链路 datagen/pipeline/engine/app/agent 回归全绿。
- **M3 Task 3 已完成并通过 controller review**：Daniel 已批准 canonical event envelope + raw lineage
  的模拟 source-truth 基础设施；实现范围限定为 raw `tms_milestones` source identity、
  `source_events` 表、idempotency key 与 pipeline lineage 评估。不接真实 carrier/API、不做 DQ issue
  workflow、outbox、MDM crosswalk 或业务规则。子代理规格/质量复审均已通过；验证：event_envelope、
  datagen、pipeline、engine、app、agent 回归全绿，engine 仍为 Recall=1.000 / Precision=1.000。
- **M4 Task 4 已完成并通过 controller review**：Daniel 已批准 simulated MDM crosswalk resolver；
  本次仅新增 external ID → internal ID crosswalk resolver、`mdm_crosswalk` 表与 key-level DQ 可观测计数
  （resolved=108 / ambiguous=1 / unresolved=1，candidate_rows=111）。
  不接企业 MDM 平台，不做自动主数据合并、人工治理队列、真实主数据同步、M5 graph traversal、
  relationship registry、DQ issue workflow 或 outbox。两轮子代理规格/质量复审均已通过；验证：
  MDM、datagen、pipeline、engine、app、agent 回归全绿，engine 仍为 Recall=1.000 / Precision=1.000。
- **M5 Task 5 已完成并通过 controller review**：Daniel 已批准 SQLite `object_relationships` registry +
  可解释 graph traversal；本次仅新增通用关系表、`engine.graph.explain_path`、既有对象关系投影、
  RiskEvent 关系边与只读 AI 查询工具。不推进 M6+，不做 DQ issue、outbox、AI redaction、
  新业务对象或新风险规则。子代理规格/质量复审均已通过；验证：graph、datagen、pipeline、engine、
  app、agent 回归全绿，engine 仍为 Recall=1.000 / Precision=1.000。
- **M6 Task 6 已完成并通过 controller review**：Daniel 已批准把模拟 pipeline 的 unresolved / parking
  记录升级为可运营 `dq_issues` 队列；本次仅新增 deterministic DQ issue 创建、assign / close
  两个处置动作、审计留痕、pipeline/evaluate 门禁、Streamlit “DQ 处置”入口和数据导读/断言同步。
  不推进 outbox、AI redaction、M7+，不做真实源系统修复、外部回写、新风险规则或新噪声类型。
  子代理规格/质量复审均已通过，质量复审指出的 DQ 幂等冲突、事务回滚、默认队列筛选和 detail
  JSON 容错均已修复；验证：DQ loop、datagen、pipeline、engine、app、agent 回归全绿，engine 仍为
  Recall=1.000 / Precision=1.000。
- **M7 Task 7 已通过 controller review 并提交（73ad293）**：主会话对抗复核（3 agent）修复 blocker
  （streamlit run 因 app/actions.py 顶层 from pipeline.outbox 崩溃 → sys.path bootstrap）+
  approve_mitigation 事务契约加固（outbox 冲突不穿透异常）+ enqueue as_of 显式化；MA6 断言还原为
  待 Daniel 裁决（撤销子代理擅自改窄）。验证：test_outbox 全绿、engine R/P=1.000。MA6 措辞仍待裁决。
- **目标重定（2026-07-08，Daniel 重申）**：本项目定位为"一步步搭建完整跨境供应链控制塔，加深 ontology
  落地理解 + 作为面客 demo 证明落地能力"。策略：以本仓库为主干，**成熟度深度优先于范围广度**；先把现有
  场景做深，再按白皮书域（采购/仓储/物流/关务）逐个 ontology 扩展。commercial FDE 楔子（对账稽核）落在
  `apps/freight-audit-agent/` 作深垂直样板（独立宪法，P0.938/R0.918）。
- **RBAC 深化已完成并通过 controller review**：把"7 tab 全显示 + 字段脱敏"（假 RBAC）改为真·角色导航
  （`app/rbac_nav.py` ROLE_WORKSPACE：ops/cs/finance/sales/compliance/manager 各自工作台）+ 经理 KPI 总览。
  动作层 ROLE_PERMS/maker-checker 未改动（git diff 为空）。验证：test_rbac_nav 全绿、三 loop 无回归、
  六角色 UI 各 0 异常。**待 Daniel 裁决**：审计日志现仅 manager 可见（原全角色），如需 ops/compliance
  保留审计可见性改 ROLE_WORKSPACE 一行即可。
- **行级数据范围（data scoping）机制已完成并通过 controller review**：`app/data_scope.py`（resolve_actor +
  scope_predicate：mine/team/all 三 mode，manager=全局不受限）+ 任务台「我的任务/本组/全部」+ 风险台
  「本区域/全部」视图级过滤（只过滤不删数据；命令栏数据域显示真实 scope 如 "US · 我的任务"）。动作层未改
  （git diff 为空）。验证：test_data_scope 31 断言全绿、rbac_nav+三 loop 无回归、六角色 UI 各 0 异常。
  **诚实标注（controller 独立复核发现）**：现有工作库全 US 单区域 + 运行时 0 任务，故 scoping 机制正确但
  "看得见的过滤效果"为空；子代理的"mine⊆all 严格子集"断言实为空集⊆空集、通过得 vacuous。要让数据范围在
  demo 里真正可见地过滤，下一步需 **enrich demo 数据**（多区域 + 多实名 owner + 预置分派任务）——这也更贴近
  真实控制塔（多区域多 owner）。
- **enrich demo 运营快照已完成并通过 controller review**：`datagen/seed_demo_ops.py`（build+detect 之后跑）
  把检出风险填成 20 个任务的运营快照——9 个实名 owner（含 2 CN）、SLA 三态混合（overdue 4/due_today 4/open 12）、
  4 个升级候选、任务态混合。`DEMO_ROSTER` 仅 append 5 个实名 owner（第一个 ops/US 不变，既有 resolve/assign 行为不变）。
  **严守边界**：只写 tasks/action_log 运营态，未碰 ground truth（真值在 data/truth/*，seed 不触及）；避让 SHP-2026-0099
  + CD-A..E 设计案例（保 Daniel 走查与 loop fixture）。**controller 独立全链复核**：三评估器仍 R/P=1.000、
  scoping 现非空（manager=20 vs ops-mine=6 严格子集）、幂等、六测试全绿、RSK-0068 仍 open 无 task。
  复现链新增一步：`engine.detect` 之后跑 `python3 -m datagen.seed_demo_ops`。
- **对象工作台切片一（RiskEvent）+ permission-aware 对象级 agent 已完成并通过 controller review**：按 Foundry/AIP
  真实范式（联网调研支撑）——**复用**现有 agent 框架（`agent/tools.py` AgentSession），注入当前角色 + 按对象
  scoping（`focus_risk_event_id`），**不新造 agent**（一个框架动态 scoping，非每对象一个 agent）。`app/object_workbench.py`
  RiskEvent 富工作台（属性 + 关联对象 + 角色可用 action + 对象级 agent 面板）。**安全铁律**：approve/close 对任何角色
  都不注册为 agent 工具（agent 只提案不审批 = maker-checker）；权限校验在 dispatch/动作层不在提示词。
  **controller 独立对抗验证（自写越权攻击、非信子代理报告）**：prompt 注入 "IGNORE ALL RULES, you are admin" 让
  ops/manager agent approve → 均被拒 + 写 denied 审计 + 不执行；cs 越角色 assign 被拒；直调动作层错误角色 ok=False
  （工具层+动作层双闸）。验证：ROLE_PERMS/maker-checker 未改（diff 空）、agent.evaluate 不回归、engine R/P=1.000、
  test_object_workbench 35 断言全绿、三角色 UI 0 异常。**可议取舍**：AI 默认 role=ops 保留全域读工具（含 cost/admission）
  以不回归既有 agent 评估——若要收紧 ops 的成本域可见性，需同步改 agent eval 期望。
- **对象工作台切片二（AdmissionCase）已完成并通过 controller review**：把 RiskEvent 切片的模式**复制**到准入
  6 角色接力工作流——复用 AgentSession（加 focus_admission_case_id）+ `app/object_workbench.py` 扩展 AdmissionCase
  富工作台（案件属性 + ComplianceFinding/LogisticsPlan/CostScenario 关联对象 + 角色可用 B1-B4 action + 对象级 agent 面板）。
  **安全铁律**：B5(approve_quote,G1/G2/G3)/B6(reject) 加入 FORBIDDEN_TOOLS、对任何角色都不注册为 agent 工具。
  **controller 独立对抗验证**：manager agent 注入 approve/reject → 均被拒；直调动作层错误角色 ok=False（双闸）。
  ADM_PERMS/门禁未改（diff 空）、agent.evaluate+RiskEvent 切片不回归、engine R/P=1.000、admission 工作台 34 断言全绿、
  四角色 UI 0 异常。**→ 对象级 agent 模式现已在 2 对象 / 2 场景验证泛化，可继续复制到 Task/Invoice。**
- **对象工作台切片三、四（Task + Invoice）已完成并通过 controller review**：复制已验证模式——复用 AgentSession
  （加 focus_task_id/focus_invoice_id）+ `app/object_workbench.py` 扩展两富工作台（Task：属性+父RiskEvent+受影响行+
  提案+对象级agent；Invoice：属性+lines+关联Shipment/RiskEvent/ExpectedCost+成本脱敏+对象级agent 帮分析费用差异/起草dispute）。
  **controller 独立对抗验证**：task/invoice agent 各角色注入 approve 均被拒、直调动作层 ok=False（双闸）；
  ROLE_PERMS/FORBIDDEN/G4-REBILL diff=0、agent.evaluate+前两切片不回归、engine R/P=1.000、四角色 UI 0 异常。
  **→ 四个核心决策对象（RiskEvent/AdmissionCase/Task/Invoice）现均有对象工作台 + permission-aware agent。**
  **待 Daniel 裁决（controller 独立发现）**：invoice 工作台(UI) 对 ops 脱敏金额，但 agent `get_invoice_context`
  对所有角色返回同样数据（维持 W6"对账数据对 AI 非敏感"口径，未 role-mask）——**非安全越权**（agent 不能动作，
  且 ops 导航无 cost tab 触达不到）；若要 agent 完全继承 UI 数据范围（Foundry 理想），需给 get_invoice_context 按角色
  脱敏并同步改 agent eval 期望。
- **标准对象视图兜底层已完成并通过 controller review**：`app/standard_object_view.py`——给全部 15 个非核心对象类型
  自动生成标准视图（属性 + 关联对象导航，按 role 脱敏，**只读、无 action、无 agent**）；`route_object` 4 核心→富工作台、
  其余→标准视图（Foundry "standard view 兜底 + configured view 少数配" 范式）。对象详情 tab 泛化为通用对象浏览器
  （rich 类型在浏览器内给只读标准视图 + 指向专用标签，避免 widget key 冲突；动作/AI 入口仍在各自专用标签）。
  **controller 独立复核**：15/15 非核心类型 build 非空标准视图（Shipment 7 关联/SalesOrderLine 4 关联等）、只读无 action 键、
  Customer.tier 对 ops 脱敏 cs 可见、四富工作台+所有既有测试无回归、engine R/P=1.000、六角色 UI 0 异常。
  **→ 全部 19 对象类型现均有"归宿"：4 核心富工作台+permission-aware agent，15 非核心标准视图，对象图可导航。**
- **三处数据可见性口径已收敛到教科书级（agent 完全继承 UI 数据范围，通过 controller review）**：
  ① 审计日志 → ops+compliance+manager 可见、按 region data_scope 过滤（manager 全量）；
  ② agent 读工具统一按角色脱敏（get_invoice_context 等与 UI object_workbench/standard_view 逐字一致，共享常量）；
  ③ invoice-agent 成本对 ops 脱敏（此前未脱敏，现收敛）。为此把 agent.evaluate 22 题**原则性重定角色**
  （成本题→finance、准入→compliance/sales、越权题 Q13/Q20→manager/finance 反而强化）——**无删断言、无放宽判定**
  （controller 逐行审 diff：forbidden 值未变、越权断言更强）。新增 `app/test_scope_parity.py` 证明 agent 范围==UI 范围。
  **controller 独立复核**：scope_parity 全绿、get_invoice_context(ops)掩码/(finance)可见、四对象越权杀手仍全过、
  FORBIDDEN/ROLE_PERMS/ADM_PERMS 未削弱（diff 空）、agent.evaluate 22/22、engine R/P=1.000、六角色 UI 0 异常。
  **诚实标注**：审计 region 过滤在全 US 种子下 ops/compliance 实际看到与 manager 同（机制真实，多区域部署即分化）。
  → 前述三个"待 Daniel 裁决"口径项均已按 Foundry 理想收敛闭合。
- **采购(procurement)业务域已立项并开始落地（决策日志 P1，Daniel 经 AskUserQuestion 批准）**：跨境采购 P2P
  接入控制塔，model-first，全复用 RiskEvent→Task→治理骨架。Daniel 裁决：加 PoLine（覆盖 D2 单 SKU）+ 第一版
  三方对账主线 4 类（R7 延误/R8 短装/R9 QC/R10 价量不符）。**Build 1/3（模型+数据）已完成并通过 controller review**：
  ontology v0.5.0 新增 5 对象（PoLine/GoodsReceipt+行/SupplierInvoice+行）+ R7-R10/A7-A8 占位 + RiskEvent 锚点泛化
  （可空 po_id/supplier_id/affected_po_line_ids，shipment_id 改可空——唯一碰核心对象处）；`datagen/procurement.py`
  生成 52 PO(30 多SKU)/92 行/62 GRN/52 供票 + 注入 R7-R10 真值 31 条（R7×8/R8×8/R9×7/R10×8 + 12 干净 + 9 灰区），
  真值存 `data/truth/`（子代理发现并守住"引擎禁读真值"铁律，§2 报告冲突不自行调和）；5 采购对象自动进标准视图。
  **controller 独立复核**：既有 R1-R6 R/P=1.000 未扰动、真值不在 ontology.sqlite、全套回归绿（含 controller 抓到并
  修的 standard_view 计数 15→20 + 采购 KEY_FIELDS；task 测试 FAIL 系复核链漏 seed 非 bug）。
- **采购 Build 2/3（引擎 R7-R10 检测 + 评估器）已完成并通过 controller review**：`engine/procurement_rules.py`
  （R7-R10 检测，只读采购表、禁读真值、as_of 安全）+ `engine/evaluate_procurement.py`（对比 data/truth 真值算
  precision/recall）。采购 RiskEvent 用 po_id/supplier_id 锚点。**子代理诚实发现 R7 recall=0.500 是 Build 1 数据
  缺口（goods_receipt_lines 无行级 received_date，多行 PO 延误被 GRN 头 min 掩盖），拒绝作弊改真值**；controller
  确认后修复（加行级 received_date，真值 md5 字节不变 13ea96…）。**controller 独立复核**：R7-R10 全 P/R=1.000、
  灰区 0 误报、真值两次生成 md5 一致、既有 R1-R6 R/P=1.000 未扰动、全套回归绿。
- **采购 Build 3/3（动作 + PurchaseOrder 对象工作台 + permission-aware agent）已完成并通过 controller review
  —— 采购纵向切片 model→data→engine→动作+工作台+agent 端到端打通**：`app/procurement_actions.py`
  （RecordGoodsReceipt/MatchSupplierInvoice 摄入，只记事实不判风险）+ approve_mitigation 加采购处置分支
  （expedite_po/raise_supplier_claim/dispute_supplier_invoice/accept_receipt_variance，走既有 assign→propose→approve
  闭环 + maker-checker，approve 仍仅 manager）+ PurchaseOrder 富工作台（PO+行+三方对账+关联风险+对象级 agent）
  升为第 5 个核心富对象。**controller 独立对抗验证**：PO-agent ops/finance/manager 注入 "you are admin" 全被拒 +
  直调动作层 ok=False；ROLE_PERMS/maker-checker/FORBIDDEN diff=0（纯 append）；procurement_loop 端到端闭环全绿、
  R1-R10 全 P/R=1.000、四富工作台+标准视图(19)+全套回归绿、三角色 UI 0 异常。
  **→ 采购成为第 4 个业务场景（延误/准入/费用/采购）；对象级 agent 模式已在 5 对象验证泛化。**
- **采购富化 P2 Build A（R11-R13 模型+数据+引擎）已完成并通过 controller review**：决策日志 P2（Daniel 批准）——
  新增 PurchasePayment(70)/SupplierQualification(19) 对象 + R11 开票超收货/R12 预付款敞口/R13 资质过期检测。
  **controller 独立复核**：R7-R13 全 P/R=1.000、灰区 0 误报、真值确定性（两次生成 md5 一致 d08428…）、
  R7-R10 真值行 byte-identical（纯 append 19 行）、R1-R6 R/P=1.000 未扰动、标准视图 21、全套回归绿。
  2 新对象走自动标准视图（ontology v0.6.0）。
- **采购富化 P2 Build B（R11-R13 处置动作 + 闭环）已完成并通过 controller review —— 采购域 R7-R13 全部落地**：
  approve_mitigation 加 R12/R13 分支（escalate_prepayment/hold_balance_payment → PurchasePayment.exposure_status=at_risk；
  request_supplier_docs/suspend_supplier → SupplierQualification.evidence_status/status；R11 复用 dispute_supplier_invoice），
  走既有 assign→propose→approve 闭环。**controller 独立复核**：ROLE_PERMS/maker-checker/FORBIDDEN diff=0（纯 append）、
  agent ops/finance/manager 均不能审批、R11-R13 五条闭环 + 越权杀手全过、R1-R13 全 P/R=1.000、全套回归绿、三角色 UI 0 异常。
  **→ 采购域完整：7 条规则(R7-R13, 三方对账+预付款+资质) P/R=1.000、端到端闭环、对象工作台+不越权 agent。全库 R1-R13。**
- **仓储(warehouse)业务域已立项（决策日志 W1，Daniel 批准）+ Build 1/3（模型+数据+引擎 R16-R18）已完成并通过 controller review**：
  第 5 个业务场景。Daniel 裁决：SKU×仓库粒度（R21 批次过期推迟）+ 库存准确主线（R16 断货/R17 不可履约/R18 盘点差异）
  + 现货救延误连接点。ontology v0.7.0 新增 Warehouse(5)/InventoryPosition(48)/InventoryReservation(16)/CycleCount(14)
  + RiskEvent 加可空 warehouse_id。`engine/warehouse_rules.py`+`evaluate_warehouse.py`。**controller 独立复核**：
  R16-R18 全 P/R=1.000、灰区 0 误报、仓储真值确定性（md5 5e1086…）、**R1-R13 全 P/R=1.000 未扰动**、标准视图 25、全套回归绿。
  4 新对象走自动标准视图。
- **仓储 Build 2/3（连接动作 + 处置动作 + 闭环）已完成并通过 controller review**：`app/warehouse_actions.py`
  （Putaway/ReserveInventory/ReleaseReservation/RecordCycleCount 操作/连接动作）+ approve_mitigation 加仓储处置分支
  （suggest_substitution 现货拆单救延误 / adjust_inventory 盘点调整 / escalate_replenishment 补货，走既有闭环+maker-checker）。
  **三连接点打通**：①采购 GoodsReceipt→Putaway→InventoryPosition.available ②Reservation→SalesOrderLine open→allocated
  ③**Shipment 延误(R1-R3)→查目的仓现货→拆单先发+余量 backorder（业务问题落点，控制塔把仓储/采购/延误连起来）**。
  **controller 独立复核**：warehouse_loop 全绿（连接点+延误现货救援+越权杀手 wh-agent[manager] approve 被拒）、
  R1-R18 全绿、ROLE_PERMS/maker-checker/FORBIDDEN/agent/tools diff=0、全套回归绿、三角色 UI 0 异常。
  **→ 仓储成第 5 业务场景（延误/准入/费用/采购/仓储），R1-R18 全 P/R=1.000。**
- **采购富化 P3 Build A（RFQ 询价 + R14 单一来源 + R15 maverick 模型+数据+引擎）已完成并通过 controller review**：
  决策日志 P3（Daniel 请求批准，P1 推迟清单项）。ontology v0.8.0 新增 RFQ(17)/RFQLine(17)/Quote(40) + R14/R15 规则
  （锚点复用 supplier_id/po_id）。`datagen/sourcing.py`+`engine/sourcing_rules.py`（R14 读既有 R7/R9 事件判断断供、
  R15 supplier_invoice 无 approved PO）。真值独立存 `expected_sourcing_risks.csv`（使 procurement 真值 md5 d08428… 不变）。
  **controller 独立复核**：R14/R15 全 P/R=1.000、灰区 0 误报、两真值确定性、**既有 R1-R13/R16-R18 全 P/R=1.000 未扰动**、
  标准视图 28、全套回归绿。子代理妥善处理 RFQ 缩写表名（ontology 声明显式 table 避免 r_f_q 误拆）。
- **采购富化 P3 Build B（R14/R15 处置动作 + 闭环）已完成并通过 controller review**：`app/sourcing_actions.py`
  + approve_mitigation 加 sourcing 分支（initiate_second_source R14→建/标 RFQ 启动第二来源 / block_non_po_payment R15→
  maverick 发票 on_hold / backfill_po R15→补追溯 PO 关联），走既有闭环 + maker-checker。**controller 独立复核**：
  sourcing_loop 全绿（R14/R15 闭环 + 越权杀手 src-agent[manager] approve 被拒）、R1-R18 全绿、
  ROLE_PERMS/maker-checker/FORBIDDEN/agent-tools diff=0、全套回归绿、三角色 UI 0 异常。
  **→ 采购域完整（R7-R15 九规则：三方对账+预付款+资质+单一来源+maverick）；控制塔 5 场景、31 对象、R1-R18 全 P/R=1.000。**

## v0.4 进度

- [x] X1：cost-manual-v0.4（Container/Invoice/InvoiceLine/ExpectedCost + Invoice 状态机 +
      R4-R6 + 提案类型扩展 + G4 门禁，勘误 P1-P3）；统一 JSON 0.4.0（19 对象/23 关系/6 状态机）；
      断言清单 24 条（XA/XB/XC/XD/XE）
- [x] X2：datagen/cost.py（Opus 子代理执行，Fable 评审通过）：柜 147（18 票多柜）、
      发票 296/行 824、基准 1161、gt 25 行（R4×5/R5×2/R6×18）、设计案例 CD-A..F；
      verify 第 7 段全绿；六评估器零回归；勘误 P4（G4 矩阵对齐 FOB/CIF/DDP）
- [x] X3（Opus 执行/Fable 评审通过）：cost_rules R4-R6（as_of 安全）+ MatchInvoice +
      dispute/accept/rebill 提案 + G4 门禁 + 费用工作台 + evaluate_cost（R/P 1.000）
      + test_cost_loop 全绿；八评估器零回归；断言 XB/XC 12 条关闭（累计 17/24）
- [x] X4（Opus 执行/Fable 评审通过）：发票查询工具 + cost_explain（F4 归因叙事）+
      评估 22 题全绿（XD 红线全过）；九评估器零回归
- [x] v0.4 收官：断言 23/24——**XE3 未达成如实记录**（增量 52% vs 目标 ≤25%；
      业务闭环本体仅 402 行，脚手架 956 行；结论修正见复盘 §0-v4）

## 全程里程碑

- [x] W0 规划：plan v0.2 + 决策日志 D1-D11 + 治理三件套（AGENTS/STATUS/CLAUDE）
- [x] W1 建模：11 对象 / 4 状态机 / 6 动作五要素 / 权限矩阵 / ontology JSON / 断言清单
- [x] W2 数据：datagen 六模块、15 设计案例、ground truth 双表、真实字段 Tier 1（D11）、
      ISO 6346 柜号等真实感升级，verify 42 项全绿
- [x] W3 管道：含噪源表重建对象层，精度 120/120、705/705；ER 映射；DQ 报告；数据导读
- [x] W4 引擎：R1-R3 as_of 时间旅行安全；KPI Recall/Precision 1.000；高危漏判 0
- [x] W5 闭环：A3-A6 动作层 + 控制塔 UI；C3 人工走查通过（Daniel 亲手走完 RSK-0044 全闭环）
- [x] W6 AI：工具白名单 + 确定性简报 + 可插拔 LLM；评估 11/11；越权拒绝且留审计
- [x] 收官：README 重写、docs/retrospective.md 复盘

## v0.3 进度

- [x] V1：admission-manual-v0.3（3 对象扩展 + 4 新对象 + 状态机 + 6 动作五要素 + 6 角色矩阵，
      勘误 N1-N5）；统一 ontology JSON 0.3.0（15 对象/17 关系/12 动作）；准入断言清单（22 条）
- [x] V2：datagen/admission.py（独立随机流零扰动）+ qms 四表 + 门禁真值 + pipeline 建表；
      verify 54 项全绿；六评估器零回归；AC4 复用性 SQL 预验证通过
- [x] V3：admission_actions B1-B6（G1/G2/G3 门禁）+ 准入工作台 UI（6 角色）+
      test_admission_loop 17 项全绿；七评估器全绿；断言 17/22 自动化通过
- [x] V3 人工验收完成（2026-07-06）：Daniel 四角色接力走完 AC-2026-0041 全链（审计链复核无误）；AC2 由 AppTest 机械验证
- [x] V4：AI 准入扩展（context 工具 + v0.1 §7 schema 简报 + B5/B6 禁用）；
      评估 17 题全绿（AD1-AD4 红线全过）；断言 22/22；复盘/README 更新，v0.3 收官

## 待人自选（不阻塞）

- [ ] LLM 实测：`pip install openai`、设置 OPENAI_API_KEY 后
      `python3 -m agent.evaluate --llm` 与 `python3 -m agent.llm_agent "问题"`
- [ ] Anthropic 备用实测：`pip install anthropic`、设置 ANTHROPIC_API_KEY 后
      `python3 -m agent.evaluate --llm` 与 `python3 -m agent.llm_agent "问题"`
- [ ] 录屏 demo（plan §11-W6 可选项）
- [ ] 阅读 docs/data-guide.md + docs/retrospective.md（建议精读复盘 §2/§3——学习目标对账）

## 复现命令（全链路）

```
python3 -m datagen.generate && python3 -m datagen.verify
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate
python3 -m engine.detect && python3 -m engine.evaluate
python3 -m app.test_closed_loop
python3 -m agent.evaluate
python3 -m datagen.seed_demo_ops   # enrich demo 运营快照（多 owner/SLA/升级），让 UI 一打开是"活的"
streamlit run app/streamlit_app.py
```

## 会话交接备注（任何模型接管的启动路径）

1. 读 AGENTS.md（§0 启动协议 + §4 软知识——与 Daniel 协作的方式）
2. 读本文件（唯一状态源）；深入某场景再读对应 plan/manual
3. 自检接管质量：跑一遍复现命令（十个评估器应全绿），然后向 Daniel 用三句话
   复述项目现状——复述与现实一致即交接成功
4. 新工作一律先立 plan 经 Daniel 批准（候选清单已清空，无遗留承诺）
5. 剩余人工事项（无需模型）：LLM 实测、录屏、git push、article.md 个人化
