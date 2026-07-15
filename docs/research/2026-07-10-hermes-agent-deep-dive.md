# Hermes Agent（Nous Research）底层机制调研报告

> 调研执行：general-purpose 子代理（WebSearch + WebFetch + GitHub API），2026-07-10
> **调研对象**：NousResearch/hermes-agent，开源自我改进型 AI 智能体框架，MIT 协议
> **当日 GitHub API 实测**：212,287 stars / 39,122 forks / 主语言 Python / 最新推送 2026-07-10；最新 release v0.18.2（2026-07-08）
> **信源分级**：【官方】= GitHub 仓库 README/官方文档站 hermes-agent.nousresearch.com；【第三方】= 36kr、CSDN、知乎、独立博客；【未证实】= 仅单一第三方来源

**先说清一个事实冲突**：GitHub API 显示仓库创建于 2025-07-22，而中文社区普遍说"2026 年 2 月 25 日开源"、36kr 说"2026 年 4 月崛起"。合理解释是仓库早已存在、2026 年 2 月正式公开发布并爆红。未找到官方发布公告原文锁死日期，如实标注。

---

## 一、整体架构：gateway、agent loop、会话管理

**三个入口喂给同一个智能体内核**【官方】：
- **入口层**：CLI（`cli.py`）、网关（`gateway/run.py`，常驻进程接 20+ 消息平台）、ACP 适配器（接 VS Code/Zed/JetBrains）
- **内核**：`run_agent.py` 的 `AIAgent` 类。官方设计原则原文："One AIAgent class serves CLI, gateway, ACP, batch, and API server. Platform differences live in the entry point, not the agent."
- **三个子系统**：提示词组装（`agent/prompt_builder.py`）、模型供应商解析（`hermes_cli/runtime_provider.py`）、工具分发（`model_tools.py` + `tools/registry.py`）

**Agent loop（单轮生命周期）**【官方】：生成 task_id → 追加用户消息 → 构建/复用缓存的系统提示 → 预检压缩（超模型窗口 50% 触发；网关模式轮间超 85% 自动压缩）→ 组装 API 消息 → 注入临时层 → 可中断的 API 调用 → 解析响应：有 tool_calls 就执行后继续循环。多工具调用经 ThreadPoolExecutor 并行（交互类工具强制串行）。`todo`、`memory`、`session_search`、`delegate_task` 这类改变智能体自身状态的工具由 `run_agent.py` 在派发前拦截处理。

**系统提示三层组装**【官方】：稳定层（SOUL.md 人格、工具规则、技能索引——字节级稳定以吃前缀缓存）→ 上下文层（项目上下文文件，优先级 `.hermes.md` > `AGENTS.md` > `CLAUDE.md` > `.cursorrules`，只取第一个命中，安全扫描后截断至 2 万字符）→ 易变层（MEMORY.md/USER.md 冻结快照、时间戳）。临时层不进缓存。

**会话管理**【官方】：所有平台所有会话统一存 `~/.hermes/state.db`（SQLite），三张表：sessions / messages / messages_fts（FTS5 全文索引）。压缩生成"子会话"并记录血缘。

**多平台"共享上下文"的真相（纠正常见误读）**：
- 网关是一个进程服务全部平台，但**活跃对话上下文默认按平台隔离**：会话键 `agent:main:{platform}:{chat_type}:{chat_id}`，官方明确"context doesn't cross platform boundaries"
- 真正的跨平台共享靠三条通道：① 全局记忆文件（MEMORY.md/USER.md 对所有入口生效）；② session_search 跨库检索（全平台写同一个 state.db）；③ 显式 `/handoff <platform>` 会话移交
- 设计动机：多租户安全（群聊不能泄漏私聊上下文）与个人助理连续性之间的平衡

---

## 二、记忆架构：官方实体 vs 社区"三层/四层"说法

**结论**：官方**不使用**"工作记忆/情景记忆/语义记忆"分类。官方实际实体是"两个记忆文件 + 一个会话库 + 可插拔外部记忆后端"。中文社区的"三层/四层记忆"都是第三方解读性映射。

