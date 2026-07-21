# 跨行业调研报告：AI/自动化系统的分层自主权与异常分级升级机制

> 调研执行：general-purpose 子代理（WebSearch + WebFetch），2026-07-10
> **用途**：为跨境供应链控制塔（多角色 AI 智能体：ops/finance/cs，低风险自主执行、高风险仅提案）设计 AI 自主权矩阵与异常升级矩阵提供已验证参照系。
> **证据标注**：`[一手证实]` = 官方文档/系统卡/标准原文；`[二手证实]` = 厂商转述/行业博客交叉印证；`[推断]` = 本报告的分析性延伸。

---

## A 组：AI 智能体的人机分权实践

### A1. 主流 agentic 产品怎么划分"AI 可自主 vs 必须人批"

#### Anthropic Claude Code `[一手证实]`
来源：https://code.claude.com/docs/en/permissions ；https://www.anthropic.com/engineering/claude-code-auto-mode

- **分级判断轴：工具的副作用类型**。三档：只读（读文件/Grep）→ 无需批准；Bash 执行 → 需批准，"不再询问"按项目目录+命令前缀永久记忆；文件修改 → 需批准，记忆仅到会话结束。内置只读命令白名单（ls/cat/grep/只读 git 等）在任何模式下免批，且**不可配置扩大**。
- **层间边界：allow / ask / deny 三色规则**，评估顺序固定为 deny → ask → allow，首个匹配生效；deny 不能被更具体的 allow 打洞（明确设计："deny 规则不能携带 allowlist 例外"）。企业 managed settings 的 deny 任何层级都推翻不了。
- **自主档位（permission modes）**：default（首次用时问）→ acceptEdits（工作目录内编辑自动过）→ auto(研究预览，分类器代批) → bypassPermissions（仅限隔离环境，且 `rm -rf /`、`rm -rf ~` 仍强制弹窗——**断路器**）。另有 plan（纯只读）和 dontAsk（未预批即拒）。
- **auto mode 的信任机制设计**：
  - 动机：用户对 93% 的权限提示无脑点批准 → 审批疲劳使人批形同虚设；
  - 三层许可决策：安全工具白名单 → 项目目录内文件操作自动过（理由：**版本控制可回滚 = 可逆**）→ 其余交分类器；
  - 分类器两阶段（单 token 快筛 → 被标记项才做链式推理），针对四类威胁：过度主动、诚实错误、提示注入、模型不对齐；
  - **生成与验证隔离**：分类器刻意看不到 agent 的解释性文字，防止 agent"说服"审批者；
  - 实测：完整管道误报 0.4%、漏检 17%，官方明言**不能替代高风险基础设施的人工审查**；
  - **自动降级**：连续 3 次拒绝或累计 20 次拒绝 → 升级人工接管。

#### OpenAI Operator / ChatGPT agent `[一手证实：系统卡 PDF 原文]`
来源：https://cdn.openai.com/operator_system_card.pdf ；https://openai.com/index/introducing-operator/ （原页 403，takeover 细节经搜索摘要与帮助中心交叉印证，`[二手证实]`）

四道递进防线（判断轴：**动作副作用 × 站点风险 × 信息敏感度 × 任务类别**）：
1. **Proactive refusals（禁区）**：银行交易、高风险决策（如决定录用与否）直接拒绝，合成评测集上拒绝 recall 94%；
2. **Confirmations（强确认）**：任何"改变世界状态"的动作前必须用户确认（完成购买、发邮件、批量删邮件），系统卡称确认使模型失误风险**降低约 90%**；
3. **Watch mode（在场监督）**："在某些网站上失误影响更大（如邮件服务可能泄露敏感信息），我们要求用户监督 Operator 的动作——用户不活跃或离开页面时自动暂停执行"（原文）；
4. **Takeover mode（人接管）**：输入登录凭证/支付信息时交还用户操作，期间不采集不截屏 `[二手证实]`。

#### Devin（Cognition）`[一手+二手混合]`
来源：https://docs.devin.ai/enterprise/features/ai-guardrails.md `[一手]`；https://cognition.com/blog/devin-review 及多篇行业分析 `[二手证实]`

