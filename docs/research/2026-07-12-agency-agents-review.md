# agency-agents 仓库研究：对本项目的启发评估

> 2026-07-12。对象：github.com/msitarzewski/agency-agents（131k stars / 21.4k forks，活跃维护）。
> 方法：README 全文 + 目录结构 + 代表性 agent 文件解剖（engineering-multi-agent-systems-architect.md）+ 根目录/分部清单核查。

## 一、它是什么（事实）

**一个 231+ 个"AI 角色人格"的 markdown 文件库**，按 22 个部门（engineering 49 个/marketing/sales/finance/healthcare/gis…）组织，
配转换脚本适配 14+ 宿主工具（Claude Code/Cursor 等）。每个 agent 文件 = frontmatter（name/emoji/description/color/vibe）
+ 人格与沟通风格 + Critical Rules（"Never…"句式的硬约束）+ **深度领域知识正文** + 检查表。

**核查结论（重要）**：它**没有运行时**——无编排引擎（README 的"协作"是场景剧本，靠人/宿主工具驱动）、无 memory 系统
（"pattern-recognition memory"仅是文案）、无评估框架、无强制权限（其"权限矩阵"是写在纸上的建议）。
根目录无 orchestrator/memory/evaluation/workflow 任何实体。**它是静态知识资产库，不是系统。**

## 二、单文件质量超预期（以 multi-agent-systems-architect 为例）

该文件是 5000+ 词的多智能体工程知识文档：5 种编排拓扑（各含失败模式）、上下文预算管理、故障模式工程
（含断路器三态机 CLOSED→OPEN→HALF-OPEN）、最小权限工具矩阵、HITL 门设计、评估驱动部署门
（≥20 用例+基线+回归）、成本治理、**70 项架构评审检查表**。

**独立收敛再+1**（与我们六路评审/教材的结论重合）：
- "Over-escalation: humans rubber-stamp gates → **HITL becomes theater**" ≡ 我们的 93% 盲批教训
- eval-driven deployment gate ≡ 我们的评估闸门
- escalation calibration paradox ≡ 我们的告警疲劳/冷启动静音
- least-privilege tool matrix + 全量审计日志规范 ≡ 我们的工具子集+审计
- "Demos lie; production tells the truth" ≡ 我们的"评审以重跑为准"

## 三、对本项目的四条可操作启发

1. **C3 打法卡（SkillAsset）的格式直接借鉴它的骨架**：frontmatter + 人格/适用场景 + Critical Rules（Never 句式）
   + 领域知识 + 检查表。该格式与 Hermes 技能、agentskills.io 生态同源——我们的打法卡对齐这个事实标准，
   未来可直接吸收社区资产（如它的 feishu-integration-developer、incident-response-commander、
   payments-billing-engineer 等文件在实施对应模块时可直接取材）。
2. **把 multi-agent-systems-architect.md 收为参考文献**，并用它的 70 项检查表对 v2.1+v2.2 做一次
   快速对照——一次免费的"第七路评审"（其中断路器三态机是我们断路器设计的现成精化）。
3. **给三同事补"人格/voice"维度**：我们的同事=工具×权限×提示词，偏机械；它证明"有性格的角色"
   （Communication Style 节：5 个示例姿态）在使用中感知更好。ops/finance/cs 在飞书/企微里的语气人格
   是低成本高感知的产品打磨。
4. **反面教材同样有价值**：231 个角色是"提示词库膨胀"的活例证（Hermes Curator 那课：技能膨胀比技能错误
   更常见）。我们的角色永远是"决策权上的可换标签"（主干哲学），按业务需要生长，不按目录好看生长。

## 四、它没有而我们有的（不必羡慕的部分）

无本体（角色不绑定对象/数据，权限是纸上建议 vs 我们 dispatch 层强制）｜无运行时治理（无审计/maker-checker/
FORBIDDEN/评估闸门）｜无记忆与进化（无先例库/用量治理/版本审批）｜无业务闭环（服务"做软件的人"，
不服务"跑业务的组织"）。**一句话：它是一柜子好西装，我们在造一个会干活的组织——西装可以买它的，骨架必须是自己的。**

## 五、行动项

- [ ] C1/C3 实施时：打法卡模板采用其骨架（frontmatter+Critical Rules+知识+检查表）
- [ ] 取回 multi-agent-systems-architect.md 全文存参考，70 项检查表对照现有设计跑一遍
- [ ] 三同事 voice 设计时参考其 Communication Style 写法
- [ ] 实施飞书集成/事件响应/对账模块时，检索其对应 agent 文件取材