**官方记忆实体**【官方】：

| 层 | 载体 | 写入 | 检索 | 清理 |
|---|---|---|---|---|
| 持久记忆 | `~/.hermes/memories/MEMORY.md`（环境事实/约定/经验，预算约 2200 字符）+ `USER.md`（用户偏好，约 1375 字符） | 智能体**自动写**（"saves automatically"）；另有每轮后的后台自我改进复查补写 | 会话启动时以**冻结快照**渲染进系统提示（为前缀缓存服务） | 无自动衰减。容量到 80% 引导读取-合并-精简；完全重复条目自动拒收 |
| 会话记忆 | `~/.hermes/state.db`（SQLite + FTS5），全平台共库 | 每条消息自动落库 | `session_search` 三模式：discovery（跨会话全文检索）、scroll（锚点翻阅）、browse（按时间） | 默认永不删；可选 auto_prune（默认关） |
| 用户模型（可选） | Honcho 外部后端 | 对话后异步推理，沉淀 peer card / conclusions | 5 个工具 + recallMode 控制自动注入 | 默认**关闭** |

**写入的质量闸门**【官方】：`memory.write_approval: true` 时所有记忆写入先入暂存区、`/memory pending` 人工审批；记忆条目接受前**做注入/外泄模式扫描**（防"记忆投毒"——它们会进系统提示）。

**"nudge"机制**：官方只说"periodic nudges"提醒智能体持久化知识；36kr 称"约每 15 轮一次"【第三方，官方未给数字】。

**一处官方口径不一致（如实报告）**：README 写 session search 带"LLM summarization"，memory 文档页写"no LLM summarization"。引用以文档页为准。

---

## 三、技能机制（Skill）：从成功任务中自动提炼可复用能力

这是 Hermes"越用越强"的核心，官方称 procedural memory（程序性记忆）。

**1. 形态**：技能 = **Markdown 文件 + YAML frontmatter**，不是代码。每技能一个目录（SKILL.md 必需 + references/templates/scripts/assets）。frontmatter 含 name/description/version/platforms/metadata，兼容 **agentskills.io 开放标准**。加载走**渐进披露**：`skills_list()` 只给元数据（约 3k tokens）→ `skill_view(name)` 取全文 → 按路径取参考文件——技能再多不炸上下文。

**2. 自动提炼流程**【官方】：智能体自己在四类时机调用 `skill_manage` 创建技能：
- 成功完成复杂任务后（**5+ 次工具调用**为门槛）
- 踩坑后**找到可行路径**时（把纠错成果固化）
- **用户纠正了它的做法**时（把人的偏好固化）
- 发现非平凡工作流时

默认**没有人工预审**——除非开启 `skills.write_approval: true`，届时所有技能写入先暂存到 `~/.hermes/pending/skills/`，走 `/skills pending → diff → approve|reject` 审批流。另有 `/learn` 让用户主动喂材料生成技能。

**3. 复用与精炼**【官方】：斜杠命令显式加载（一条消息最多 5 个）或智能体自主检索加载；支持条件显隐（requires_toolsets/fallback_for_toolsets）。精炼用 `skill_manage` 的 **`patch`**（old→new 定点修改）持续小步改进。每技能有 `.usage.json` 边车记录 use_count / last_used_at / view_count / patch_count——用量数据驱动 Curator 治理。

**4. 版本管理与回滚**【官方】：无 git 式逐版本历史，但有四重保障：frontmatter version 字段；**Curator 每次运行前对整个技能库打 tar.gz 快照**，`hermes curator rollback` 一键回滚（回滚本身也先自拍快照）；内置技能 origin-hash 清单（本地改过的永不被上游覆盖）；snapshot export 导出。

**5. 技能市场（Hub）与信任分级**【官方】：browse/search/inspect/install/audit；来源含 official / skills-sh / well-known / github / browse-sh；信任级 builtin/official/trusted/community；**所有 Hub 安装过安全扫描器**（数据外泄、提示注入、破坏性命令、供应链信号）。