- **一手证实——Guardrails 四级递进处置**（对"异常处置分级"直接可抄）：`log_only`（只记录）→ `warn_user`（横幅警告但继续）→ `block_message`（拦截该消息）→ `kill_session`（终止整个会话）。管理员在 Settings > Guardrails 按组织配置；每次违规写入审计日志（`ai_guardrail_violation`），可经 violations API 程序化检索，且每条违规链接回源会话。
- **二手证实——交付通道式分权**：Interactive Planning（计划先经人批再执行）、一切改动走 PR + CI gates + 强制 code review、repo allowlist、无生产环境直写权限。其思路是**不做动作级审批，而是把 AI 的输出全部塞进人类既有的工程治理闸门**（branch protection / code owners / 审批后才部署）。
- 官方文档中**未找到**动作级"哪些必须人批"清单（如实说：官方以 RBAC、guardrails、沙箱、审计为主）。

#### AWS Bedrock Agents `[一手证实]`
来源：https://docs.aws.amazon.com/bedrock/latest/userguide/agents-userconfirmation.html

- **声明式确认**：在动作组函数 schema 里设 `requireConfirmation: ENABLED`（OpenAPI 则 `x-requireConfirmation`），运行时 agent 决定调用该动作时，先把动作详情返回给应用，用户 CONFIRM 才执行、DENY 则不执行。默认 DISABLED。设计动机：防提示注入触发变更类（mutating）动作。
- 参照价值：**"需人批"是工具/动作定义上的一个属性位**，而非运行时才判断。

#### A1 共同模式小结
- 自主权按**动作可逆性/副作用**分档（只读自由 → 可逆写轻批 → 不可逆/外部副作用强批 → 禁区直接拒）；
- 三色规则（allow/ask/deny）+ deny 绝对优先；
- 两条技术路线并存：**声明式**（在动作定义处标风险位）与**运行时分类器**（Claude Code auto mode）；
- 都内建**断路器**与**升级人工的降级路径**。

### A2. 学界/业界的自主权分级框架

#### HITL / HOTL / HOOTL `[二手证实，业界通用定义]`
来源：https://www.credo.ai/glossary/human-on-the-loop ；https://tekleaders.com/human-in-the-loop-vs-human-on-the-loop-agentic-ai/

- **Human-in-the-loop**：AI 分析并提案，**每个关键动作执行前需人批准**，系统在检查点暂停等待。适用：资金支出、法律协议、敏感数据访问等高风险决策。
- **Human-on-the-loop**：AI 自主执行，人**监督总体行为、审例外、可随时介入**。适用：大规模高频场景（逐单人批会摧毁吞吐量）。
- **Human-out-of-the-loop**：运行期无人参与。生产环境高风险任务普遍回避。
- 判断轴 = **人介入的时机**：事前批准 / 事中监督 / 事后审计。"低风险自主执行、高风险仅提案"= 同一产品内按动作风险混用 HOTL 与 HITL。

#### SAE J3016 L0-L5 及其类比 `[标准内容为证实；类比为推断]`
来源：https://www.sae.org (标准)；https://www.aminext.blog/en/post/sae-autonomous-driving-levels-explained

- L0-L2 = 驾驶支援（**人始终负责监控**）；L3 = 条件自动（系统请求时人必须接管，"fallback-ready"）；L4 = 高度自动（系统能**自行达到最小风险状态**，但限定在 ODD 运营设计域内）；L5 = 全域全自动。
- `[推断]` 对企业软件的三个可借鉴点：① **ODD 概念**——自动化授权永远绑定"限定场景"（某航线/某金额内/某类异常），出域即强制降级，这比笼统的"低/高风险"更可运营；② **L3 是最危险区间**——人名义上监督实际上不在状态；对应到控制塔："AI 提案人秒批"若人不真审，就是伪 HITL（与 Claude Code 93% 盲批数据互证）；③ fallback 责任必须显式指定（谁在 AI 报错时接住）。

#### Levels of Autonomy for AI Agents（arXiv 工作论文）`[一手证实]`
来源：https://arxiv.org/html/2506.12469v1

- 五级按**用户角色**定义：L1 Operator（人主导）→ L2 Collaborator（共同计划执行）→ L3 Consultant（AI 主导、人供偏好）→ **L4 Approver（AI 自主运行，人只在阻塞点/审批点介入）** → L5 Observer（全自主，人只看日志+急停）。控制塔产品定位即 L3-L4 混合。
- **核心论点：自主性与能力解耦**——"一个很强的 agent 也可以被要求每步咨询用户，从而以低自主级别运行"。自主级别是**刻意的设计选择**，不是模型能力的函数。
- **Autonomy certificates**：第三方对"特定 agent + 特定环境"发放最高允许自主级证书，规格或环境变了要重新认证——对应到产品：**自主权授予应绑定（agent 版本 × 场景域），任一变更即回收重评**。
- **Assisted evaluations**：逐步增加人参与直到任务成功率达标，以实测定级——即"用数据而非拍脑袋决定某类动作放到哪一档"。