**设计动机**：把"这次是怎么做成的"从对话历史（会被压缩丢失）提升为独立文件资产；Markdown 形态让人可读可改可审计、可分发——与"微调模型"路线的最大区别：**能力沉淀在数据层而非权重层，随时可回滚**。

---

## 四、GEPA 提示词进化

**它是什么**：GEPA = **Genetic-Pareto 反思式提示词进化**，源自 ICLR 2026 Oral 论文（gepa-ai/gepa），Nous 将其与 DSPy 结合，放在独立仓库 **NousResearch/hermes-agent-self-evolution**（2026-06 发布）。

**机制**【官方 README】：不是盲目变异——GEPA **读执行轨迹理解失败原因**，提出针对性变异；维护候选种群，按 Pareto 前沿多目标评估-选择-迭代。纯 API 调用无需 GPU，每次优化约 $2-10。

**进化什么**（官方分阶段路线图）：Phase 1（已实现）技能文件 SKILL.md → Phase 2 工具描述 → Phase 3 系统提示分段 → Phase 4 工具实现代码 → Phase 5 持续改进闭环。

**怎么评估好坏**【官方】：eval 数据集 + 执行轨迹回灌；硬闸门：**pytest 全量 100% 通过**、尺寸限制（技能 ≤15KB）、语义保持检查。

**关键设计——落地方式**：**没有自动排期**，是离线手动触发的优化任务；**"All changes go through human review, never direct commit"**——最优变体以 **PR 形式提交**，人审合并后随版本发布。即 GEPA 目前是**舰队级**进化（所有用户共享的内置技能/提示词），不是个人实例在线自进化；个人实例的"越用越强"走技能 patch/后台复查路径。

**【未证实，勿引用】**："GEPA 带来 33-38% SWE-bench 提升"——官方 README 无任何基准数字，判定为不可靠转述。

**另一条"进化"线（模型层）**：批量并行轨迹生成、Atropos RL 集成、ShareGPT 轨迹导出用于微调——Nous 的野心是"智能体使用数据 → 反哺开源模型训练"闭环。

---

## 五、自我进化的质量控制：防"越学越错"

不是单一评估器，而是**分层闸门 + 可回滚 + 用量数据驱动的生命周期治理**：

**1. Curator（策展人）——技能库后台维护**【官方】：
- 触发：距上次运行超 168 小时（默认 7 天）**且**智能体空闲超 2 小时
- 确定性动作（自动）：技能 30 天未用标 stale；90 天未用移入 .archive/——判据来自 .usage.json 真实用量
- LLM 整合（**默认关闭**）：开启后 fork 智能体巡检全部自建技能，保留/修补/合并重叠窄技能为伞技能/归档
- 安全网：每次运行前全库 tar.gz 快照；--dry-run；`curator pin` 钉住关键技能（连智能体自己都删不掉）；JSON + REPORT.md 双格式审计报告
- 解决的问题（官方原文）："Without maintenance, you end up with dozens of narrow near-duplicates that pollute the catalog and waste tokens"——**技能膨胀比技能错误更常见**

**2. 写入审批门**：memory.write_approval / skills.write_approval 两个独立开关，默认 false。**默认信任智能体、把审批留给高风险场景**是它的取态。

**3. 内容扫描**：记忆条目入库前扫注入/外泄；自建技能可开 guard_agent_created 扫描器；Hub 技能全量扫。

**4. GEPA 侧**：测试 100% + 尺寸 + 语义保持 + 强制人审 PR。

**5. 文件系统层**：Checkpoints——破坏性操作前自动对项目文件打影子 git 快照，`/rollback <N>` 恢复文件**并同时撤销最后一轮对话**（上下文与文件系统对齐）。

**明确的空白（官方文档未见）**：没有技能 A/B 测试；没有单技能自动化效果评估器（评估只在 GEPA 离线管线）；记忆无自动衰减。个人实例的技能质量依赖"用量数据 + Curator 周期治理 + 可选人审"，而非在线量化评估。

---

## 六、安全与自主权分层（对产品设计最有参考价值）

全部【官方】，细节密度是全文档最高的部分。

**1. 谁能跟它说话（用户授权，6 层从高到低）**：单平台 allow-all → DM 配对通过名单 → 平台白名单 → 全局白名单 → 全局 allow-all → **默认拒绝**。DM 配对：陌生人私聊收 8 位配对码，机主 CLI 批准后永久放行；码 1 小时过期、限流、5 次失败锁 1 小时。

**2. 什么算危险、审批怎么走**：三种模式（approvals.mode）：**manual**（默认，危险命令一律问人）/ **smart**（辅助 LLM 风险评估，拿不准升级问人）/ off。会话级 --yolo 全放行。
- **绝对黑名单（任何模式都拦）**：rm -rf /、fork bomb、mkfs 挂载根分区、dd 写块设备、根目录管道执行不可信 URL
- **需审批的危险命令**（内置模式匹配）：递归删除、chmod 777、DROP TABLE/无 WHERE DELETE、写 /etc/、systemctl stop、curl | sh、写 ~/.ssh/ 等敏感路径
- **用户自定义 deny 规则**（fnmatch 通配），优先级高于 yolo
- **审批交互**：CLI 四选一 [o]nce/[s]ession/[a]lways/[d]eny；**消息平台上直接回复 "yes"/"no" 即审批**（审批流嵌进聊天）；`approvals.timeout: 60` 秒无人响应**默认拒绝**
- **边界转移规则**：docker/singularity/modal/daytona 执行后端**跳过危险命令检查**——"容器本身就是安全边界"。裸机上自主权被命令级审批切细；容器里整体放开、风险由隔离层兜底

**3. 容器隔离**：Docker 默认 --cap-drop ALL、no-new-privileges、pids-limit 256；资源限额 CPU 1 核/内存 5GB/磁盘 50GB；6 种执行后端。

**4. 环境变量过滤**：MCP 子进程只透传安全集，**API 密钥默认全部剥离**；技能可声明 required_environment_variables 定向透传；MCP 错误消息凭据脱敏（[REDACTED]）。

**5. 上下文扫描**：上下文文件注入前扫描（"忽略先前指令"话术、HTML 注释藏指令、读 .env 企图、零宽字符），命中直接 [BLOCKED]；SSRF 防护（内网段/云元数据端点默认全拒）；Tirith 预执行扫描器。

**6. 划线逻辑提炼**：默认态是**行动自由 + 模式化拦截**——普通读写自主；命中危险模式→问人；写自身记忆/技能→默认自主但可一键改全审批；陌生用户→配对审批；容器内→自主权放大。**按"操作的不可逆性"分级，而非按任务类型分级**，且每道闸都是部署者可移动的配置项。**官方无花费上限机制——成本失控防护是空白。**

---

## 七、多智能体：spawn 子代理与 Kanban 看板

**同步子代理（delegate_task）**【官方】：
- `delegate_task(goal, context, toolsets, role)`；role 分 **leaf**（默认，不能再委派）与 **orchestrator**（可再生 worker）；支持批量并行
- 隔离与分工：子代理**零继承父对话历史**，只拿到显式 context + toolsets 白名单工具 + 独立终端会话；**子代理被禁用四类工具**：delegation（leaf 级）、clarify（不能反问用户）、**memory（不能写共享持久记忆——防漂移设计：子代理临时认知不污染主记忆）**、code_execution
- 聚合：只有**最终结构化摘要**回流父上下文
- 限额：默认并发 3、深度 1、每子 50 轮；可为子代理指定更便宜的模型

**Kanban 多代理看板（持久异步协作）**【官方】：官方对比："delegate_task is a function call; Kanban is a work queue where every handoff is a row any profile (or human) can see and edit."
- SQLite 持久任务板 + 状态机（triage|todo|ready|running|blocked|done|archived）；worker 是**有名字、有各自持久记忆的 profile**（而非匿名子代理）；dispatcher 每 60 秒扫描，依赖完成自动推进；连败 2 次自动 block
- 工作区隔离三选一：scratch / dir / worktree
- 人的介入点：任务评论、typed block 原因（needs_input/capability/transient 显式上浮给人）、仪表盘拖拽、/kanban 随时插话
- 协议强制：worker 退出时任务仍 running → 自动判协议违规并 block