#### Gartner 供应链计划六级自主性 `[二手证实，经 ToolsGroup 转述]`
来源：https://www.toolsgroup.com/blog/gartners-six-levels-of-supply-chain-planning-autonomy/

L1 一般信息 → L2 具体建议（统计预测）→ L3 咨询性告警 → **L4 opt-in 自动化**（系统做完复杂任务如多方案模拟，**人确认采纳才生效**）→ **L5 可否决自动化**（opt-out：系统默认执行如自动再订货点，**人可推翻**）→ L6 非可选自动化（人不再审）。
这是供应链行业自己的"提案制 → 默认执行可否决 → 完全自主"正式阶梯，**L4→L5 的跃迁（从"人点头才做"到"默认做、人可拦"）正是信任升级的产品化表达**。

---

## B 组：传统行业已验证的自动化分层

### B3. 金融支付/银行的 STP + 例外管理 `[证实]`
来源：https://www.backbase.com/blog/straight-through-processing-banking ；https://stripe.com/resources/more/what-is-straight-through-processing-heres-what-you-need-to-know ；https://www.paystand.com/blog/straight-through-processing

- **结构**：默认全自动直通；**任一校验失败即掉入例外队列**（exception queue）人工修复（manual repair）。度量 = STP 率（无人工干预交易占比）。
- **进例外队列的判断轴**：数据完整性/格式（IBAN/BIC 校验失败、字段缺失）、金额限额（超阈值）、欺诈模式异常、客户风险等级。注意：**掉队列的主因是数据质量而非业务风险**——Backbase 指出参考数据不一致是 STP 失败的头号驱动。
- **量化基准**：多数银行卡在 ~60% STP 率（瓶颈=架构碎片化、系统间"白空间"）；best-in-class 约 67.2%（Ardent 2020，经转述 `[二手]`）。
- **例外处理设计**：失败交易**带完整上下文**路由给对的员工；哲学是"快速解决，而非完美自动化"。
- **信任升级机制**：① 跟踪人工修复的方式 → 反哺自动化规则（例外队列是自动化的训练集）；② 单一领域试点成功后再扩展；③ 提升 STP 率的正道是**减少例外**（源头数据校验），不是加速处理。
- 查不到的：具体金额阈值数字属各行内部政策，无公开标准。

### B4. 风控分级审批矩阵 `[证实]`
来源：https://seon.io/resources/fraud-scores-how-to-calculate-them/ ；https://validadvantage.com/blog/check-fraud-scoring ；https://www.hyperbots.com/glossary/credit-authorization-matrix ；https://www.cslucas.com/how-banking-mandates-protect-your-treasury-from-unauthorised-payments/

- **欺诈分三段闸（行业同构）**：分数 < A 自动通过；A–B 之间进人工审核队列或自动挑战（如 3DS 验证）；> B 自动拒绝/冻结。示例刻度：0-30 / 31-69 / 70+（支票欺诈场景）。**灰带宽度就是人力预算的调节阀**。
- **信贷授权矩阵 = 角色 × 金额 × 风险等级** 三维查表：示例——Credit Analyst 批 ≤$50k 低风险、Credit Manager ≤$200k 中风险、Finance Director 批更大金额或任何高风险客户；超阈值/高风险强制升级到高管或信贷委员会。**同一金额在不同风险评级下需要不同审批层**——金额单轴不够。
- **四眼原则（maker-checker）**：超过预定限额的支付强制双人（发起人≠授权人），由系统按金额查表**结构性强制**，不可绕过，全程留痕以满足审计与 AML 合规。
- **信任升级/降级的标准管道（规则/模型如何"晋升"）**：shadow mode（新策略 100% 并行跑、只记录不生效，对比与现役策略的决策差异）→ canary（~1% 流量实断）→ champion/challenger 渐进放量 → 胜出者转正，**配自动回滚**。来源：https://www.finbox.in/blog/how-do-canary-testing-and-champion-challenger-features-in-a-business-rules-engine-benefit-credit-decision-making ；https://www.fico.com/blogs/benefits-championchallenger-testing-decision-management