**设计动机**：fork-join 并行用 delegate_task（上下文成本最小）；跨天、多角色、需人验收的流水线用 Kanban（可审计、可恢复、人机同板）。**与控制塔多角色智能体需求高度同构。**

---

## 八、工程形态

- **技术栈**：Python 82.5%（3.11+）+ TypeScript 14.8%（终端 UI、Web 面板）；**约 25,000 个 pytest 用例**；存储只有 **SQLite + FTS5 单文件**（无外部数据库依赖——自托管友好的关键决策）
- **部署**：一行安装脚本；Docker 配置随仓库；"$5 VPS 到 GPU 集群"；桌面版/Termux/Nix；多 profile 并行；插件三来源
- **LLM 后端抽象**：`(provider, model)` 解析为 `(api_mode, api_key, base_url)`；**18+ 供应商归一到 3 种 API 模式**：chat_completions（OpenAI 兼容）/ anthropic / codex_responses；配 OAuth、凭据池、fallback 链。注意："200+ LLM 后端"实为"18+ providers"；Nous Portal 聚合 300+ 模型——两个数字别混
- **模型训练闭环**：批量轨迹生成、Atropos RL、ShareGPT 导出

---

## 九、信息冲突与未决问题（写教材前必读）

1. 发布日期：仓库创建 2025-07-22（API 硬事实）vs "2026-02-25 开源发布"（第三方）。表述为"2026 年 2 月公开发布（社区口径），仓库历史可追溯至 2025 年 7 月"。
2. 星数曲线：只有 21.2 万（7/10 API 实测）是核实数字。
3. session_search 有无 LLM 摘要：官方两处表述相反，以 memory 文档页为准。
4. "三层/四层记忆"：均为第三方解读，官方无此分类。
5. "33-38% SWE-bench 提升"：无官方佐证，勿引用。
6. "nudge 每 15 轮"：仅 36kr 提及。
7. hermes-agent.org 归属存疑（GitHub 指定主页是 nousresearch.com 子域）。
8. "五阶段学习闭环"出自官方首页营销文案，是理解模型而非代码模块。

## 十、给创始人的两句机制总结

- **"越用越强"在 Hermes 里是四条独立的腿**：记忆文件（小事实常驻提示）→ 技能文件（成功路径固化为 Markdown 资产，patch 迭代 + Curator 治理）→ GEPA 离线进化（舰队级，出 PR 人审）→ 轨迹导出反哺模型训练。前两条在数据层、随用随长、可回滚；后两条在发布层/权重层、有人审闸门。**没有任何一条是"模型在线自己变聪明"——全部是可审计的文件与流程。**
- **自主权划线的本质是"按不可逆性分级 + 边界可配置"**：可逆操作自主做，不可逆操作模式匹配拦截问人（聊天里回 yes 即批），超时默认拒绝，容器内整体放权，自我修改（记忆/技能）默认放行但一键可改全审——每道闸都是部署者可移动的配置项。

## 信源清单

**官方**：github.com/NousResearch/hermes-agent（+ GitHub API 元数据）；文档站 hermes-agent.nousresearch.com（developer-guide/{architecture, agent-loop, prompt-assembly, gateway-internals}、user-guide/{sessions, security, checkpoints-and-rollback}、user-guide/features/{memory, skills, curator, delegation, kanban, honcho, overview}）；github.com/NousResearch/hermes-agent-self-evolution；hermes-agent.org（归属存疑）

**第三方**：36kr 英文版 eu.36kr.com/en/p/3767963450196480；CSDN blog.csdn.net/RickyIT/article/details/160347751；the-agent-report.com/2026/06/hermes-agent-self-evolution-dspy-gepa-june2026/；知乎多篇（见正文）；社区文档镜像 github.com/mudrii/hermes-agent-docs；GEPA 原始库 github.com/gepa-ai/gepa