### B5. RPA attended vs unattended `[一手证实：UiPath 官方文档]`
来源：https://docs.uipath.com/overview/other/latest/overview/attended-vs-unattended-automation ；https://docs.uipath.com/action-center/automation-suite/2023.4/user-guide/introduction

- **划分标准**：attended = 碎片化任务 + 过程中需要人的实时判断（前台场景）；unattended = 规则完全预定义、可批量、高重复（后台批处理）。**判据本质是"决策是否可完全规则化"**。
- **权限边界（关键发现）**：attended 机器人**只能在触发它的用户自身权限内运行**——官方明言"无法保证自动化与机器用户之间的安全隔离"，所以绝不给它超越用户的权限；unattended 才能用管理员配置的**凭证资产**执行特权操作，且有独立审计链。→ 映射到 AI 同事：**贴身助手型 agent 继承用户权限，后台自主型 agent 用独立的服务账号+独立审计**。
- **混合模式（Action Center）**：无人值守流程跑到需人决策的节点（审批/升级/例外）→ **自动创建人工任务、挂起该工作流实例，机器人转做下一单**；人完成输入后流程自动恢复。UiPath Agents 的 escalations 同构（agent 挂起直到指定人解决）。这就是"长时间运行的工作流 + 中途人批 + 恢复"的成熟工程范式，直接对应"高风险仅提案"。

---

## C 组：异常分级与升级机制

### C6. ITIL/ITSM 事件管理 `[证实]`
来源：https://blog.invgate.com/itil-priority-matrix ；https://www.atomicwork.com/itil/itil-priority-matrix ；https://www.novelvista.com/blogs/it-service-management/escalation-management-itil ；https://clearfeed.ai/blogs/incident-escalation-matrix

- **priority = impact × urgency 二维矩阵**：impact = 影响面（多少用户/关键服务是否不可用/收入/合规/声誉风险）；urgency = 时间敏感度（业务截止期、运营周期、客户承诺）。高×高=P1，低×低=P4，中间落 P2/P3。
- **P1-P4 与 SLA 挂钩**（示例模板值，`[非标准，各组织自定]`）：P1 十分钟应答/4 小时解决、P2 15 分钟/8 小时、P3 1 小时/2 工作日、P4 4 小时/5 工作日。
- **两种升级正交**：**functional escalation（横向）**= 转给有技能/系统权限的人——按**能力**路由；**hierarchic escalation（纵向）**= 转给有决策权/更资深的人——按**职权**路由，典型触发是 SLA 即将击穿时通知管理层。最佳实践明确：**两条是独立通道、常并行**（专家干活 + 领导知情），不要混成一条链。
- **治理**：谁有权定 P1/宣布重大事件必须显式指定（incident manager / major incident coordinator），防止分级权失控。

### C7. 供应链控制塔行业的异常管理 `[证实 + 如实说缺口]`
来源：https://www.project44.com/resources/what-is-an-exception-event-in-supply-chain-management/ ；https://www.fourkites.com/platform/ ；https://blueyonder.com/solutions/supply-chain-command-center ；https://newsroom.ibm.com/2020-07-01-IBM-Introduces-Sterling-Inventory-Control-Tower-...

- **project44**：AI 持续监控，按**性质（nature）与严重度（severity）**分类异常（延误/漏接驳/海关扣留/货损/温度偏移），经**可配置业务规则**路由到指定干系人；"高级实现"支持执行**预批准动作**（改道、通知客户、重配资源）。但其公开资源页**没有** severity 分级定义表、阈值或升级程序细节（抓取原页确认了这一缺失）。
- **FourKites**：Intelligent Control Tower + **Digital Workforce**——命名数字工人（Inbound Scheduler AI、Customer Connect AI、AutoGate AI 等）自动预约、发文档包、更新状态、**"解决低价值异常"**（resolve low-value exceptions），人转向战略工作。**"低价值异常给 AI、高价值给人"是其公开的分权表述**，但无公开的分界矩阵。
- **Blue Yonder**：**降噪式告警**——只对"处于风险的货件"告警而非全量播报；AI 给出**带成本对比的处置建议**（空运补救 $500 vs 迟到罚金 $2,000，人看着经济账拍板）；宣称可自主改道/优化末端/通知干系人。
- **IBM Sterling**：**Resolution Rooms**（跨部门跨伙伴的异常协作作战室）+ **Digital Playbooks**（沉淀历史最佳实践、给出推荐动作，随使用变快）。
- **行业结论（缺口即机会）**：公开材料一致证实"AI 检测分级 → 规则路由 → 低价值自动处置 → 高价值人决策"的方向，但**没有任何一家公开 exception severity 的定义表或"自动处置 vs 人批"的分级矩阵**（类似 ITIL P1-P4 那种可操作标准在此行业是空白）。`[推断]` 把 B/C 组的成熟矩阵方法引入控制塔异常管理，本身就是可辩护的产品差异点。

### C8. SRE/PagerDuty 分页升级链 `[一手证实]`
来源：https://response.pagerduty.com/before/severity_levels/ ；https://support.pagerduty.com/main/docs/escalation-policies ；https://support.pagerduty.com/main/docs/notification-rules ；https://support.pagerduty.com/main/docs/incidents

- **SEV 定义表**：SEV-1（危急，需公告+高管联络）/ SEV-2（危急系统问题，正影响大量客户使用）→ 两者都触发重大事件响应、呼叫 Incident Commander；SEV-3（稳定性/轻度客户影响，高优呼叫服务团队，视需要升格）/ SEV-4（需处理但不影响客户使用，低优呼叫）/ SEV-5（表面问题，只开工单）。建议定义**尽量具体到"受影响用户/账户百分比"**。
- **"Always assume the worst"**：拿不准 SEV-2 还是 SEV-1，就按高的处理；**事中不辩论级别，事后复盘再调**。
- **升级链机制**：escalation policy 由多级规则组成；**escalation timeout**（默认 30 分钟、最小 1-3 分钟）内无人 ack 自动升下一级；策略最多循环 9 轮，之后停留在末级；**一次只通知一个目标直到有人 ack**（单一责任人原则）；round robin 均衡分派。
- **urgency 双轨**：high urgency = 电话/短信/推送强通知、未 ack 自动沿升级链上升；**low urgency = 安静通知（推送/邮件），不自动升级、也不能直接手动升级——必须先改成 high urgency 才能进升级链**。severity → urgency 映射决定"叫醒谁、多大声、超时是否自动上顶"。
- 对"AI 一线分诊 + 人分级审批"的借鉴：**时间驱动的自动升级**（AI 提案无人响应 X 分钟即升下一级/更高职级）、**单一责任人直到显式确认**、**分级决定通知强度而非只决定顺序**。

---

## 跨行业共同模式提炼（10 条）

1. **分级判断轴至少二维、且做乘积**：影响面 × 紧迫度（ITIL）、金额 × 风险评分（信贷矩阵）、动作可逆性 × 信息敏感度（Operator/Claude Code）。所有成熟体系都拒绝单轴分级；控制塔的异常分级轴建议：金额敞口 × 时间窗口（还来得及吗）× 可逆性。
2. **三色决策带是最普适结构**：自动通过（绿）/ 人工审核（灰）/ 自动拒绝（红）——欺诈分闸、STP 直通/例外队列、allow/ask/deny 完全同构。**灰带的宽度是人力预算的调节阀**，阈值 A/B 可随信任数据移动，结构不变。
3. **自主权跟"可逆性与副作用"走，不跟"能力"走**：只读自由、项目/域内可逆写轻批（因为可回滚）、不可逆或对外副作用强确认、禁区直接拒。arXiv 框架明确"能力与自主解耦"——给 AI 同事的授权是设计决定，不是模型越强权限越大。
4. **红线先画、deny 绝对优先**：Operator 早期直接拒绝银行交易类任务；Claude Code 的 deny 不可被任何 allow 打洞；信贷超权限强制升级无例外。先定义"AI 永远不能做什么"，再谈分级。
5. **升级有两条正交通道，别混成一条链**：functional（转给会处理的人/agent——按能力）与 hierarchic（通知有权拍板的人——按职权），常需并行（专家干活+领导知情）。AI 分诊后的路由设计应显式区分这两类目标。
6. **时间是第四根轴——超时即自动升级**：PagerDuty escalation timeout、ITIL"SLA 将破通知管理层"、watch mode"人不在场即暂停"。AI 的提案等待人批也必须带超时升级，否则"仅提案"会变成"永久搁置"。
7. **信任升级是数据驱动的渐进管道，晋升凭证是一致率不是资历**：shadow（并行跑不生效）→ canary（小流量）→ 渐进放量 → 转正+自动回滚（风控）；试点域→扩展（STP）；opt-in 提案 → opt-out 默认执行可否决 → 非可选（Gartner L4→L5→L6）。让 AI 同事先"影子提案"，用"提案与人最终决定的一致率"决定某类动作何时从灰带划入绿带。
8. **降级机制必须与升级机制同时内建**：连续拒绝计数升人工（Claude Code auto：3 连拒/20 总拒）、champion 失守自动回滚、断路器（rm -rf 永远弹窗）、四级处置的顶格 kill_session（Devin）。授权绑定（agent 版本 × 场景域），任一变更即回收重评（autonomy certificates）。
9. **例外队列是自动化的训练集**：人工修复必须带完整上下文、修复方式必须被记录并反哺规则（STP manual repair 跟踪、IBM Digital Playbooks）。目标是持续收窄灰带，而非把灰带处理得更快。
10. **宁高勿低 + 降噪并举**：分级拿不准按高处理、事中不争论、复盘再调（PagerDuty）；同时只对"至险对象"告警（Blue Yonder 降噪）并给决策者附经济账（$500 vs $2,000）。审批/告警疲劳是分权体系的头号腐蚀剂——93% 盲批（Claude Code 数据）证明：**要求人批太多次，等于没有人批**。

---

## 查不到/未证实事项（如实声明）

- project44、FourKites、Blue Yonder 均**无公开的** exception severity 定义表、阈值标准或"自动处置 vs 人批"分级矩阵（project44 资源页经原文抓取确认缺失，其余为搜索未见）。
- 银行 STP 与支付审批的**具体金额阈值**属各行内部政策，无公开行业标准数字；本报告引用的均为示例刻度。
- Devin 官方文档**没有动作级"必须人批"清单**（其治理靠 RBAC + guardrails + PR 通道）；其工作流细节（Interactive Planning 等）来自二手资料。
- OpenAI Operator 的 takeover mode 措辞未出现在系统卡正文（PDF 全文检索确认），出自其发布页/帮助中心（原页 403，标注为二手证实）。
- ITIL P1-P4 的 SLA 时限为咨询机构模板示例值，非 ITIL 标准规定。

## 主要来源清单

Claude Code 权限文档 https://code.claude.com/docs/en/permissions ｜ Anthropic auto mode https://www.anthropic.com/engineering/claude-code-auto-mode ｜ Operator 系统卡 https://cdn.openai.com/operator_system_card.pdf ｜ Levels of Autonomy for AI Agents https://arxiv.org/html/2506.12469v1 ｜ Bedrock 用户确认 https://docs.aws.amazon.com/bedrock/latest/userguide/agents-userconfirmation.html ｜ Devin Guardrails https://docs.devin.ai/enterprise/features/ai-guardrails.md ｜ Credo AI HOTL https://www.credo.ai/glossary/human-on-the-loop ｜ Gartner 六级（转述）https://www.toolsgroup.com/blog/gartners-six-levels-of-supply-chain-planning-autonomy/ ｜ Backbase STP https://www.backbase.com/blog/straight-through-processing-banking ｜ SEON 欺诈评分 https://seon.io/resources/fraud-scores-how-to-calculate-them/ ｜ 信贷授权矩阵 https://www.hyperbots.com/glossary/credit-authorization-matrix ｜ 四眼原则 https://www.cslucas.com/how-banking-mandates-protect-your-treasury-from-unauthorised-payments/ ｜ champion/challenger https://www.fico.com/blogs/benefits-championchallenger-testing-decision-management ｜ UiPath attended/unattended https://docs.uipath.com/overview/other/latest/overview/attended-vs-unattended-automation ｜ UiPath Action Center https://docs.uipath.com/action-center/automation-suite/2023.4/user-guide/introduction ｜ ITIL 矩阵 https://blog.invgate.com/itil-priority-matrix ｜ 升级类型 https://www.novelvista.com/blogs/it-service-management/escalation-management-itil ｜ project44 异常 https://www.project44.com/resources/what-is-an-exception-event-in-supply-chain-management/ ｜ FourKites https://www.fourkites.com/platform/ ｜ Blue Yonder https://blueyonder.com/solutions/supply-chain-command-center ｜ IBM Sterling https://newsroom.ibm.com/2020-07-01-IBM-Introduces-Sterling-Inventory-Control-Tower-to-Help-Organizations-More-Effectively-Manage-Inventory-and-Build-Resilient-Supply-Chains ｜ PagerDuty severity https://response.pagerduty.com/before/severity_levels/ ｜ escalation policies https://support.pagerduty.com/main/docs/escalation-policies ｜ notification rules https://support.pagerduty.com/main/docs/notification-rules
